from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
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
    QSizePolicy,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .debug_log import get_logger
from .game_library import DiscoveredExecutable, discover_games
from .icons import cache_artwork, cache_executable_icon
from .models import AppRecord
from .profile_backend import DummyProfileBackend
from .storage import Store
from .window_watcher import ForegroundWatcher


MIN_COLUMNS = 5
TARGET_TILE_SIZE = 156
MIN_TILE_SIZE = 112
TILE_SPACING = 8
DEFAULT_WINDOW_WIDTH = 840
DEFAULT_WINDOW_HEIGHT = 576
_LOG = get_logger("ui")


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


class ScanThread(QThread):
    status = Signal(str)

    def __init__(self, explicit: bool, parent=None) -> None:
        super().__init__(parent)
        self.explicit = explicit
        self.results: list[DiscoveredExecutable] = []
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            _LOG.info("Background scan started explicit=%s", self.explicit)
            self.results = discover_games(self.status.emit)
            _LOG.info("Background scan finished with %d game(s)", len(self.results))
        except BaseException as error:
            self.error = error
            _LOG.exception("Background scan crashed")


class Tile(QFrame):
    activated = Signal(object)
    context_requested = Signal(object, object)

    def __init__(self, record: AppRecord | None, add_tile: bool = False) -> None:
        super().__init__()
        self.record = record
        self.add_tile = add_tile
        self.setFixedSize(TARGET_TILE_SIZE, TARGET_TILE_SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QFrame { border: 1px solid #3d3d3d; border-radius: 8px; background: #262626; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.label = QLabel("+" if add_tile else record.title)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        font = QFont()
        font.setBold(True)
        font.setPointSize(24 if add_tile else 10)
        self.label.setFont(font)
        self.label.setStyleSheet("color: white; background: transparent; border: 0;")
        layout.addWidget(self.label)

    def set_tile_size(self, size: int) -> None:
        if self.width() != size or self.height() != size:
            self.setFixedSize(size, size)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.record)
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        if self.add_tile and event.button() == Qt.LeftButton:
            self.activated.emit(None)
        elif not self.add_tile and event.button() == Qt.RightButton:
            self.context_requested.emit(self.record, event.globalPosition().toPoint())
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.add_tile or not self.record or not self.record.icon_path:
            return

        icon_path = Path(self.record.icon_path)
        if not icon_path.is_file():
            return

        pixmap = QPixmap(str(icon_path))
        if pixmap.isNull():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        scaled = pixmap.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        source_x = max(0, (scaled.width() - self.width()) // 2)
        source_y = max(0, (scaled.height() - self.height()) // 2)
        source = QRect(source_x, source_y, self.width(), self.height())
        painter.drawPixmap(self.rect(), scaled, source)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 125))
        painter.end()
        self.label.raise_()


class FlowLayout(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[Tile] = []
        self._relayout_running = False
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumWidth(MIN_COLUMNS * MIN_TILE_SIZE + (MIN_COLUMNS - 1) * TILE_SPACING)

    def clear(self) -> None:
        for item in self.items:
            item.setParent(None)
            item.deleteLater()
        self.items.clear()
        self._relayout()

    def add(self, widget: Tile) -> None:
        widget.setParent(self)
        widget.show()
        self.items.append(widget)
        self._relayout()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        if self._relayout_running:
            return

        self._relayout_running = True
        try:
            width = max(1, self.width())
            columns = max(MIN_COLUMNS, (width + TILE_SPACING) // (TARGET_TILE_SIZE + TILE_SPACING))
            tile_size = max(1, (width - (columns - 1) * TILE_SPACING) // columns)

            for index, item in enumerate(self.items):
                row, column = divmod(index, columns)
                item.set_tile_size(tile_size)
                item.move(column * (tile_size + TILE_SPACING), row * (tile_size + TILE_SPACING))

            rows = (len(self.items) + columns - 1) // columns
            height = rows * tile_size + max(0, rows - 1) * TILE_SPACING
            if self.minimumHeight() != height:
                self.setMinimumHeight(height)
        finally:
            self._relayout_running = False


class ModifyDialog(QDialog):
    def __init__(self, record: AppRecord, parent=None) -> None:
        super().__init__(parent)
        self.record = record
        self.selected_artwork: str | None = None
        self.setWindowTitle(f"Modify {record.title}")
        self.resize(520, 380)

        layout = QVBoxLayout(self)
        self.preview = QPushButton()
        self.preview.setFixedSize(180, 180)
        self.preview.setIconSize(QSize(172, 172))
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
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose tile image",
            "",
            "Images or executables (*.png *.jpg *.jpeg *.webp *.bmp *.ico *.exe);;All files (*.*)",
        )
        if path:
            self.selected_artwork = path
            self._update_preview(path)

    def choose_executable(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose executable",
            self.exe_edit.text(),
            "Executables (*.exe);;All files (*.*)",
        )
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
    def __init__(self, store: Store, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.settings = store.load_settings()
        self.setWindowTitle("Settings")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.subprocess = QCheckBox("Allow parent/subprocess matching")
        self.subprocess.setChecked(bool(self.settings.get("match_subprocesses", True)))
        form.addRow("Process matching", self.subprocess)
        layout.addLayout(form)
        save = QPushButton("Save")
        save.clicked.connect(self.accept)
        layout.addWidget(save)

    def accept(self) -> None:
        self.settings["match_subprocesses"] = self.subprocess.isChecked()
        self.store.save_settings(self.settings)
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.store = Store()
        self.apps = self.store.load_apps()
        self.overrides = self.store.load_overrides()
        self.backend = DummyProfileBackend()
        self.scan_thread: ScanThread | None = None
        self.scanning = False

        self.watcher = ForegroundWatcher(self.store, self.backend, self.apps)
        self.watcher.start()

        self.setWindowTitle("Synapse Game Profiles")
        self.setMinimumSize(640, 480)
        self._set_default_window_size()
        self.setStyleSheet(
            "QMainWindow, QDialog { background: #171717; color: white; } "
            "QLabel, QCheckBox { color: white; } "
            "QScrollArea { border: 0; background: #171717; }"
        )

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        top = QHBoxLayout(header)
        top.setContentsMargins(12, 8, 12, 8)

        title = QLabel("Applications")
        font = title.font()
        font.setPointSize(14)
        font.setBold(True)
        title.setFont(font)
        top.addWidget(title)

        default_label = QLabel("Default:")
        top.addWidget(default_label)
        self.default_profile = QComboBox()
        self._populate_default_profiles()
        self.default_profile.currentIndexChanged.connect(self.default_profile_changed)
        top.addWidget(self.default_profile)

        top.addStretch()
        self.status = QLabel("Ready")
        top.addWidget(self.status)
        scan = QPushButton("Rescan")
        scan.clicked.connect(lambda: self.scan(explicit=True))
        settings = QPushButton("Settings")
        settings.clicked.connect(self.open_settings)
        top.addWidget(scan)
        top.addWidget(settings)
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
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
        scan_action.triggered.connect(lambda: self.scan(explicit=True))
        tray_menu.addSeparator()
        quit_action = tray_menu.addAction("Quit")
        quit_action.triggered.connect(QApplication.instance().quit)
        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(lambda reason: self.showNormal() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

        self.refresh_tiles()
        if self.store.load_settings().get("scan_on_startup", True):
            self.scan(explicit=False)

    def _set_default_window_size(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
            return
        available = screen.availableGeometry().size()
        width = min(DEFAULT_WINDOW_WIDTH, max(640, available.width() - 80))
        height = min(DEFAULT_WINDOW_HEIGHT, max(480, available.height() - 80))
        self.resize(width, height)

    def _populate_default_profiles(self) -> None:
        settings = self.store.load_settings()
        selected = settings.get("default_profile_uuid")
        self.default_profile.blockSignals(True)
        self.default_profile.clear()
        self.default_profile.addItem("Last unassociated", None)
        for profile in self.backend.list_profiles():
            self.default_profile.addItem(profile.name, profile.uuid)
            if profile.uuid == selected:
                self.default_profile.setCurrentIndex(self.default_profile.count() - 1)
        self.default_profile.blockSignals(False)

    def default_profile_changed(self) -> None:
        settings = self.store.load_settings()
        settings["default_profile_uuid"] = self.default_profile.currentData()
        self.store.save_settings(settings)
        self.watcher.reload_settings()
        _LOG.info("Default profile changed to %s", self.default_profile.currentData())

    def closeEvent(self, event) -> None:
        if self.tray.isVisible():
            self.hide()
            event.ignore()
        else:
            super().closeEvent(event)

    def refresh_tiles(self) -> None:
        _LOG.debug("Refreshing tiles from %d app record(s)", len(self.apps))
        self.flow.clear()
        add = Tile(None, True)
        add.activated.connect(lambda _: self.manual_add())
        self.flow.add(add)
        for record in sorted(
            (item for item in self.apps.values() if not item.removed),
            key=lambda item: item.title.casefold(),
        ):
            tile = Tile(record)
            tile.activated.connect(self.launch)
            tile.context_requested.connect(self.context_menu)
            self.flow.add(tile)

    def scan(self, explicit: bool = True) -> None:
        if self.scanning:
            _LOG.debug("Ignoring scan request because a scan is already running")
            return
        self.scanning = True
        self.status.setText("Scanning...")
        self.scan_thread = ScanThread(explicit, self)
        self.scan_thread.status.connect(self.status.setText)
        self.scan_thread.finished.connect(self.scan_completed)
        self.scan_thread.start()

    def scan_completed(self) -> None:
        thread = self.scan_thread
        if thread is None:
            return

        _LOG.info(
            "Scan thread completion reached UI thread: results=%d error=%r explicit=%s",
            len(thread.results),
            thread.error,
            thread.explicit,
        )

        self.scanning = False
        self.scan_thread = None

        try:
            if thread.error is not None:
                self.status.setText("Scan failed")
                QMessageBox.critical(self, "Scan failed", f"{type(thread.error).__name__}: {thread.error}")
                return
            self.apply_discovery(thread.results, explicit=thread.explicit)
        except Exception as error:
            _LOG.exception("Failed while applying discovery results")
            self.status.setText("Failed applying scan")
            QMessageBox.critical(self, "Scan apply failed", f"{type(error).__name__}: {error}")
        finally:
            thread.deleteLater()

    def apply_discovery(self, discovered: list[DiscoveredExecutable], explicit: bool) -> None:
        _LOG.info("Applying %d playable game(s), explicit=%s", len(discovered), explicit)
        known_by_path = {_norm(record.executable): record for record in self.apps.values()}
        discovered_paths = {_norm(item.executable) for item in discovered}
        added = 0
        restored = 0
        stale_hidden = 0
        icons_repaired = 0

        for item in discovered:
            key = _norm(item.executable)
            record = known_by_path.get(key)
            if record:
                if record.removed and (explicit or not record.removed_by_user):
                    record.removed = False
                    record.removed_by_user = False
                    restored += 1

                override = self.overrides.get(record.id, {})
                if not record.manually_added:
                    if not override.get("title"):
                        record.title = item.title or Path(item.executable).stem
                    record.launcher = item.launcher
                    record.install_root = item.install_root

                custom_icon = bool(override.get("custom_icon", override.get("icon_path")))
                icon_valid = bool(record.icon_path and Path(record.icon_path).is_file())
                if not custom_icon and (explicit or not icon_valid):
                    repaired = cache_executable_icon(record.executable, force=explicit)
                    if repaired:
                        if record.icon_path != repaired:
                            icons_repaired += 1
                        record.icon_path = repaired
                continue

            record = AppRecord(
                id=str(uuid.uuid4()),
                executable=item.executable,
                title=item.title or Path(item.executable).stem,
                launcher=item.launcher,
                install_root=item.install_root,
                icon_path=cache_executable_icon(item.executable),
            )
            self.apps[record.id] = record
            known_by_path[key] = record
            added += 1

        for record in self.apps.values():
            if record.manually_added or record.removed_by_user:
                continue
            if _norm(record.executable) not in discovered_paths and not record.removed:
                record.removed = True
                stale_hidden += 1

        self.store.save_apps(self.apps)
        self.status.setText(f"Found {len(discovered)} game(s)")
        _LOG.info(
            "Discovery applied: games=%d added=%d restored=%d stale_hidden=%d icons_repaired=%d total_records=%d",
            len(discovered),
            added,
            restored,
            stale_hidden,
            icons_repaired,
            len(self.apps),
        )
        self.refresh_tiles()

    def manual_add(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Add application", "", "Executables (*.exe);;All files (*.*)")
        if not path:
            return
        existing = next((item for item in self.apps.values() if _norm(item.executable) == _norm(path)), None)
        if existing:
            existing.removed = False
            existing.removed_by_user = False
            existing.manually_added = True
            self.store.save_apps(self.apps)
            self.modify(existing)
            return
        record = AppRecord(
            str(uuid.uuid4()),
            path,
            Path(path).stem,
            manually_added=True,
            icon_path=cache_executable_icon(path),
        )
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

        old_executable = record.executable
        executable_changed = _norm(old_executable) != _norm(executable)
        record.executable = str(Path(executable).resolve())
        record.title = dialog.title_edit.text().strip() or Path(executable).stem

        old_override = self.overrides.get(record.id, {})
        custom_icon = bool(old_override.get("custom_icon", old_override.get("icon_path")))

        if dialog.selected_artwork:
            cached = cache_artwork(dialog.selected_artwork)
            if cached:
                record.icon_path = cached
                custom_icon = True
        elif executable_changed and not custom_icon:
            record.icon_path = cache_executable_icon(record.executable, force=True)

        if executable_changed:
            record.manually_added = True
            record.removed = False
            record.removed_by_user = False

        override = {
            "title": record.title,
            "executable": record.executable,
        }
        if custom_icon and record.icon_path:
            override["icon_path"] = record.icon_path
            override["custom_icon"] = True
        self.overrides[record.id] = override
        self.store.save_overrides(self.overrides)
        self.store.save_apps(self.apps)
        self.refresh_tiles()

    def remove(self, record: AppRecord) -> None:
        record.removed = True
        record.removed_by_user = True
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
            _LOG.exception("Could not launch %s", record.executable)
            QMessageBox.critical(self, "Launch failed", str(error))

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.store, self)
        if dialog.exec() == QDialog.Accepted:
            self.watcher.reload_settings()
