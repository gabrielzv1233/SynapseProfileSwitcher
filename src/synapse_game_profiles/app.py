from __future__ import annotations

import os
import sys

from PySide6.QtWidgets import QApplication

from .ui import MainWindow


def main() -> None:
    if os.name != "nt":
        raise SystemExit("Synapse Game Profiles currently supports Windows only.")
    app = QApplication(sys.argv)
    app.setApplicationName("Synapse Game Profiles")
    app.setQuitOnLastWindowClosed(False)
    window = MainWindow()
    window.show()
    raise SystemExit(app.exec())
