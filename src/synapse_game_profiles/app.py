from __future__ import annotations

import os
import platform
import signal
import sys

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QTimer, qVersion
from PySide6.QtWidgets import QApplication

from .debug_log import configure_logging
from .ui import MainWindow


def main() -> None:
    logger = configure_logging()
    logger.info("Starting Synapse Game Profiles")
    logger.info("Python %s", sys.version.replace("\n", " "))
    logger.info("Platform %s", platform.platform())
    logger.info("Executable %s", sys.executable)
    logger.info("PySide6 %s / Qt %s", pyside_version, qVersion())

    if os.name != "nt":
        logger.error("Unsupported operating system: %s", os.name)
        raise SystemExit("Synapse Game Profiles currently supports Windows only.")

    try:
        app = QApplication(sys.argv)
        app.setApplicationName("Synapse Game Profiles")
        app.setQuitOnLastWindowClosed(False)

        previous_sigint = signal.getsignal(signal.SIGINT)

        def exit_from_console(signum, frame) -> None:
            logger.info("Received Ctrl-C/SIGINT; exiting")
            app.quit()

        signal.signal(signal.SIGINT, exit_from_console)

        # Qt can otherwise sit in its native event loop long enough that Python
        # does not get a chance to dispatch SIGINT promptly on Windows.
        signal_timer = QTimer()
        signal_timer.timeout.connect(lambda: None)
        signal_timer.start(200)

        window = MainWindow()
        window.show()
        exit_code = app.exec()
        signal.signal(signal.SIGINT, previous_sigint)
        logger.info("Qt event loop exited with code %s", exit_code)
        raise SystemExit(exit_code)
    except SystemExit:
        raise
    except BaseException:
        logger.exception("Fatal application startup/runtime error")
        raise
