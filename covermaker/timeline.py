from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QWidget

from .model import Project


class TimelineWidget(QWidget):
    """Small waveform/timing overview; intentionally not a full NLE timeline."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = Project()
        self.waveforms: dict[str, np.ndarray] = {}
        self.setMinimumHeight(150)

    def set_project(self, project: Project) -> None:
        self.project = project
        self.update()

    def set_waveform(self, path: str, peaks: np.ndarray) -> None:
        self.waveforms[path] = peaks
        self.update()

    def clear_waveforms(self) -> None:
        self.waveforms.clear()
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), self.palette().base())

        tracks = self.project.tracks
        if not tracks:
            painter.drawText(self.rect(), Qt.AlignCenter, "Add a take to begin")
            return

        total = 1.0
        for t in tracks:
            d = t.effective_duration or 10.0
            total = max(total, t.offset + d)

        left, right, top = 110, 12, 10
        row_h = max(28, min(42, (self.height() - 20) // len(tracks)))
        usable = max(1, self.width() - left - right)
        text_pen = QPen(self.palette().text().color())
        painter.setPen(text_pen)

        for i, t in enumerate(tracks):
            y = top + i * row_h
            painter.drawText(8, y + 20, t.name[:16])
            d = t.effective_duration or max(1.0, total - t.offset)
            x = left + usable * (t.offset / total)
            w = max(3.0, usable * (d / total))
            rect = QRectF(x, y + 3, min(w, left + usable - x), row_h - 7)
            painter.drawRoundedRect(rect, 3, 3)

            peaks = self.waveforms.get(t.path)
            if peaks is not None and peaks.size > 1 and rect.width() > 4:
                painter.save()
                painter.setClipRect(rect)
                center = rect.center().y()
                amp = rect.height() * 0.40
                step = max(1, int(np.ceil(peaks.size / max(1.0, rect.width()))))
                visible = peaks[::step]
                for j, value in enumerate(visible):
                    px = rect.left() + (j / max(1, len(visible) - 1)) * rect.width()
                    dy = float(value) * amp
                    painter.drawLine(int(px), int(center - dy), int(px), int(center + dy))
                painter.restore()

            if t.sync_marker is not None:
                local = t.sync_marker - t.trim_start
                if 0.0 <= local <= d:
                    marker_global = t.offset + local
                    mx = left + usable * (marker_global / total)
                    painter.drawLine(int(mx), int(rect.top()), int(mx), int(rect.bottom()))
                    painter.drawText(int(mx) + 3, int(rect.top()) + 11, "clap")
