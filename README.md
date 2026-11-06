# CoverMaker 0.6

A small desktop app for assembling multi-take music-cover videos (guitar, bağlama, vocals, etc.). All edits remain **non-destructive** and live in a JSON project; FFmpeg assembles the output. Runs on Linux and Windows.

**New in v0.6:** zoom and scroll through long recordings, snap edits to synchronization markers, and smooth audio at middle-cut joins.

- **Timeline zoom (1×–32×):** zoom around the visible playhead (or the centre of the viewport), scroll horizontally with the scrollbar or Shift+mouse wheel. Ctrl+mouse wheel also zooms. Both composite project time and original source time have zoomable displays. The zoom is a view setting, not a destructive project edit.
- **Sync-marker snapping:** enable **Snap sync markers** (on by default). While dragging a take in the composite, its sync marker snaps to another take's sync marker if within 10 screen pixels. Source cut/trim handles similarly snap to the selected take's source-time sync marker. Hold **Alt while dragging** or uncheck the box to bypass snapping. Snapping only works where markers exist; it doesn't invent beats.
- **Audio fades around cut joins:** a **Cut fade (ms)** control specifies a short linear fade out at the end of a retained audio segment and fade in at the beginning of the next one. Default: **12 ms**, adjustable 0–100 ms (0 disables it). These prevent clicks at hard audio joins; they are **not overlapping crossfades**, and the video cuts remain sharp. Both previews and exports use the same processing.
- **Undo/redo:** fade changes are undoable. Zoom/scroll/snap settings affect only the editor view, not project JSON.

**Retained from v0.5:** interactive two-mode waveform timeline, draggable take offsets and trim/cut boundaries, plus up to 100 undo/redo actions.

The v0.4 composite audio/video preview is retained. The preview uses the same FFmpeg graph as the final export and still regenerates in the background after a committed edit.

## Install · Ubuntu / Debian

```bash
sudo apt update
sudo apt install ffmpeg python3-venv libxcb-cursor0

# From the extracted covermaker directory:
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
python -m covermaker
```

Run `ffmpeg -version` and `ffprobe -version` to check your FFmpeg install. If upgrading from v0.5, extract v0.6 into a new folder (or update the source files in your repository) and run `pip install -e .` again in the activated venv. Your `.json` projects remain compatible.

If Qt fails to load its XCB plugin (`Could not load the Qt platform plugin "xcb"`), try `sudo apt install libxcb-cursor0`. If needed, install these additional Qt/XCB runtime dependencies:

```bash
sudo apt install \
    libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
    libxcb-keysyms1 libxcb-render-util0 libxcb-xinerama0 \
    libxcb-xkb1 libxkbcommon-x11-0
```

For diagnostics: `QT_DEBUG_PLUGINS=1 python -m covermaker`. On Wayland, you may also test `QT_QPA_PLATFORM=wayland python -m covermaker`.

## Windows

