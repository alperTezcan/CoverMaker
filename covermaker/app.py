from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QListWidget,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .audio_sync import (
    SAMPLE_RATE,
    detect_first_prominent_transient,
    estimate_correlation_lag,
    extract_mono_pcm,
    normalized_offsets,
    waveform_peaks,
)
from .ffmpeg import (FFmpegError, build_export_command, build_preview_command,
                     probe_duration, project_duration, require_ffmpeg)
from .model import Project, Track, align_marked_tracks
from .history import ProjectHistory
from .preview import preview_identity, preview_window
from .timeline import TimelineWidget


APP_VERSION = "0.6"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"CoverMaker {APP_VERSION}")
        self.resize(1370, 940)

        self.project = Project()
        self.history = ProjectHistory(limit=100)
        self.project_path: str | None = None
        self._updating_controls = False
        self._audio_cache: dict[str, object] = {}
        self._preview_dir = tempfile.TemporaryDirectory(prefix="covermaker-preview-")
        self._proxy_path: str | None = None
        self._proxy_start = 0.0
        self._proxy_end = 0.0
        self._proxy_revision = -1
        self._preview_revision = 0
        self._render_revision = -1
        self._render_path: str | None = None
        self._render_tmp_path: str | None = None
        self._resume_project_time: float | None = None
        self._render_pending = False
        self._preview_log = ""

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.video = QVideoWidget(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.audio.setVolume(0.7)

        self.export_process = QProcess(self)
        self.export_process.readyReadStandardError.connect(self._consume_export_stderr)
        self.export_process.finished.connect(self._export_finished)
        self._export_log = ""

        self.preview_process = QProcess(self)
        self.preview_process.readyReadStandardError.connect(self._consume_preview_stderr)
        self.preview_process.finished.connect(self._preview_finished)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(650)
        self.preview_timer.timeout.connect(self._render_preview)
        self.player.errorOccurred.connect(self._playback_error)

        self._build_ui()
        self._build_menu()
        self._refresh_table()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        topbar = QHBoxLayout()
        add_btn = QPushButton("+ Add take")
        add_btn.clicked.connect(self.add_take)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self.remove_selected)
        play_btn = QPushButton("▶ / ❚❚")
        play_btn.clicked.connect(self.toggle_play)
        sync_btn = QPushButton("Auto Sync")
        sync_btn.setToolTip("Detect the pre-song clap in each take and align all takes")
        sync_btn.clicked.connect(self.auto_sync)
        export_btn = QPushButton("Export MP4")
        export_btn.clicked.connect(self.export_mp4)
        self.layout_combo = QComboBox()
        self.layout_combo.addItems(["auto", "single", "split", "grid"])
        self.layout_combo.currentTextChanged.connect(self._layout_changed)

        topbar.addWidget(add_btn)
        topbar.addWidget(remove_btn)
        topbar.addSpacing(12)
        topbar.addWidget(play_btn)
        topbar.addWidget(sync_btn)
        topbar.addStretch(1)
        topbar.addWidget(QLabel("Layout:"))
        topbar.addWidget(self.layout_combo)
        topbar.addWidget(export_btn)
        root.addLayout(topbar)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        preview_options = QHBoxLayout()
        self.preview_mode = QComboBox()
        self.preview_mode.addItems(["Composite mix", "Selected source"])
        self.preview_mode.setToolTip("Composite: rendered mix including cuts and sync. Source: raw take for placing markers and cuts.")
        self.preview_mode.currentIndexChanged.connect(self._preview_mode_changed)
        self.preview_length = QComboBox()
        self.preview_length.addItems(["15 s", "30 s", "60 s", "Full project"])
        self.preview_length.setCurrentIndex(1)
        self.preview_length.currentIndexChanged.connect(self._preview_settings_changed)
        self.preview_start = self._spin(0, 86400, 1, 2)
        self.preview_start.setToolTip("Project time at which a composite preview window begins")
        self.preview_start.valueChanged.connect(self._preview_settings_changed)
        self.preview_button = QPushButton("↻ Refresh mix")
        self.preview_button.clicked.connect(self._force_refresh_preview)
        for label, item in (("View:", self.preview_mode), ("Length:", self.preview_length),
                            ("From (project s):", self.preview_start)):
            preview_options.addWidget(QLabel(label))
            preview_options.addWidget(item)
        preview_options.addWidget(self.preview_button)
        left_layout.addLayout(preview_options)
        self.preview_hint = QLabel("Composite preview: quick FFmpeg proxy · source view: original recording")
        self.preview_hint.setWordWrap(True)
        left_layout.addWidget(self.preview_hint)
        left_layout.addWidget(self.video, 1)

        transport = QHBoxLayout()
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(self._seek_to_ms)
        self.player.positionChanged.connect(self._preview_position_changed)
        self.player.durationChanged.connect(self._player_duration_changed)
        self.transport_label = QLabel("Project time")
        transport.addWidget(self.transport_label)
        transport.addWidget(self.position_slider, 1)
        self.preview_time = self._spin(0, 86400, 0.01, 3)
        self.preview_time.setToolTip("Composite view: project seconds. Source view: original take seconds.")
        self.preview_time.valueChanged.connect(self._seek_preview)
        transport.addWidget(self.preview_time)
        left_layout.addLayout(transport)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels(
            ["Take", "Offset", "Start", "End", "Cuts", "Volume", "Mute", "Solo", "Pan", "Sync", "Conf."]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        right_layout.addWidget(self.table, 1)

        form = QFormLayout()
        self.offset = self._spin(0, 3600, 0.01, 3)
        self.start = self._spin(0, 3600, 0.01, 3)
        self.end = self._spin(0, 3600, 0.01, 3)
        self.end.setSpecialValueText("full")
        self.volume = self._spin(0, 4, 0.05, 2)
        self.mute = QCheckBox()
        self.solo = QCheckBox()
        self.pan = self._spin(-1.0, 1.0, 0.05, 2)
        self.pan.setToolTip("-1.0 = full left, 0 = centre, +1.0 = full right")
        for w in (self.offset, self.start, self.end, self.volume, self.pan):
            w.valueChanged.connect(self._controls_changed)
        self.mute.toggled.connect(self._controls_changed)
        self.solo.toggled.connect(self._controls_changed)
        form.addRow("Offset (s)", self.offset)
        form.addRow("Trim start (s)", self.start)
        form.addRow("Trim end (s)", self.end)
        form.addRow("Volume", self.volume)
        form.addRow("Mute", self.mute)
        form.addRow("Solo (audio)", self.solo)
        form.addRow("Pan (L −1 / R +1)", self.pan)
        right_layout.addLayout(form)

        nudge_row = QHBoxLayout()
        for label, amount in [("−100 ms", -0.100), ("−10 ms", -0.010), ("+10 ms", 0.010), ("+100 ms", 0.100)]:
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, a=amount: self.nudge_selected(a))
            nudge_row.addWidget(b)
        right_layout.addLayout(nudge_row)
        sync_group = QGroupBox("Manual synchronization · source time")
        sync_layout = QVBoxLayout(sync_group)
        marker_row = QHBoxLayout()
        self.marker_time = self._spin(0, 86400, 0.01, 3)
        self.marker_time.setToolTip("Marker location in original source video (seconds)")
        marker_row.addWidget(QLabel("Marker (s):"))
        marker_row.addWidget(self.marker_time)
        mark_button = QPushButton("Use playhead")
        mark_button.clicked.connect(self.mark_at_playhead)
        marker_row.addWidget(mark_button)
        sync_layout.addLayout(marker_row)
        mark_row = QHBoxLayout()
        set_marker = QPushButton("Set marker")
        set_marker.clicked.connect(self.set_manual_marker)
        clear_marker = QPushButton("Clear")
        clear_marker.clicked.connect(self.clear_manual_marker)
        align = QPushButton("Align marked takes")
        align.setToolTip("Align all takes with manually assigned markers; others are unchanged")
        align.clicked.connect(self.manual_align)
        for w in (set_marker, clear_marker, align):
            mark_row.addWidget(w)
        sync_layout.addLayout(mark_row)
        right_layout.addWidget(sync_group)

        cut_group = QGroupBox("Cut / remove content · source video seconds")
        cut_layout = QVBoxLayout(cut_group)
        trim_buttons = QHBoxLayout()
        in_btn = QPushButton("Trim IN at playhead")
        out_btn = QPushButton("Trim OUT at playhead")
        in_btn.clicked.connect(lambda: self.trim_at_playhead("in"))
        out_btn.clicked.connect(lambda: self.trim_at_playhead("out"))
        trim_buttons.addWidget(in_btn)
        trim_buttons.addWidget(out_btn)
        cut_layout.addLayout(trim_buttons)
        cut_times = QHBoxLayout()
        self.cut_a = self._spin(0, 86400, 0.01, 3)
        self.cut_b = self._spin(0, 86400, 0.01, 3)
        a_btn = QPushButton("A ← ▶")
        b_btn = QPushButton("B ← ▶")
        a_btn.setToolTip("Set cut start A to selected preview playhead")
        b_btn.setToolTip("Set cut end B to selected preview playhead")
        a_btn.clicked.connect(lambda: self.set_cut_bound("a"))
        b_btn.clicked.connect(lambda: self.set_cut_bound("b"))
        for w in (QLabel("A"), self.cut_a, a_btn, QLabel("B"), self.cut_b, b_btn):
            cut_times.addWidget(w)
        cut_layout.addLayout(cut_times)
        cut_buttons = QHBoxLayout()
        add_cut = QPushButton("Remove interval [A, B]")
        add_cut.clicked.connect(self.add_cut)
        delete_cut = QPushButton("Restore selected interval")
        delete_cut.clicked.connect(self.remove_cut)
        cut_buttons.addWidget(add_cut)
        cut_buttons.addWidget(delete_cut)
        cut_layout.addLayout(cut_buttons)
        self.cut_list = QListWidget()
        self.cut_list.setMaximumHeight(84)
        cut_layout.addWidget(self.cut_list)
        right_layout.addWidget(cut_group)
        splitter.addWidget(right)
        splitter.setSizes([760, 610])

        self.timeline = TimelineWidget()
        self.timeline.seekRequested.connect(self.seek_project_time)
        self.timeline.seekSourceRequested.connect(self._seek_source_time)
        self.timeline.trackSelected.connect(self.table.selectRow)
        self.timeline.offsetEdited.connect(self._timeline_offset_edited)
        self.timeline.trimEdgeEdited.connect(self._timeline_trim_edited)
        self.timeline.cutEdgeEdited.connect(self._timeline_cut_edited)
        self.timeline.dragStatus.connect(lambda message: self.statusBar().showMessage(message, 3500))
        self.timeline.set_source_mode(not self._is_composite())
        tools = QHBoxLayout()
        tools.addWidget(QLabel("Timeline zoom:"))
        self.timeline_zoom = QComboBox()
        self.timeline_zoom.addItems(["1×", "2×", "4×", "8×", "16×", "32×"])
        self.timeline_zoom.currentIndexChanged.connect(self._zoom_changed)
        self.timeline.zoomRequested.connect(self._zoom_step)
        tools.addWidget(self.timeline_zoom)
        tools.addWidget(QLabel("Scroll:"))
        self.timeline_scroll = QSlider(Qt.Horizontal)
        self.timeline_scroll.setRange(0, 1000)
        self.timeline_scroll.valueChanged.connect(self._timeline_scroll_changed)
        self.timeline.viewportChanged.connect(self._timeline_view_changed)
        tools.addWidget(self.timeline_scroll, 1)
        self.snap_checkbox = QCheckBox("Snap sync markers")
        self.snap_checkbox.setChecked(True)
        self.snap_checkbox.toggled.connect(self.timeline.set_snap_enabled)
        self.snap_checkbox.setToolTip("Snap draggable take markers / source cut handles to matching sync markers; hold Alt to bypass")
        tools.addWidget(self.snap_checkbox)
        tools.addWidget(QLabel("Cut fade (ms):"))
        self.cut_fade = self._spin(0, 100, 1, 1)
        self.cut_fade.setValue(self.project.cut_fade_ms)
        self.cut_fade.setToolTip("Audio fade-in/out at each internal cut join (0 disables); video stays a hard cut")
        self.cut_fade.valueChanged.connect(self._cut_fade_changed)
        tools.addWidget(self.cut_fade)
        root.addLayout(tools)
        root.addWidget(self.timeline)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setRange(0, 0)
        root.addWidget(self.progress)
        self.statusBar().showMessage("Ready · choose Composite mix to hear the combined edited tracks")
        self._set_source_edit_controls()

    def _build_menu(self):
        menu = self.menuBar().addMenu("File")
        for text, shortcut, fn in [
            ("New", QKeySequence.New, self.new_project),
            ("Open…", QKeySequence.Open, self.open_project),
            ("Save", QKeySequence.Save, self.save_project),
            ("Save As…", QKeySequence.SaveAs, self.save_project_as),
        ]:
            act = QAction(text, self)
            act.setShortcut(shortcut)
            act.triggered.connect(fn)
            menu.addAction(act)

        edit_menu = self.menuBar().addMenu("Edit")
        self.undo_action = QAction("Undo", self)
        self.undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        self.undo_action.triggered.connect(self.undo)
        edit_menu.addAction(self.undo_action)
        self.redo_action = QAction("Redo", self)
        self.redo_action.setShortcuts([QKeySequence("Ctrl+Shift+Z"), QKeySequence("Ctrl+Y")])
        self.redo_action.triggered.connect(self.redo)
        edit_menu.addAction(self.redo_action)
        self._update_history_actions()

        self._add_action("Play/Pause", "Space", self.toggle_play)
        self._add_action("Nudge −10 ms", "Ctrl+Left", lambda: self.nudge_selected(-0.010))
        self._add_action("Nudge +10 ms", "Ctrl+Right", lambda: self.nudge_selected(0.010))
        self._add_action("Nudge −100 ms", "Alt+Left", lambda: self.nudge_selected(-0.100))
        self._add_action("Nudge +100 ms", "Alt+Right", lambda: self.nudge_selected(0.100))
        self._add_action("Set sync marker at playhead", "Ctrl+M", self.mark_at_playhead)
        self._add_action("Trim IN at playhead", "[", lambda: self.trim_at_playhead("in"))
        self._add_action("Trim OUT at playhead", "]", lambda: self.trim_at_playhead("out"))
        self._add_action("Zoom timeline in", "Ctrl++", lambda: self._zoom_step(1))
        self._add_action("Zoom timeline out", "Ctrl+-", lambda: self._zoom_step(-1))

    def _add_action(self, text: str, shortcut: str, fn):
        act = QAction(text, self)
        act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(fn)
        self.addAction(act)

    @staticmethod
    def _spin(lo: float, hi: float, step: float, decimals: int) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setDecimals(decimals)
        s.setKeyboardTracking(False)  # commit one undo step after editing the value
        return s

    def _zoom_step(self, direction: int) -> None:
        self.timeline_zoom.setCurrentIndex(max(0, min(5, self.timeline_zoom.currentIndex() + direction)))

    def _zoom_changed(self, index: int) -> None:
        if index >= 0:
            self.timeline.set_zoom(float(2 ** index))

    def _timeline_scroll_changed(self, value: int) -> None:
        total = self.timeline._total()
        span = total / self.timeline.zoom
        self.timeline.set_view_start((total - span) * value / 1000.0)

    def _timeline_view_changed(self, start: float, span: float) -> None:
        total = self.timeline._total()
        limit = max(0.0, total - span)
        self.timeline_scroll.blockSignals(True)
        self.timeline_scroll.setEnabled(limit > 1e-7)
        self.timeline_scroll.setValue(round(start / limit * 1000) if limit > 1e-7 else 0)
        self.timeline_scroll.blockSignals(False)

    def _cut_fade_changed(self, milliseconds: float) -> None:
        if self._updating_controls:
            return
        before = self.project.to_dict()
        self.project.cut_fade_ms = round(milliseconds, 1)
        if self._record_edit(before, "Change audio cut fade"):
            self._invalidate_preview()

    def _selected_index(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _samples(self, path: str):
        cached = self._audio_cache.get(path)
        if cached is None:
            cached = extract_mono_pcm(path)
            self._audio_cache[path] = cached
        return cached

    def _ensure_waveform(self, track: Track) -> None:
        if track.path in self.timeline.waveforms:
            return
        try:
            samples = self._samples(track.path)
            self.timeline.set_waveform(track.path, waveform_peaks(samples, 1200), len(samples) / SAMPLE_RATE)
        except FFmpegError:
            pass

    def add_take(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Add phone/video takes",
            "",
            "Video files (*.mp4 *.mov *.mkv *.m4v *.avi);;All files (*)",
        )
        if not files:
            return
        try:
            require_ffmpeg()
        except FFmpegError as e:
            QMessageBox.warning(self, "FFmpeg missing", str(e))
            return
        before = self.project.to_dict()
        for file in files:
            track = Track(path=file, duration=probe_duration(file))
            self.project.tracks.append(track)
            self._ensure_waveform(track)
        self._record_edit(before, "Add take")
        self._refresh_table(select=len(self.project.tracks) - 1)
        self._invalidate_preview()

    def remove_selected(self):
        idx = self._selected_index()
        if idx is None:
            return
        before = self.project.to_dict()
        del self.project.tracks[idx]
        self._record_edit(before, "Remove take")
        self.player.stop()
        self.player.setSource(QUrl())
        self._refresh_table(select=min(idx, len(self.project.tracks) - 1))
        self._invalidate_preview()

    def _is_composite(self) -> bool:
        return self.preview_mode.currentIndex() == 0

    def _preview_seconds(self) -> float | None:
        return [15.0, 30.0, 60.0, None][self.preview_length.currentIndex()]

    def _player_duration_changed(self, ms: int) -> None:
        if self._is_composite():
            total = project_duration(self.project)
            self.position_slider.setRange(0, int(round((total or 0) * 1000)))
        else:
            self.position_slider.setRange(0, ms)

    def _preview_position_changed(self, ms: int) -> None:
        seconds = ms / 1000 + (self._proxy_start if self._is_composite() else 0.0)
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(round(seconds * 1000))
        self.position_slider.blockSignals(False)
        self.preview_time.blockSignals(True)
        self.preview_time.setValue(seconds)
        self.preview_time.blockSignals(False)
        self.timeline.set_position(seconds)

    def _seek_source_time(self, seconds: float) -> None:
        if self._is_composite():
            self.preview_mode.setCurrentIndex(1)
        self.player.setPosition(round(seconds * 1000))

    def _seek_to_ms(self, ms: int) -> None:
        self._seek_preview(ms / 1000.0)

    def _seek_preview(self, seconds: float) -> None:
        if not self._is_composite():
            self.player.setPosition(round(seconds * 1000))
            return
        self.seek_project_time(seconds)

    def seek_project_time(self, seconds: float) -> None:
        if not self._is_composite():
            self.preview_mode.setCurrentIndex(0)
        total = project_duration(self.project)
        if total is None or total <= 0:
            return
        seconds = min(max(0.0, seconds), max(0.0, total - 0.001))
        # Existing proxy can be sought immediately without invoking FFmpeg.
        if self._proxy_path and self._proxy_revision == self._preview_revision and self._proxy_start <= seconds < self._proxy_end:
            self.player.setPosition(round((seconds - self._proxy_start) * 1000))
        else:
            self.preview_start.setValue(seconds)
            self._schedule_preview()

    def _set_source_edit_controls(self):
        source = not self._is_composite()
        self.transport_label.setText("Source time" if source else "Project time")
        self.timeline.set_source_mode(source)
        self.timeline_zoom.blockSignals(True)
        self.timeline_zoom.setCurrentIndex(0)
        self.timeline_zoom.blockSignals(False)
        # Source-time buttons must not silently interpret composite time as raw source time.
        for w in (self.marker_time, self.cut_a, self.cut_b):
            w.setEnabled(source)
        self.preview_hint.setText(
            "Original source time: place sync markers, trims and cut boundaries here. "
            "Switch back to Composite mix to hear the result."
            if source else
            "Edited composite: video layout + all audible takes, including cuts, offsets, volume, solo and pan. "
            "Click the timeline to seek. Switch to Selected source for cutting/marking."
        )

    def _preview_mode_changed(self, *_):
        self.player.pause()
        self._set_source_edit_controls()
        if self._is_composite():
            self._player_duration_changed(0)
            self._schedule_preview()
        else:
            self.preview_timer.stop()
            self._show_source()

    def _show_source(self):
        idx = self._selected_index()
        if idx is None:
            self.player.stop()
            self.player.setSource(QUrl())
            return
        t = self.project.tracks[idx]
        if self.player.source().toLocalFile() != t.path:
            self.player.stop()
            self.player.setSource(QUrl.fromLocalFile(t.path))
            self.player.setPosition(round(t.trim_start * 1000))
        self._player_duration_changed(self.player.duration())
        self.timeline.set_position(self.player.position() / 1000.0)

    def _preview_settings_changed(self, *_):
        if self._is_composite():
            # Distinguish new preview windows from an in-flight older render.
            self._invalidate_preview(preserve_position=False)

    def _force_refresh_preview(self):
        if self._is_composite():
            self._schedule_preview(immediate=True)
        else:
            self._show_source()

    def _invalidate_preview(self, *, preserve_position: bool = True):
        if (preserve_position and self._is_composite() and self._proxy_path
                and self._proxy_revision == self._preview_revision):
            self._resume_project_time = self._proxy_start + self.player.position() / 1000.0
        elif not preserve_position:
            self._resume_project_time = None
        self._preview_revision += 1
        self._proxy_revision = -1
        if self._is_composite():
            self.player.pause()
            self.player.setSource(QUrl())
            if self.preview_process.state() != QProcess.NotRunning:
                # Old work is now useless; don't delay new preview generation.
                self._render_pending = True
                self.preview_process.kill()
            self._schedule_preview()

    def _schedule_preview(self, *, immediate: bool = False):
        if not self._is_composite() or not self.project.tracks:
            return
        self.preview_hint.setText("Preview outdated — generating updated composite…")
        self.preview_timer.start(0 if immediate else 650)

    def _render_preview(self):
        if not self._is_composite() or not self.project.tracks:
            return
        if self.preview_process.state() != QProcess.NotRunning:
            self._render_pending = True
            return
        try:
            a, b = preview_window(self.project, self.preview_start.value(), self._preview_seconds())
            fingerprint = preview_identity(self.project, a, self._preview_seconds())
            file = Path(self._preview_dir.name) / f"{fingerprint}.mp4"
            if file.exists() and file.stat().st_size > 0:
                self._activate_proxy(str(file), a, b)
                return
            temporary = file.with_name(file.stem + ".part.mp4")
            cmd = build_preview_command(self.project, str(temporary), start=a, seconds=b - a)
        except (FFmpegError, ValueError) as e:
            self.preview_hint.setText(f"Cannot preview: {e}")
            return
        self._render_revision = self._preview_revision
        self._render_path = str(file)
        self._render_tmp_path = str(temporary)
        self._render_start, self._render_end = a, b
        self._preview_log = ""
        self._render_pending = False
        self.progress.setVisible(True)
        self.preview_button.setEnabled(False)
        self.statusBar().showMessage(f"Preparing composite {a:.1f}–{b:.1f} s…")
        self.preview_process.start(cmd[0], cmd[1:])

    def _consume_preview_stderr(self):
        self._preview_log += bytes(self.preview_process.readAllStandardError()).decode("utf-8", "replace")
        self._preview_log = self._preview_log[-8000:]

    def _preview_finished(self, code: int, status):
        self.progress.setVisible(self.export_process.state() != QProcess.NotRunning)
        self.preview_button.setEnabled(True)
        stale = (self._render_revision != self._preview_revision or not self._is_composite())
        if not stale and code == 0 and self._render_tmp_path and Path(self._render_tmp_path).is_file():
            Path(self._render_tmp_path).replace(self._render_path)
            self._activate_proxy(self._render_path, self._render_start, self._render_end)
        elif not stale:
            self.preview_hint.setText("Preview failed; use Refresh mix or inspect FFmpeg errors")
            QMessageBox.warning(self, "Preview render failed", self._preview_log[-3000:] or f"FFmpeg exited {code}")
        if self._render_pending or stale and self._is_composite():
            self._render_pending = False
            self._schedule_preview(immediate=True)

    def _activate_proxy(self, path: str, start: float, end: float):
        self._proxy_path = path
        self._proxy_start, self._proxy_end = start, end
        self._proxy_revision = self._preview_revision
        if self.player.source().toLocalFile() != path:
            self.player.stop()
            self.player.setSource(QUrl.fromLocalFile(path))
        wanted = self._resume_project_time
        self._resume_project_time = None
        target = (round((wanted - start) * 1000) if wanted is not None and start <= wanted < end
                  else 0)
        self.player.setPosition(target)
        self._preview_position_changed(target)
        self.preview_hint.setText(f"Composite ready · project {start:.2f}–{end:.2f} s · Play ▶ to hear the edited mix")
        self.statusBar().showMessage("Composite preview ready · edits are reflected in this proxy", 5000)

    def _playback_error(self, error, message):
        if error != QMediaPlayer.NoError:
            self.statusBar().showMessage(f"Media playback: {message}", 8000)

    def _refresh_table(self, select: int | None = None):
        old_index = self._selected_index()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.project.tracks))
        for row, t in enumerate(self.project.tracks):
            vals = [
                t.name,
                f"{t.offset:.3f}",
                f"{t.trim_start:.3f}",
                "full" if t.trim_end is None else f"{t.trim_end:.3f}",
                str(len(t.cuts)),
                f"{t.volume:.2f}",
                "yes" if t.muted else "",
                "yes" if t.solo else "",
                f"{t.pan:+.2f}",
                "—" if t.sync_marker is None else f"{t.sync_marker:.3f}",
                "—" if t.sync_marker is None else ("manual" if t.sync_confidence is None else f"{t.sync_confidence:.1f}"),
            ]
            for col, value in enumerate(vals):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self.timeline.set_project(self.project)
        self._updating_controls = True
        self.cut_fade.setValue(self.project.cut_fade_ms)
        self._updating_controls = False
        if select is None:
            select = old_index
        if select is not None and 0 <= select < len(self.project.tracks):
            self.table.selectRow(select)
        self.table.blockSignals(False)
        self.timeline.set_selected_index(select if select is not None and 0 <= select < len(self.project.tracks) else None)
        self._selection_changed()

    def _selection_changed(self):
        idx = self._selected_index()
        if idx is None:
            self.timeline.set_selected_index(None)
            self.cut_list.clear()
            return
        self.timeline.set_selected_index(idx)
        t = self.project.tracks[idx]
        self._ensure_waveform(t)
        self._updating_controls = True
        for widget, value in ((self.offset, t.offset), (self.start, t.trim_start),
                              (self.end, 0 if t.trim_end is None else t.trim_end),
                              (self.volume, t.volume), (self.pan, t.pan),
                              (self.marker_time, 0 if t.sync_marker is None else t.sync_marker)):
            widget.setValue(value)
        self.mute.setChecked(t.muted)
        self.solo.setChecked(t.solo)
        self.cut_list.clear()
        for a, b in t.cuts:
            self.cut_list.addItem(f"{a:.3f} – {b:.3f} s (removed)")
        self._updating_controls = False

        if not self._is_composite():
            self._show_source()
        self.statusBar().showMessage(t.path)

    def _controls_changed(self, *_):
        if self._updating_controls:
            return
        idx = self._selected_index()
        if idx is None:
            return
        t = self.project.tracks[idx]
        before = self.project.to_dict()
        new_start = self.start.value()
        new_end = None if self.end.value() == 0 else self.end.value()
        old_start, old_end = t.trim_start, t.trim_end
        t.trim_start, t.trim_end = new_start, new_end
        try:
            if t.source_end is not None:
                t.kept_ranges()
        except ValueError as e:
            t.trim_start, t.trim_end = old_start, old_end
            self._refresh_table(select=idx)
            self.statusBar().showMessage(str(e), 5000)
            return
        t.offset = self.offset.value()
        t.volume = self.volume.value()
        t.muted = self.mute.isChecked()
        t.solo = self.solo.isChecked()
        t.pan = self.pan.value()
        self._invalidate_deleted_marker(t)
        if not self._record_edit(before, "Adjust take controls"):
            return
        self._refresh_table(select=idx)
        self._invalidate_preview()

    def _update_history_actions(self) -> None:
        self.undo_action.setEnabled(self.history.can_undo)
        self.redo_action.setEnabled(self.history.can_redo)
        self.undo_action.setText("Undo " + (self.history.undo_label or ""))
        self.redo_action.setText("Redo " + (self.history.redo_label or ""))

    def _record_edit(self, before: dict, label: str) -> bool:
        changed = self.history.record(before, self.project.to_dict(), label)
        self._update_history_actions()
        return changed

    def _restore_history_project(self, project: Project, label: str) -> None:
        selected = self._selected_index()
        self.project = project
        self.layout_combo.blockSignals(True)
        self.layout_combo.setCurrentText(self.project.layout)
        self.layout_combo.blockSignals(False)
        self._refresh_table(select=selected if selected is not None and selected < len(project.tracks) else
                            len(project.tracks) - 1 if project.tracks else None)
        self._invalidate_preview()
        self._update_history_actions()
        self.statusBar().showMessage(label, 3500)

    def undo(self) -> None:
        label = self.history.undo_label
        project = self.history.undo()
        if project is not None:
            self._restore_history_project(project, "Undid " + (label or "edit"))

    def redo(self) -> None:
        label = self.history.redo_label
        project = self.history.redo()
        if project is not None:
            self._restore_history_project(project, "Redid " + (label or "edit"))

    def _timeline_offset_edited(self, index: int, seconds: float) -> None:
        if not 0 <= index < len(self.project.tracks):
            return
        before = self.project.to_dict()
        self.project.tracks[index].offset = max(0.0, round(seconds, 3))
        if self._record_edit(before, "Drag take to synchronize"):
            self._refresh_table(select=index)
            self._invalidate_preview()

    def _timeline_trim_edited(self, edge: str, seconds: float) -> None:
        index = self._selected_index()
        if index is None:
            return
        before = self.project.to_dict()
        t = self.project.tracks[index]
        try:
            t.set_trim_edge(edge, seconds)
        except (ValueError, IndexError) as exc:
            self.statusBar().showMessage("Trim not changed: " + str(exc), 5500)
            return
        self._invalidate_deleted_marker(t)
        if self._record_edit(before, "Drag trim " + edge):
            self._refresh_table(select=index)
            self._invalidate_preview()

    def _timeline_cut_edited(self, cut_index: int, edge: str, seconds: float) -> None:
        index = self._selected_index()
        if index is None:
            return
        before = self.project.to_dict()
        t = self.project.tracks[index]
        try:
            t.set_cut_edge(cut_index, edge, seconds)
        except (ValueError, IndexError) as exc:
            self.statusBar().showMessage("Cut not changed: " + str(exc), 5500)
            return
        self._invalidate_deleted_marker(t)
        if self._record_edit(before, "Drag cut " + edge):
            self._refresh_table(select=index)
            self._invalidate_preview()

    def _require_source_view(self) -> bool:
        if self._is_composite():
            QMessageBox.information(self, "Use original source", "Switch View to Selected source to mark or cut the original recording.")
            return False
        return True

    def mark_at_playhead(self):
        if not self._require_source_view() or self._selected_index() is None:
            return
        self.marker_time.setValue(self.player.position() / 1000.0)
        self.set_manual_marker()

    def set_manual_marker(self):
        if not self._require_source_view():
            return
        idx = self._selected_index()
        if idx is None:
            return
        t = self.project.tracks[idx]
        position = self.marker_time.value()
        try:
            mapped = t.source_to_local(position)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid trim/cuts", str(e))
            return
        if mapped is None:
            QMessageBox.warning(self, "Marker outside take", "This moment is trimmed or removed. Choose a moment that remains in the take.")
            return
        before = self.project.to_dict()
        t.sync_marker = position
        t.sync_confidence = None
        self._record_edit(before, "Set sync marker")
        self._refresh_table(select=idx)
        self.statusBar().showMessage(f"Manual marker at {position:.3f} s on {t.name}", 4000)

    def clear_manual_marker(self):
        idx = self._selected_index()
        if idx is None:
            return
        t = self.project.tracks[idx]
        before = self.project.to_dict()
        t.sync_marker = None
        t.sync_confidence = None
        self._record_edit(before, "Clear sync marker")
        self._refresh_table(select=idx)

    def manual_align(self):
        before = self.project.to_dict()
        try:
            n = align_marked_tracks(self.project.tracks)
        except ValueError as e:
            QMessageBox.warning(self, "Manual alignment", str(e))
            return
        self._record_edit(before, "Align marked takes")
        self._refresh_table()
        self._invalidate_preview()
        self.statusBar().showMessage(f"Aligned {n} marked takes — verify by ear", 6000)

    def trim_at_playhead(self, which: str):
        if not self._require_source_view():
            return
        idx = self._selected_index()
        if idx is None:
            return
        t = self.project.tracks[idx]
        pos = round(self.player.position() / 1000, 3)
        before = self.project.to_dict()
        if which == "in":
            old = t.trim_start
            t.trim_start = pos
        else:
            old = t.trim_end
            t.trim_end = pos
        try:
            t.kept_ranges()
        except ValueError as e:
            if which == "in":
                t.trim_start = old
            else:
                t.trim_end = old
            QMessageBox.warning(self, "Trim rejected", str(e))
            return
        self._invalidate_deleted_marker(t)
        self._record_edit(before, "Trim take")
        self._refresh_table(select=idx)
        self._invalidate_preview()

    def set_cut_bound(self, bound: str):
        if not self._require_source_view() or self._selected_index() is None:
            return
        (self.cut_a if bound == "a" else self.cut_b).setValue(self.player.position() / 1000)

    def add_cut(self):
        idx = self._selected_index()
        if idx is None:
            return
        t = self.project.tracks[idx]
        before = self.project.to_dict()
        try:
            t.add_cut(self.cut_a.value(), self.cut_b.value())
        except ValueError as e:
            QMessageBox.warning(self, "Cut rejected", str(e))
            return
        self._invalidate_deleted_marker(t)
        self._record_edit(before, "Remove source interval")
        self._refresh_table(select=idx)
        self._invalidate_preview()

    def remove_cut(self):
        idx = self._selected_index()
        which = self.cut_list.currentRow()
        if idx is None or which < 0:
            return
        before = self.project.to_dict()
        del self.project.tracks[idx].cuts[which]
        self._record_edit(before, "Restore source interval")
        self._refresh_table(select=idx)
        self._invalidate_preview()

    @staticmethod
    def _invalidate_deleted_marker(track: Track):
        if track.sync_marker is not None and track.source_to_local(track.sync_marker) is None:
            track.sync_marker = None
            track.sync_confidence = None

    def nudge_selected(self, amount: float):
        idx = self._selected_index()
        if idx is None:
            return
        t = self.project.tracks[idx]
        before = self.project.to_dict()
        t.offset = max(0.0, round(t.offset + amount, 3))
        if not self._record_edit(before, "Nudge take"):
            return
        self._refresh_table(select=idx)
        self._invalidate_preview()
        self.statusBar().showMessage(f"{t.name}: offset {t.offset:.3f} s", 2500)

    def auto_sync(self):
        if len(self.project.tracks) < 2:
            QMessageBox.information(self, "Auto Sync", "Add at least two takes first.")
            return
        try:
            require_ffmpeg()
            samples = [self._samples(t.path) for t in self.project.tracks]
        except FFmpegError as e:
            QMessageBox.critical(self, "Auto Sync failed", str(e))
            return

        before = self.project.to_dict()
        detections = [detect_first_prominent_transient(x, SAMPLE_RATE) for x in samples]
        origins: list[float | None] = []
        for track, det in zip(self.project.tracks, detections):
            if det is not None and det.confidence >= 3.0:
                track.sync_marker = det.time
                track.sync_confidence = det.confidence
                try:
                    location = track.source_to_local(det.time)
                except ValueError:
                    location = None
                if location is None:
                    track.sync_marker = None
                    track.sync_confidence = None
                origins.append(location)
            else:
                track.sync_marker = None
                track.sync_confidence = None
                origins.append(None)

        # Correlation fallback relative to the first track that has a confident clap.
        ref_idx = next((i for i, value in enumerate(origins) if value is not None), 0)
        ref_origin = origins[ref_idx] if origins[ref_idx] is not None else 0.0
        for i, origin in enumerate(origins):
            if origin is not None or i == ref_idx:
                continue
            # Raw-source correlation is not a reliable fallback once either
            # take has had time removed; require a marker in that case.
            if self.project.tracks[ref_idx].cuts or self.project.tracks[i].cuts:
                continue
            estimate = estimate_correlation_lag(samples[ref_idx], samples[i], SAMPLE_RATE)
            if estimate is None:
                continue
            lag, confidence = estimate
            if confidence >= 1.15:
                origins[i] = float(ref_origin - lag)
                self.project.tracks[i].sync_confidence = confidence

        if any(value is None for value in origins):
            failed = [self.project.tracks[i].name for i, value in enumerate(origins) if value is None]
            # A failed multi-take alignment must not silently change markers.
            self.project = Project.from_dict(before)
            self._refresh_table()
            QMessageBox.warning(
                self,
                "Partial Auto Sync",
                "Could not confidently align: " + ", ".join(failed) +
                "\n\nKeep their offsets manual and use the 10/100 ms nudge controls.",
            )
            return

        offsets = normalized_offsets([float(x) for x in origins])
        for track, offset in zip(self.project.tracks, offsets):
            track.offset = round(offset, 3)
            self._ensure_waveform(track)

        selected = self._selected_index()
        self._record_edit(before, "Auto Sync")
        self._refresh_table(select=selected)
        self._invalidate_preview()
        self.statusBar().showMessage("Auto Sync complete — verify by ear and nudge if necessary", 6000)

    def _layout_changed(self, value: str):
        if self.project.layout != value:
            before = self.project.to_dict()
            self.project.layout = value
            self._record_edit(before, "Change layout")
            self._invalidate_preview()

    def toggle_play(self):
        if self._is_composite() and self._proxy_revision != self._preview_revision:
            self._schedule_preview(immediate=True)
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def new_project(self):
        self._invalidate_preview()
        self._resume_project_time = None
        self.player.stop()
        self.project = Project()
        self.history.clear()
        self._update_history_actions()
        self.project_path = None
        self._audio_cache.clear()
        self.timeline.clear_waveforms()
        self.player.setSource(QUrl())
        self.layout_combo.blockSignals(True)
        self.layout_combo.setCurrentText("auto")
        self.layout_combo.blockSignals(False)
        self._refresh_table()
        self.setWindowTitle(f"CoverMaker {APP_VERSION}")

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open project", "", "CoverMaker (*.json)")
        if not path:
            return
        try:
            self.project = Project.load(path)
        except Exception as e:
            QMessageBox.critical(self, "Could not open project", str(e))
            return
        self._invalidate_preview()
        self._resume_project_time = None
        self.project_path = path
        self.history.clear()
        self._update_history_actions()
        self._audio_cache.clear()
        self.timeline.clear_waveforms()
        self.player.setSource(QUrl())
        self.layout_combo.blockSignals(True)
        self.layout_combo.setCurrentText(self.project.layout)
        self.layout_combo.blockSignals(False)
        for track in self.project.tracks:
            self._ensure_waveform(track)
        self._refresh_table(select=0 if self.project.tracks else None)
        self._schedule_preview()
        self.setWindowTitle(f"CoverMaker {APP_VERSION} — {Path(path).name}")

    def save_project(self):
        if not self.project_path:
            return self.save_project_as()
        self.project.save(self.project_path)
        self.statusBar().showMessage(f"Saved {self.project_path}", 3000)

    def save_project_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save project", "cover.json", "CoverMaker (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        self.project_path = path
        self.save_project()
        self.setWindowTitle(f"CoverMaker {APP_VERSION} — {Path(path).name}")

    def export_mp4(self):
        if self.export_process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "Export running", "An export is already running.")
            return
        if not self.project.tracks:
            QMessageBox.information(self, "Nothing to export", "Add at least one take first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export MP4", "cover.mp4", "MP4 video (*.mp4)")
        if not path:
            return
        if not path.lower().endswith(".mp4"):
            path += ".mp4"
        try:
            cmd = build_export_command(self.project, path)
        except FFmpegError as e:
            QMessageBox.critical(self, "Cannot export", str(e))
            return

        self._export_log = ""
        self.progress.setVisible(True)
        self.statusBar().showMessage("Rendering…")
        self.export_process.start(cmd[0], cmd[1:])

    def _consume_export_stderr(self):
        text = bytes(self.export_process.readAllStandardError()).decode("utf-8", "replace")
        self._export_log += text
        if "time=" in text:
            last = [line for line in text.splitlines() if "time=" in line]
            if last:
                self.statusBar().showMessage("Rendering… " + last[-1][-90:])

    def _export_finished(self, exit_code: int, _status):
        self.progress.setVisible(self.preview_process.state() != QProcess.NotRunning)
        if exit_code == 0:
            self.statusBar().showMessage("Export finished", 5000)
            QMessageBox.information(self, "Done", "The cover video was exported successfully.")
        else:
            tail = self._export_log[-4000:]
            QMessageBox.critical(self, "FFmpeg failed", tail or f"Exit code {exit_code}")
            self.statusBar().showMessage("Export failed", 5000)


    def closeEvent(self, event):  # noqa: N802
        self.player.stop()
        self.player.setSource(QUrl())
        if self.preview_process.state() != QProcess.NotRunning:
            self.preview_process.kill()
            self.preview_process.waitForFinished(2000)
        self._preview_dir.cleanup()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("CoverMaker")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
