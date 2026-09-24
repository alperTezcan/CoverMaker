from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json


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
    sync_marker: float | None = None
    sync_confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.name:
            self.name = Path(self.path).stem

    @property
    def effective_duration(self) -> float | None:
        end = self.trim_end if self.trim_end is not None else self.duration
        if end is None:
            return None
        return max(0.0, end - self.trim_start)


@dataclass
class Project:
    title: str = "Untitled cover"
    width: int = 1280
    height: int = 720
    fps: int = 30
    layout: str = "auto"
    tracks: list[Track] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "layout": self.layout,
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
        )
        p.tracks = [Track(**t) for t in data.get("tracks", [])]
        return p

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