Install Python 3.10+ and FFmpeg (`ffmpeg` and `ffprobe` on `PATH`). From PowerShell in the extracted project:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
python -m covermaker
```

## v0.6 quick workflow

1. **Record:** make individual guitar/bağlama/vocal recordings on your phone. Listen to the same reference/click in headphones. Clap near the start of each recording if you want Auto Sync.
2. **Import:** click **+ Add take** and select up to four video files. The preview may need a few seconds to render the first composite.
3. **Inspect the edited mix:** select **View: Composite mix**, choose **Length: 15 s / 30 s / 60 s / Full project**, and set **From (project s)** to pick where the preview begins. Click **↻ Refresh mix** as needed. Hit **▶ / ❚❚** or Space to play/pause. The composite includes all audible instruments and your layout.
4. **Seek:** drag the project-time slider, type a time, or **click the waveform timeline** to jump to another part of the song. Seeking within the already rendered preview is immediate; seeking outside it produces a new preview segment. If you want to listen continuously to the whole song, select **Full project** starting at 0 (takes longer to build).
5. **Edit source time:** change **View** to **Selected source** and select a take. Drag the **blue IN/OUT** handles to trim, or drag the **orange A/B** handles around any existing middle cut. To create a new cut, use the A/B controls in the right panel (source-time playhead), then drag its edges on the timeline.
6. **Align takes:** choose **Composite mix**, then drag a take horizontally by its waveform bar to align it in project time. For finer alignment use ±10/100 ms nudge buttons. You can alternatively use **Auto Sync** or place corresponding source-time markers in at least two takes and click **Align marked takes**.
7. **Adjust the mix:** select a take and change **Volume / Mute / Solo / Pan**. Choose `auto`, `single`, `split` or `grid` layout. Switching to **Composite mix** shows/hears the new result after the preview is rebuilt.
8. **Undo** mistakes with `Ctrl+Z` (redo `Ctrl+Shift+Z` / `Ctrl+Y`). **Save** via File → Save (`Ctrl+S`) and **Export MP4** for full-resolution H.264/AAC.

### Editing cuts

All trim, cut and sync-marker timestamps are in **original source time**, not project time. In **Selected source** view:

- Remove beginning: seek, then **Trim IN at playhead** (`[`) or type **Trim start**.
- Remove end: seek, then **Trim OUT at playhead** (`]`) or type **Trim end**. `full` means until the recording ends.
- Remove middle: seek to A, click **A ← ▶**, seek to B, click **B ← ▶**, then **Remove interval [A, B]**. Its orange A/B handles are now draggable directly on the source waveform. To restore a cut, select it in the cuts list and click **Restore selected interval**.
- **Dragging**: trims and cut edges are only committed on release; if an edit would cross a boundary, overlap another cut, or remove all surviving footage, it is rejected and the original interval is preserved. Press Esc while dragging to cancel. Time precision is 1 ms internally, limited by the timeline's current pixel scale.

**Important:** middle cuts are *ripple edits inside that particular take*: the later portion closes the removed gap. If only one instrument has an interval removed mid-song, the instruments can become out of sync *after the join*. Take this into account when editing ensemble performances.

### Interactive timeline and undo/redo

**View: Composite mix** displays every edited track in *project time*. Click a take row to select it; **drag the filled waveform bar** to shift that take's offset left or right. The cursor becomes a horizontal drag cursor. Offset never goes below 0 s. Clicking outside the waveform bars seeks the composite preview instead. The existing numerical Offset field and precise keyboard nudges remain available.

**View: Selected source** displays only the selected original take in *source time*, initially from 0 to its full recording duration. Dark areas are trimmed out; orange shaded areas are removed middle intervals. Drag the **IN**, **OUT**, or **A1/B1** handles to adjust them. Clicking elsewhere seeks in the selected original video. Changes regenerate the composite only after release. Zooming or scrolling does not regenerate the video preview.

**Undo/redo** works for changes to the project model, not video transport or zoom. Actions are recorded as complete snapshots of the tiny project JSON model (up to 100 transactions). It is *session-only*: undo history is not saved into `.json` files, and existing v0.2–v0.4 JSON projects still load. Audio waveform caches and source files are never modified by undo/redo.

### Zoom, marker snapping and audio cut fades (v0.6)

The **Timeline zoom** selector ranges from **1×** (entire recording) to **32×** (1/32 of the recording visible). Use **Ctrl+mouse wheel** over the waveform or the selector to zoom; **Shift+mouse wheel** or the horizontal **Scroll** slider to pan the timeline. Zoom is anchored at the playhead when it is visible, otherwise at the viewport centre. Switching between Composite mix and Selected source resets zoom to 1×, so neither time base is accidentally confused with the other.

The **Snap sync markers** checkbox controls *drag snapping*. In Composite mode, a dragged take can snap its sync marker to another take's saved marker. In Selected source mode, an IN/OUT or cut boundary can snap to that take's source marker. The threshold is **10 screen pixels**, so zooming in automatically makes the timing tolerance more precise. **Alt** temporarily disables snapping while dragging. Snapping never replaces the 10 ms nudges and does not force unrelated clips to align.

**Cut fade (ms)** is a project-wide audio-only setting (default **12.0 ms**). If a middle interval is removed, CoverMaker fades down the retained audio *before* the edit point and fades up the retained audio *after* it. Video is concatenated without fading. Setting it to zero returns to the original v0.5 hard-cut sound. The fade is limited to at most half each retained segment, including when segments are extremely short. This **does not** preserve the removed time: middle cuts still ripple that particular instrument and may desynchronize it from other takes later in the song.

### Synchronization

- Manual: in Selected source view, find a matching musical event in each recording and click **Use playhead**; then **Align marked takes**. Markers inside removed/trimmed regions are rejected.
- Auto: CoverMaker first tries to detect claps in the initial ~12 seconds; audio-correlation fallback is less reliable between different instruments recorded with the reference song in headphones.
- Both functions work with saved JSON projects; manual offsets can be nudged with the controls.

### Composite preview: how it works

- After an edit, the previous composite is marked **outdated** and a new preview is generated asynchronously. **Qt remains responsive** during FFmpeg encoding. Repeated adjustments cancel the preceding proxy job where possible.
- The preview is cached **for the current application session** according to the project edits, source file size/mtime, window start, and window length. Reopening a matching segment in the same session avoids re-rendering it.
- Only the **video resolution/frame rate/encoding quality** are reduced for preview (up to 640 px width, 24 fps, faster encoding). Synchronization, trimming, cuts, audio mixing, and per-track effects are generated from the *same* exporter graph as final MP4.
- The preview files are temporary and cleaned up on normal exit; they do not overwrite the source recordings or appear in the project JSON.
- Preview time slider and waveform timeline use **project time**; source view player uses **original take time**. Cut and marker actions are intentionally restricted to source view to avoid confusing the two time bases.
- FFmpeg needs to decode all relevant media up to your preview start position for this implementation; jumping very far into long recordings can be slow.
- This is **an asynchronously rendered proxy preview, not a frame-by-frame real-time mixer**. You can listen to all the tracks together, but there is a short re-render delay following edits. The composite audio/video is synchronized within the rendered segment.

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Space` | Play/pause the currently displayed preview or source |
| `Ctrl+M` | Add a manual sync marker at the *source* playhead (Selected source view) |
| `[` / `]` | Trim IN / OUT at the *source* playhead |
| `Ctrl+← / Ctrl+→` | Offset selected take −/+10 ms |
| `Alt+← / Alt+→` | Offset selected take −/+100 ms |
| `Ctrl+Z` | Undo most recent committed edit |
| `Ctrl+Shift+Z` / `Ctrl+Y` | Redo |
| `Esc` during a drag | Cancel drag before committing |
| `Ctrl+S` | Save project |
| `Ctrl++` / `Ctrl+-` | Zoom timeline in/out (also Ctrl+mouse wheel) |
| `Shift+mouse wheel` | Scroll timeline horizontally |
| `Alt` while dragging | Temporarily bypass marker snapping |

