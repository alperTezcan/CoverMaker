from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
import os
import subprocess

import numpy as np

from .ffmpeg import FFmpegError, require_ffmpeg


SAMPLE_RATE = 8000
MAX_ANALYSIS_SECONDS = 90.0


@dataclass(frozen=True)
class TransientDetection:
    time: float
    confidence: float


def _cache_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        path = root / "CoverMaker" / "cache"
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        path = root / "covermaker"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_path(path: str | Path, sample_rate: int, max_seconds: float) -> Path:
    p = Path(path).resolve()
    stat = p.stat()
    token = f"{p}|{stat.st_mtime_ns}|{stat.st_size}|{sample_rate}|{max_seconds}".encode()
    return _cache_dir() / f"{sha1(token).hexdigest()}.npz"


def extract_mono_pcm(
    path: str | Path,
    sample_rate: int = SAMPLE_RATE,
    max_seconds: float = MAX_ANALYSIS_SECONDS,
) -> np.ndarray:
    """Extract low-rate mono float32 PCM using FFmpeg, with a disk cache."""
    cache = _cache_path(path, sample_rate, max_seconds)
    if cache.exists():
        try:
            with np.load(cache) as data:
                return data["samples"].astype(np.float32, copy=False)
        except Exception:
            cache.unlink(missing_ok=True)

    ffmpeg, _ = require_ffmpeg()
    cmd = [
        ffmpeg,
        "-v",
        "error",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-t",
        f"{max_seconds:.3f}",
        "-f",
        "f32le",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace")
        raise FFmpegError(f"Could not extract audio from {path}:\n{err[-2000:]}")

    samples = np.frombuffer(proc.stdout, dtype=np.float32).copy()
    if samples.size == 0:
        raise FFmpegError(f"No audio samples found in {path}")

    np.savez_compressed(cache, samples=samples)
    return samples


def waveform_peaks(samples: np.ndarray, bins: int = 1000) -> np.ndarray:
    """Return normalized peak amplitudes for cheap waveform drawing."""
    if samples.size == 0:
        return np.zeros(0, dtype=np.float32)
    bins = max(1, min(int(bins), samples.size))
    edges = np.linspace(0, samples.size, bins + 1, dtype=int)
    out = np.empty(bins, dtype=np.float32)
    abs_samples = np.abs(samples)
    for i in range(bins):
        chunk = abs_samples[edges[i] : edges[i + 1]]
        out[i] = float(chunk.max()) if chunk.size else 0.0
    peak = float(out.max())
    if peak > 1e-12:
        out /= peak
    return out


def _moving_mean(x: np.ndarray, window: int) -> np.ndarray:
    window = max(1, int(window))
    if window == 1:
        return x.astype(np.float32, copy=False)
    kernel = np.ones(window, dtype=np.float32) / window
    return np.convolve(x, kernel, mode="same").astype(np.float32, copy=False)


def detect_first_prominent_transient(
    samples: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    search_seconds: float = 12.0,
    ignore_first_seconds: float = 0.25,
) -> TransientDetection | None:
    """
    Detect the first strong short transient, intended for a pre-song hand clap.

    This deliberately favors the *first prominent* transient rather than the
    globally loudest sample, because the song itself may later be louder.
    """
    if samples.size < sample_rate // 2:
        return None

    n = min(samples.size, int(search_seconds * sample_rate))
    x = samples[:n].astype(np.float32, copy=False)
    x = x - float(np.mean(x))

    # 4 ms absolute-amplitude envelope, then compare against a slower 120 ms floor.
    fast = _moving_mean(np.abs(x), max(1, int(0.004 * sample_rate)))
    slow = _moving_mean(fast, max(1, int(0.120 * sample_rate)))
    score = fast / (slow + 1e-6)

    start = min(n - 1, int(ignore_first_seconds * sample_rate))
    if start >= n - 1:
        return None

    local_fast = fast[start:]
    local_score = score[start:]
    amplitude_floor = float(np.percentile(local_fast, 75)) + 1e-6
    strong_amp = local_fast >= max(amplitude_floor * 4.0, float(local_fast.max()) * 0.18)
    strong_rise = local_score >= 3.0
    candidates = np.flatnonzero(strong_amp & strong_rise)

    if candidates.size:
        # Collapse contiguous samples and pick the peak within the first group.
        first = int(candidates[0])
        group_end = first
        while group_end + 1 < local_fast.size and strong_amp[group_end + 1] and strong_rise[group_end + 1]:
            group_end += 1
        rel = first + int(np.argmax(local_fast[first : group_end + 1]))
    else:
        # Fallback: strongest rise. Confidence will communicate that it is weaker.
        rel = int(np.argmax(local_score))

    idx = start + rel
    confidence = float(score[idx])
    return TransientDetection(time=idx / sample_rate, confidence=confidence)


def _onset_envelope(samples: np.ndarray, sample_rate: int, envelope_hz: int = 100) -> np.ndarray:
    """Low-rate positive-onset envelope for correlation fallback."""
    hop = max(1, sample_rate // envelope_hz)
    n = (samples.size // hop) * hop
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    frames = np.abs(samples[:n]).reshape(-1, hop)
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    onset = np.maximum(0.0, np.diff(rms, prepend=rms[0]))
    onset -= float(onset.mean())
    norm = float(np.linalg.norm(onset))
    if norm > 1e-12:
        onset /= norm
    return onset.astype(np.float32, copy=False)


def estimate_correlation_lag(
    reference: np.ndarray,
    target: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    search_seconds: float = 20.0,
    max_lag_seconds: float = 6.0,
) -> tuple[float, float] | None:
    """
    Estimate correlation lag in seconds using low-rate onset envelopes.

    Returns (lag, confidence). A negative lag means matching content appears
    later inside the target file than in the reference file.
    """
    limit = int(search_seconds * sample_rate)
    a = _onset_envelope(reference[:limit], sample_rate)
    b = _onset_envelope(target[:limit], sample_rate)
    if a.size < 20 or b.size < 20:
        return None

    corr = np.correlate(a, b, mode="full")
    lags = np.arange(-len(b) + 1, len(a))
    max_lag = int(max_lag_seconds * 100)
    mask = np.abs(lags) <= max_lag
    if not np.any(mask):
        return None
    sub = corr[mask]
    sub_lags = lags[mask]
    i = int(np.argmax(sub))
    peak = float(sub[i])
    # Rough confidence: peak compared to robust background magnitude.
    background = float(np.percentile(np.abs(sub), 90)) + 1e-9
    confidence = peak / background
    return float(sub_lags[i] / 100.0), float(confidence)


def normalized_offsets(origins: list[float]) -> list[float]:
    """Convert per-file event/origin times into non-negative timeline offsets."""
    if not origins:
        return []
    latest = max(origins)
    return [max(0.0, latest - value) for value in origins]
