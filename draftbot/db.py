"""Schema SQLite e accesso ai dati locali."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .paths import db_path

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- Ogni download registrato con data e origine: rende riproducibili import e backtest.
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_code TEXT NOT NULL,
    source TEXT NOT NULL,          -- scryfall | 17lands | arena_db
    kind TEXT NOT NULL,            -- cards | draft | game | cards_csv | ...
    url TEXT,
    last_updated TEXT,             -- data dichiarata dalla fonte
    fetched_at TEXT NOT NULL,
    info TEXT                      -- JSON libero
);

-- Una riga per carta giocabile nel draft del set (chiave: nome canonico Scryfall).
CREATE TABLE IF NOT EXISTS cards (
    set_code TEXT NOT NULL,
    name TEXT NOT NULL,
    norm_name TEXT NOT NULL,
    scryfall_id TEXT,
    oracle_id TEXT,
    printed_set TEXT,
    collector_number TEXT,
    rarity TEXT,
    layout TEXT,
    type_line TEXT,
    oracle_text TEXT,
    keywords TEXT,                 -- JSON
    mana_cost TEXT,
    mv REAL,
    colors TEXT,                   -- colori della carta (WUBRG)
    color_identity TEXT,
    cost_colors TEXT,              -- colori richiesti dal costo
    pip_w INTEGER, pip_u INTEGER, pip_b INTEGER, pip_r INTEGER, pip_g INTEGER,
    max_pips INTEGER,
    hybrid TEXT,                   -- JSON
    phyrexian TEXT,                -- JSON
    has_x INTEGER,
    is_creature INTEGER, is_land INTEGER, is_instant INTEGER, is_sorcery INTEGER,
    is_artifact INTEGER, is_enchantment INTEGER, is_planeswalker INTEGER, is_battle INTEGER,
    power TEXT, toughness TEXT,
    image_uri TEXT,
    PRIMARY KEY (set_code, name)
);
CREATE INDEX IF NOT EXISTS cards_norm ON cards(set_code, norm_name);
CREATE INDEX IF NOT EXISTS cards_mv ON cards(set_code, mv);

-- Mappatura ID interni di Arena (grpId) -> carta.
CREATE TABLE IF NOT EXISTS arena_ids (
    set_code TEXT NOT NULL,
    grp_id INTEGER NOT NULL,
    name TEXT NOT NULL,            -- nome canonico in cards
    source TEXT NOT NULL,
    source_expansion TEXT,
    PRIMARY KEY (set_code, grp_id)
);

-- Statistiche 17Lands aggregate (Fase 3). Ogni import e' uno snapshot datato (tabella snapshots).
-- Chiavi: colori principali del mazzo (WUBRG), giorno (AAAA-MM-GG), fascia del giocatore.
CREATE TABLE IF NOT EXISTS stat_games (
    snapshot_id INTEGER NOT NULL, set_code TEXT NOT NULL, format TEXT NOT NULL,
    colors TEXT NOT NULL, day TEXT NOT NULL, tier TEXT NOT NULL, card TEXT NOT NULL,
    n_deck INTEGER, w_deck INTEGER, n_gih INTEGER, w_gih INTEGER, n_oh INTEGER, w_oh INTEGER
);
CREATE INDEX IF NOT EXISTS stat_games_k ON stat_games(snapshot_id, card);
CREATE TABLE IF NOT EXISTS stat_decks (
    snapshot_id INTEGER NOT NULL, set_code TEXT NOT NULL, format TEXT NOT NULL,
    colors TEXT NOT NULL, day TEXT NOT NULL, tier TEXT NOT NULL, n INTEGER, w INTEGER
);
CREATE TABLE IF NOT EXISTS stat_picks (
    snapshot_id INTEGER NOT NULL, set_code TEXT NOT NULL, format TEXT NOT NULL,
    day TEXT NOT NULL, tier TEXT NOT NULL, card TEXT NOT NULL,
    n_seen INTEGER, n_taken INTEGER, sum_taken_at INTEGER, n_last INTEGER, sum_last_seen INTEGER
);
CREATE INDEX IF NOT EXISTS stat_picks_k ON stat_picks(snapshot_id, card);

-- Problemi rilevati durante l'import: carte senza mappatura, conflitti, nomi non risolti.
CREATE TABLE IF NOT EXISTS issues (
    set_code TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

CARD_COLUMNS = [
    "set_code", "name", "norm_name", "scryfall_id", "oracle_id", "printed_set", "collector_number",
    "rarity", "layout", "type_line", "oracle_text", "keywords", "mana_cost", "mv", "colors",
    "color_identity", "cost_colors", "pip_w", "pip_u", "pip_b", "pip_r", "pip_g", "max_pips",
    "hybrid", "phyrexian", "has_x", "is_creature", "is_land", "is_instant", "is_sorcery",
    "is_artifact", "is_enchantment", "is_planeswalker", "is_battle", "power", "toughness", "image_uri",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or db_path(), timeout=60)   # piu' processi (UI, verifiche) sullo stesso file
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    with con:  # commit subito: una transazione aperta bloccherebbe le altre connessioni
        con.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    return con


def add_snapshot(con: sqlite3.Connection, set_code: str, source: str, kind: str, *, url: str | None = None,
                 last_updated: str | None = None, info: dict[str, Any] | None = None) -> None:
    con.execute(
        "INSERT INTO snapshots(set_code, source, kind, url, last_updated, fetched_at, info) VALUES (?,?,?,?,?,?,?)",
        (set_code, source, kind, url, last_updated, now(), json.dumps(info or {}, ensure_ascii=False)),
    )


def replace_set(con: sqlite3.Connection, set_code: str, cards: Iterable[dict[str, Any]],
                arena_ids: Iterable[tuple[int, str, str, str]], issues: Iterable[tuple[str, str]]) -> None:
    """Sostituisce in una transazione carte, mappature e problemi di un set."""
    with con:
        for table in ("cards", "arena_ids", "issues"):
            con.execute(f"DELETE FROM {table} WHERE set_code = ?", (set_code,))
        cols = ",".join(CARD_COLUMNS)
        marks = ",".join("?" * len(CARD_COLUMNS))
        con.executemany(f"INSERT INTO cards({cols}) VALUES ({marks})",
                        [tuple(c.get(k) for k in CARD_COLUMNS) for c in cards])
        con.executemany("INSERT INTO arena_ids VALUES (?,?,?,?,?)",
                        [(set_code, g, n, s, e) for g, n, s, e in arena_ids])
        ts = now()
        con.executemany("INSERT INTO issues VALUES (?,?,?,?)", [(set_code, k, d, ts) for k, d in issues])


def query_cards(con: sqlite3.Connection, set_code: str, *, name: str | None = None, mv: float | None = None,
                color: str | None = None, rarity: str | None = None, card_type: str | None = None,
                grp_id: int | None = None) -> list[sqlite3.Row]:
    sql = "SELECT c.* FROM cards c WHERE c.set_code = ?"
    args: list[Any] = [set_code]
    if name:
        sql += " AND c.norm_name LIKE ?"
        from .names import normalize
        args.append(f"%{normalize(name)}%")
    if mv is not None:
        sql += " AND c.mv = ?"
        args.append(mv)
    if color:
        for ch in color.upper():
            if ch == "C":
                sql += " AND c.cost_colors = ''"
            else:
                sql += " AND instr(c.cost_colors, ?) > 0"
                args.append(ch)
    if rarity:
        sql += " AND c.rarity = ?"
        args.append(rarity.lower())
    if card_type:
        sql += " AND c.type_line LIKE ?"
        args.append(f"%{card_type}%")
    if grp_id is not None:
        sql += " AND c.name IN (SELECT name FROM arena_ids WHERE set_code = ? AND grp_id = ?)"
        args += [set_code, grp_id]
    return list(con.execute(sql + " ORDER BY c.mv, c.name", args))


def arena_names(con: sqlite3.Connection, set_code: str) -> dict[int, str]:
    """grpId -> nome canonico per un set importato."""
    return {r[0]: r[1] for r in con.execute("SELECT grp_id, name FROM arena_ids WHERE set_code = ?", (set_code,))}
