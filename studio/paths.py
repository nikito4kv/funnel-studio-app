"""Where the bot repository lives (read-only for the Studio).

The "bot repository" is the folder that holds the engine's launches:
`launches/<slug>/launch.yaml`, `launches/<slug>/funnel*.yaml`, `launches/<slug>/static/`.
A small fictional one ships in `demo/botrepo`, which is the default.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_BOT_REPO_PATH = str(Path(__file__).resolve().parent.parent / "demo" / "botrepo")


def bot_repo_path() -> Path:
    return Path(os.environ.get("BOT_REPO_PATH", DEFAULT_BOT_REPO_PATH))


def funnel_files() -> list[Path]:
    """All launch funnels: launches/*/funnel*.yaml (without copies like "x 2.yaml")."""
    root = bot_repo_path() / "launches"
    return sorted(p for p in root.glob("*/funnel*.yaml") if " " not in p.name)
