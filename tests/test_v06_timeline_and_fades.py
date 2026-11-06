"""v0.6: pure timeline snapping/zoom, JSON compatibility, actual cut audio fade."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from covermaker.ffmpeg import FFmpegError, build_export_command
from covermaker.model import Project, Track
from covermaker.timeline_math import (visible_window, snap_seconds, snap_track_offset,
                                      offset_after_drag)


def test_viewport_window_and_clamping():
    assert visible_window(60, 1, 100) == (0, 60)
    assert visible_window(60, 4, 100) == (45, 15)
    assert visible_window(60, 4, 8) == (8, 15)
    with pytest.raises(ValueError):
        visible_window(60, 0, 0)
    with pytest.raises(ValueError):
        visible_window(60, 2, math.nan)


def test_zoom_precision_and_snap_radius():
    assert offset_after_drag(2.0, 10, width=800, duration=80) == 3.0
    assert offset_after_drag(2.0, 10, width=800, duration=5) == 2.062
    # 10 pixels represent 0.1 sec at this zoom level.
    assert snap_seconds(5.06, [1, 5], 0.01) == (5, True)
    assert snap_seconds(5.15, [1, 5], 0.01) == (5.15, False)
    assert snap_seconds(0.0, [], 0.01) == (0.0, False)


def test_sync_marker_snapping_keeps_nonnegative_offset():
    # Moving marker is at local 1.2; fixed track marker is at project 3.5.
    assert snap_track_offset(2.25, 1.2, [3.5], 0.01) == (2.3, True)
    assert snap_track_offset(2.25, None, [3.5], 0.01) == (2.25, False)
    assert snap_track_offset(2.25, 1.2, [], 0.01) == (2.25, False)
    # Never snap the moving marker to a target that would need negative offset.
    assert snap_track_offset(0.0, 1.2, [1.15], 0.01) == (0.0, False)


def test_cut_fade_json_round_trip_and_old_projects(tmp_path):
    p = Project(tracks=[Track(path='take.mp4', duration=3)], cut_fade_ms=23.5)
    f = tmp_path / 'project.json'
    p.save(f)
    assert Project.load(f).cut_fade_ms == pytest.approx(23.5)
    old = p.to_dict()
    old.pop('cut_fade_ms')
    assert Project.from_dict(old).cut_fade_ms == pytest.approx(12.0)


def test_cut_fade_duration_validation(monkeypatch):
    monkeypatch.setattr('covermaker.ffmpeg.require_ffmpeg', lambda: ('ffmpeg', 'ffprobe'))
    p = Project(tracks=[Track('x.mp4', duration=4, cuts=[[1, 2]])])
    p.cut_fade_ms = -1
    with pytest.raises(FFmpegError, match='Cut fade'):
        build_export_command(p, 'out.mp4')
    p.cut_fade_ms = 101
    with pytest.raises(FFmpegError, match='Cut fade'):
        build_export_command(p, 'out.mp4')
    p.cut_fade_ms = 20
    cmd = build_export_command(p, 'out.mp4')
    graph = cmd[cmd.index('-filter_complex') + 1]
    assert 'afade=t=out:' in graph
    assert 'afade=t=in:' in graph
    p.cut_fade_ms = 0
    cmd = build_export_command(p, 'out.mp4')
    graph = cmd[cmd.index('-filter_complex') + 1]
    assert 'afade=' not in graph


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg absent')
def test_cut_fades_real_ffmpeg_export(tmp_path):
    src = tmp_path / 'original.mp4'
    dst = tmp_path / 'edited.mp4'
    subprocess.run([
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=25',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
        '-t', '3', '-c:v', 'mpeg4', '-q:v', '5', '-c:a', 'aac', str(src),
    ], check=True)
    p = Project(width=64, height=64, fps=25, cut_fade_ms=60,
                tracks=[Track(str(src), duration=3, cuts=[[1.0, 2.0]])])
    subprocess.run(build_export_command(p, str(dst)), stdout=subprocess.DEVNULL,
                   stderr=subprocess.PIPE, check=True)
    data = subprocess.run([
        'ffmpeg', '-loglevel', 'error', '-i', str(dst), '-vn',
        '-ac', '1', '-ar', '48000', '-f', 'f32le', '-'
    ], capture_output=True, check=True).stdout
    samples = np.frombuffer(data, dtype='<f4')
    assert samples.size / 48000 == pytest.approx(2.0, abs=0.075)
    def rms(a, b):
        region = samples[int(a * 48000):int(b * 48000)]
        return float(np.sqrt(np.mean(region ** 2)))
    baseline = rms(0.5, 0.7)
    assert baseline > 0.01
    assert rms(0.987, 1.013) < baseline * 0.4
    assert rms(1.16, 1.25) > baseline * 0.6
