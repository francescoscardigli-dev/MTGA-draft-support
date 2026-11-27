"""Download e aggregazione dei dataset pubblici 17Lands (game data e draft data).

I file grezzi restano in ``data/raw/17lands/`` (cache, riscaricati solo se 17Lands li aggiorna).
Nel database si salvano solo aggregati per carta, colori del mazzo, giorno e fascia di abilita'
del giocatore, legati a uno snapshot datato. Dal giorno si ricavano filtri "as of" (backtest) e
il decadimento temporale senza rileggere i file.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from .. import db
from ..config import FormatConfig, SetConfig, load_format
from ..names import keys
from ..paths import raw_dir
from ..sources import seventeenlands

# Fasce sulla win rate del giocatore (colonna user_game_win_rate_bucket di 17Lands).
# Non dipendono dal set. "high" e' il gruppo usato come "giocatori forti".
TIER_EDGES = [(0.58, "high"), (0.50, "mid"), (0.0, "low")]
CHUNK = 40_000
COLOR_ORDER = "WUBRG"


@dataclass
class IngestResult:
    kind: str
    format: str
    rows: int
    snapshot_id: int
    first_day: str
    last_day: str
    unmapped: list[str]
    note: str = ""


def tier_of(bucket: pd.Series) -> pd.Series:
    b = pd.to_numeric(bucket, errors="coerce")
    out = pd.Series("na", index=bucket.index, dtype=object)
    for edge, name in reversed(TIER_EDGES):
        out[b >= edge] = name
    return out


def norm_colors(s: pd.Series) -> pd.Series:
    def f(x: object) -> str:
        x = x if isinstance(x, str) else ""
        return "".join(c for c in COLOR_ORDER if c in x.upper())
    return s.map(f)


def name_map(con: sqlite3.Connection, set_code: str, names: list[str]) -> tuple[dict[str, str], list[str]]:
    """Nome 17Lands -> nome canonico della tabella cards. Restituisce anche i non trovati."""
    by_norm = {r[0]: r[1] for r in con.execute("SELECT norm_name, name FROM cards WHERE set_code=?", (set_code,))}
    by_front = {}
    for n in by_norm.values():
        for k in keys(n):
            by_front.setdefault(k, n)
    out, missing = {}, []
    for n in names:
        hit = next((by_front[k] for k in keys(n) if k in by_front), None)
        if hit is None:
            missing.append(n)
            hit = n  # si tiene il nome 17Lands: la carta resta nelle statistiche, ma segnalata
        out[n] = hit
    return out, missing


# --------------------------------------------------------------------------- download

def cached_dataset(ds: seventeenlands.Dataset, *, force: bool = False, log: Callable[[str], None] = print) -> Path:
    d = raw_dir("17lands")
    path = d / ds.url.rsplit("/", 1)[-1]
    meta = path.with_suffix(path.suffix + ".json")
    if not force and path.exists() and meta.exists():
        if json.loads(meta.read_text()).get("last_updated") == ds.last_updated:
            log(f"  cache: {path.name} (aggiornato da 17Lands il {ds.last_updated[:10]})")
            return path
    log(f"  download: {ds.url}")
    seventeenlands.download(ds.url, path)
    meta.write_text(json.dumps(seventeenlands.dataset_meta(ds)))
    return path


# --------------------------------------------------------------------------- game data

def aggregate_games(path: Path, *, chunksize: int = CHUNK) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Restituisce (per carta, per mazzo, righe lette).

    Per carta e per (colori, giorno, fascia): partite con la carta nel mazzo (``n_deck``), in mano
    iniziale (``n_oh``), vista in partita cioe' in mano iniziale o pescata (``n_gih``), e le vittorie
    corrispondenti. Una carta in piu' copie conta una partita sola, come su 17Lands.
    """
    header = pd.read_csv(path, nrows=0).columns
    cards = [c[5:] for c in header if c.startswith("deck_")]
    meta = ["game_time", "main_colors", "won", "user_game_win_rate_bucket"]
    usecols = meta + [f"{p}{c}" for p in ("deck_", "opening_hand_", "drawn_") for c in cards]
    dtypes = {c: np.int16 for c in usecols if c not in meta}
    dtypes.update({c: str for c in meta})
    parts: list[pd.DataFrame] = []
    decks: list[pd.DataFrame] = []
    rows = 0
    for ch in pd.read_csv(path, usecols=usecols, dtype=dtypes, chunksize=chunksize):
        rows += len(ch)
        won = ch["won"].astype(str).str.lower().eq("true").to_numpy()
        keys_df = pd.DataFrame({"colors": norm_colors(ch["main_colors"]).to_numpy(),
                                "day": ch["game_time"].astype(str).str[:10].to_numpy(),
                                "tier": tier_of(ch["user_game_win_rate_bucket"]).to_numpy()})
        deck = ch[[f"deck_{c}" for c in cards]].to_numpy() > 0
        oh = ch[[f"opening_hand_{c}" for c in cards]].to_numpy() > 0
        gih = oh | (ch[[f"drawn_{c}" for c in cards]].to_numpy() > 0)
        gk = [keys_df["colors"], keys_df["day"], keys_df["tier"]]
        cols = {}
        for name, m in (("deck", deck), ("gih", gih), ("oh", oh)):
            mdf = pd.DataFrame(m.astype(np.int32), columns=cards)
            cols[f"n_{name}"] = mdf.groupby(gk).sum().stack()
            cols[f"w_{name}"] = mdf.mul(won, axis=0).groupby(gk).sum().stack()
        part = pd.DataFrame(cols)
        parts.append(part[part["n_deck"] > 0])
        dk = pd.DataFrame({"n": 1, "w": won.astype(int)}).groupby(gk).sum()
        decks.append(dk)
    by_card = pd.concat(parts).groupby(level=[0, 1, 2, 3]).sum()
    by_card.index.names = ["colors", "day", "tier", "card"]
    by_deck = pd.concat(decks).groupby(level=[0, 1, 2]).sum()
    by_deck.index.names = ["colors", "day", "tier"]
    return by_card.reset_index(), by_deck.reset_index(), rows


