"""v0.5 edits are atomic and undoable independently from Qt."""
import pytest

from covermaker.history import ProjectHistory
from covermaker.model import Project, Track
from covermaker.timeline_math import x_to_seconds, seconds_to_x, offset_after_drag


def test_cut_handle_edits_update_original_source_time():
    t = Track('guitar.mp4', duration=20.0, trim_start=2.0, trim_end=19.0)
    t.add_cut(7, 9)
    t.set_cut_edge(0, 'start', 6.25)
    t.set_cut_edge(0, 'end', 10.0)
    assert t.cuts == [[6.25, 10.0]]
    assert t.kept_ranges() == [(2.0, 6.25), (10.0, 19.0)]
    assert t.source_to_local(11.0) == pytest.approx(5.25)
    assert t.effective_duration == pytest.approx(13.25)


def test_cut_handle_rejects_crossed_edges_atomically():
    t = Track('x.mp4', duration=10)
    t.add_cut(4, 6)
    for end in (3.99, 4.0):
        with pytest.raises(ValueError):
            t.set_cut_edge(0, 'end', end)
        assert t.cuts == [[4, 6]]
    with pytest.raises(ValueError):
        t.set_cut_edge(0, 'start', 8.1)
    assert t.cuts == [[4, 6]]


def test_cut_handle_cannot_cross_adjacent_cut():
    t = Track('x.mp4', duration=10)
    t.add_cut(2, 3)
    t.add_cut(6, 7)
    with pytest.raises(ValueError, match='another removed interval'):
        t.set_cut_edge(0, 'end', 6.5)
    assert t.cuts == [[2, 3], [6, 7]]


def test_cut_handle_rejects_total_deletion_without_losing_old_cut():
    t = Track('x.mp4', duration=10)
    t.add_cut(2, 3)
    t.set_cut_edge(0, 'start', 0)
    with pytest.raises(ValueError, match='all video content'):
        t.set_cut_edge(0, 'end', 10)
    assert t.cuts == [[0, 3]]  # failed second edit did not mutate the cut


def test_source_trim_handles():
    t = Track('x.mp4', duration=12)
    t.set_trim_edge('start', 2.45)
    t.set_trim_edge('end', 8.1)
    assert t.trim_start == 2.45
    assert t.trim_end == 8.1
    with pytest.raises(ValueError):
        t.set_trim_edge('start', 9)
    assert t.trim_start == 2.45
    t.set_trim_edge('end', 12.0)
    assert t.trim_end is None


def test_source_trim_honors_middle_cuts():
    t = Track('x.mp4', duration=6)
    t.add_cut(2, 5)
    with pytest.raises(ValueError, match='all video content'):
        t.set_trim_edge('start', 2.5)
        t.set_trim_edge('end', 4.5)
    assert t.trim_end is None


def test_drag_offset_geometry_and_clamping():
    # 1000 px represents a 50 s timeline: 70 px is +3.5 s.
    assert offset_after_drag(1.25, 70, 1000, 50) == 4.75
    assert offset_after_drag(1.25, -90, 1000, 50) == 0
    assert x_to_seconds(500, 100, 1000, 50) == 20
    assert seconds_to_x(20, 100, 1000, 50) == 500
    assert x_to_seconds(2500, 100, 1000, 50) == 50


def test_undo_redo_cut_and_offset_transactions():
    p = Project(tracks=[Track('guitar.mp4', duration=12), Track('baglama.mp4', duration=12)])
    history = ProjectHistory(limit=100)
    before = p.to_dict()
    p.tracks[1].offset = 0.352
    assert history.record(before, p.to_dict(), 'Drag to sync')
    before = p.to_dict()
    p.tracks[0].add_cut(4, 6)
    assert history.record(before, p.to_dict(), 'Cut middle')
    before = p.to_dict()
    p.tracks[0].set_cut_edge(0, 'end', 6.5)
    assert history.record(before, p.to_dict(), 'Drag cut edge')
    assert p.tracks[0].cuts == [[4, 6.5]]
    p = history.undo()
    assert p.tracks[0].cuts == [[4, 6]]
    p = history.undo()
    assert p.tracks[0].cuts == []
    p = history.undo()
    assert p.tracks[1].offset == 0.0
    p = history.redo()
    assert p.tracks[1].offset == 0.352
    p = history.redo()
    assert p.tracks[0].cuts == [[4, 6]]
    p = history.redo()
    assert p.tracks[0].cuts == [[4, 6.5]]


def test_undo_branch_clears_redo_and_noops_do_not_clobber_it():
    p = Project(tracks=[Track('x.mp4', duration=10)])
    h = ProjectHistory()
    before = p.to_dict()
    p.tracks[0].offset = 1
    h.record(before, p.to_dict(), 'Move')
    p = h.undo()
    assert h.can_redo
    assert not h.record(p.to_dict(), p.to_dict(), 'No-op')
    assert h.can_redo
    before = p.to_dict()
    p.tracks[0].pan = -0.5
    h.record(before, p.to_dict(), 'Pan')
    assert not h.can_redo
    assert h.undo().tracks[0].pan == 0


def test_history_bounded_and_snapshots_are_independent():
    h = ProjectHistory(limit=2)
    p = Project(tracks=[Track('x.mp4', duration=10)])
    for i in range(3):
        before = p.to_dict()
        p.tracks[0].offset = i + 1
        h.record(before, p.to_dict(), 'Move')
    p.tracks[0].offset = 99  # stored snapshot must be isolated
    assert h.undo().tracks[0].offset == 2
    assert h.undo().tracks[0].offset == 1
    assert h.undo() is None


def test_history_does_not_enter_json_project_file(tmp_path):
    p = Project(tracks=[Track('x.mp4', duration=10)])
    h = ProjectHistory()
    before = p.to_dict()
    p.tracks[0].set_trim_edge('start', 1)
    h.record(before, p.to_dict(), 'Trim')
    file = tmp_path / 'test.json'
    p.save(file)
    assert 'history' not in file.read_text()
    assert Project.load(file).tracks[0].trim_start == 1
