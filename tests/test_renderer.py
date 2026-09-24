from covermaker.ffmpeg import layout_slots
from covermaker.model import Project, Track


def test_auto_two_tracks_is_split():
    p = Project(width=1280, height=720, layout="auto")
    p.tracks = [Track("a.mp4"), Track("b.mp4")]
    slots = layout_slots(p, 2)
    assert [(s.x, s.y, s.width, s.height) for s in slots] == [
        (0, 0, 640, 720),
        (640, 0, 640, 720),
    ]


def test_grid_four_tracks():
    p = Project(width=1280, height=720, layout="grid")
    slots = layout_slots(p, 4)
    assert len(slots) == 4
    assert slots[3].x == 640
    assert slots[3].y == 360


def test_project_roundtrip_sync_metadata(tmp_path):
    p = Project(layout="split")
    p.tracks = [
        Track("a.mp4", duration=12.0, sync_marker=1.234, sync_confidence=8.5)
    ]
    path = tmp_path / "project.json"
    p.save(path)
    loaded = Project.load(path)
    assert loaded.tracks[0].sync_marker == 1.234
    assert loaded.tracks[0].sync_confidence == 8.5
