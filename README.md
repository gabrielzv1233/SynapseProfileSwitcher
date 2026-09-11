# Synapse Game Profiles

Standalone Windows tray application for discovering launcher-managed games, mapping them to Razer Synapse 4 software profiles, and switching profiles based on the focused application.

Profile discovery and switching are handled by [SynapseCTRL](https://github.com/gabrielzv1233/SynapseCTRL).

## Features

- Steam library discovery, including libraries on arbitrary drives from `libraryfolders.vdf`
- Steam `appinfo.vdf` metadata support for filtering non-game entries and resolving launch executables
- Epic Games Launcher manifest discovery
- GOG registry discovery
- Xbox / Microsoft Gaming `.GamingRoot` and `XboxGames` discovery
- EA, Ubisoft Connect, Battle.net, and Rockstar registry discovery
- Resolves one playable runtime executable per detected game instead of exposing launchers, crash reporters, redistributables, editors, dedicated servers, and other helper executables
- Public executable/folder title mappings in `resources/game_mappings.json`
- User overrides stored separately under the writable `data` directory
- Manual application add
- Cached tile artwork/icons extracted from the selected playable executable
- Responsive square tile grid
- Right-click actions: Modify item, Remove item, Set profile, Launch application
- Modify dialog supports executable, `.ico`, and common image files for tile art
- Live SynapseCTRL profile discovery from connected and controllable Razer devices
- Stores the SynapseCTRL stable device ID together with the software-profile GUID so profile switching cannot target the wrong device
- Foreground-window watcher using exact executable matching first
- Optional subprocess/ancestor process matching
- Configurable default Synapse profile target
- If no explicit default is configured, captures the currently active profile on the same Razer device immediately before entering a mapped game and restores it afterward
- System tray support and background operation
- Debug logging and Ctrl+C support for console-launched sessions

## Requirements

- Windows 10/11
- Python 3.13+
- Razer Synapse 4
- SynapseCTRL 0.3.1+
- SynapseCTRL's inspector hook installed and Synapse restarted afterward

Install/repair the hook with:

```powershell
SynapseCTRL hook install
```

SynapseCTRL's own status/diagnostics commands are useful if profile discovery is unavailable:

```powershell
SynapseCTRL status
SynapseCTRL doctor
```

## Run

Install the project and all runtime dependencies, including SynapseCTRL:

```powershell
py -m pip install -e .
synapse-game-profiles
```

Or:

```powershell
py -m synapse_game_profiles
```

If you pulled an update that added SynapseCTRL after already installing the project editable, rerun:

```powershell
py -m pip install -e .
```

## Nuitka build

Build a single-file Windows executable locally with:

```powershell
.\build_nuitka.ps1
```

The script installs the `build` dependency group, builds `dist\SynapseProfileSwitcher.exe`, includes the launcher/game mapping data and SynapseCTRL hook resources, and prints the resulting SHA256 hash.

The executable uses Nuitka's `attach` console mode: normal GUI launches do not create a console, while launches from an existing PowerShell/Terminal session can still receive Ctrl+C.

A manually triggered GitHub Actions workflow is available at **Actions → Build Nuitka EXE → Run workflow**. It uploads `SynapseProfileSwitcher.exe` and a matching `.sha256` file as a workflow artifact.

## Data location

When running from source, writable state is kept in `./data` next to the project. When packaged, it uses `%LOCALAPPDATA%\\SynapseGameProfiles`.

The main files are:

- `apps.json`: discovered/manual app records and assigned Synapse target IDs
- `settings.json`: watcher and fallback settings
- `user_overrides.json`: user title/icon/executable overrides
- `cached_icons/`: copied or extracted tile artwork
- `logs/`: runtime/debug logs

## SynapseCTRL integration

`profile_backend.py` owns one long-lived `SynapseService` instance. SynapseCTRL keeps a persistent local inspector connection and background state cache rather than reconnecting on every foreground-window poll.

The UI still treats a selected profile as one opaque ID. Internally that ID contains both:

- SynapseCTRL stable device ID
- Synapse software-profile GUID

This keeps existing app/settings storage simple while preserving SynapseCTRL's device-scoped profile model.

Expected Synapse/inspector failures are logged and return an empty profile list rather than crashing the application. The service is closed cleanly when the Qt application exits.
