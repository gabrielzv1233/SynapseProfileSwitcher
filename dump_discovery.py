from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from synapse_game_profiles.debug_log import configure_logging
from synapse_game_profiles.game_library import discover_games


OUTPUT = ROOT / "game_discovery_dump.txt"


def main() -> None:
    logger = configure_logging()
    logger.info("Starting standalone discovery dump")
    games = discover_games(print)

    lines = [
        "SynapseProfileSwitcher discovery dump",
        f"Found: {len(games)} executable(s)",
        "",
    ]

    for index, game in enumerate(games, 1):
        executable = Path(game.executable)
        install_root = Path(game.install_root)
        lines.extend(
            (
                f"[{index}] {game.title}",
                f"Launcher:     {game.launcher}",
                f"Executable:   {game.executable}",
                f"EXE exists:   {executable.is_file()}",
                f"Install root: {game.install_root}",
                f"Root exists:  {install_root.is_dir()}",
                "",
            )
        )

    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {len(games)} entries to: {OUTPUT}")


if __name__ == "__main__":
    main()
