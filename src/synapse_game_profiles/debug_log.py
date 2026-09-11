from __future__ import annotations

import faulthandler
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import qInstallMessageHandler

from .paths import data_dir


_LOGGER_NAME = "synapse_game_profiles"
_FAULT_FILE = None


def log_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_logging() -> logging.Logger:
    global _FAULT_FILE

    root = log_dir()
    latest = root / "latest.log"
    timestamped = root / f"debug-{datetime.now():%Y-%m-%d_%H-%M-%S}.log"

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)s] [%(threadName)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    for path in (latest, timestamped):
        handler = logging.FileHandler(path, mode="w", encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    _FAULT_FILE = (root / "fatal.log").open("a", encoding="utf-8")
    try:
        faulthandler.enable(file=_FAULT_FILE, all_threads=True)
    except (RuntimeError, OSError):
        logger.exception("Could not enable faulthandler")

    def unhandled(exc_type, exc_value, exc_traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))

    def thread_unhandled(args: threading.ExceptHookArgs) -> None:
        logger.critical(
            "Unhandled thread exception in %s",
            args.thread.name if args.thread else "unknown",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = unhandled
    threading.excepthook = thread_unhandled

    def qt_message(mode, context, message) -> None:
        file_name = getattr(context, "file", None) or "?"
        line = getattr(context, "line", 0)
        logger.debug("Qt[%s] %s:%s %s", int(mode), file_name, line, message)

    qInstallMessageHandler(qt_message)
    logger.info("Logging initialized: %s", latest)
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")
