"""Interactive two-time-base waveform timeline for CoverMaker.

Composite mode: drag take bodies in PROJECT time to change offsets.
Source mode: drag IN/OUT and A/B handles in ORIGINAL SOURCE time.
Edits are only committed when the mouse is released (one undo step).
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .model import Project, Track
from .timeline_math import (offset_after_drag, seconds_to_x, x_to_seconds,
                            visible_window, snap_seconds, snap_track_offset)


class TimelineWidget(QWidget):
    seekRequested = Signal(float)  # project seconds
    seekSourceRequested = Signal(float)  # original source seconds
    trackSelected = Signal(int)
    offsetEdited = Signal(int, float)
    cutEdgeEdited = Signal(int, str, float)  # cut index, "start"/"end", source time
    trimEdgeEdited = Signal(str, float)  # "start"/"end", source time
    dragStatus = Signal(str)
    viewportChanged = Signal(float, float)  # start second and visible span (UI only)
    zoomRequested = Signal(int)  # +1 or -1 via Ctrl+mouse wheel

    LEFT = 112
    RIGHT = 16
    HIT_RADIUS = 9

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = Project()
        self.selected_index: int | None = None
        self.source_mode = False
        self.position_seconds: float | None = None
        self.waveforms: dict[str, np.ndarray] = {}
        self.waveform_seconds: dict[str, float] = {}
        self._drag: dict | None = None
        self.zoom = 1.0
        self.view_start = 0.0
        self.snap_enabled = True
        self.setMinimumHeight(195)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setToolTip("Composite: drag a take horizontally to sync. Source: drag IN/OUT or A/B cut boundaries.")

    def set_project(self, project: Project) -> None:
        self.project = project
        self._drag = None
        self._update_viewport()
        self.update()

    def set_zoom(self, zoom: float) -> None:
        """Zoom around current playhead if visible, otherwise viewport centre."""
        zoom = max(1.0, min(32.0, float(zoom)))
        old_start, old_span = self._viewport()
        anchor = self.position_seconds
        if anchor is None or not old_start <= anchor <= old_start + old_span:
            anchor = old_start + old_span / 2
        fraction = (anchor - old_start) / old_span
        self.zoom = zoom
        new_span = self._total() / zoom
        self.view_start = anchor - fraction * new_span
        self._update_viewport()
        self.update()

    def set_view_start(self, seconds: float) -> None:
        self.view_start = float(seconds)
        self._update_viewport()
        self.update()

    def set_snap_enabled(self, enabled: bool) -> None:
        self.snap_enabled = bool(enabled)

    def _total(self) -> float:
        return self._source_duration() if self.source_mode else self._total_duration()

    def _viewport(self) -> tuple[float, float]:
        # Keep the visible scale fixed throughout a drag. Otherwise extending
        # the last take changes total duration mid-gesture and makes it jump.
        if self._drag is not None and self._drag.get('kind') == 'offset' and 'view_first' in self._drag:
            return self._drag['view_first'], self._drag['span']
        return visible_window(self._total(), self.zoom, self.view_start)

    def _update_viewport(self) -> None:
        self.view_start, span = self._viewport()
        self.viewportChanged.emit(self.view_start, span)

    def _pixel_scale(self) -> float:
        return self._viewport()[1] / self._width()

    def _project_x(self, sec: float) -> float:
        start, span = self._viewport()
        return self.LEFT + (sec - start) / span * self._width()

    def _project_at(self, x: float) -> float:
        start, span = self._viewport()
        return start + x_to_seconds(x, self.LEFT, self._width(), span)


    def set_selected_index(self, idx: int | None) -> None:
        self.selected_index = idx
        if self.source_mode:
            self._update_viewport()
        self.update()

    def set_source_mode(self, enabled: bool) -> None:
        self._drag = None
        if self.source_mode != enabled:
            self.zoom, self.view_start = 1.0, 0.0
        self.source_mode = enabled
        self._update_viewport()
        self.update()

    def set_waveform(self, path: str, peaks: np.ndarray, analysis_seconds: float) -> None:
        self.waveforms[path] = peaks
        self.waveform_seconds[path] = analysis_seconds
        self.update()

    def clear_waveforms(self) -> None:
        self.waveforms.clear()
        self.waveform_seconds.clear()
        self.update()

    def set_position(self, seconds: float | None) -> None:
        self.position_seconds = seconds
        self.update()

    def _width(self) -> float:
        return max(1.0, self.width() - self.LEFT - self.RIGHT)

    def _total_duration(self) -> float:
        total = 1.0
        for i, track in enumerate(self.project.tracks):
            try:
                d = track.effective_duration or 10.0
            except ValueError:
                d = 10.0
            offset = self._display_offset(i, track)
            total = max(total, offset + d)
        return total

    def _display_offset(self, index: int, track: Track) -> float:
        if self._drag and self._drag['kind'] == 'offset' and self._drag['track'] == index:
            return self._drag['value']
        return track.offset

    def _source_track(self) -> Track | None:
        idx = self.selected_index
        return self.project.tracks[idx] if idx is not None and 0 <= idx < len(self.project.tracks) else None

    def _source_duration(self) -> float:
        t = self._source_track()
        return (t.duration if t and t.duration and t.duration > 0 else 1.0)

    def _source_x(self, seconds: float) -> float:
        first, span = self._viewport()
        return self.LEFT + (seconds - first) / span * self._width()

    def _source_at(self, x: float) -> float:
        first, span = self._viewport()
        return first + x_to_seconds(x, self.LEFT, self._width(), span)

    def _source_handles(self) -> list[tuple[float, str, int, str]]:
        t = self._source_track()
        if t is None or t.duration is None:
            return []
        handles = [(self._source_x(t.trim_start), 'trim', -1, 'start'),
                   (self._source_x(t.source_end or t.duration), 'trim', -1, 'end')]
        for index, (a, b) in enumerate(t.cuts):
            handles.extend(((self._source_x(a), 'cut', index, 'start'),
                            (self._source_x(b), 'cut', index, 'end')))
        return handles

    def _hit_source_handle(self, x: float) -> tuple[str, int, str] | None:
        matches = [(abs(hx - x), kind == 'trim', kind, idx, edge)
                   for hx, kind, idx, edge in self._source_handles()
                   if self.LEFT <= hx <= self.LEFT + self._width() and abs(hx - x) <= self.HIT_RADIUS]
        if not matches:
            return None
        _, _, kind, idx, edge = min(matches)
        return kind, idx, edge

    def _composite_row(self, y: float) -> int | None:
        n = len(self.project.tracks)
        if not n:
            return None
        rh = max(28, min(46, (self.height() - 32) // n))
        row = int((y - 14) // rh)
        if 0 <= row < n and 14 + row * rh <= y <= 14 + (row + 1) * rh:
            return row
        return None

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        x, y = event.position().x(), event.position().y()
        if self.source_mode:
            track = self._source_track()
            if track is None:
                return
            handle = self._hit_source_handle(x)
            if handle and 32 <= y <= self.height() - 18:
                kind, idx, edge = handle
                original = track.trim_start if kind == 'trim' and edge == 'start' else (
                    track.source_end if kind == 'trim' else track.cuts[idx][0 if edge == 'start' else 1]
                )
                self._drag = dict(kind=kind, index=idx, edge=edge,
                                  value=float(original), initial=float(original), start_x=x)
                self.grabMouse()
            else:
                self.seekSourceRequested.emit(self._source_at(x))
        else:
            row = self._composite_row(y)
            if row is not None:
                self.trackSelected.emit(row)
                t = self.project.tracks[row]
                total = self._total_duration()
                try:
                    d = t.effective_duration or 10.0
                except ValueError:
                    d = 10.0
                begin = self._project_x(t.offset)
                finish = self._project_x(t.offset + d)
                if begin - 3 <= x <= finish + 3:
                    first, span = self._viewport()
                    self._drag = dict(kind='offset', track=row, initial=t.offset,
                                      value=t.offset, start_x=x,
                                      view_first=first, span=span)
                    self.grabMouse()
                else:
                    self.seekRequested.emit(self._project_at(x))
            else:
                self.seekRequested.emit(self._project_at(x))
        self.update()

    def mouseMoveEvent(self, event):  # noqa: N802
        x = event.position().x()
        if self._drag:
            d = self._drag
            if d['kind'] == 'offset':
                d['value'] = offset_after_drag(d['initial'], x - d['start_x'], self._width(), d['span'])
                snapped = False
                track = self.project.tracks[d['track']]
                if self.snap_enabled and not (event.modifiers() & Qt.AltModifier):
                    try:
                        local = (track.source_to_local(track.sync_marker)
                                 if track.sync_marker is not None else None)
                        targets = []
                        for i, other in enumerate(self.project.tracks):
                            if i != d['track'] and other.sync_marker is not None:
                                other_local = other.source_to_local(other.sync_marker)
                                if other_local is not None:
                                    targets.append(other.offset + other_local)
                        d['value'], snapped = snap_track_offset(d['value'], local, targets, self._pixel_scale())
                    except ValueError:
                        pass
                d['snapped'] = snapped
                self.dragStatus.emit(f"Move take: {d['value']:.3f} s" +
                                     (' · snapped to sync marker' if snapped else ' · Alt bypasses snapping'))
            else:
                value = self._source_at(x)
                snapped = False
                track = self._source_track()
                if (track is not None and self.snap_enabled and
                        not (event.modifiers() & Qt.AltModifier)):
                    targets = ([track.sync_marker] if track.sync_marker is not None else [])
                    # The currently selected sync marker is a precise source-time reference.
                    value, snapped = snap_seconds(value, targets, self._pixel_scale())
                d['value'] = round(value, 3)
                d['snapped'] = snapped
                label = ('Trim' if d['kind'] == 'trim' else f"Cut {d['index'] + 1}")
                self.dragStatus.emit(f"{label} {d['edge']}: {d['value']:.3f} source s" +
                                     (' · snapped to marker' if snapped else ' · release to apply'))
            self.update()
            return
        if self.source_mode and self._hit_source_handle(x):
            self.setCursor(Qt.SizeHorCursor)
        elif not self.source_mode and self._composite_row(event.position().y()) is not None:
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() != Qt.LeftButton or self._drag is None:
            return super().mouseReleaseEvent(event)
        drag = self._drag
        self._drag = None
        self.releaseMouse()
        self.unsetCursor()
        self.update()
        if abs(event.position().x() - drag['start_x']) < 2:
            if drag['kind'] == 'offset':
                self.seekRequested.emit(self._project_at(event.position().x()))
            return
        if drag['value'] == drag['initial']:
            return
        if drag['kind'] == 'offset':
            self.offsetEdited.emit(drag['track'], drag['value'])
        elif drag['kind'] == 'trim':
            self.trimEdgeEdited.emit(drag['edge'], drag['value'])
        else:
            self.cutEdgeEdited.emit(drag['index'], drag['edge'], drag['value'])

    def wheelEvent(self, event):  # noqa: N802
        if event.modifiers() & Qt.ControlModifier:
            dy = event.angleDelta().y()
            if dy:
                self.zoomRequested.emit(1 if dy > 0 else -1)
                event.accept()
                return
        if event.modifiers() & Qt.ShiftModifier:
            dy = event.angleDelta().y()
            if dy:
                self.set_view_start(self.view_start - (self._viewport()[1] * 0.12 if dy > 0 else -self._viewport()[1] * 0.12))
                event.accept()
                return
        super().wheelEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key_Escape and self._drag:
            self._drag = None
            self.releaseMouse()
            self.unsetCursor()
            self.update()
            self.dragStatus.emit('Drag cancelled')
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), self.palette().base())
        if self.source_mode:
            self._paint_source(p)
        else:
            self._paint_composite(p)

    def _paint_waveform(self, painter: QPainter, rect: QRectF, track: Track,
                        regions: list[tuple[float, float]], duration: float, source: bool,
                        local_start: float = 0.0, sample_span: float | None = None) -> None:
        peaks = self.waveforms.get(track.path)
        analyzed = self.waveform_seconds.get(track.path, 0.0)
        if peaks is None or not peaks.size or analyzed <= 0 or rect.width() <= 4:
            return
        painter.save()
        painter.setClipRect(rect)
        painter.setPen(QPen(self.palette().text().color(), 1))
        count = max(4, min(1800, int(rect.width())))
        middle, amplitude = rect.center().y(), rect.height() * 0.38
        for j in range(count):
            local = local_start + (duration if sample_span is None else sample_span) * j / (count - 1)
            timestamp = local if source else track.trim_start + local
            if not source and regions:
                elapsed = 0.0
                for a, b in regions:
                    if local <= elapsed + b - a + 1e-8:
                        timestamp = a + max(0.0, local - elapsed)
                        break
                    elapsed += b - a
            if timestamp > analyzed:
                continue
            k = min(peaks.size - 1, max(0, int(timestamp / analyzed * peaks.size)))
            dy = float(peaks[k]) * amplitude
            px = int(rect.left() + j / (count - 1) * rect.width())
            painter.drawLine(px, int(middle - dy), px, int(middle + dy))
        painter.restore()

    def _paint_composite(self, p: QPainter) -> None:
        if not self.project.tracks:
            p.setPen(self.palette().text().color())
            p.drawText(self.rect(), Qt.AlignCenter, 'Add a take to begin')
            return
        first, span = self._viewport()
        rh = max(28, min(46, (self.height() - 32) // len(self.project.tracks)))
        for i, t in enumerate(self.project.tracks):
            y = 14 + i * rh
            offset = self._display_offset(i, t)
            name = t.name[:15] + (' ★' if t.solo else '')
            p.setPen(self.palette().text().color())
            p.drawText(5, y + 20, name)
            try:
                regions = t.kept_ranges()
                duration = t.effective_duration or max(1.0, self._total_duration() - offset)
            except ValueError:
                regions, duration = [], max(1.0, self._total_duration() - offset)
            visible_a = max(first, offset)
            visible_b = min(first + span, offset + duration)
            if visible_b <= visible_a:
                continue
            begin = self._project_x(visible_a)
            width = self._project_x(visible_b) - begin
            rect = QRectF(begin, y + 3, width, rh - 7)
            selected = i == self.selected_index
            p.setBrush(QColor('#cfe8ff') if selected else QColor('#e1e8ef'))
            p.setPen(QPen(QColor('#2772a8') if selected else QColor('#8b9baa'), 2 if selected else 1))
            p.drawRoundedRect(rect, 3, 3)
            self._paint_waveform(p, rect, t, regions, duration, False,
                                 visible_a - offset, visible_b - visible_a)
            elapsed = 0.0
            for a, b in regions[:-1]:
                elapsed += b - a
                xx = int(self._project_x(offset + elapsed))
                if rect.left() <= xx <= rect.right():
                    p.setPen(QPen(QColor('#bb6500'), 2))
                    p.drawLine(xx, int(rect.top()), xx, int(rect.bottom()))
                    p.drawText(xx + 2, int(rect.bottom()) - 1, '✂')
            if t.sync_marker is not None:
                try:
                    local = t.source_to_local(t.sync_marker)
                except ValueError:
                    local = None
                if local is not None:
                    xx = int(self._project_x(offset + local))
                    if rect.left() <= xx <= rect.right():
                        p.setPen(QPen(QColor('#136e39'), 2))
                        p.drawLine(xx, int(rect.top()), xx, int(rect.bottom()))
                        p.drawText(xx + 2, int(rect.top()) + 11, 'sync')
        if self.position_seconds is not None and first <= self.position_seconds <= first + span:
            xx = int(self._project_x(self.position_seconds))
            p.setPen(QPen(self.palette().highlight().color(), 2))
            p.drawLine(xx, 0, xx, self.height())
        p.setPen(self.palette().text().color())
        p.drawText(self.LEFT, self.height() - 4,
                   f'Project time · {first:.2f}–{first + span:.2f} s · drag takes · sync snap (Alt bypass)')

    def _paint_source(self, p: QPainter) -> None:
        t = self._source_track()
        if t is None:
            p.drawText(self.rect(), Qt.AlignCenter, 'Select a take to edit its original waveform')
            return
        if t.duration is None or t.duration <= 0:
            p.drawText(self.rect(), Qt.AlignCenter, 'Cannot drag cut handles: source duration is unknown')
            return
        first, span = self._viewport()
        rect = QRectF(self.LEFT, 43, self._width(), max(46, self.height() - 94))
        p.setPen(self.palette().text().color())
        p.drawText(6, 21, t.name[:17])
        p.drawText(self.LEFT, 21, f'Original source: {first:.3f} – {first + span:.3f} s · drag handles · snap to marker')
        p.setBrush(QColor('#eaf1f6'))
        p.setPen(QPen(QColor('#93a9be')))
        p.drawRect(rect)
        self._paint_waveform(p, rect, t, [], t.duration, True, first, span)
        # Gray outside the retained trim region, amber over removed source cuts.
        start, end = t.trim_start, t.source_end or t.duration
        if self._drag and self._drag['kind'] == 'trim':
            if self._drag['edge'] == 'start':
                start = self._drag['value']
            else:
                end = self._drag['value']
        x_start, x_end = self._source_x(start), self._source_x(end)
        p.fillRect(QRectF(rect.left(), rect.top(), max(0, x_start - rect.left()), rect.height()), QColor(40, 40, 40, 85))
        p.fillRect(QRectF(x_end, rect.top(), max(0, rect.right() - x_end), rect.height()), QColor(40, 40, 40, 85))
        for cut_index, (a, b) in enumerate(t.cuts):
            if self._drag and self._drag['kind'] == 'cut' and self._drag['index'] == cut_index:
                if self._drag['edge'] == 'start':
                    a = self._drag['value']
                else:
                    b = self._drag['value']
            xa, xb = self._source_x(min(a, b)), self._source_x(max(a, b))
            p.fillRect(QRectF(xa, rect.top(), max(0, xb - xa), rect.height()), QColor(237, 135, 50, 105))
        for x, kind, idx, edge in self._source_handles():
            if self._drag and self._drag['kind'] == kind and self._drag['index'] == idx and self._drag['edge'] == edge:
                x = self._source_x(self._drag['value'])
            color = QColor('#115a9f') if kind == 'trim' else QColor('#b45108')
            p.setPen(QPen(color, 3))
            p.drawLine(int(x), int(rect.top() - 6), int(x), int(rect.bottom() + 6))
            p.setPen(QPen(color, 1))
            label = ('IN' if edge == 'start' else 'OUT') if kind == 'trim' else f'{"A" if edge == "start" else "B"}{idx + 1}'
            p.drawText(int(x + 3), int(rect.top() - 9 if kind == 'trim' else rect.bottom() + 18), label)
        if t.sync_marker is not None:
            xx = int(self._source_x(t.sync_marker))
            if rect.left() <= xx <= rect.right():
                p.setPen(QPen(QColor('#137a42'), 2, Qt.DashLine))
                p.drawLine(xx, int(rect.top()), xx, int(rect.bottom()))
                p.drawText(xx + 3, int(rect.top()) + 16, 'sync')
        if self.position_seconds is not None and first <= self.position_seconds <= first + span:
            xx = int(self._source_x(max(0.0, min(t.duration, self.position_seconds))))
            p.setPen(QPen(self.palette().highlight().color(), 2))
            p.drawLine(xx, int(rect.top()), xx, int(rect.bottom()))
        p.setPen(self.palette().text().color())
        p.drawText(self.LEFT, self.height() - 5, 'Source time · orange = removed · green = sync marker · Ctrl+wheel zoom / Shift+wheel scroll')
