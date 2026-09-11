# Synapse Game Profiles

Standalone Windows tray application for discovering launcher-managed games, mapping executables to Razer Synapse software-profile UUIDs, and switching profiles based on the focused application.

This project is intentionally separate from SynapseCTRL. SynapseCTRL is an optional future backend dependency.

## Current first-part features

- Steam library discovery, including libraries on arbitrary drives from `libraryfolders.vdf`
- Epic Games Launcher manifest discovery
- GOG registry discovery
- Xbox / Microsoft Gaming `.GamingRoot` and `XboxGames` discovery
- EA, Ubisoft Connect, Battle.net, and Rockstar registry discovery
- Enumerates every `.exe` inside each detected game install
- Public executable/folder title mappings in `resources/game_mappings.json`
- User overrides stored separately under the writable `data` directory
- Manual application add
- Cached tile artwork/icons
- Tile grid with dark overlay and centered wrapped title
- Right-click actions: Modify item, Remove item, Set profile, Launch application
- Modify dialog supports executable, `.ico`, and common image files for tile art
- Dummy profile list using stable UUIDs
- Foreground-window watcher using exact executable matching first
- Optional subprocess/ancestor process matching
- Configurable fallback profile UUID
- If no fallback profile is configured, remembers the last observed unassociated profile UUID
- System tray support and background operation

## Run

```powershell
py -m pip install -e .
synapse-game-profiles
```

Or:

```powershell
py -m synapse_game_profiles
```

## Data location

When running from source, writable state is kept in `./data` next to the project. When packaged, it uses `%LOCALAPPDATA%\\SynapseGameProfiles`.

The main files are:

- `apps.json`: discovered/manual app records and assigned profile UUIDs
- `settings.json`: watcher and fallback settings
- `user_overrides.json`: user title/icon/executable overrides
- `cached_icons/`: copied or extracted tile artwork

## Synapse integration

`profile_backend.py` currently exposes a dummy backend. Replace or enable the SynapseCTRL backend later without changing the UI data model because profile assignments are already stored by UUID.