# --------------------------------------------------------------------------- draft data

def aggregate_drafts(path: Path, fmt: FormatConfig, *, chunksize: int = CHUNK) -> tuple[pd.DataFrame, int, int]:
    """Per carta, giorno e fascia: volte vista, volte presa, somma del turno di presa (ATA) e
    somma del turno in cui ogni copia e' stata vista l'ultima volta (ALSA).

    ALSA: la stessa busta torna dopo ``pod_size`` turni. Le copie presenti al turno t e assenti al
    turno t + pod_size sono state prese tra i due turni: l'ultima volta che le abbiamo viste e' t.
    Se la busta del turno t + pod_size manca nei dati (ma dovrebbe esserci) la riga non conta per l'ALSA.
    Turni contati da 1, come su 17Lands.
    """
    header = pd.read_csv(path, nrows=0).columns
    cards = [c[len("pack_card_"):] for c in header if c.startswith("pack_card_")]
    idx = {c: i for i, c in enumerate(cards)}
    meta = ["draft_id", "draft_time", "pack_number", "pick_number", "pick", "user_game_win_rate_bucket"]
    if "pick_2" in header:
        meta.append("pick_2")
    usecols = meta + [f"pack_card_{c}" for c in cards]
    dtypes = {f"pack_card_{c}": np.int16 for c in cards}
    dtypes.update({c: str for c in meta})  # draft_id esadecimali come "12e34..." non devono diventare numeri
    acc: list[pd.DataFrame] = []
    rows = skipped = 0
    carry: pd.DataFrame | None = None
    reader = pd.read_csv(path, usecols=usecols, dtype=dtypes, chunksize=chunksize)
    for ch in reader:
        if carry is not None:
            ch = pd.concat([carry, ch], ignore_index=True)
        last = ch["draft_id"].iloc[-1]
        carry = ch[ch["draft_id"] == last]
        ch = ch[ch["draft_id"] != last]
        if len(ch):
            a, s = _draft_chunk(ch, cards, idx, fmt)
            acc.append(a)
            rows += len(ch)
            skipped += s
    if carry is not None and len(carry):
        a, s = _draft_chunk(carry, cards, idx, fmt)
        acc.append(a)
        rows += len(carry)
        skipped += s
    out = pd.concat(acc).groupby(level=[0, 1, 2]).sum()
    out.index.names = ["day", "tier", "card"]
    return out.reset_index(), rows, skipped


