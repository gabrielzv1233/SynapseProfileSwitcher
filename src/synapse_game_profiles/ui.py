from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

from PySide6.QtCore import QRunnable, QSize, Qt, QThreadPool, Signal, QObject
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .game_library import DiscoveredExecutable, discover_games
from .icons import cache_artwork, cache_executable_icon
from .models import AppRecord
from .profile_backend import DummyProfileBackend
from .storage import Store
from .window_watcher import ForegroundWatcher


TILE_WIDTH = 210
TILE_HEIGHT = 125


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


class WorkerSignals(QObject):
    finished = Signal(object)
    status = Signal(str)


class ScanWorker(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = WorkerSignals()

    def run(self) -> None:
        result = discover_games(self.signals.status.emit)
        self.signals.finished.emit(result)


class Tile(QFrame):
    activated = Signal(object)
    context_requested = Signal(object, object)

    def __init__(self, record: AppRecord | None, add_tile: bool = False) -> None:
        super().__init__()
        self.record = record
        self.add_tile = add_tile
        self.setFixedSize(TILE_WIDTH, TILE_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("QFrame { border: 1px solid #3d3d3d; border-radius: 12px; background: #262626; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        label = QLabel("+" if add_tile else record.title)
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        font = QFont()
        font.setBold(True)
        font.setPointSize(28 if add_tile else 11)
        label.setFont(font)
        label.setStyleSheet("color: white; background: transparent; border: 0;")
        layout.addWidget(label)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.record)

    def mousePressEvent(self, event) -> None:
        if self.add_tile and event.button() == Qt.LeftButton:
            self.activated.emit(None)
        elif not self.add_tile and event.button() == Qt.RightButton:
            self.context_requested.emit(self.record, event.globalPosition().toPoint())
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.add_tile or not self.record or not self.record.icon_path or not Path(self.record.icon_path).is_file():
            return
        pixmap = QPixmap(self.record.icon_path)
        if pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        scaled = pixmap.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = (scaled.width() - self.width()) // 2
        y = (scaled.height() - self.height()) // 2
        painter.drawPixmap(self.rect(), scaled, scaled.rect().adjusted(x, y, -x, -y))
        painter.fillRect(self.rect(), QColor(0, 0, 0, 125))
        painter.end()
        for child in self.findChildren(QLabel):
            child.raise_()


class FlowLayout(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[QWidget] = []

    def clear(self) -> None:
        for item in self.items:
            item.setParent(None)
            item.deleteLater()
        self.items.clear()

    def add(self, widget: QWidget) -> None:
        widget.setParent(self)
        widget.show()
        self.items.append(widget)
        self._relayout()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        margin = 12
        spacing = 12
        width = max(self.width(), TILE_WIDTH + margin * 2)
        columns = max(1, (width - margin) // (TILE_WIDTH + spacing))
        for index, item in enumerate(self.items):
            row, column = divmod(index, columns)
            item.move(margin + column * (TILE_WIDTH + spacing), margin + row * (TILE_HEIGHT + spacing))
        rows = (len(self.items) + columns - 1) // columns
        self.setMinimumHeight(margin + rows * (TILE_HEIGHT + spacing))


class ModifyDialog(QDialog):
    def __init__(self, record: AppRecord, parent=None) -> None:
        super().__init__(parent)
        self.record = record
        self.selected_artwork: str | None = None
        self.setWindowTitle(f"Modify {record.title}")
        self.resize(520, 330)

        layout = QVBoxLayout(self)
        self.preview = QPushButton()
        self.preview.setFixedSize(210, 125)
        self.preview.setIconSize(QSize(200, 115))
        self.preview.clicked.connect(self.choose_artwork)
        self._update_preview(record.icon_path)
        layout.addWidget(self.preview, alignment=Qt.AlignHCenter)

        form = QFormLayout()
        self.title_edit = QLineEdit(record.title)
        self.exe_edit = QLineEdit(record.executable)
        browse_exe = QPushButton("Browse...")
        browse_exe.clicked.connect(self.choose_executable)
        exe_row = QHBoxLayout()
        exe_row.addWidget(self.exe_edit)
        exe_row.addWidget(browse_exe)
        exe_widget = QWidget()
        exe_widget.setLayout(exe_row)
        form.addRow("Title", self.title_edit)
        form.addRow("Executable", exe_widget)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        save = QPushButton("Save")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _update_preview(self, path: str | None) -> None:
        icon = QIcon(path) if path else QIcon(self.exe_edit.text() if hasattr(self, "exe_edit") else self.record.executable)
        self.preview.setIcon(icon)
        self.preview.setText("Click to change artwork" if icon.isNull() else "")

    def choose_artwork(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose tile image", "", "Images or executables (*.png *.jpg *.jpeg *.webp *.bmp *.ico *.exe);;All files (*.*)")
        if path:
            self.selected_artwork = path
            self._update_preview(path)

    def choose_executable(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose executable", self.exe_edit.text(), "Executables (*.exe);;All files (*.*)")
        if path:
            self.exe_edit.setText(path)


class ProfileDialog(QDialog):
    def __init__(self, backend: DummyProfileBackend, selected_uuid: str | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set profile")
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        none = QListWidgetItem("No profile")
        none.setData(Qt.UserRole, None)
        self.list.addItem(none)
        for profile in backend.list_profiles():
            item = QListWidgetItem(profile.name)
            item.setData(Qt.UserRole, profile.uuid)
            item.setToolTip(profile.uuid)
            self.list.addItem(item)
            if profile.uuid == selected_uuid:
                self.list.setCurrentItem(item)
        if not selected_uuid:
            self.list.setCurrentItem(none)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        layout.addWidget(self.list)
        button = QPushButton("Select")
        button.clicked.connect(self.accept)
        layout.addWidget(button)

    def selected_uuid(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None


class SettingsDialog(QDialog):
    def __init__(self, store: Store, backend: DummyProfileBackend, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.settings = store.load_settings()
        self.backend = backend
        self.setWindowTitle("Settings")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.subprocess = QCheckBox("Allow parent/subprocess matching")
        self.subprocess.setChecked(bool(self.settings.get("match_subprocesses", True)))
        self.default_profile = QComboBox()
        self.default_profile.addItem("Use last unassociated profile", None)
        for profile in backend.list_profiles():
            self.default_profile.addItem(profile.name, profile.uuid)
            if profile.uuid == self.settings.get("default_profile_uuid"):
                self.default_profile.setCurrentIndex(self.default_profile.count() - 1)
        form.addRow("Process matching", self.subprocess)
        form.addRow("Fallback profile", self.default_profile)
        layout.addLayout(form)
        save = QPushButton("Save")
        save.clicked.connect(self.accept)
        layout.addWidget(save)

    def accept(self) -> None:
        self.settings["match_subprocesses"] = self.subprocess.isChecked()
        self.settings["default_profile_uuid"] = self.default_profile.currentData()
        self.store.save_settings(self.settings)
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.store = Store()
        self.apps = self.store.load_apps()
        self.overrides = self.store.load_overrides()
        self.backend = DummyProfileBackend()
        self.pool = QThreadPool.globalInstance()
        self.watcher = ForegroundWatcher(self.store, self.backend, self.apps)
        self.watcher.start()
        self.scanning = False

        self.setWindowTitle("Synapse Game Profiles")
        self.resize(1050, 720)
        self.setStyleSheet("QMainWindow, QDialog { background: #171717; color: white; } QLabel, QCheckBox { color: white; }")

        central = QWidget()
        outer = QVBoxLayout(central)
        top = QHBoxLayout()
        title = QLabel("Applications")
        font = title.font()
        font.setPointSize(18)
        font.setBold(True)
        title.setFont(font)
        top.addWidget(title)
        top.addStretch()
        self.status = QLabel("Ready")
        top.addWidget(self.status)
        scan = QPushButton("Rescan")
        scan.clicked.connect(self.scan)
        settings = QPushButton("Settings")
        settings.clicked.connect(self.open_settings)
        top.addWidget(scan)
        top.addWidget(settings)
        outer.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.flow = FlowLayout()
        scroll.setWidget(self.flow)
        outer.addWidget(scroll)
        self.setCentralWidget(central)

        self.tray = QSystemTrayIcon(QIcon.fromTheme("applications-games"), self)
        tray_menu = QMenu()
        show_action = tray_menu.addAction("Show")
        show_action.triggered.connect(self.showNormal)
        scan_action = tray_menu.addAction("Rescan")
        scan_action.triggered.connect(self.scan)
        tray_menu.addSeparator()
        quit_action = tray_menu.addAction("Quit")
        quit_action.triggered.connect(QApplication.instance().quit)
        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(lambda reason: self.showNormal() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

        self.refresh_tiles()
        if self.store.load_settings().get("scan_on_startup", True):
            self.scan()

    def closeEvent(self, event) -> None:
        if self.tray.isVisible():
            self.hide()
            event.ignore()
        else:
            super().closeEvent(event)

    def refresh_tiles(self) -> None:
        self.flow.clear()
        add = Tile(None, True)
        add.activated.connect(lambda _: self.manual_add())
        self.flow.add(add)
        for record in sorted((item for item in self.apps.values() if not item.removed), key=lambda item: item.title.casefold()):
            tile = Tile(record)
            tile.activated.connect(self.launch)
            tile.context_requested.connect(self.context_menu)
            self.flow.add(tile)

    def scan(self) -> None:
        if self.scanning:
            return
        self.scanning = True
        worker = ScanWorker()
        worker.signals.status.connect(self.status.setText)
        worker.signals.finished.connect(self.scan_finished)
        self.pool.start(worker)

    def scan_finished(self, discovered: list[DiscoveredExecutable]) -> None:
        known_by_path = {_norm(record.executable): record for record in self.apps.values()}
        for item in discovered:
            key = _norm(item.executable)
            record = known_by_path.get(key)
            if record:
                if not record.manually_added and not self.overrides.get(record.id, {}).get("title"):
                    record.title = item.title
                    record.launcher = item.launcher
                    record.install_root = item.install_root
                continue
            record = AppRecord(
                id=str(uuid.uuid4()),
                executable=item.executable,
                title=item.title or Path(item.executable).stem,
                launcher=item.launcher,
                install_root=item.install_root,
            )
            record.icon_path = cache_executable_icon(record.executable)
            self.apps[record.id] = record
            known_by_path[key] = record
        self.store.save_apps(self.apps)
        self.scanning = False
        self.status.setText(f"Found {len(discovered)} executable(s)")
        self.refresh_tiles()

    def manual_add(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Add application", "", "Executables (*.exe);;All files (*.*)")
        if not path:
            return
        existing = next((item for item in self.apps.values() if _norm(item.executable) == _norm(path)), None)
        if existing:
            existing.removed = False
            self.modify(existing)
            return
        record = AppRecord(str(uuid.uuid4()), path, Path(path).stem, manually_added=True, icon_path=cache_executable_icon(path))
        self.apps[record.id] = record
        self.store.save_apps(self.apps)
        self.modify(record)

    def context_menu(self, record: AppRecord, position) -> None:
        menu = QMenu(self)
        modify = menu.addAction("Modify item")
        remove = menu.addAction("Remove item")
        profile = menu.addAction("Set profile")
        menu.addSeparator()
        launch = menu.addAction("Launch application")
        chosen = menu.exec(position)
        if chosen == modify:
            self.modify(record)
        elif chosen == remove:
            self.remove(record)
        elif chosen == profile:
            self.set_profile(record)
        elif chosen == launch:
            self.launch(record)

    def modify(self, record: AppRecord) -> None:
        dialog = ModifyDialog(record, self)
        if dialog.exec() != QDialog.Accepted:
            return
        executable = dialog.exe_edit.text().strip()
        if not executable or not Path(executable).is_file():
            QMessageBox.warning(self, "Invalid executable", "Choose an existing executable.")
            return
        record.executable = str(Path(executable).resolve())
        record.title = dialog.title_edit.text().strip() or Path(executable).stem
        if dialog.selected_artwork:
            cached = cache_artwork(dialog.selected_artwork)
            if cached:
                record.icon_path = cached
        elif not record.icon_path:
            record.icon_path = cache_executable_icon(record.executable)
        self.overrides[record.id] = {
            "title": record.title,
            "executable": record.executable,
            "icon_path": record.icon_path,
        }
        self.store.save_overrides(self.overrides)
        self.store.save_apps(self.apps)
        self.refresh_tiles()

    def remove(self, record: AppRecord) -> None:
        record.removed = True
        self.store.save_apps(self.apps)
        self.refresh_tiles()

    def set_profile(self, record: AppRecord) -> None:
        dialog = ProfileDialog(self.backend, record.profile_uuid, self)
        if dialog.exec() == QDialog.Accepted:
            record.profile_uuid = dialog.selected_uuid()
            self.store.save_apps(self.apps)
            self.refresh_tiles()

    def launch(self, record: AppRecord | None) -> None:
        if not record:
            return
        try:
            subprocess.Popen([record.executable], cwd=str(Path(record.executable).parent))
        except OSError as error:
            QMessageBox.critical(self, "Launch failed", str(error))

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.store, self.backend, self)
        if dialog.exec() == QDialog.Accepted:
            self.watcher.reload_settings()
