from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
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
        raise FFmpegError(
            "FFmpeg/ffprobe were not found on PATH. Install FFmpeg and restart CoverMaker."
        )
    return ffmpeg, ffprobe


def probe_duration(path: str) -> float | None:
    _, ffprobe = require_ffmpeg()
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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
        if n == 1:
            layout = "single"
        elif n == 2:
            layout = "split"
        else:
            layout = "grid"

    w, h = project.width, project.height
    if layout == "single":
        return [Slot(0, 0, w, h) for _ in range(n)]

    if layout == "split":
        cell_w = w // 2
        slots = [Slot(0, 0, cell_w, h), Slot(cell_w, 0, w - cell_w, h)]
        if n > 2:
            # Predictable fallback rather than silently overlapping extra tracks.
            return layout_slots(Project(width=w, height=h, layout="grid"), n)
        return slots[:n]

    # 2x2 grid for now. More than four takes are intentionally rejected by the renderer.
    cell_w, cell_h = w // 2, h // 2
    slots = [
        Slot(0, 0, cell_w, cell_h),
        Slot(cell_w, 0, w - cell_w, cell_h),
        Slot(0, cell_h, cell_w, h - cell_h),
        Slot(cell_w, cell_h, w - cell_w, h - cell_h),
    ]
    return slots[:n]


def _trim_filter(track: Track, media: str) -> str:
    start = max(0.0, track.trim_start)
    end = track.trim_end
    if end is not None and end <= start:
        raise FFmpegError(f"Track '{track.name}' has trim_end <= trim_start")

    if media == "v":
        f = f"trim=start={start:.6f}"
        if end is not None:
            f += f":end={end:.6f}"
        f += ",setpts=PTS-STARTPTS"
        if track.offset > 0:
            f += f",tpad=start_duration={track.offset:.6f}:start_mode=add:color=black"
        return f

    f = f"atrim=start={start:.6f}"
    if end is not None:
        f += f":end={end:.6f}"
    f += ",asetpts=PTS-STARTPTS"
    if track.offset > 0:
        delay_ms = int(round(track.offset * 1000))
        f += f",adelay={delay_ms}:all=1"
    return f


def build_export_command(project: Project, output_path: str) -> list[str]:
    ffmpeg, _ = require_ffmpeg()
    if not project.tracks:
        raise FFmpegError("Project has no tracks")
    if len(project.tracks) > 4:
        raise FFmpegError("CoverMaker currently supports at most four simultaneous video takes")
    if project.layout == "single" and len(project.tracks) != 1:
        raise FFmpegError("The single layout requires exactly one video take")
    if project.layout == "split" and len(project.tracks) > 2:
        raise FFmpegError("The split layout supports at most two video takes")
    if any(t.offset < 0 for t in project.tracks):
        raise FFmpegError("Offsets must be >= 0 seconds")

    slots = layout_slots(project, len(project.tracks))

    known_ends = []
    for track in project.tracks:
        d = track.effective_duration
        if d is not None:
            known_ends.append(track.offset + d)
    project_duration = max(known_ends) if len(known_ends) == len(project.tracks) else None

    cmd = [ffmpeg, "-y"]
    for track in project.tracks:
        cmd += ["-i", track.path]

    filters: list[str] = []
    vlabels: list[str] = []
    alabels: list[str] = []

    for i, (track, slot) in enumerate(zip(project.tracks, slots)):
        vlabel = f"v{i}"
        alabel = f"a{i}"
        vlabels.append(vlabel)
        alabels.append(alabel)

        scale_pad = (
            f"scale={slot.width}:{slot.height}:force_original_aspect_ratio=decrease,"
            f"pad={slot.width}:{slot.height}:(ow-iw)/2:(oh-ih)/2"
        )
        vfilter = f"[{i}:v]{_trim_filter(track, 'v')},{scale_pad},fps={project.fps}"
        afilter = f"[{i}:a]{_trim_filter(track, 'a')}"
        if project_duration is not None and track.effective_duration is not None:
            tail = max(0.0, project_duration - (track.offset + track.effective_duration))
            if tail > 1e-6:
                vfilter += f",tpad=stop_duration={tail:.6f}:stop_mode=clone"
                afilter += f",apad=pad_dur={tail:.6f}"
        filters.append(vfilter + f"[{vlabel}]")

        volume = 0.0 if track.muted else max(0.0, track.volume)
        filters.append(afilter + f",volume={volume:.4f}[{alabel}]")

    if len(vlabels) == 1:
        filters.append(f"[{vlabels[0]}]format=yuv420p[vout]")
    else:
        layout = "|".join(f"{s.x}_{s.y}" for s in slots)
        vin = "".join(f"[{label}]" for label in vlabels)
        filters.append(
            f"{vin}xstack=inputs={len(vlabels)}:layout={layout}:fill=black,format=yuv420p[vout]"
        )

    ain = "".join(f"[{label}]" for label in alabels)
    if len(alabels) == 1:
        filters.append(f"[{alabels[0]}]anull[aout]")
    else:
        filters.append(
            f"{ain}amix=inputs={len(alabels)}:duration=longest:normalize=0[aout]"
        )

    cmd += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
    ]
    if project_duration is not None:
        cmd += ["-t", f"{project_duration:.6f}"]
    else:
        cmd += ["-shortest"]
    cmd += [output_path]
    return cmd
