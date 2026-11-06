"""Preview-window and real FFmpeg proxy regression tests."""
import json
import shutil
import subprocess

import numpy as np
import pytest

from covermaker.ffmpeg import FFmpegError, build_preview_command, project_duration
from covermaker.model import Project, Track
from covermaker.preview import preview_identity, preview_window


def test_window_and_duration(tmp_path):
    p = Project(tracks=[Track("example.mp4", duration=5.0, offset=1.0)])
    assert project_duration(p) == 6.0
    assert preview_window(p, 2, 10) == (2, 6)
    assert preview_window(p, 0, None) == (0, 6)
    with pytest.raises(FFmpegError):
        preview_window(p, 6, 10)
    with pytest.raises(FFmpegError):
        preview_window(p, 0, -1)


def test_cache_identity_tracks_edits_and_files(tmp_path):
    file = tmp_path / "test.mp4"
    file.write_bytes(b"a")
    p = Project(tracks=[Track(str(file), duration=2)])
    base = preview_identity(p, 0, 15)
    assert base == preview_identity(p, 0, 15)
    assert base != preview_identity(p, 0.5, 15)
    p.tracks[0].volume = 0.7
    assert base != preview_identity(p, 0, 15)
    p.tracks[0].volume = 1
    file.write_bytes(b"second contents")
    assert base != preview_identity(p, 0, 15)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="FFmpeg not available")
def test_proxy_cut_offset_pan_and_output_side_seek(tmp_path):
    src_a, src_b, proxy = (tmp_path / n for n in ("a.mp4", "b.mp4", "preview.mp4"))
    for src, freq in ((src_a, 330), (src_b, 880)):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=3",
                        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=3",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", str(src)], check=True, capture_output=True)

    a = Track(str(src_a), duration=3, offset=0.25, solo=True, pan=-1)
    a.add_cut(0.7, 1.2)
    b = Track(str(src_b), duration=3, offset=0.6, pan=1)
    project = Project(tracks=[a, b], width=640, height=360, fps=30)
    cmd = build_preview_command(project, str(proxy), start=1.0, seconds=1.2)
    assert "ultrafast" in cmd and "-ss" in cmd
    subprocess.run(cmd, check=True, capture_output=True)
    output = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
                                      "format=duration:stream=width,height", "-of", "json", str(proxy)])
    info = json.loads(output)
    assert abs(float(info["format"]["duration"]) - 1.2) < 0.12
    assert max(s["width"] for s in info["streams"] if "width" in s) <= 640
    pcm = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(proxy), "-vn",
                                   "-ac", "2", "-ar", "8000", "-f", "f32le", "pipe:1"])
    stereo = np.frombuffer(pcm, dtype=np.float32).reshape(-1, 2)
    rms = lambda a: float(np.sqrt(np.mean(a**2)))
    assert rms(stereo[:, 0]) > 0.005
    assert rms(stereo[:, 1]) < 0.001  # b is not soloed, and a is hard left
