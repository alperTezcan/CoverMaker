"""Qt-independent composite preview identity and window computation."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .ffmpeg import FFmpegError, project_duration
from .model import Project


def preview_window(project: Project, start: float, seconds: float | None) -> tuple[float, float]:
    """Return [start, end] of the project-time portion being rendered."""
    if not math.isfinite(start) or start < 0:
        raise FFmpegError("Preview start must be >= 0")
    if seconds is not None and (not math.isfinite(seconds) or seconds <= 0):
        raise FFmpegError("Preview length must be > 0")
    total = project_duration(project)
    if total is None:
        raise FFmpegError("Unknown video duration: re-import or probe the source")
    if total <= 0 or start >= total:
        raise FFmpegError("Preview must start before the project ends")
    return start, min(total, start + seconds) if seconds is not None else total


def preview_identity(project: Project, start: float, seconds: float | None) -> str:
    """Hash edit state, source file identity, and window for a reusable proxy."""
    files = []
    for t in project.tracks:
        path = Path(t.path)
        try:
            stat = path.stat()
            files.append([str(path.resolve()), stat.st_size, stat.st_mtime_ns])
        except OSError:
            # The renderer will raise the useful error when opening the file.
            files.append([t.path, None, None])
    blob = json.dumps({"project": project.to_dict(), "files": files,
                       "start": start, "seconds": seconds,
                       "preview": {"max_width": 640, "max_fps": 24, "version": 1}},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]