def _draft_chunk(ch: pd.DataFrame, cards: list[str], idx: dict[str, int], fmt: FormatConfig
                 ) -> tuple[pd.DataFrame, int]:
    ch = ch.reset_index(drop=True)
    pack = ch[[f"pack_card_{c}" for c in cards]].to_numpy().astype(np.int32)
    ch["pick_number"] = ch["pick_number"].astype(int)
    ch["pack_number"] = ch["pack_number"].astype(int)
    turn = ch["pick_number"].to_numpy()
    taken = np.zeros_like(pack)
    pick_cols = ["pick"] + (["pick_2"] if "pick_2" in ch else [])
    for col in pick_cols:
        for r, name in enumerate(ch[col].to_numpy()):
            j = idx.get(name) if isinstance(name, str) else None
            if j is not None:
                taken[r, j] += 1
    # riga della stessa busta pod_size turni dopo
    key = pd.MultiIndex.from_arrays([ch["draft_id"], ch["pack_number"], ch["pick_number"]])
    pos = pd.Series(np.arange(len(ch)), index=key)
    pos = pos[~pos.index.duplicated()]
    nxt_key = pd.MultiIndex.from_arrays([ch["draft_id"], ch["pack_number"], ch["pick_number"] + fmt.pod_size])
    nxt = pos.reindex(nxt_key).to_numpy()
    beyond = (turn + fmt.pod_size) >= fmt.picks_per_pack
    has_next = ~np.isnan(nxt)
    valid = beyond | has_next
    nxt_pack = np.zeros_like(pack)
    nxt_pack[has_next] = pack[nxt[has_next].astype(int)]
    ends = np.clip(pack - nxt_pack, 0, None) * valid[:, None]
    t1 = (turn + 1)[:, None]
    gk = [ch["draft_time"].astype(str).str[:10], tier_of(ch["user_game_win_rate_bucket"])]
    cols = {}
    for name, m in (("n_seen", pack > 0), ("n_taken", taken), ("sum_taken_at", taken * t1),
                    ("n_last", ends), ("sum_last_seen", ends * t1)):
        cols[name] = pd.DataFrame(np.asarray(m, dtype=np.int64), columns=cards).groupby(gk).sum().stack()
    return pd.DataFrame(cols), int((~valid).sum())


# --------------------------------------------------------------------------- salvataggio

def _store(con: sqlite3.Connection, table: str, df: pd.DataFrame, fixed: dict[str, object]) -> None:
    df = df.assign(**fixed)
    cols = list(df.columns)
    con.executemany(f"INSERT INTO {table}({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    df.itertuples(index=False, name=None))


def ingest(cfg: SetConfig, fmt_name: str, kind: str, *, force: bool = False,
           log: Callable[[str], None] = print) -> IngestResult | None:
    fmt = load_format(fmt_name)
    ds = [d for d in seventeenlands.list_datasets()
          if d.expansion.upper() == cfg.seventeenlands_expansion.upper()
          and d.event_type == fmt.seventeenlands_event_type and d.kind == kind]
    if not ds:
        log(f"{cfg.code} {fmt.display_name}: nessun dataset pubblico '{kind}' su 17Lands")
        return None
    path = cached_dataset(ds[0], force=force, log=log)
    con = db.connect()
    if kind == "game":
        by_card, by_deck, rows = aggregate_games(path)
        days = by_deck["day"]
        mapping, missing = name_map(con, cfg.code, sorted(by_card["card"].unique()))
        by_card["card"] = by_card["card"].map(mapping)
        by_card = by_card.groupby(["colors", "day", "tier", "card"], as_index=False).sum()
        note = ""
    else:
        by_card, rows, skipped = aggregate_drafts(path, fmt)
        days = by_card["day"]
        mapping, missing = name_map(con, cfg.code, sorted(by_card["card"].unique()))
        by_card["card"] = by_card["card"].map(mapping)
        by_card = by_card.groupby(["day", "tier", "card"], as_index=False).sum()
        by_card = by_card[by_card["n_seen"] > 0]
        note = f"righe senza busta di ritorno (escluse dall'ALSA): {skipped}"
    with con:
        cur = con.execute(
            "INSERT INTO snapshots(set_code, source, kind, url, last_updated, fetched_at, info) VALUES (?,?,?,?,?,?,?)",
            (cfg.code, "17lands", f"{kind}_stats", ds[0].url, ds[0].last_updated, db.now(),
             json.dumps({"format": fmt.name, "rows": rows, "first_day": days.min(), "last_day": days.max()})))
        sid = cur.lastrowid
        fixed = {"snapshot_id": sid, "set_code": cfg.code, "format": fmt.name}
        if kind == "game":
            _store(con, "stat_games", by_card, fixed)
            _store(con, "stat_decks", by_deck, fixed)
        else:
            _store(con, "stat_picks", by_card, fixed)
        con.execute("DELETE FROM issues WHERE set_code=? AND kind=?", (cfg.code, f"17lands_{kind}_unmapped"))
        con.executemany("INSERT INTO issues VALUES (?,?,?,?)",
                        [(cfg.code, f"17lands_{kind}_unmapped", f"{fmt.name}: {n}", db.now()) for n in missing])
    con.close()
    return IngestResult(kind, fmt.name, rows, sid, str(days.min()), str(days.max()), missing, note)
