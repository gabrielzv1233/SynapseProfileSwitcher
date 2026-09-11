from __future__ import annotations

import json
import math
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .debug_log import get_logger
from .paths import resources_dir

try:
    import winreg
except ImportError:
    winreg = None


_LOG = get_logger("discovery")
_VDF_PAIR = re.compile(r'"([^"]+)"\s*"([^"]*)"')
_WORD = re.compile(r"[a-z0-9]+")


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


_NON_GAME_TITLES = (
    "steamworks common redistributables",
    "steamvr",
    "dedicated server",
    "server tool",
    "unity editor",
    "unity hub",
    "unreal engine",
    "unreal editor",
    "source sdk",
    "sdk base",
    "software development kit",
    "redistributable",
    "rockstar games launcher",
    "social club",
)

_BAD_EXE_FRAGMENTS = (
    "crashreport",
    "crashhandler",
    "crashpad",
    "crashsender",
    "uninstall",
    "unins000",
    "installer",
    "installhelper",
    "setup",
    "redistributable",
    "redist",
    "prereq",
    "launcher",
    "bootstrapper",
    "patcher",
    "updater",
    "updatehelper",
    "webhelper",
    "webview",
    "cefsubprocess",
    "helper",
    "diagnostic",
    "benchmark",
    "easyanticheat",
    "eosoverlayrenderer",
    "battleye",
    "beservice",
    "protected_game",
    "anticheat",
    "unitycrash",
    "unicrash",
    "unrealeditor",
    "ue4editor",
    "ue5editor",
    "shadercompileworker",
    "swarmagent",
    "steamerrorreporter",
    "nettest",
    "neacclient",
)

_BAD_PATH_PARTS = {
    "_commonredist",
    "redistributables",
    "redist",
    "prerequisites",
    "prereqs",
    "easyanticheat",
    "battleye",
    "webviewsupport",
    "crashpad",
    "installer",
    "installers",
    "thirdparty",
}

_GENERIC_WORDS = {
    "the",
    "game",
    "games",
    "edition",
    "ultimate",
    "deluxe",
    "complete",
    "remastered",
    "definitive",
    "client",
    "win32",
    "win64",
    "x64",
    "x86",
    "shipping",
    "release",
    "retail",
}


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
                    folded = name.casefold()
                    if folded not in seen:
                        seen.add(folded)
                        found.append(name)
        except OSError:
            continue
    return found


