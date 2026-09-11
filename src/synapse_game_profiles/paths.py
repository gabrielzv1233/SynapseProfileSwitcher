from __future__ import annotations

import os
import sys
from pathlib import Path


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "SynapseGameProfiles"
    else:
        base = project_root() / "data"
    base.mkdir(parents=True, exist_ok=True)
    (base / "cached_icons").mkdir(parents=True, exist_ok=True)
    return base


def resources_dir() -> Path:
    return project_root() / "resources"
