"""Optional GUI interaction tests: run when PySide6 is installed.

On headless CI: QT_QPA_PLATFORM=offscreen python -m pytest -q
"""
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


def test_dragging_take_emits_one_offset_edit(app):
    w = TimelineWidget()
    w.resize(920, 230)
    w.set_project(Project(tracks=[Track('a.mp4', duration=8, offset=1), Track('b.mp4', duration=8)]))
    w.show()
    app.processEvents()
    edits = []
    w.offsetEdited.connect(lambda index, value: edits.append((index, value)))
    start = QPoint(240, 35)
    end = QPoint(305, 35)
    QTest.mousePress(w, Qt.LeftButton, pos=start)
    QTest.mouseMove(w, pos=end)
    QTest.mouseRelease(w, Qt.LeftButton, pos=end)
    assert len(edits) == 1
    assert edits[0][0] == 0
    assert edits[0][1] > 1
    # Preview gesture does not directly mutate model; controller records once.
    assert w.project.tracks[0].offset == 1
    w.close()


def test_dragging_cut_handle_emits_source_seconds(app):
    w = TimelineWidget()
    w.resize(920, 230)
    t = Track('a.mp4', duration=10)
    t.add_cut(4, 6)
    w.set_project(Project(tracks=[t]))
    w.set_selected_index(0)
    w.set_source_mode(True)
    w.show()
    app.processEvents()
    edits = []
    w.cutEdgeEdited.connect(lambda idx, edge, value: edits.append((idx, edge, value)))
    left = w._source_x(4)
    start = QPoint(round(left), 100)
    end = QPoint(round(w._source_x(5)), 100)
    QTest.mousePress(w, Qt.LeftButton, pos=start)
    QTest.mouseMove(w, pos=end)
    QTest.mouseRelease(w, Qt.LeftButton, pos=end)
    assert len(edits) == 1
    assert edits[0][:2] == (0, 'start')
    assert edits[0][2] == pytest.approx(5, abs=0.02)
    assert t.cuts == [[4, 6]]
    w.close()
