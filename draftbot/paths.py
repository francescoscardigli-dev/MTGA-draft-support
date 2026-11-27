"""Percorsi della cache locale. Default: ./data, modificabile con DRAFTBOT_DATA."""

from __future__ import annotations

import os
from pathlib import Path

from .config import ROOT


def data_dir() -> Path:
    d = Path(os.environ.get("DRAFTBOT_DATA", ROOT / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def raw_dir(source: str) -> Path:
    d = data_dir() / "raw" / source
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "draftbot.sqlite"
