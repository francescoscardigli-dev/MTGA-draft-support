"""Lettura in sola lettura del database delle carte installato dal client di Arena.

File: ``<MTGA>/MTGA_Data/Downloads/Raw/Raw_CardDatabase_*.mtga`` (SQLite).

ATTENZIONE: lo schema non e' documentato da Wizards. Questo modulo segue lo schema descritto
dai tool open source (tabella ``Cards`` con GrpId, TitleId, ExpansionCode, CollectorNumber e
tabelle ``Localizations*`` con i testi) e lo verifica a runtime: se non lo riconosce, lo dice
invece di indovinare. Da validare sul PC dell'utente.
"""

from __future__ import annotations

import os
import platform
import sqlite3
from dataclasses import dataclass
from pathlib import Path


class ArenaDbError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArenaCard:
    grp_id: int
    name: str
    expansion: str
    collector_number: str


def default_paths() -> list[Path]:
    """Cartelle Raw tipiche su Windows e macOS (installazioni standard e Steam)."""
    cands: list[Path] = []
    env = os.environ.get("DRAFTBOT_ARENA_RAW")
    if env:
        cands.append(Path(env))
    system = platform.system()
    if system == "Windows":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            cands.append(Path(base) / "Wizards of the Coast" / "MTGA" / "MTGA_Data" / "Downloads" / "Raw")
            cands.append(Path(base) / "Steam" / "steamapps" / "common" / "MTGA" / "MTGA_Data" / "Downloads" / "Raw")
    elif system == "Darwin":
        home = Path.home()
        cands.append(home / "Library" / "Application Support" / "com.wizards.mtga" / "Downloads" / "Raw")
        cands.append(Path("/Applications/MTGA.app/Contents/Resources/Data/Downloads/Raw"))
    return cands


def find_database(paths: list[Path] | None = None) -> Path | None:
    for d in paths if paths is not None else default_paths():
        if d.is_file():
            return d
        if d.is_dir():
            files = sorted(d.glob("Raw_CardDatabase_*.mtga"), key=lambda p: p.stat().st_mtime, reverse=True)
            if files:
                return files[0]
    return None


def _columns(con: sqlite3.Connection, table: str) -> dict[str, str]:
    return {r[1].lower(): r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}


def _find(cols: dict[str, str], *names: str) -> str | None:
    for n in names:
        if n.lower() in cols:
            return cols[n.lower()]
    return None


def read_cards(path: Path, expansions: set[str] | None = None) -> list[ArenaCard]:
    """Legge (grpId, nome inglese, set, numero di collezione). Apre il file in sola lettura."""
    uri = f"file:{path.as_posix()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        raise ArenaDbError(f"impossibile aprire {path}: {e}") from e
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "Cards" not in tables:
            raise ArenaDbError(f"schema non riconosciuto: manca la tabella Cards ({sorted(tables)[:10]})")
        cc = _columns(con, "Cards")
        grp = _find(cc, "GrpId")
        title = _find(cc, "TitleId")
        exp = _find(cc, "ExpansionCode", "Expansion")
        num = _find(cc, "CollectorNumber")
        if not all((grp, title, exp, num)):
            raise ArenaDbError(f"schema Cards non riconosciuto: colonne {sorted(cc.values())}")
        loc_table = next((t for t in ("Localizations_enUS", "Localizations") if t in tables), None)
        if loc_table is None:
            raise ArenaDbError("schema non riconosciuto: manca la tabella delle localizzazioni")
        lc = _columns(con, loc_table)
        loc_id = _find(lc, "LocId")
        loc_text = _find(lc, "Loc", "enUS", "Text")
        if not (loc_id and loc_text):
            raise ArenaDbError(f"schema {loc_table} non riconosciuto: colonne {sorted(lc.values())}")
        where, params = "", []
        if expansions:
            where = f' WHERE UPPER(c."{exp}") IN ({",".join("?" * len(expansions))})'
            params = sorted(e.upper() for e in expansions)
        # Se esiste la colonna Formatted, si prende la variante con formattazione minima.
        order = ""
        fmt = _find(lc, "Formatted")
        if fmt:
            order = f' ORDER BY l."{fmt}"'
        sql = (f'SELECT c."{grp}", l."{loc_text}", c."{exp}", c."{num}" FROM Cards c '
               f'LEFT JOIN "{loc_table}" l ON l."{loc_id}" = c."{title}"{where}{order}')
        seen: dict[int, ArenaCard] = {}
        for g, name, e, n in con.execute(sql, params):
            if g in seen or not name:
                continue
            seen[int(g)] = ArenaCard(int(g), str(name), str(e or ""), str(n or ""))
        return list(seen.values())
    except sqlite3.Error as e:
        raise ArenaDbError(f"errore leggendo {path}: {e}") from e
    finally:
        con.close()