def _command_executable(root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    match = re.search(r'(?i)(?:"([^"]+\.exe)"|([^"\r\n]+?\.exe))', value)
    if not match:
        return None
    raw = (match.group(1) or match.group(2) or "").strip()
    if not raw:
        return None
    candidate = Path(os.path.expandvars(raw))
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate


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
        primary = root / launch if isinstance(launch, str) and launch else None
        installs.append(_Install(root, title or root.name, "Epic", primary))
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
                if not root.is_dir():
                    continue
                title = _registry_value(hive, key, ("gameName", "GameName", "name", "Name")) or root.name
                command = _registry_value(hive, key, ("exe", "Exe", "launchCommand", "LaunchCommand", "command", "Command"))
                installs.append(_Install(root, title, "GOG", _command_executable(root, command)))
    return installs


def _registry_launcher(
    launcher: str,
    bases: tuple[str, ...],
    path_names: tuple[str, ...],
    executable_names: tuple[str, ...] = ("Executable", "Exe", "GameExe", "LaunchExecutable"),
) -> list[_Install]:
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
                if primary is None:
                    primary = _command_executable(root, _registry_value(hive, key, executable_names))
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
                if not location:
                    continue
                root = Path(location)
                if not root.is_dir():
                    continue
                title = _registry_value(hive, key, ("DisplayName", "GameName", "Name", "Title")) or root.name or game_id
                command = _registry_value(hive, key, ("Executable", "Exe", "GameExe", "LaunchExecutable"))
                installs.append(_Install(root, title, "Ubisoft Connect", _command_executable(root, command)))
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


def _mapping_matches(rule: dict[str, str], executable: Path, launcher: str) -> bool:
    exe = executable.name.casefold()
    parent = executable.parent.name.casefold()
    ancestors = {part.casefold() for part in executable.parent.parts}
    if rule.get("exe") and str(rule["exe"]).casefold() != exe:
        return False
    if rule.get("launcher") and str(rule["launcher"]).casefold() != launcher.casefold():
        return False
    if rule.get("parent") and str(rule["parent"]).casefold() != parent:
        return False
    if rule.get("folder") and str(rule["folder"]).casefold() not in ancestors:
        return False
    return bool(rule.get("exe") or rule.get("launcher") or rule.get("parent") or rule.get("folder"))


def _mapped_title(executable: Path, default: str, launcher: str, mappings: list[dict[str, str]]) -> str:
    for rule in mappings:
        if not _mapping_matches(rule, executable, launcher):
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


def _words(value: str) -> set[str]:
    words = set(_WORD.findall(value.casefold()))
    return {word for word in words if word not in _GENERIC_WORDS and len(word) > 1}


def _is_non_game_install(install: _Install) -> bool:
    title = install.title.casefold().strip()
    root_text = str(install.root).replace("/", "\\").casefold()

    if title in {"launcher", "social club", "rockstar games launcher"}:
        return True
    if any(fragment in title for fragment in _NON_GAME_TITLES):
        return True
    if "\\unity\\hub\\editor\\" in root_text:
        return True
    if re.search(r"\\ue_[45](?:\.\d+)?(?:\\|$)", root_text):
        return True
    if "\\unrealengine\\" in root_text or "\\unreal engine\\" in root_text:
        return True
    return False


def _is_playable_candidate(executable: Path) -> bool:
    name = executable.name.casefold()
    stem = executable.stem.casefold()
    parts = {part.casefold() for part in executable.parts}
    normalized = str(executable).replace("/", "\\").casefold()

    if any(fragment in name for fragment in _BAD_EXE_FRAGMENTS):
        return False
    if "server" in stem or "editor" in stem or "service" in stem:
        return False
    if name in {"render.exe", "unity.exe", "unityhub.exe", "dxsetup.exe"}:
        return False
    if parts & _BAD_PATH_PARTS:
        return False
    if "\\engine\\binaries\\thirdparty\\" in normalized:
        return False
    if "\\engine\\extras\\" in normalized:
        return False
    if "_eac_" in stem or stem.endswith("_eac") or stem.startswith("eac_"):
        return False
    if "_be_" in stem or stem.endswith("_be"):
        return False
    return True


def _candidate_score(executable: Path, install: _Install, mappings: list[dict[str, str]]) -> float:
    score = 0.0
    resolved = _safe_resolve(executable)
    normalized = str(resolved).replace("/", "\\").casefold()
    stem = resolved.stem.casefold()

    if any(_mapping_matches(rule, resolved, install.launcher) for rule in mappings):
        score += 2500

    if install.primary_executable and _norm(resolved) == _norm(install.primary_executable):
        score += {
            "Xbox": 1800,
            "GOG": 1000,
            "Battle.net": 800,
            "Ubisoft Connect": 700,
            "EA": 650,
            "Rockstar": 600,
            "Epic": 450,
        }.get(install.launcher, 500)

    title_words = _words(install.title)
    root_words = _words(install.root.name)
    exe_words = _words(re.sub(r"(?i)(?:[-_](?:win(?:32|64)|wingdk|x64|x86|shipping|release|retail))+$", "", stem))

    if title_words and exe_words:
        overlap = len(title_words & exe_words)
        if exe_words == title_words:
            score += 600
        elif overlap:
            score += 500 * (overlap / max(len(title_words), len(exe_words)))

    if root_words and exe_words:
        overlap = len(root_words & exe_words)
        if overlap:
            score += 300 * (overlap / max(len(root_words), len(exe_words)))

    if re.search(r"(?i)(?:-|_)(?:win64|wingdk)(?:-|_)?shipping$", stem):
        score += 350
    elif "shipping" in stem:
        score += 250

    if "\\binaries\\win64\\" in normalized or "\\binaries\\wingdk\\" in normalized:
        score += 220
    elif "\\binaries\\" in normalized:
        score += 120

    try:
        relative = resolved.relative_to(_safe_resolve(install.root))
        depth = max(0, len(relative.parts) - 1)
        if depth == 0:
            score += 90
        score -= min(depth * 4, 40)
    except ValueError:
        score -= 100

    try:
        size = resolved.stat().st_size
        if size >= 128 * 1024 * 1024:
            score += 220
        elif size >= 32 * 1024 * 1024:
            score += 160
        elif size >= 8 * 1024 * 1024:
            score += 100
        elif size >= 2 * 1024 * 1024:
            score += 50
        elif size < 256 * 1024:
            score -= 80
        score += min(25, max(0, math.log2(max(size, 1)) - 20))
    except OSError:
        score -= 20

    if "\\engine\\binaries\\" in normalized:
        score -= 120

    return score


def _select_game_executable(install: _Install, mappings: list[dict[str, str]]) -> Path | None:
    if _is_non_game_install(install):
        _LOG.debug("Skipping non-game install %s [%s] at %s", install.title, install.launcher, install.root)
        return None

    root = _safe_resolve(install.root)
    candidates = list(_exe_files(root))
    if install.primary_executable and install.primary_executable.is_file():
        primary_key = _norm(install.primary_executable)
        if all(_norm(candidate) != primary_key for candidate in candidates):
            candidates.insert(0, install.primary_executable)

    scored: list[tuple[float, Path]] = []
    rejected = 0
    for candidate in candidates:
        resolved = _safe_resolve(candidate)
        if not _is_playable_candidate(resolved):
            rejected += 1
            continue
        scored.append((_candidate_score(resolved, install, mappings), resolved))

    if not scored:
        _LOG.warning(
            "No playable executable found for %s [%s] at %s (%d executable(s), %d rejected)",
            install.title,
            install.launcher,
            root,
            len(candidates),
            rejected,
        )
        return None

    scored.sort(key=lambda item: (item[0], item[1].name.casefold()), reverse=True)
    score, selected = scored[0]
    _LOG.info(
        "Selected game executable: %s [%s] -> %s (score %.1f, %d candidate(s), %d rejected)",
        install.title,
        install.launcher,
        selected,
        score,
        len(scored),
        rejected,
    )
    for candidate_score, candidate in scored[:5]:
        _LOG.debug("  candidate %.1f: %s", candidate_score, candidate)
    return selected


def discover_games(progress: Callable[[str], None] | None = None) -> list[DiscoveredExecutable]:
    if os.name != "nt":
        return []

    providers = (
        ("Steam", _steam),
        ("Epic", _epic),
        ("GOG", _gog),
        ("Xbox", _xbox),
        (
            "EA",
            lambda: _registry_launcher(
                "EA",
                (
                    r"SOFTWARE\EA Games",
                    r"SOFTWARE\WOW6432Node\EA Games",
                    r"SOFTWARE\Electronic Arts\EA Games",
                    r"SOFTWARE\WOW6432Node\Electronic Arts\EA Games",
                ),
                ("Install Dir", "InstallDir", "InstallLocation", "Path"),
            ),
        ),
        ("Ubisoft Connect", _ubisoft),
        ("Battle.net", _battlenet),
        (
            "Rockstar",
            lambda: _registry_launcher(
                "Rockstar",
                (r"SOFTWARE\Rockstar Games", r"SOFTWARE\WOW6432Node\Rockstar Games"),
                ("InstallFolder", "InstallLocation", "Path"),
            ),
        ),
    )

    installs: list[_Install] = []
    for name, provider in providers:
        if progress:
            progress(f"Scanning {name}...")
        try:
            found = provider()
            installs.extend(found)
            _LOG.info("%s -> %d install(s)", name, len(found))
            for install in found:
                _LOG.debug(
                    "%s [%s]: root=%s primary=%s",
                    install.title,
                    install.launcher,
                    install.root,
                    install.primary_executable,
                )
        except (OSError, PermissionError, ValueError):
            _LOG.exception("%s discovery failed", name)

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
            progress(f"Resolving {install.title}...")

        selected = _select_game_executable(install, mappings)
        if selected is None:
            continue

        key = _norm(selected)
        title = _mapped_title(selected, install.title, install.launcher, mappings)
        result[key] = DiscoveredExecutable(str(selected), title, install.launcher, str(root))

    discovered = sorted(result.values(), key=lambda item: (item.title.casefold(), item.executable.casefold()))
    _LOG.info("Discovery complete: %d playable game(s)", len(discovered))
    return discovered
