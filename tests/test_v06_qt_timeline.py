"""Optional v0.6 Qt behavior tests, run with QT_QPA_PLATFORM=offscreen."""
from __future__ import annotations

import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
pytest.importorskip('PySide6.QtWidgets')
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from covermaker.model import Project, Track
from covermaker.timeline import TimelineWidget


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def test_source_zoom_coordinates_and_marker_snap(app):
    w = TimelineWidget()
    t = Track('guitar.mp4', duration=10.0, sync_marker=5.0, cuts=[[4.0, 6.0]])
    w.resize(920, 230)
    w.set_project(Project(tracks=[t]))
    w.set_selected_index(0)
    w.set_source_mode(True)
    w.set_position(5.0)
    w.set_zoom(2)
    assert w._viewport() == pytest.approx((2.5, 5.0))
    assert w._source_at(w._source_x(5.0)) == pytest.approx(5.0)
    w.show()
    app.processEvents()
    edits = []
    w.cutEdgeEdited.connect(lambda idx, edge, val: edits.append((idx, edge, val)))
    start = QPoint(round(w._source_x(4)), 100)
    finish = QPoint(round(w._source_x(4.96)), 100)
    QTest.mousePress(w, Qt.LeftButton, pos=start)
    QTest.mouseMove(w, pos=finish)
    QTest.mouseRelease(w, Qt.LeftButton, pos=finish)
    assert edits == [(0, 'start', pytest.approx(5.0))]
    w.close()


def test_composite_drag_marker_snaps(app):
    w = TimelineWidget()
    a = Track('a.mp4', duration=10, offset=0, sync_marker=1.0)
    b = Track('b.mp4', duration=10, offset=0.5, sync_marker=1.0)
    w.resize(920, 230)
    w.set_project(Project(tracks=[a, b]))
    w.show()
    app.processEvents()
    edits = []
    w.offsetEdited.connect(lambda idx, value: edits.append((idx, value)))
    start = QPoint(round(w._project_x(3)), 35)
    finish = QPoint(round(w._project_x(3.49)), 35)
    QTest.mousePress(w, Qt.LeftButton, pos=start)
    QTest.mouseMove(w, pos=finish)
    QTest.mouseRelease(w, Qt.LeftButton, pos=finish)
    assert len(edits) == 1
    assert edits[0] == (0, pytest.approx(0.5))
    w.close()
