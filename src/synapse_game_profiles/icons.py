from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from PIL import Image
from PySide6.QtGui import QIcon

from .paths import data_dir


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".ico"}


def _destination(source: Path, extension: str = ".png") -> Path:
    key = hashlib.sha256(f"{source.resolve()}|{source.stat().st_mtime_ns}".encode("utf-8", errors="replace")).hexdigest()[:24]
    return data_dir() / "cached_icons" / f"{key}{extension}"


def cache_image(source: str | Path) -> str | None:
    source = Path(source)
    if not source.is_file():
        return None
    extension = source.suffix.casefold()
    if extension not in _IMAGE_EXTENSIONS:
        return None
    destination = _destination(source, extension if extension != ".gif" else ".png")
    if destination.exists():
        return str(destination)
    try:
        if extension == ".gif":
            with Image.open(source) as image:
                image.seek(0)
                image.convert("RGBA").save(destination)
        else:
            shutil.copy2(source, destination)
        return str(destination)
    except (OSError, ValueError):
        return None


def cache_executable_icon(executable: str | Path) -> str | None:
    executable = Path(executable)
    if os.name != "nt" or not executable.is_file():
        return None
    destination = _destination(executable, ".png")
    if destination.exists():
        return str(destination)

    try:
        icon = QIcon(str(executable))
        pixmap = icon.pixmap(512, 512)
        if not pixmap.isNull() and pixmap.save(str(destination), "PNG"):
            return str(destination)
    except Exception:
        pass
    return None


def cache_artwork(path: str | Path) -> str | None:
    source = Path(path)
    if source.suffix.casefold() == ".exe":
        return cache_executable_icon(source)
    return cache_image(source)
