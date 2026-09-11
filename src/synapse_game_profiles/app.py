from __future__ import annotations

import os
import platform
import signal
import sys

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QTimer, qVersion
from PySide6.QtWidgets import QApplication

from .debug_log import configure_logging
from .synapse_preflight import ensure_synapsectrl_ready


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

    window = None
    previous_sigint = signal.getsignal(signal.SIGINT)

    try:
        app = QApplication(sys.argv)
        app.setApplicationName("Synapse Game Profiles")
        app.setQuitOnLastWindowClosed(False)

        def exit_from_console(signum, frame) -> None:
            logger.info("Received Ctrl-C/SIGINT; exiting")
            app.quit()

        signal.signal(signal.SIGINT, exit_from_console)

        # Qt can otherwise sit in its native event loop long enough that Python
        # does not get a chance to dispatch SIGINT promptly on Windows.
        signal_timer = QTimer()
        signal_timer.timeout.connect(lambda: None)
        signal_timer.start(200)

        # Do this before importing the UI/profile backend. If SynapseCTRL needs
        # to be installed or upgraded, the backend must not have already cached
        # an ImportError from the old environment state.
        if not ensure_synapsectrl_ready():
            logger.warning("SynapseCTRL startup prerequisites were not satisfied")
            raise SystemExit(1)

        from .ui import MainWindow

        window = MainWindow()
        window.show()
        exit_code = app.exec()
        logger.info("Qt event loop exited with code %s", exit_code)
        raise SystemExit(exit_code)
    except SystemExit:
        raise
    except BaseException:
        logger.exception("Fatal application startup/runtime error")
        raise
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        if window is not None:
            try:
                window.watcher.stop()
                window.backend.close()
                logger.info("SynapseCTRL backend closed")
            except Exception:
                logger.exception("Failed while closing SynapseCTRL backend")
