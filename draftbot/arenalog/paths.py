"""Percorsi tipici di Player.log. Override: variabile DRAFTBOT_PLAYER_LOG."""

from __future__ import annotations

import os
import platform
from pathlib import Path


def default_log_paths() -> list[Path]:
    env = os.environ.get("DRAFTBOT_PLAYER_LOG")
    if env:
        return [Path(env)]
    home = Path.home()
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("USERPROFILE", home)) / "AppData" / "LocalLow" / "Wizards Of The Coast" / "MTGA"
    elif system == "Darwin":
        base = home / "Library" / "Logs" / "Wizards Of The Coast" / "MTGA"
    else:  # Linux (Wine/Proton): non c'e' un percorso standard
        return []
    return [base / "Player-prev.log", base / "Player.log"]


def find_logs(paths: list[Path] | None = None) -> list[Path]:
    """File esistenti, in ordine cronologico (Player-prev.log prima di Player.log)."""
    return [p for p in (paths if paths is not None else default_log_paths()) if p.is_file()]
