"""FFmpeg filter graph: cut/concatenate source regions, offset, pan, mix, stack."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
import shutil
import subprocess

from .model import Project, Track


class FFmpegError(RuntimeError):
    pass


def find_binary(name: str) -> str | None:
    return shutil.which(name)


def require_ffmpeg() -> tuple[str, str]:
    ffmpeg = find_binary("ffmpeg")
    ffprobe = find_binary("ffprobe")
    if not ffmpeg or not ffprobe:
        raise FFmpegError("FFmpeg and ffprobe must be installed and available on PATH")
    return ffmpeg, ffprobe


def probe_duration(path: str) -> float | None:
    _, ffprobe = require_ffmpeg()
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        return None
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


@dataclass(frozen=True)
class Slot:
    x: int
    y: int
    width: int
    height: int


def layout_slots(project: Project, n: int) -> list[Slot]:
    if n <= 0:
        return []
    layout = project.layout
    if layout == "auto":
        layout = "single" if n == 1 else "split" if n == 2 else "grid"
    w, h = project.width, project.height
    if layout == "single":
        return [Slot(0, 0, w, h) for _ in range(n)]
    if layout == "split":
        if n > 2:
            return layout_slots(Project(width=w, height=h, layout="grid"), n)
        return [Slot(0, 0, w // 2, h), Slot(w // 2, 0, w - w // 2, h)][:n]
    return [
        Slot(0, 0, w // 2, h // 2), Slot(w // 2, 0, w - w // 2, h // 2),
        Slot(0, h // 2, w // 2, h - h // 2),
        Slot(w // 2, h // 2, w - w // 2, h - h // 2),
    ][:n]


def _media_cuts(index: int, track: Track, kind: str, filters: list[str],
                cut_fade_ms: float = 0.0) -> str:
    """Produce an audio or video label with removed intervals concatenated."""
    regions = track.kept_ranges() if track.source_end is not None else []
    if not regions:
        if track.source_end is not None:
            raise FFmpegError(f"No content left in {track.name}")
        # Legacy JSON files whose duration was never probed.
        label = f"{kind}{index}_raw"
        cmd = (f"trim=start={track.trim_start:.6f},setpts=PTS-STARTPTS" if kind == "v" else
               f"atrim=start={track.trim_start:.6f},asetpts=PTS-STARTPTS")
        filters.append(f"[{index}:{kind}]{cmd}[{label}]")
        return label

    parts = []
    for j, (start, end) in enumerate(regions):
        label = f"{kind}{index}_{j}"
        op = "trim" if kind == "v" else "atrim"
        pts = "setpts" if kind == "v" else "asetpts"
        chain = (f"[{index}:{kind}]{op}=start={start:.6f}:end={end:.6f},"
                 f"{pts}=PTS-STARTPTS")
        if kind == "a" and len(regions) > 1 and cut_fade_ms > 0:
            # Fades are confined to audio segment joins: the video stays a hard cut.
            # Cap both ends to half of this segment so even tiny sections are safe.
            length = end - start
            fade = min(cut_fade_ms / 1000.0, length / 2.0)
            if j > 0:
                chain += f",afade=t=in:st=0:d={fade:.6f}"
            if j < len(regions) - 1:
                chain += f",afade=t=out:st={length - fade:.6f}:d={fade:.6f}"
        filters.append(chain + f"[{label}]")
        parts.append(label)
    if len(parts) == 1:
        return parts[0]
    final = f"{kind}{index}_joined"
    inp = "".join(f"[{p}]" for p in parts)
    # Separate audio/video concats avoid audio/video time-base interdependence.
    if kind == "v":
        filters.append(f"{inp}concat=n={len(parts)}:v=1:a=0[{final}]")
    else:
        filters.append(f"{inp}concat=n={len(parts)}:v=0:a=1[{final}]")
    return final


def _pan_gains(value: float) -> tuple[float, float]:
    """Equal-power mono->stereo panning."""
    theta = (value + 1.0) * math.pi / 4.0
    return math.cos(theta), math.sin(theta)


def project_duration(project: Project) -> float | None:
    """Length in project seconds; None means a legacy file needs a duration probe."""
    if not project.tracks:
        return 0.0
    lengths = [t.effective_duration for t in project.tracks]
    if any(d is None for d in lengths):
        return None
    return max(t.offset + float(d) for t, d in zip(project.tracks, lengths))


def build_export_command(project: Project, output_path: str, *,
                         preview: bool = False, preview_start: float = 0.0,
                         preview_seconds: float | None = None) -> list[str]:
    ffmpeg, _ = require_ffmpeg()
    if not project.tracks:
        raise FFmpegError("Add at least one track")
    if len(project.tracks) > 4:
        raise FFmpegError("At most four simultaneous video takes are supported")
    if project.layout == "single" and len(project.tracks) != 1:
        raise FFmpegError("Single layout requires exactly one take")
    if project.layout == "split" and len(project.tracks) > 2:
        raise FFmpegError("Split layout supports at most two takes")
    if (not math.isfinite(project.cut_fade_ms) or not 0 <= project.cut_fade_ms <= 100):
        raise FFmpegError("Cut fade must be between 0 and 100 ms")
    if project.width <= 0 or project.height <= 0 or project.fps <= 0:
        raise FFmpegError("Invalid export dimensions/frame rate")

    durations: list[float | None] = []
    for t in project.tracks:
        try:
            d = t.effective_duration
        except ValueError as e:
            raise FFmpegError(str(e)) from e
        if not math.isfinite(t.offset) or t.offset < 0:
            raise FFmpegError(f"{t.name}: offset must be non-negative")
        if not math.isfinite(t.volume) or t.volume < 0:
            raise FFmpegError(f"{t.name}: volume must be non-negative")
        if not math.isfinite(t.pan) or not -1.0 <= t.pan <= 1.0:
            raise FFmpegError(f"{t.name}: pan must be between -1 and +1")
        durations.append(d)

    duration = project_duration(project)
    if duration is not None and duration <= 0:
        raise FFmpegError("No content to export")
    any_solo = any(t.solo for t in project.tracks)

    cmd = [ffmpeg, "-hide_banner", "-y"]
    for t in project.tracks:
        cmd.extend(["-i", t.path])
    filters: list[str] = []
    vlabels: list[str] = []
    alabels: list[str] = []
    slots = layout_slots(project, len(project.tracks))

    for i, (track, slot, length) in enumerate(zip(project.tracks, slots, durations)):
        source_v = _media_cuts(i, track, "v", filters)
        source_a = _media_cuts(i, track, "a", filters, project.cut_fade_ms)
        vlabel, alabel = f"v{i}", f"a{i}"
        vlabels.append(vlabel)
        alabels.append(alabel)
        # Normalize the frame rate before padding to make duration predictable.
        video = (f"[{source_v}]scale={slot.width}:{slot.height}:"
                 f"force_original_aspect_ratio=decrease,"
                 f"pad={slot.width}:{slot.height}:(ow-iw)/2:(oh-ih)/2,"
                 f"setsar=1,fps={project.fps}")
        if track.offset > 0:
            video += f",tpad=start_duration={track.offset:.6f}:start_mode=add:color=black"
        audio = f"[{source_a}]aformat=sample_fmts=fltp:channel_layouts=mono"
        if track.offset > 0:
            audio += f",adelay={int(round(track.offset * 1000))}:all=1"

        if duration is not None and length is not None:
            tail = max(0.0, duration - (track.offset + length))
            if tail > 1e-6:
                video += f",tpad=stop_duration={tail:.6f}:stop_mode=add:color=black"
                audio += f",apad=pad_dur={tail:.6f}"
        filters.append(video + f"[{vlabel}]")
        vol = track.volume if not track.muted and (track.solo or not any_solo) else 0.0
        left, right = _pan_gains(track.pan)
        audio += (f",volume={vol:.6f},"
                  f"pan=stereo|c0={left:.8f}*c0|c1={right:.8f}*c0")
        filters.append(audio + f"[{alabel}]")

    if len(vlabels) == 1:
        filters.append(f"[{vlabels[0]}]format=yuv420p[vout]")
    else:
        positions = "|".join(f"{s.x}_{s.y}" for s in slots)
        inputs = "".join(f"[{label}]" for label in vlabels)
        filters.append(
            f"{inputs}xstack=inputs={len(vlabels)}:layout={positions}:"
            "fill=black,format=yuv420p[vout]"
        )
    if len(alabels) == 1:
        filters.append(f"[{alabels[0]}]anull[aout]")
    else:
        inputs = "".join(f"[{label}]" for label in alabels)
        filters.append(f"{inputs}amix=inputs={len(alabels)}:duration=longest:normalize=0[aout]")
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264",
        "-preset", "ultrafast" if preview else "medium",
        "-crf", "29" if preview else "18",
        "-c:a", "aac", "-b:a", "128k" if preview else "192k",
        "-movflags", "+faststart",
    ]
    if preview:
        if not math.isfinite(preview_start) or preview_start < 0:
            raise FFmpegError("Preview start must be a nonnegative finite number")
        if preview_seconds is not None and (not math.isfinite(preview_seconds) or preview_seconds <= 0):
            raise FFmpegError("Preview length must be positive")
        if duration is not None and preview_start >= duration:
            raise FFmpegError("Preview starts beyond the end of this project")
        if preview_start > 0:
            # Output-side seeking preserves the original graph's trim, cut, and
            # delay semantics. FFmpeg decodes earlier frames when necessary.
            cmd += ["-ss", f"{preview_start:.6f}"]
        if duration is not None:
            remaining = duration - preview_start
            segment = min(remaining, preview_seconds) if preview_seconds is not None else remaining
            cmd += ["-t", f"{segment:.6f}"]
        elif preview_seconds is not None:
            cmd += ["-t", f"{preview_seconds:.6f}"]
        else:
            cmd += ["-shortest"]
    elif duration is not None:
        cmd += ["-t", f"{duration:.6f}"]
    else:
        cmd += ["-shortest"]
    cmd.append(output_path)
    return cmd


def build_preview_command(project: Project, output_path: str, *,
                          start: float = 0.0, seconds: float | None = 20.0,
                          max_width: int = 640, max_fps: int = 24) -> list[str]:
    """Generate a quick-to-decode *accurate* composite proxy using the export graph.

    Video frames are reduced, but source time edits and audio routing are shared
    exactly with the final export. This is an offline preview, not a live mixer.
    """
    if max_width < 2 or max_fps < 1:
        raise FFmpegError("Invalid preview quality settings")
    factor = min(1.0, max_width / project.width)
    w = max(2, int(project.width * factor) // 2 * 2)
    h = max(2, int(project.height * factor) // 2 * 2)
    smaller = replace(project, width=w, height=h, fps=min(project.fps, max_fps))
    return build_export_command(smaller, output_path, preview=True,
                                preview_start=start, preview_seconds=seconds)
