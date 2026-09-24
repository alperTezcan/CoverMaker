# CoverMaker 0.2

A deliberately small desktop app for assembling multi-take music cover videos.
It is **not** a general video editor: it is optimized for recording yourself playing several parts of the same song.

## Workflow

1. Record each guitar / bağlama / vocal part on your phone while monitoring the same reference track in headphones.
2. **Clap once before the music starts** in every take.
3. Import the phone videos.
4. Press **Auto Sync**.
5. Verify the alignment by ear and use ±10 ms / ±100 ms nudges if needed.
6. Set trim, level, mute and layout.
7. Export MP4 through FFmpeg.

## v0.2 features

- PySide6 desktop GUI
- Import multiple phone videos
- Selected-take video preview
- Per-take offset / trim / volume / mute
- Cached low-rate audio extraction through FFmpeg
- Waveform display on the compact timeline
- First-prominent-transient (clap) detection
- Correlation fallback when clap detection is uncertain
- Auto Sync with non-negative normalized offsets
- ±10 ms and ±100 ms nudge buttons and keyboard shortcuts
- Auto / single / split / 2x2 grid layouts
- JSON project save/load, including sync markers
- H.264/AAC FFmpeg export
- Up to 4 simultaneous video takes

## Linux setup (recommended for development)

On Ubuntu/Debian-like systems:

```bash
sudo apt install ffmpeg python3-venv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
python -m covermaker
```

Check FFmpeg first:

```bash
ffmpeg -version
ffprobe -version
```

## Windows

The code remains cross-platform. Install Python 3.10+ and FFmpeg, then:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
python -m covermaker
```

## Keyboard nudging

- `Ctrl+Left`  → −10 ms
- `Ctrl+Right` → +10 ms
- `Alt+Left`   → −100 ms
- `Alt+Right`  → +100 ms
- `Space`      → play/pause selected take

## Sync design

The primary sync signal is the pre-song hand clap. This is intentional: if you monitor the reference song through closed headphones, the backing track may barely appear in the S22 microphone recording, so guitar-vs-bağlama waveform correlation can be unreliable.

For confidently detected claps, CoverMaker aligns each clap to a common timeline point. If a clap is not confidently detected, it tries low-rate onset-envelope correlation against a reference take. If that is also uncertain, the track is left for manual nudging.

Audio analysis is cached outside the project directory:

- Linux: `$XDG_CACHE_HOME/covermaker` or `~/.cache/covermaker`
- Windows: `%LOCALAPPDATA%/CoverMaker/cache`

## Architecture

```text
PySide6 UI
    |
    +--> compact waveform timeline
    |
    v
Project / Track dataclasses <--> JSON
    |
    +--> audio_sync.py --> FFmpeg PCM extraction --> NumPy analysis/cache
    |
    v
FFmpeg command builder
    |
    v
cover.mp4
```

The UI still does not implement a full real-time compositing engine. The selected raw take is previewed with Qt; the final FFmpeg render is authoritative.

## Known v0.2 limitations

- Clap detection assumes the clap is near the beginning (first ~12 seconds).
- Correlation across different instruments is only a fallback and can fail.
- Waveform analysis currently runs synchronously, so importing a long/high-latency file may briefly block the GUI on the first analysis. Subsequent runs use the cache.
- No composite live preview yet.
- No solo/pan/EQ yet.

## Likely v0.3

- background worker for waveform/sync analysis
- manual click-to-place sync marker
- solo / pan
- simple high-pass and gain staging
- clipping/loudness indicator
