"""Caricamento e validazione delle config di set e formato.

Il motore non conosce nessun set: tutto cio' che dipende dal set o dal formato
arriva da ``sets/<codice>.yaml`` e ``formats/<nome>.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
SETS_DIR = ROOT / "sets"
FORMATS_DIR = ROOT / "formats"


class ConfigError(ValueError):
    """Config mancante o non valida."""


@dataclass(frozen=True)
class FormatConfig:
    name: str
    display_name: str
    seventeenlands_event_type: str
    arena_event_prefixes: tuple[str, ...]
    pod_size: int
    packs: int
    pack_size: int
    picks_per_turn: int
    pass_directions: tuple[str, ...]
    best_of: int

    def __post_init__(self) -> None:
        if self.pod_size < 2:
            raise ConfigError(f"{self.name}: pod_size deve essere >= 2")
        if self.picks_per_turn < 1 or self.pack_size % self.picks_per_turn:
            raise ConfigError(f"{self.name}: pack_size deve essere multiplo di picks_per_turn")
        if len(self.pass_directions) != self.packs:
            raise ConfigError(f"{self.name}: serve una direzione di passaggio per busta")
        if any(d not in ("left", "right") for d in self.pass_directions):
            raise ConfigError(f"{self.name}: direzioni ammesse: left, right")

    @property
    def picks_per_pack(self) -> int:
        """Numero di turni di pick per busta (indice 0-based da 0 a picks_per_pack-1)."""
        return self.pack_size // self.picks_per_turn

    def pack_size_at(self, pick_index: int) -> int:
        """Carte presenti nella busta al pick ``pick_index`` (0-based)."""
        if not 0 <= pick_index < self.picks_per_pack:
            raise ValueError(f"pick fuori range: {pick_index}")
        return self.pack_size - pick_index * self.picks_per_turn

    def wheel_picks(self) -> list[int]:
        """Pick (0-based) in cui torna una busta gia' vista: multipli di pod_size."""
        return list(range(self.pod_size, self.picks_per_pack, self.pod_size))

    def first_seen_pick(self, pick_index: int) -> int | None:
        """Pick in cui si e' vista per la prima volta la busta di ``pick_index``, se e' una wheel."""
        return pick_index - self.pod_size if pick_index >= self.pod_size else None

    @property
    def total_picks(self) -> int:
        return self.packs * self.picks_per_pack

    @property
    def pool_size(self) -> int:
        return self.total_picks * self.picks_per_turn


@dataclass(frozen=True)
class SetConfig:
    code: str
    name: str
    scryfall_sets: tuple[str, ...]
    seventeenlands_expansion: str
    formats: tuple[str, ...]
    arena_id_sources: tuple[str, ...]
    release_date: str | None = None
    name_aliases: dict[str, str | None] = field(default_factory=dict)
    mechanics: tuple[str, ...] = ()
    archetypes: tuple[Any, ...] = ()
    synergy_tags_file: str | None = None


_ARENA_SOURCES = {"arena_card_database", "seventeenlands", "scryfall"}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"file di config non trovato: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"config non valida (atteso un dizionario): {path}")
    return data


def _require(data: dict[str, Any], keys: list[str], path: Path) -> None:
    missing = [k for k in keys if k not in data]
    if missing:
        raise ConfigError(f"{path.name}: campi mancanti: {', '.join(missing)}")


def load_format(name: str, formats_dir: Path = FORMATS_DIR) -> FormatConfig:
    path = formats_dir / f"{name}.yaml"
    d = _load_yaml(path)
    keys = [
        "name", "display_name", "seventeenlands_event_type", "arena_event_prefixes",
        "pod_size", "packs", "pack_size", "picks_per_turn", "pass_directions", "best_of",
    ]
    _require(d, keys, path)
    if d["name"] != name:
        raise ConfigError(f"{path.name}: name '{d['name']}' diverso dal nome del file")
    return FormatConfig(
        name=d["name"],
        display_name=d["display_name"],
        seventeenlands_event_type=d["seventeenlands_event_type"],
        arena_event_prefixes=tuple(d["arena_event_prefixes"]),
        pod_size=int(d["pod_size"]),
        packs=int(d["packs"]),
        pack_size=int(d["pack_size"]),
        picks_per_turn=int(d["picks_per_turn"]),
        pass_directions=tuple(d["pass_directions"]),
        best_of=int(d["best_of"]),
    )


def load_set(code: str, sets_dir: Path = SETS_DIR, formats_dir: Path = FORMATS_DIR) -> SetConfig:
    code = code.upper()
    path = sets_dir / f"{code}.yaml"
    d = _load_yaml(path)
    _require(d, ["code", "name", "scryfall_sets", "seventeenlands_expansion", "formats", "arena_id_sources"], path)
    if str(d["code"]).upper() != code:
        raise ConfigError(f"{path.name}: code '{d['code']}' diverso dal nome del file")
    unknown = set(d["arena_id_sources"]) - _ARENA_SOURCES
    if unknown:
        raise ConfigError(f"{path.name}: fonti arena_id sconosciute: {sorted(unknown)}")
    for f in d["formats"]:
        load_format(f, formats_dir)  # valida che il formato esista
    return SetConfig(
        code=code,
        name=d["name"],
        scryfall_sets=tuple(s.lower() for s in d["scryfall_sets"]),
        seventeenlands_expansion=d["seventeenlands_expansion"],
        formats=tuple(d["formats"]),
        arena_id_sources=tuple(d["arena_id_sources"]),
        release_date=str(d["release_date"]) if d.get("release_date") else None,
        name_aliases=dict(d.get("name_aliases") or {}),
        mechanics=tuple(d.get("mechanics") or ()),
        archetypes=tuple(d.get("archetypes") or ()),
        synergy_tags_file=d.get("synergy_tags_file"),
    )


def available_sets(sets_dir: Path = SETS_DIR) -> list[str]:
    return sorted(p.stem for p in sets_dir.glob("*.yaml") if not p.stem.endswith(".tags"))


def available_formats(formats_dir: Path = FORMATS_DIR) -> list[str]:
    return sorted(p.stem for p in formats_dir.glob("*.yaml"))
