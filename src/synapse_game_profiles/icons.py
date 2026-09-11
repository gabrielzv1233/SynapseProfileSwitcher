from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QFileInfo
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFileIconProvider

from .debug_log import get_logger
from .paths import data_dir


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".ico"}
_LOG = get_logger("icons")


def _destination(source: Path, extension: str = ".png", namespace: str = "image") -> Path:
    key = hashlib.sha256(
        f"{namespace}|{source.resolve()}|{source.stat().st_mtime_ns}".encode("utf-8", errors="replace")
    ).hexdigest()[:24]
    return data_dir() / "cached_icons" / f"{key}{extension}"


def cache_image(source: str | Path) -> str | None:
    source = Path(source)
    if not source.is_file():
        return None
    extension = source.suffix.casefold()
    if extension not in _IMAGE_EXTENSIONS:
        return None
    destination = _destination(source, extension if extension != ".gif" else ".png", "custom-image-v1")
    if destination.exists():
        return str(destination)
    try:
        if extension == ".gif":
            with Image.open(source) as image:
                image.seek(0)
                image.convert("RGBA").save(destination)
        else:
            shutil.copy2(source, destination)
        _LOG.debug("Cached image %s -> %s", source, destination)
        return str(destination)
    except (OSError, ValueError):
        _LOG.exception("Failed to cache image %s", source)
        return None


def cache_executable_icon(executable: str | Path, *, force: bool = False) -> str | None:
    executable = Path(executable)
    if os.name != "nt" or not executable.is_file():
        return None

    destination = _destination(executable, ".png", "windows-shell-icon-v2")
    if destination.exists() and not force:
        return str(destination)

    try:
        icon = QFileIconProvider().icon(QFileInfo(str(executable)))
        if icon.isNull():
            icon = QIcon(str(executable))
        pixmap = icon.pixmap(512, 512)
        if pixmap.isNull():
            _LOG.warning("Windows did not return an icon for %s", executable)
            return None
        if pixmap.save(str(destination), "PNG"):
            _LOG.debug("Cached executable icon %s -> %s", executable, destination)
            return str(destination)
        _LOG.warning("Could not save executable icon for %s", executable)
    except Exception:
        _LOG.exception("Failed to extract executable icon from %s", executable)
    return None


def cache_artwork(path: str | Path) -> str | None:
    source = Path(path)
    if source.suffix.casefold() == ".exe":
        return cache_executable_icon(source, force=True)
    return cache_image(source)
