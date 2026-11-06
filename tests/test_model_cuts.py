import pytest

from covermaker.model import Project, Track, align_marked_tracks


def test_middle_and_edge_cut_ranges():
    t = Track("instrument.mp4", duration=12, trim_start=1, trim_end=11)
    t.add_cut(4, 6)
    t.add_cut(5, 7)  # overlapping cuts merge in the derived keep ranges
    assert t.kept_ranges() == [(1, 4), (7, 11)]
    assert t.effective_duration == 7
    assert t.source_to_local(2) == 1
    assert t.source_to_local(8) == 4
    assert t.source_to_local(5) is None
    assert t.source_to_local(0.5) is None


def test_trim_beginning_end_cuts():
    t = Track("x.mp4", duration=8, trim_start=2, trim_end=7)
    assert t.kept_ranges() == [(2, 7)]
    assert t.effective_duration == 5
    t.add_cut(2, 3)
    t.add_cut(6, 7)
    assert t.kept_ranges() == [(3, 6)]


def test_cannot_cut_entire_track():
    t = Track("x.mp4", duration=5)
    with pytest.raises(ValueError):
        t.add_cut(0, 5)
    assert t.cuts == []


def test_manual_alignment_accounts_for_middle_cuts():
    a = Track("a.mp4", duration=10, sync_marker=4.0)
    b = Track("b.mp4", duration=10, sync_marker=6.5)
    b.add_cut(1, 3)
    # Both events appear at 4.0 and 4.5 on their edited takes.
    assert align_marked_tracks([a, b]) == 2
    assert a.offset == 0.5
    assert b.offset == 0.0


def test_manual_alignment_does_not_touch_unmarked():
    a = Track("a.mp4", duration=10, sync_marker=2)
    b = Track("b.mp4", duration=10, sync_marker=3)
    c = Track("c.mp4", duration=10, offset=9)
    align_marked_tracks([a, b, c])
    assert [a.offset, b.offset, c.offset] == [1.0, 0.0, 9]


def test_manual_alignment_rejects_deleted_marker_without_mutation():
    a = Track("a.mp4", duration=5, sync_marker=2.5, offset=1)
    b = Track("b.mp4", duration=5, sync_marker=4, offset=2)
    a.add_cut(2, 3)
    with pytest.raises(ValueError, match="marker lies"):
        align_marked_tracks([a, b])
    assert (a.offset, b.offset) == (1, 2)


def test_json_backwards_compatible():
    old = {"tracks": [{"path": "old.mp4", "duration": 5.0, "trim_start": 1.0}]}
    project = Project.from_dict(old)
    t = project.tracks[0]
    assert t.cuts == [] and not t.solo and t.pan == 0
    fresh = Project.from_dict(project.to_dict())
    assert fresh.tracks[0].effective_duration == 4


def test_json_roundtrip_new_metadata(tmp_path):
    t = Track("a.mp4", duration=7, pan=-0.75, solo=True, sync_marker=2.25)
    t.add_cut(4, 5)
    path = tmp_path / "cover.json"
    Project(tracks=[t]).save(path)
    t2 = Project.load(path).tracks[0]
    assert t2.pan == -0.75
    assert t2.solo is True
    assert t2.cuts == [[4, 5]]
    assert t2.sync_marker == 2.25
