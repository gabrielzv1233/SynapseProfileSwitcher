from __future__ import annotations

import json
from pathlib import Path

from .models import AppRecord
from .paths import data_dir


class Store:
    def __init__(self) -> None:
        self.root = data_dir()
        self.apps_path = self.root / "apps.json"
        self.settings_path = self.root / "settings.json"
        self.overrides_path = self.root / "user_overrides.json"

    @staticmethod
    def _read(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            return default

    @staticmethod
    def _write(path: Path, value) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)

    def load_apps(self) -> dict[str, AppRecord]:
        raw = self._read(self.apps_path, [])
        result: dict[str, AppRecord] = {}
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    record = AppRecord.from_dict(item)
                except TypeError:
                    continue
                result[record.id] = record
        return result

    def save_apps(self, apps: dict[str, AppRecord]) -> None:
        self._write(self.apps_path, [item.to_dict() for item in apps.values()])

    def load_settings(self) -> dict:
        value = self._read(self.settings_path, {})
        defaults = {
            "match_subprocesses": True,
            "default_profile_uuid": None,
            "last_unassociated_profile_uuid": None,
            "scan_on_startup": True,
        }
        if isinstance(value, dict):
            defaults.update(value)
        return defaults

    def save_settings(self, value: dict) -> None:
        self._write(self.settings_path, value)

    def load_overrides(self) -> dict:
        value = self._read(self.overrides_path, {})
        return value if isinstance(value, dict) else {}

    def save_overrides(self, value: dict) -> None:
        self._write(self.overrides_path, value)
