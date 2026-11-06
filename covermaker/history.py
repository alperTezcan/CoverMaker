"""Qt-independent, bounded undo/redo for edits to CoverMaker project JSON.

Only successful user actions are recorded; transient UI/player state is omitted.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from .model import Project


@dataclass(frozen=True)
class HistoryEntry:
    label: str
    before: dict
    after: dict


class ProjectHistory:
    def __init__(self, limit: int = 100) -> None:
        if limit < 1:
            raise ValueError("History limit must be positive")
        self.limit = limit
        self._undo: list[HistoryEntry] = []
        self._redo: list[HistoryEntry] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str | None:
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    def record(self, before: dict, after: dict, label: str) -> bool:
        """Record a committed action; return False for a no-op."""
        if before == after:
            return False
        self._undo.append(HistoryEntry(label, deepcopy(before), deepcopy(after)))
        if len(self._undo) > self.limit:
            del self._undo[: len(self._undo) - self.limit]
        self._redo.clear()
        return True

    def undo(self) -> Project | None:
        if not self._undo:
            return None
        entry = self._undo.pop()
        self._redo.append(entry)
        return Project.from_dict(deepcopy(entry.before))

    def redo(self) -> Project | None:
        if not self._redo:
            return None
        entry = self._redo.pop()
        self._undo.append(entry)
        return Project.from_dict(deepcopy(entry.after))
