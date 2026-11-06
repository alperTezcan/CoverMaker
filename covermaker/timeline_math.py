"""Coordinate calculations for timeline interaction, independent of Qt."""
from __future__ import annotations

import math


def x_to_seconds(x: float, left: float, width: float, duration: float) -> float:
    if width <= 0 or duration < 0 or not all(map(math.isfinite, (x, left, width, duration))):
        raise ValueError("Invalid timeline geometry")
    return max(0.0, min(duration, (x - left) / width * duration))


def seconds_to_x(seconds: float, left: float, width: float, duration: float) -> float:
    if width <= 0 or duration <= 0:
        raise ValueError("Invalid timeline geometry")
    return left + seconds / duration * width


def offset_after_drag(original: float, pixel_delta: float, width: float, duration: float) -> float:
    """Non-negative project-time offset rounded to 1 ms."""
    if width <= 0 or duration <= 0 or not math.isfinite(pixel_delta):
        raise ValueError("Invalid drag geometry")
    return max(0.0, round(original + pixel_delta * duration / width, 3))


def visible_window(total: float, zoom: float, start: float) -> tuple[float, float]:
    """Clamp viewport and return (first visible second, displayed duration)."""
    if not all(math.isfinite(v) for v in (total, zoom, start)) or total <= 0 or zoom < 1:
        raise ValueError("Invalid timeline viewport")
    span = total / zoom
    return max(0.0, min(start, total - span)), span


def snap_seconds(value: float, targets: list[float], seconds_per_pixel: float,
                 threshold_pixels: float = 10.0) -> tuple[float, bool]:
    """Find nearest candidate within a fixed pixel radius, independent of zoom."""
    if seconds_per_pixel <= 0 or not math.isfinite(seconds_per_pixel):
        raise ValueError("Invalid timeline scale")
    if not targets:
        return value, False
    target = min(targets, key=lambda t: abs(t - value))
    if abs(target - value) <= threshold_pixels * seconds_per_pixel:
        return target, True
    return value, False


def snap_track_offset(candidate: float, moving_marker_local: float | None,
                      other_project_markers: list[float], seconds_per_pixel: float,
                      threshold_pixels: float = 10.0) -> tuple[float, bool]:
    """Align a moving take's source-derived marker to another take's project marker."""
    if moving_marker_local is None or not other_project_markers:
        return candidate, False
    target, snapped = snap_seconds(candidate + moving_marker_local,
                                   other_project_markers, seconds_per_pixel,
                                   threshold_pixels)
    if snapped and target >= moving_marker_local:
        return round(target - moving_marker_local, 3), True
    return candidate, False
