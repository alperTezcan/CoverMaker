"""Small real-FFmpeg end-to-end tests (skip if the executable is not installed)."""
import json
import shutil
import subprocess

import numpy as np
import pytest

from covermaker.ffmpeg import build_export_command
from covermaker.model import Project, Track


pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                                reason="FFmpeg+ffprobe not installed")


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def make_take(path, freq=440):
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=25:d=3",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)])


def probe_duration(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
                                   "format=duration", "-of", "json", str(path)], text=True)
    return float(json.loads(out)["format"]["duration"])


def audio_stereo(path):
    data = subprocess.check_output(["ffmpeg", "-loglevel", "error", "-i", str(path),
                                    "-vn", "-ac", "2", "-ar", "16000", "-f", "f32le", "pipe:1"])
    return np.frombuffer(data, dtype=np.float32).reshape(-1, 2)


def rms(x):
    return float(np.sqrt(np.mean(x*x)))


def test_mid_cut_trim_and_audio_pan(tmp_path):
    src, out = tmp_path / "src.mp4", tmp_path / "out.mp4"
    make_take(src)
    t = Track(str(src), duration=3.0, trim_start=0.2, trim_end=2.8, pan=-1)
    t.add_cut(1.0, 1.8)
    # 0.2..1.0 (0.8 s) followed by 1.8..2.8 (1.0 s)
    run(build_export_command(Project(tracks=[t], width=160, height=90, fps=25), str(out)))
    assert abs(probe_duration(out) - 1.8) < 0.1
    audio = audio_stereo(out)
    assert rms(audio[:, 0]) > 0.02
    assert rms(audio[:, 1]) < 0.001


def test_solo_and_delayed_track_keep_full_tail(tmp_path):
    src_a, src_b, out = tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "mix.mp4"
    make_take(src_a, freq=440)
    make_take(src_b, freq=880)
    a = Track(str(src_a), duration=3, offset=0, solo=True, pan=-1)
    b = Track(str(src_b), duration=3, offset=0.5, pan=1)
    run(build_export_command(Project(tracks=[a, b], width=320, height=180, fps=25), str(out)))
    assert abs(probe_duration(out) - 3.5) < 0.11
    audio = audio_stereo(out)
    assert rms(audio[:, 0]) > 0.02
    assert rms(audio[:, 1]) < 0.001  # right-panned non-solo take is silent


def test_removed_middle_video_is_not_present(tmp_path):
    """Three 1-second colors; remove green interval, expecting red then blue."""
    src, out = tmp_path / "colored.mp4", tmp_path / "cut.mp4"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:s=160x90:r=25:d=1",
         "-f", "lavfi", "-i", "color=c=green:s=160x90:r=25:d=1",
         "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=25:d=1",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
         "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
         "-map", "[v]", "-map", "3:a", "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-c:a", "aac", str(src)])
    t = Track(str(src), duration=3)
    t.add_cut(1, 2)
    run(build_export_command(Project(tracks=[t], width=160, height=90, fps=25), str(out)))

    def frame_rgb(seconds):
        image = subprocess.check_output(["ffmpeg", "-hide_banner", "-loglevel", "error",
                                         "-ss", str(seconds), "-i", str(out),
                                         "-frames:v", "1", "-f", "rawvideo",
                                         "-pix_fmt", "rgb24", "pipe:1"])
        return np.frombuffer(image, dtype=np.uint8).reshape(-1, 3).mean(axis=0)

    early, late = frame_rgb(0.4), frame_rgb(1.4)
    assert early[0] > 180 and early[2] < 70  # red
    assert late[2] > 180 and late[0] < 70  # blue, not green


def test_v05_draggable_cut_then_offset_affects_actual_export(tmp_path):
    """A cut edge move and a synchronization drag both reach FFmpeg correctly."""
    src, out = tmp_path / 'take.mp4', tmp_path / 'dragged.mp4'
    make_take(src)
    t = Track(str(src), duration=3.0, offset=0.0)
    t.add_cut(1, 2)
    t.set_cut_edge(0, 'start', 1.4)  # old remove 1 s, new remove 0.6 s
    t.offset = 0.3  # result of a project-timeline drag
    run(build_export_command(Project(tracks=[t], width=160, height=90, fps=25), str(out)))
    assert probe_duration(out) == pytest.approx(2.7, abs=0.10)
