from __future__ import annotations

import os
import sys
from pathlib import Path


def is_packaged() -> bool:
    """Return whether the app is running from a frozen/compiled bundle."""
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def project_root() -> Path:
    # Nuitka onefile data is unpacked beside the compiled module's __file__, so
    # keep using the module path there. PyInstaller-style bundles use the EXE.
    if getattr(sys, "frozen", False) and "__compiled__" not in globals():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    if is_packaged():
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "SynapseGameProfiles"
    else:
        base = project_root() / "data"
    base.mkdir(parents=True, exist_ok=True)
    (base / "cached_icons").mkdir(parents=True, exist_ok=True)
    return base


def resources_dir() -> Path:
    return project_root() / "resources"
