from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .paths import resources_dir

try:
    import winreg
except ImportError:
    winreg = None


@dataclass(frozen=True, slots=True)
class DiscoveredExecutable:
    executable: str
    title: str
    launcher: str
    install_root: str


@dataclass(frozen=True, slots=True)
class _Install:
    root: Path
    title: str
    launcher: str
    primary_executable: Path | None = None


_VDF_PAIR = re.compile(r'"([^"]+)"\s*"([^"]*)"')


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _safe_resolve(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser().absolute()


def _registry_value(root, subkey: str, names: Iterable[str]) -> str | None:
    if winreg is None:
        return None
    views = (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
    for view in views:
        try:
            with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | view) as key:
                for name in names:
                    try:
                        value, _ = winreg.QueryValueEx(key, name)
                    except OSError:
                        continue
                    if isinstance(value, str) and value.strip():
                        return os.path.expandvars(value.strip().strip('"'))
        except OSError:
            continue
    return None


def _registry_subkeys(root, subkey: str) -> list[str]:
    if winreg is None:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for view in (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0)):
        try:
            with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | view) as key:
                index = 0
                while True:
                    try:
                        name = winreg.EnumKey(key, index)
                    except OSError:
                        break
                    index += 1
                    if name.casefold() not in seen:
                        seen.add(name.casefold())
                        found.append(name)
        except OSError:
            continue
    return found


