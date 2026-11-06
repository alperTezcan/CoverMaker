"""Small, Qt-independent project model, including source-time non-destructive cuts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path

_EPS = 1e-7


@dataclass
class Track:
    path: str
    name: str = ""
    offset: float = 0.0
    trim_start: float = 0.0
    trim_end: float | None = None
    volume: float = 1.0
    muted: bool = False
    duration: float | None = None
    sync_marker: float | None = None  # source-file seconds, NOT project seconds
    sync_confidence: float | None = None
    cuts: list[list[float]] = field(default_factory=list)  # removed [start,end] source intervals
    solo: bool = False
    pan: float = 0.0  # -1.0 = left; 0.0 = centre; +1.0 = right

    def __post_init__(self) -> None:
        if not self.name:
            self.name = Path(self.path).stem

    @property
    def source_end(self) -> float | None:
        return self.trim_end if self.trim_end is not None else self.duration

    def kept_ranges(self) -> list[tuple[float, float]]:
        """Ordered source intervals surviving trim + middle cuts.

        `cuts` are always in original video time and may overlap. Edits are
        non-destructive and do not change the original recording.
        """
        start = float(self.trim_start)
        end = self.source_end
        if not math.isfinite(start) or start < 0:
            raise ValueError(f"{self.name}: trim start must be >= 0")
        if end is None:
            if self.cuts:
                raise ValueError(f"{self.name}: source duration is needed for middle cuts")
            return []  # unknown duration; renderer may use open-ended trim
        end = float(end)
        if not math.isfinite(end) or end <= start + _EPS:
            raise ValueError(f"{self.name}: trim end must exceed trim start")
        if self.duration is not None and end > self.duration + 0.001:
            raise ValueError(f"{self.name}: trim end exceeds source duration")

        removed: list[tuple[float, float]] = []
        for pair in self.cuts:
            if len(pair) != 2:
                raise ValueError(f"{self.name}: invalid cut interval {pair!r}")
            a, b = map(float, pair)
            if not all(math.isfinite(v) for v in (a, b)) or a < 0 or a >= b:
                raise ValueError(f"{self.name}: cut start must be < cut end")
            a, b = max(start, a), min(end, b)
            if b - a > _EPS:
                removed.append((a, b))
        removed.sort()
        merged: list[list[float]] = []
        for a, b in removed:
            if merged and a <= merged[-1][1] + _EPS:
                merged[-1][1] = max(b, merged[-1][1])
            else:
                merged.append([a, b])
        cursor = start
        kept = []
        for a, b in merged:
            if a > cursor + _EPS:
                kept.append((cursor, a))
            cursor = b
        if cursor < end - _EPS:
            kept.append((cursor, end))
        if not kept:
            raise ValueError(f"{self.name}: all video content was removed")
        return kept

    @property
    def effective_duration(self) -> float | None:
        if self.source_end is None:
            return None
        return sum(b - a for a, b in self.kept_ranges())

    def source_to_local(self, timestamp: float) -> float | None:
        """Map a source timestamp to its position after cutting, or None if cut."""
        if self.source_end is None:
            return max(0.0, timestamp - self.trim_start) if timestamp >= self.trim_start else None
        running = 0.0
        for a, b in self.kept_ranges():
            if a - _EPS <= timestamp <= b + _EPS:
                return running + max(0.0, min(timestamp, b) - a)
            running += b - a
        return None

    def add_cut(self, start: float, end: float) -> None:
        """Add a removed source-time interval, preserving other cuts."""
        if not all(math.isfinite(v) for v in (start, end)) or start < 0 or end <= start:
            raise ValueError("Cut start must be >= 0 and strictly before cut end")
        if self.duration is None:
            raise ValueError("Source duration must be known to add a cut")
        if end > self.duration + 0.001:
            raise ValueError("Cut end exceeds source duration")
        candidate = self.cuts + [[round(start, 3), round(end, 3)]]
        old = self.cuts
        self.cuts = candidate
        try:
            self.kept_ranges()
        except ValueError:
            self.cuts = old
            raise


    def set_cut_edge(self, index: int, edge: str, seconds: float) -> None:
        """Move one source-time cut boundary without changing other cuts.

        Crossing its own opposite edge, a different cut, or deleting all
        surviving material is rejected atomically. Existing overlapping cuts
        from older projects remain readable but cannot be crossed by a drag.
        """
        if edge not in ("start", "end"):
            raise ValueError("Cut edge must be 'start' or 'end'")
        if index < 0 or index >= len(self.cuts):
            raise IndexError("Cut index out of range")
        if self.duration is None:
            raise ValueError("Source duration is required to edit cut handles")
        if not math.isfinite(seconds) or not 0 <= seconds <= self.duration:
            raise ValueError("Cut boundary lies outside the source recording")
        seconds = round(float(seconds), 3)
        previous = [part[:] for part in self.cuts]
        a, b = previous[index]
        if edge == "start":
            a = seconds
        else:
            b = seconds
        if b - a <= 0.001:
            raise ValueError("Cut start must remain before cut end")
        for i, (other_a, other_b) in enumerate(previous):
            if i != index and a < other_b - _EPS and b > other_a + _EPS:
                raise ValueError("Cut handles cannot cross another removed interval")
        self.cuts[index] = [a, b]
        try:
            self.kept_ranges()
        except ValueError:
            self.cuts = previous
            raise

    def set_trim_edge(self, edge: str, seconds: float) -> None:
        """Move trim IN/OUT in original source time (not project time)."""
        if edge not in ("start", "end"):
            raise ValueError("Trim edge must be 'start' or 'end'")
        if self.duration is None:
            raise ValueError("Source duration is required to edit trim handles")
        if not math.isfinite(seconds) or not 0 <= seconds <= self.duration:
            raise ValueError("Trim boundary lies outside the source recording")
        previous = (self.trim_start, self.trim_end)
        seconds = round(float(seconds), 3)
        if edge == "start":
            self.trim_start = seconds
        else:
            self.trim_end = None if abs(seconds - self.duration) < 0.001 else seconds
        try:
            self.kept_ranges()
        except ValueError:
            self.trim_start, self.trim_end = previous
            raise


@dataclass
class Project:
    title: str = "Untitled cover"
    width: int = 1280
    height: int = 720
    fps: int = 30
    layout: str = "auto"
    tracks: list[Track] = field(default_factory=list)
    cut_fade_ms: float = 12.0  # automatic audio fade on either side of a middle-cut join

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "layout": self.layout,
            "cut_fade_ms": self.cut_fade_ms,
            "tracks": [asdict(t) for t in self.tracks],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        p = cls(
            title=data.get("title", "Untitled cover"),
            width=int(data.get("width", 1280)),
            height=int(data.get("height", 720)),
            fps=int(data.get("fps", 30)),
            layout=data.get("layout", "auto"),
            cut_fade_ms=float(data.get("cut_fade_ms", 12.0)),
        )
        # Missing v0.3 fields receive dataclass defaults: old v0.2 JSON works.
        p.tracks = [Track(**t) for t in data.get("tracks", [])]
        return p

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def align_marked_tracks(tracks: list[Track]) -> int:
    """Manually align marked takes, without changing unmarked take offsets.

    Aligns their marked source events at a common nonnegative project time.
    A marker inside a removed region is an error, not a silently changed sync.
    """
    marked = [t for t in tracks if t.sync_marker is not None]
    if len(marked) < 2:
        raise ValueError("Set a manual marker in at least two takes")
    locations = [t.source_to_local(float(t.sync_marker)) for t in marked]
    if any(t is None for t in locations):
        raise ValueError("A sync marker lies in trimmed or removed video; move that marker")
    target = max(float(x) for x in locations)
    for track, local in zip(marked, locations):
        track.offset = round(target - float(local), 3)
    return len(marked)
