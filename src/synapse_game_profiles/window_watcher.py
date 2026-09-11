from __future__ import annotations

import os
from dataclasses import dataclass

import psutil
from PySide6.QtCore import QObject, QTimer, Signal

from .models import AppRecord
from .profile_backend import DummyProfileBackend
from .storage import Store


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def _foreground_pid() -> int | None:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) or None
else:

    def _foreground_pid() -> int | None:
        return None


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


@dataclass(slots=True)
class Match:
    app: AppRecord
    pid: int
    exact: bool


class ForegroundWatcher(QObject):
    changed = Signal(object)

    def __init__(self, store: Store, backend: DummyProfileBackend, apps: dict[str, AppRecord]) -> None:
        super().__init__()
        self.store = store
        self.backend = backend
        self.apps = apps
        self.settings = store.load_settings()
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)
        self.last_match_id: str | None = None
        self.last_switched_uuid: str | None = None

    def start(self) -> None:
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def reload_settings(self) -> None:
        self.settings = self.store.load_settings()

    def _match(self, pid: int) -> Match | None:
        configured = [
            item
            for item in self.apps.values()
            if not item.removed and item.profile_uuid and os.path.isfile(item.executable)
        ]
        by_path = {_norm(item.executable): item for item in configured}
        try:
            process = psutil.Process(pid)
            executable = process.exe()
        except (psutil.Error, OSError):
            return None
        direct = by_path.get(_norm(executable))
        if direct:
            return Match(direct, pid, True)
        if not self.settings.get("match_subprocesses", True):
            return None
        try:
            parent = process.parent()
            while parent is not None:
                try:
                    item = by_path.get(_norm(parent.exe()))
                except (psutil.Error, OSError):
                    item = None
                if item:
                    return Match(item, pid, False)
                parent = parent.parent()
        except psutil.Error:
            return None
        return None

    def _switch(self, profile_uuid: str | None) -> None:
        if not profile_uuid or profile_uuid == self.last_switched_uuid:
            return
        if self.backend.switch_profile(profile_uuid):
            self.last_switched_uuid = profile_uuid

    def _capture_unassociated_for(self, profile_uuid: str | None) -> None:
        active = self.backend.get_active_profile_for(profile_uuid)
        if not active:
            return
        if self.settings.get("last_unassociated_profile_uuid") == active:
            return
        self.settings["last_unassociated_profile_uuid"] = active
        self.store.save_settings(self.settings)

    def _tick(self) -> None:
        pid = _foreground_pid()
        match = self._match(pid) if pid else None
        if match:
            # Only capture when entering mapped-app territory from an unassociated
            # window. Switching directly between mapped games keeps the original
            # desktop/non-game profile as the eventual restore target.
            if self.last_match_id is None:
                self._capture_unassociated_for(match.app.profile_uuid)
            self.last_match_id = match.app.id
            self._switch(match.app.profile_uuid)
            self.changed.emit(match)
            return

        target = self.settings.get("default_profile_uuid") or self.settings.get("last_unassociated_profile_uuid")
        if self.last_match_id is not None:
            self._switch(target)
        self.last_match_id = None
        self.changed.emit(None)