## Tests

```bash
python -m pip install pytest
pytest -q
```

The tests include actual FFmpeg renders verifying temporal mid-cuts, source alignment, stereo pan, solo, export tail duration, and preview segment timing. v0.5 also tests cut/trim boundary validation, offset drag geometry, undo/redo and old JSON compatibility. v0.6 adds viewport/snap tests, JSON fade compatibility, and a real FFmpeg output test that measures sound level around an edited cut. The Qt drag interaction tests are optional and automatically skipped when PySide6 is unavailable. For headless Qt testing, use `QT_QPA_PLATFORM=offscreen python -m pytest -q`.

## Architecture

```text
PySide6 UI
  ├── interactive composite timeline (drag track offsets / click seek)
  ├── original-source waveform timeline (drag cut/trim handles)
  ├── selected original take (source-time editing)
  └── composite proxy playback (Qt QMediaPlayer)
         ↑
    asynchronous QProcess FFmpeg render + per-session cache
         ↑
Project / Track dataclasses ↔ JSON
  ├── history.py → bounded, undoable project snapshots
  ├── timeline_math.py → Qt-free zoom, viewport, snapping and drag calculations
  ├── source-time cuts/kept ranges + manual/auto sync
  ├── audio_sync.py → cached PCM + clap/correlation analysis
  ├── ffmpeg.py → shared trim/concat/audio fade/delay/pan/mix/xstack graph
  │      ├── proxy preset (lower resolution/FPS/quality, time window)
  │      └── full-resolution MP4 export
  └── preview.py → preview window + stable cache key
```

## Current limitations / possible v0.7 work

- Zoom is **timeline-only**; waveform peaks are extracted at a fixed resolution and therefore may appear blocky at 32×. Detailed zoom would benefit from multiresolution waveform caching.
- Snapping targets **saved sync markers only**, not beat transients or arbitrary grid divisions. Alt bypasses snapping during a drag. The snap threshold depends on the visible timeline scale.
- Cut smoothing uses **short fades to zero and back**, not a true overlapping audio crossfade. Video remains a hard cut and the edit still closes the gap (ripple behavior). For a sustained note crossing a removed region, an overlapping audio-only crossfade would be a future enhancement.
- The compositor uses **asynchronously rendered proxy segments**, not a real-time NLE; edits update the preview after a render.
- **Undo history is session-only** (up to 100 successful actions). Save the project JSON to preserve the edits themselves.
- First-import waveform analysis still runs synchronously for uncached files; very large files may briefly pause the UI.
- Source videos must have both video and audio streams; up to four simultaneous takes. Sources are downmixed to mono before stereo panning; no EQ, live VU meters, or beat grid.
- I couldn't exercise the actual PySide6 GUI in the build environment (PySide6 absent); the optional headless Qt interaction tests run on your computer with `QT_QPA_PLATFORM=offscreen python -m pytest -q`.
