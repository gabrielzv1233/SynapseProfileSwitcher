from __future__ import annotations

import importlib
import importlib.metadata
import os
import re
import subprocess
import sys

from PySide6.QtWidgets import QMessageBox, QWidget

from .debug_log import get_logger


_LOG = get_logger("synapse_preflight")
_REQUIRED_VERSION = (0, 3, 1)
_REQUIRED_SPEC = "SynapseCTRL>=0.3.1"


def _version_tuple(value: str | None) -> tuple[int, int, int]:
    if not value:
        return (0, 0, 0)
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value.strip())
    if match is None:
        return (0, 0, 0)
    return tuple(int(part) for part in match.groups())


def _installed_version() -> str | None:
    try:
        return importlib.metadata.version("SynapseCTRL")
    except importlib.metadata.PackageNotFoundError:
        try:
            module = importlib.import_module("synapsectrl")
        except ImportError:
            return None
        value = getattr(module, "__version__", None)
        return str(value) if value else None


def _clear_synapsectrl_modules() -> None:
    for name in tuple(sys.modules):
        if name == "synapsectrl" or name.startswith("synapsectrl."):
            sys.modules.pop(name, None)
    importlib.invalidate_caches()


def _details_from_bootstrap(bootstrap: dict) -> str:
    lines: list[str] = []
    for issue in bootstrap.get("issues") or []:
        if isinstance(issue, dict):
            code = issue.get("code")
            message = issue.get("message")
            if code and message:
                lines.append(f"{code}: {message}")
            elif message:
                lines.append(str(message))
        else:
            lines.append(str(issue))
    repairs = bootstrap.get("repair") or []
    if repairs:
        if lines:
            lines.append("")
        lines.append("Suggested repair:")
        lines.extend(f"- {step}" for step in repairs)
    return "\n".join(lines)


def _ask(parent: QWidget | None, title: str, text: str, details: str, action_text: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    if details:
        box.setDetailedText(details)
    action = box.addButton(action_text, QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Exit", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is action


def _install_or_upgrade_package(parent: QWidget | None, current_version: str | None) -> bool:
    state = "not installed" if current_version is None else f"version {current_version} is too old"
    if not _ask(
        parent,
        "SynapseCTRL Required",
        f"SynapseCTRL is {state}. SynapseProfileSwitcher requires SynapseCTRL 0.3.1 or newer.",
        "The app can install or update SynapseCTRL in the current Python environment now.",
        "Install / Update",
    ):
        return False

    if getattr(sys, "frozen", False):
        QMessageBox.critical(
            parent,
            "SynapseCTRL Missing",
            "This packaged build does not contain the required SynapseCTRL dependency. Reinstall or update SynapseProfileSwitcher.",
        )
        return False

    command = [sys.executable, "-m", "pip", "install", "--upgrade", _REQUIRED_SPEC]
    _LOG.info("Installing/updating SynapseCTRL with %s", command)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
    except OSError as error:
        _LOG.exception("Could not start SynapseCTRL package installation")
        QMessageBox.critical(parent, "SynapseCTRL Install Failed", str(error))
        return False

    if completed.returncode != 0:
        _LOG.error(
            "SynapseCTRL install failed exit=%s stdout=%s stderr=%s",
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )
        output = (completed.stderr or completed.stdout or "No installer output was returned.").strip()
        QMessageBox.critical(
            parent,
            "SynapseCTRL Install Failed",
            f"pip exited with code {completed.returncode}.\n\n{output[-3000:]}",
        )
        return False

    _clear_synapsectrl_modules()
    return True


def ensure_synapsectrl_ready(parent: QWidget | None = None) -> bool:
    """Require a usable SynapseCTRL package and healthy automatic launch hook."""
    version = _installed_version()
    if version is None or _version_tuple(version) < _REQUIRED_VERSION:
        if not _install_or_upgrade_package(parent, version):
            return False
        version = _installed_version()

    if version is None or _version_tuple(version) < _REQUIRED_VERSION:
        QMessageBox.critical(
            parent,
            "SynapseCTRL Unavailable",
            "SynapseCTRL 0.3.1 or newer is still not available after installation.",
        )
        return False

    _LOG.info("SynapseCTRL package ready: %s", version)

    try:
        bootstrap_module = importlib.import_module("synapsectrl.bootstrap")
        hook_module = importlib.import_module("synapsectrl.hook")
        inspect_bootstrap = bootstrap_module.inspect_bootstrap
        run_hook_installer = hook_module.run_hook_installer
    except (ImportError, AttributeError) as error:
        _LOG.exception("SynapseCTRL package is missing required hook APIs")
        QMessageBox.critical(
            parent,
            "SynapseCTRL Unavailable",
            f"SynapseCTRL {version} is installed, but the required hook APIs could not be loaded.\n\n{error}",
        )
        return False

    try:
        bootstrap = inspect_bootstrap()
    except Exception as error:
        _LOG.exception("Could not inspect SynapseCTRL hook state")
        QMessageBox.critical(parent, "SynapseCTRL Check Failed", str(error))
        return False

    if bootstrap.get("hookHealthy"):
        _LOG.info("SynapseCTRL launch hook is healthy")
        return True

    issue_codes = {
        issue.get("code")
        for issue in bootstrap.get("issues") or []
        if isinstance(issue, dict)
    }
    details = _details_from_bootstrap(bootstrap)

    if "synapse_not_installed" in issue_codes:
        _LOG.error("Razer Synapse installation was not detected by SynapseCTRL")
        QMessageBox.critical(
            parent,
            "Razer Synapse Required",
            "SynapseCTRL could not find a usable Razer Synapse 4 installation. Install or repair Synapse, then start SynapseProfileSwitcher again."
            + (f"\n\n{details}" if details else ""),
        )
        return False

    action = "repair" if bootstrap.get("hookInstalled") else "install"
    action_label = "Repair Hook" if action == "repair" else "Install Hook"
    state = "installed but unhealthy" if bootstrap.get("hookInstalled") else "not installed"

    if not _ask(
        parent,
        "SynapseCTRL Hook Required",
        f"The SynapseCTRL automatic launch hook is {state}. It is required for profile control.",
        details,
        action_label,
    ):
        return False

    _LOG.info("Running SynapseCTRL hook %s", action)
    try:
        result = run_hook_installer(action)
        bootstrap = result.get("bootstrap") or inspect_bootstrap()
    except Exception as error:
        code = getattr(error, "code", type(error).__name__)
        _LOG.exception("SynapseCTRL hook %s failed", action)
        QMessageBox.critical(
            parent,
            "SynapseCTRL Hook Failed",
            f"Hook {action} failed [{code}]: {error}",
        )
        return False

    if not bootstrap.get("hookHealthy"):
        details = _details_from_bootstrap(bootstrap)
        _LOG.error("SynapseCTRL hook is still unhealthy after %s: %s", action, details)
        QMessageBox.critical(
            parent,
            "SynapseCTRL Hook Still Unhealthy",
            "The hook operation completed, but SynapseCTRL still reports an unhealthy launch hook."
            + (f"\n\n{details}" if details else ""),
        )
        return False

    _LOG.info("SynapseCTRL launch hook is healthy after %s", action)
    if bootstrap.get("synapseRunning"):
        QMessageBox.information(
            parent,
            "Restart Razer Synapse",
            "The SynapseCTRL hook is installed and healthy. Fully exit and reopen Razer Synapse so the currently running instance starts with the hook enabled.",
        )

    return True
