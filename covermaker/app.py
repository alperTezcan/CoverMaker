from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QUrl, Qt
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
from .ffmpeg import FFmpegError, build_export_command, probe_duration, require_ffmpeg
from .model import Project, Track
from .timeline import TimelineWidget


APP_VERSION = "0.2"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"CoverMaker {APP_VERSION}")
        self.resize(1240, 820)

        self.project = Project()
        self.project_path: str | None = None
        self._updating_controls = False
        self._audio_cache: dict[str, object] = {}

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
        left_layout.addWidget(self.video, 1)

        transport = QHBoxLayout()
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(self.player.setPosition)
        self.player.positionChanged.connect(self.position_slider.setValue)
        self.player.durationChanged.connect(lambda d: self.position_slider.setRange(0, d))
        transport.addWidget(QLabel("Selected take"))
        transport.addWidget(self.position_slider, 1)
        left_layout.addLayout(transport)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Take", "Offset", "Start", "End", "Volume", "Mute", "Clap", "Conf."]
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
        for w in (self.offset, self.start, self.end, self.volume):
            w.valueChanged.connect(self._controls_changed)
        self.mute.toggled.connect(self._controls_changed)
        form.addRow("Offset (s)", self.offset)
        form.addRow("Trim start (s)", self.start)
        form.addRow("Trim end (s)", self.end)
        form.addRow("Volume", self.volume)
        form.addRow("Mute", self.mute)
        right_layout.addLayout(form)

        nudge_row = QHBoxLayout()
        for label, amount in [("−100 ms", -0.100), ("−10 ms", -0.010), ("+10 ms", 0.010), ("+100 ms", 0.100)]:
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, a=amount: self.nudge_selected(a))
            nudge_row.addWidget(b)
        right_layout.addLayout(nudge_row)
        splitter.addWidget(right)
        splitter.setSizes([760, 480])

        self.timeline = TimelineWidget()
        root.addWidget(self.timeline)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setRange(0, 0)
        root.addWidget(self.progress)
        self.statusBar().showMessage("Ready")

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

        self._add_action("Play/Pause", "Space", self.toggle_play)
        self._add_action("Nudge −10 ms", "Ctrl+Left", lambda: self.nudge_selected(-0.010))
        self._add_action("Nudge +10 ms", "Ctrl+Right", lambda: self.nudge_selected(0.010))
        self._add_action("Nudge −100 ms", "Alt+Left", lambda: self.nudge_selected(-0.100))
        self._add_action("Nudge +100 ms", "Alt+Right", lambda: self.nudge_selected(0.100))

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
        return s

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
            self.timeline.set_waveform(track.path, waveform_peaks(samples, 1200))
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
        for file in files:
            track = Track(path=file, duration=probe_duration(file))
            self.project.tracks.append(track)
            self._ensure_waveform(track)
        self._refresh_table(select=len(self.project.tracks) - 1)

    def remove_selected(self):
        idx = self._selected_index()
        if idx is None:
            return
        del self.project.tracks[idx]
        self.player.stop()
        self.player.setSource(QUrl())
        self._refresh_table(select=min(idx, len(self.project.tracks) - 1))

    def _refresh_table(self, select: int | None = None):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.project.tracks))
        for row, t in enumerate(self.project.tracks):
            vals = [
                t.name,
                f"{t.offset:.3f}",
                f"{t.trim_start:.3f}",
                "full" if t.trim_end is None else f"{t.trim_end:.3f}",
                f"{t.volume:.2f}",
                "yes" if t.muted else "",
                "—" if t.sync_marker is None else f"{t.sync_marker:.3f}",
                "—" if t.sync_confidence is None else f"{t.sync_confidence:.1f}",
            ]
            for col, value in enumerate(vals):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self.timeline.set_project(self.project)
        self.table.blockSignals(False)
        if select is not None and 0 <= select < len(self.project.tracks):
            self.table.selectRow(select)

    def _selection_changed(self):
        idx = self._selected_index()
        if idx is None:
            return
        t = self.project.tracks[idx]
        self._ensure_waveform(t)
        self._updating_controls = True
        self.offset.setValue(t.offset)
        self.start.setValue(t.trim_start)
        self.end.setValue(0 if t.trim_end is None else t.trim_end)
        self.volume.setValue(t.volume)
        self.mute.setChecked(t.muted)
        self._updating_controls = False

        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(t.path))
        if t.trim_start:
            self.player.setPosition(int(t.trim_start * 1000))
        self.statusBar().showMessage(t.path)

    def _controls_changed(self, *_):
        if self._updating_controls:
            return
        idx = self._selected_index()
        if idx is None:
            return
        t = self.project.tracks[idx]
        t.offset = self.offset.value()
        t.trim_start = self.start.value()
        t.trim_end = None if self.end.value() == 0 else self.end.value()
        t.volume = self.volume.value()
        t.muted = self.mute.isChecked()
        self._refresh_table(select=idx)

    def nudge_selected(self, amount: float):
        idx = self._selected_index()
        if idx is None:
            return
        t = self.project.tracks[idx]
        t.offset = max(0.0, round(t.offset + amount, 3))
        self._refresh_table(select=idx)
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

        detections = [detect_first_prominent_transient(x, SAMPLE_RATE) for x in samples]
        origins: list[float | None] = []
        for track, det in zip(self.project.tracks, detections):
            if det is not None and det.confidence >= 3.0:
                track.sync_marker = det.time
                track.sync_confidence = det.confidence
                origins.append(det.time - track.trim_start)
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
            estimate = estimate_correlation_lag(samples[ref_idx], samples[i], SAMPLE_RATE)
            if estimate is None:
                continue
            lag, confidence = estimate
            if confidence >= 1.15:
                origins[i] = float(ref_origin - lag)
                self.project.tracks[i].sync_confidence = confidence

        if any(value is None for value in origins):
            failed = [self.project.tracks[i].name for i, value in enumerate(origins) if value is None]
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
        self._refresh_table(select=selected)
        self.statusBar().showMessage("Auto Sync complete — verify by ear and nudge if necessary", 6000)

    def _layout_changed(self, value: str):
        self.project.layout = value

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def new_project(self):
        self.player.stop()
        self.project = Project()
        self.project_path = None
        self._audio_cache.clear()
        self.timeline.clear_waveforms()
        self.layout_combo.setCurrentText("auto")
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
        self.project_path = path
        self._audio_cache.clear()
        self.timeline.clear_waveforms()
        self.layout_combo.setCurrentText(self.project.layout)
        for track in self.project.tracks:
            self._ensure_waveform(track)
        self._refresh_table(select=0 if self.project.tracks else None)
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
        self.progress.setVisible(False)
        if exit_code == 0:
            self.statusBar().showMessage("Export finished", 5000)
            QMessageBox.information(self, "Done", "The cover video was exported successfully.")
        else:
            tail = self._export_log[-4000:]
            QMessageBox.critical(self, "FFmpeg failed", tail or f"Exit code {exit_code}")
            self.statusBar().showMessage("Export failed", 5000)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("CoverMaker")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