def _steam() -> list[_Install]:
    roots: list[Path] = []
    if winreg is not None:
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            value = _registry_value(hive, r"SOFTWARE\Valve\Steam", ("SteamPath", "InstallPath"))
            if value:
                roots.append(Path(value))
    for env in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES")):
        if env:
            roots.append(Path(env) / "Steam")

    installs: list[_Install] = []
    for steam_root in {_safe_resolve(path) for path in roots if path.is_dir()}:
        libraries = [steam_root]
        try:
            text = (steam_root / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
            libraries.extend(Path(path.replace("\\\\", "\\")) for path in re.findall(r'"path"\s*"([^"]+)"', text, re.I))
        except OSError:
            pass
        for library in {_safe_resolve(path) for path in libraries if path.is_dir()}:
            steamapps = library / "steamapps"
            try:
                manifests = list(steamapps.glob("appmanifest_*.acf"))
            except OSError:
                manifests = []
            for manifest in manifests:
                try:
                    pairs = {k.casefold(): v for k, v in _VDF_PAIR.findall(manifest.read_text(encoding="utf-8", errors="replace"))}
                except OSError:
                    continue
                folder = pairs.get("installdir")
                if not folder:
                    continue
                root = steamapps / "common" / folder
                if root.is_dir():
                    installs.append(_Install(root, pairs.get("name") or folder, "Steam"))
    return installs


def _epic() -> list[_Install]:
    manifest_dir = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    installs: list[_Install] = []
    try:
        files = list(manifest_dir.glob("*.item"))
    except OSError:
        return installs
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        location = data.get("InstallLocation")
        if not isinstance(location, str):
            continue
        root = Path(os.path.expandvars(location))
        if not root.is_dir():
            continue
        title = data.get("DisplayName") if isinstance(data.get("DisplayName"), str) else root.name
        launch = data.get("LaunchExecutable")
        installs.append(_Install(root, title or root.name, "Epic", root / launch if isinstance(launch, str) and launch else None))
    return installs


def _gog() -> list[_Install]:
    if winreg is None:
        return []
    installs: list[_Install] = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base in (r"SOFTWARE\GOG.com\Games", r"SOFTWARE\WOW6432Node\GOG.com\Games"):
            for game_id in _registry_subkeys(hive, base):
                key = f"{base}\\{game_id}"
                location = _registry_value(hive, key, ("path", "Path", "install_path", "InstallPath"))
                if not location:
                    continue
                root = Path(location)
                if root.is_dir():
                    title = _registry_value(hive, key, ("gameName", "GameName", "name", "Name")) or root.name
                    installs.append(_Install(root, title, "GOG"))
    return installs


def _registry_launcher(launcher: str, bases: tuple[str, ...], path_names: tuple[str, ...]) -> list[_Install]:
    if winreg is None:
        return []
    installs: list[_Install] = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base in bases:
            for child in _registry_subkeys(hive, base):
                key = f"{base}\\{child}"
                location = _registry_value(hive, key, path_names)
                if not location:
                    continue
                candidate = Path(location)
                root = candidate.parent if candidate.suffix.casefold() == ".exe" else candidate
                if not root.is_dir():
                    continue
                title = _registry_value(hive, key, ("DisplayName", "GameName", "Name", "Title")) or child or root.name
                primary = candidate if candidate.suffix.casefold() == ".exe" else None
                installs.append(_Install(root, title, launcher, primary))
    return installs


def _ubisoft() -> list[_Install]:
    if winreg is None:
        return []
    installs: list[_Install] = []
    bases = (r"SOFTWARE\Ubisoft\Launcher\Installs", r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs")
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base in bases:
            for game_id in _registry_subkeys(hive, base):
                key = f"{base}\\{game_id}"
                location = _registry_value(hive, key, ("InstallDir", "InstallLocation", "Path", ""))
                if location and Path(location).is_dir():
                    root = Path(location)
                    installs.append(_Install(root, root.name or game_id, "Ubisoft Connect"))
    return installs


def _battlenet() -> list[_Install]:
    if winreg is None:
        return []
    base = r"SOFTWARE\Blizzard Entertainment\Battle.net\Launch Options"
    installs: list[_Install] = []
    for product in _registry_subkeys(winreg.HKEY_CURRENT_USER, base):
        key = f"{base}\\{product}"
        location = _registry_value(winreg.HKEY_CURRENT_USER, key, ("Path", "Executable", "InstallPath"))
        if not location:
            continue
        candidate = Path(location)
        root = candidate.parent if candidate.suffix.casefold() == ".exe" else candidate
        if root.is_dir():
            installs.append(_Install(root, product, "Battle.net", candidate if candidate.suffix.casefold() == ".exe" else None))
    return installs


def _drive_roots() -> list[Path]:
    if os.name != "nt":
        return []
    try:
        import ctypes

        length = ctypes.windll.kernel32.GetLogicalDriveStringsW(0, None)
        buffer = ctypes.create_unicode_buffer(length)
        ctypes.windll.kernel32.GetLogicalDriveStringsW(length, buffer)
        return [Path(value) for value in buffer[:].split("\0") if value]
    except Exception:
        return [Path(f"{letter}:\\") for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ" if Path(f"{letter}:\\").exists()]


def _gaming_root(marker: Path) -> Path | None:
    try:
        raw = marker.read_bytes()
    except OSError:
        return None
    for payload in (raw[4:], raw):
        for encoding in ("utf-16-le", "utf-8"):
            text = payload.decode(encoding, errors="ignore").replace("\x00", "").strip().lstrip("\\/")
            if text:
                candidate = marker.parent / text
                if candidate.is_dir():
                    return candidate
    return None


def _xbox() -> list[_Install]:
    libraries: list[Path] = []
    for drive in _drive_roots():
        libraries.append(drive / "XboxGames")
        root = _gaming_root(drive / ".GamingRoot")
        if root:
            libraries.append(root)
    installs: list[_Install] = []
    for library in {_safe_resolve(path) for path in libraries if path.is_dir()}:
        try:
            games = [path for path in library.iterdir() if path.is_dir()]
        except OSError:
            continue
        for game in games:
            root = game / "Content" if (game / "Content").is_dir() else game
            title = game.name
            primary = None
            config = root / "MicrosoftGame.config"
            if config.is_file():
                try:
                    tree = ET.parse(config)
                    shell = tree.find(".//ShellVisuals")
                    if shell is not None:
                        display = shell.attrib.get("DisplayName", "")
                        if display and not display.casefold().startswith("ms-resource:"):
                            title = display
                    exe_node = tree.find(".//Executable")
                    if exe_node is not None:
                        name = exe_node.attrib.get("Name") or exe_node.attrib.get("Executable")
                        if name:
                            primary = root / name
                except (OSError, ET.ParseError):
                    pass
            installs.append(_Install(root, title, "Xbox", primary))
    return installs


def _mappings() -> list[dict[str, str]]:
    try:
        value = json.loads((resources_dir() / "game_mappings.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _mapped_title(executable: Path, default: str, launcher: str, mappings: list[dict[str, str]]) -> str:
    exe = executable.name.casefold()
    parent = executable.parent.name.casefold()
    ancestors = {part.casefold() for part in executable.parent.parts}
    for rule in mappings:
        if rule.get("exe") and str(rule["exe"]).casefold() != exe:
            continue
        if rule.get("launcher") and str(rule["launcher"]).casefold() != launcher.casefold():
            continue
        if rule.get("parent") and str(rule["parent"]).casefold() != parent:
            continue
        if rule.get("folder") and str(rule["folder"]).casefold() not in ancestors:
            continue
        title = rule.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return default.strip() or executable.stem


def _exe_files(root: Path):
    try:
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if not name.startswith(".")]
            for file in files:
                if file.casefold().endswith(".exe"):
                    yield Path(current) / file
    except (OSError, PermissionError):
        return


def discover_games(progress: Callable[[str], None] | None = None) -> list[DiscoveredExecutable]:
    if os.name != "nt":
        return []
    providers = (
        ("Steam", _steam),
        ("Epic", _epic),
        ("GOG", _gog),
        ("Xbox", _xbox),
        ("EA", lambda: _registry_launcher("EA", (r"SOFTWARE\EA Games", r"SOFTWARE\WOW6432Node\EA Games", r"SOFTWARE\Electronic Arts\EA Games", r"SOFTWARE\WOW6432Node\Electronic Arts\EA Games"), ("Install Dir", "InstallDir", "InstallLocation", "Path"))),
        ("Ubisoft Connect", _ubisoft),
        ("Battle.net", _battlenet),
        ("Rockstar", lambda: _registry_launcher("Rockstar", (r"SOFTWARE\Rockstar Games", r"SOFTWARE\WOW6432Node\Rockstar Games"), ("InstallFolder", "InstallLocation", "Path"))),
    )

    installs: list[_Install] = []
    for name, provider in providers:
        if progress:
            progress(f"Scanning {name}...")
        try:
            installs.extend(provider())
        except (OSError, PermissionError, ValueError):
            continue

    mappings = _mappings()
    result: dict[str, DiscoveredExecutable] = {}
    seen_installs: set[tuple[str, str]] = set()
    for install in installs:
        root = _safe_resolve(install.root)
        install_key = (_norm(root), install.launcher.casefold())
        if install_key in seen_installs:
            continue
        seen_installs.add(install_key)
        if progress:
            progress(f"Indexing {install.title}...")
        executables = list(_exe_files(root))
        if install.primary_executable and install.primary_executable.is_file() and _norm(install.primary_executable) not in {_norm(path) for path in executables}:
            executables.insert(0, install.primary_executable)
        for executable in executables:
            resolved = _safe_resolve(executable)
            key = _norm(resolved)
            title = _mapped_title(resolved, install.title, install.launcher, mappings)
            result[key] = DiscoveredExecutable(str(resolved), title, install.launcher, str(root))
    return sorted(result.values(), key=lambda item: (item.title.casefold(), item.executable.casefold()))
