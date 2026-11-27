"""Metriche per carta calcolate in proprio dagli aggregati 17Lands, con blend bayesiano.

Metriche (stesse definizioni di 17Lands):
- GIH WR: win rate nelle partite in cui la carta e' stata vista (mano iniziale o pescata);
- OH WR: in mano iniziale; GD WR: pescata dopo la mano iniziale; GP WR: nel mazzo;
- GNS WR: nel mazzo ma non vista; IWD = GIH WR - GNS WR;
- ATA: turno medio di presa; ALSA: turno medio dell'ultima volta vista; taken rate: prese / viste.

Blend bayesiano della GIH WR (modello beta-binomiale):
    stima = (vittorie + m * prior) / (partite + m),   m = p0 (1 - p0) / tau^2
dove ``prior`` viene dal modello dei prior (ATA, ALSA, rarita', stimato sui set precedenti) e
``tau^2`` e' la varieta' reale delle carte attorno al prior, stimata dai dati del set stesso
(empirical Bayes) o, se i dati sono pochi, dal modello dei prior. Con poche partite la stima resta
vicina al prior, con molte coincide con il dato osservato.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import load_format

MIN_N_TAU = 200       # partite minime per stimare tau^2 dai dati del set
MIN_CARDS_TAU = 15    # carte minime con MIN_N_TAU partite per fidarsi della stima


@dataclass(frozen=True)
class Filters:
    colors: str | None = None            # colori principali del mazzo, es. "WU" (ordine libero)
    tiers: tuple[str, ...] | None = None  # fasce del giocatore: low, mid, high
    as_of: str | None = None             # solo dati fino a questo giorno incluso (AAAA-MM-GG)
    since: str | None = None             # solo dati da questo giorno incluso
    half_life: float | None = None       # decadimento temporale in giorni (None = nessuno)

    def norm_colors(self) -> str | None:
        if not self.colors:
            return None
        return "".join(c for c in "WUBRG" if c in self.colors.upper())


@dataclass
class Table:
    cards: pd.DataFrame
    baseline_deck: float | None       # win rate media dei mazzi nel filtro
    baseline_gih: float | None        # GIH WR media (pesata) delle carte nel filtro
    tau2: float | None
    tau2_source: str
    snapshots: dict[str, int | None] = field(default_factory=dict)
    days: tuple[str | None, str | None] = (None, None)
    n_games: float = 0.0              # partite (mazzi) nel filtro


def latest_snapshot(con: sqlite3.Connection, table: str, set_code: str, fmt: str) -> int | None:
    r = con.execute(f"SELECT MAX(snapshot_id) FROM {table} WHERE set_code=? AND format=?", (set_code, fmt)).fetchone()
    return r[0]


def _load(con: sqlite3.Connection, table: str, sid: int | None, f: Filters) -> pd.DataFrame:
    if sid is None:
        return pd.DataFrame()
    sql = f"SELECT * FROM {table} WHERE snapshot_id=?"
    args: list[object] = [sid]
    if f.as_of:
        sql += " AND day <= ?"
        args.append(f.as_of)
    if f.since:
        sql += " AND day >= ?"
        args.append(f.since)
    if f.tiers:
        sql += f" AND tier IN ({','.join('?' * len(f.tiers))})"
        args += list(f.tiers)
    if table != "stat_picks" and f.norm_colors():
        sql += " AND colors = ?"
        args.append(f.norm_colors())
    df = pd.read_sql_query(sql, con, params=args)
    if f.half_life and len(df):
        ref = pd.Timestamp(f.as_of) if f.as_of else pd.to_datetime(df["day"]).max()
        age = (ref - pd.to_datetime(df["day"])).dt.days.clip(lower=0)
        wt = np.power(0.5, age / f.half_life)
        num = [c for c in df.columns if c.startswith(("n_", "w_", "sum_")) or c in ("n", "w")]
        df[num] = df[num].mul(wt, axis=0)
    return df


def card_meta(con: sqlite3.Connection, set_code: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT name AS card, rarity, cost_colors, mv, type_line, is_land, is_creature FROM cards WHERE set_code=?",
        con, params=[set_code])


def is_basic(type_line: pd.Series) -> pd.Series:
    return type_line.fillna("").str.contains("Basic Land")


def raw_table(con: sqlite3.Connection, set_code: str, fmt_name: str, f: Filters = Filters(), *,
              game_snapshot: int | None = None, pick_snapshot: int | None = None) -> Table:
    """Metriche osservate, senza prior. Le terre base sono escluse."""
    fmt = load_format(fmt_name)
    gs = game_snapshot or latest_snapshot(con, "stat_games", set_code, fmt_name)
    ps = pick_snapshot or latest_snapshot(con, "stat_picks", set_code, fmt_name)
    games = _load(con, "stat_games", gs, f)
    decks = _load(con, "stat_decks", gs, f)
    picks = _load(con, "stat_picks", ps, f)
    meta = card_meta(con, set_code)
    out = meta[~is_basic(meta["type_line"])][["card", "rarity", "cost_colors", "mv", "type_line"]].copy()

    if len(games):
        g = games.groupby("card")[["n_deck", "w_deck", "n_gih", "w_gih", "n_oh", "w_oh"]].sum()
        out = out.merge(g, left_on="card", right_index=True, how="left")
    else:
        for c in ("n_deck", "w_deck", "n_gih", "w_gih", "n_oh", "w_oh"):
            out[c] = 0.0
    if len(picks):
        p = picks.groupby("card")[["n_seen", "n_taken", "sum_taken_at", "n_last", "sum_last_seen"]].sum()
        out = out.merge(p, left_on="card", right_index=True, how="left")
    else:
        for c in ("n_seen", "n_taken", "sum_taken_at", "n_last", "sum_last_seen"):
            out[c] = 0.0
    out = out.fillna({c: 0 for c in out.columns if c.startswith(("n_", "w_", "sum_"))})

    def ratio(a: pd.Series, b: pd.Series) -> pd.Series:
        return (a / b.where(b > 0)).astype(float)

    out["gih_wr"] = ratio(out["w_gih"], out["n_gih"])
    out["oh_wr"] = ratio(out["w_oh"], out["n_oh"])
    out["gd_wr"] = ratio(out["w_gih"] - out["w_oh"], out["n_gih"] - out["n_oh"])
    out["gp_wr"] = ratio(out["w_deck"], out["n_deck"])
    out["gns_wr"] = ratio(out["w_deck"] - out["w_gih"], out["n_deck"] - out["n_gih"])
    out["iwd"] = out["gih_wr"] - out["gns_wr"]
    out["ata"] = ratio(out["sum_taken_at"], out["n_taken"])
    out["alsa"] = ratio(out["sum_last_seen"], out["n_last"])
    out["taken_rate"] = ratio(out["n_taken"], out["n_seen"])
    out["ata_norm"] = (out["ata"] - 1) / (fmt.picks_per_pack - 1)
    out["alsa_norm"] = (out["alsa"] - 1) / (fmt.picks_per_pack - 1)

    base_deck = float(decks["w"].sum() / decks["n"].sum()) if len(decks) and decks["n"].sum() > 0 else None
    base_gih = float(out["w_gih"].sum() / out["n_gih"].sum()) if out["n_gih"].sum() > 0 else None
    days = pd.concat([games.get("day", pd.Series(dtype=str)), picks.get("day", pd.Series(dtype=str))])
    return Table(out.reset_index(drop=True), base_deck, base_gih, None, "",
                 {"game": gs, "draft": ps}, (days.min() if len(days) else None, days.max() if len(days) else None),
                 float(decks["n"].sum()) if len(decks) else 0.0)


def estimate_tau2(n: np.ndarray, w: np.ndarray, mu: np.ndarray, min_n: float = MIN_N_TAU) -> tuple[float | None, int]:
    """Varianza reale delle carte attorno al prior: varianza dei residui meno il rumore binomiale."""
    ok = n >= min_n
    if ok.sum() < MIN_CARDS_TAU:
        return None, int(ok.sum())
    p = w[ok] / n[ok]
    r2 = (p - mu[ok]) ** 2
    noise = p * (1 - p) / n[ok]
    return float(max(1e-5, np.mean(r2 - noise))), int(ok.sum())


def blend(n: np.ndarray, w: np.ndarray, mu: np.ndarray, tau2: float, p0: float) -> tuple[np.ndarray, np.ndarray, float]:
    m = p0 * (1 - p0) / tau2
    post = (w + m * mu) / (n + m)
    sd = np.sqrt(post * (1 - post) / (n + m))
    return post, sd, m


def ratings(con: sqlite3.Connection, set_code: str, fmt_name: str, f: Filters = Filters(), *,
            prior=None, game_snapshot: int | None = None, pick_snapshot: int | None = None) -> Table:
    """Tabella completa: metriche osservate, prior e GIH WR bayesiana (``gih_bayes`` +- ``gih_sd``).

    ``prior``: un ``PriorModel`` (vedi prior.py) o None (si restringe verso la media del set).
    """
    t = raw_table(con, set_code, fmt_name, f, game_snapshot=game_snapshot, pick_snapshot=pick_snapshot)
    c = t.cards
    p0 = t.baseline_gih
    if p0 is None:
        p0 = prior.baseline_gih if prior is not None else None
    if p0 is None:
        c["prior_wr"] = np.nan
        c["gih_bayes"] = np.nan
        c["gih_sd"] = np.nan
        c["gih_sd_next"] = np.nan
        t.tau2_source = "nessun dato di gioco e nessun prior"
        return t
    if f.norm_colors():
        # forza per archetipo: si parte dalla stima complessiva della carta, spostata della
        # differenza tra la GIH WR media dell'archetipo e quella complessiva
        from dataclasses import replace
        overall = ratings(con, set_code, fmt_name, replace(f, colors=None), prior=prior,
                          game_snapshot=game_snapshot, pick_snapshot=pick_snapshot)
        o = overall.cards.set_index("card")
        c["prior_wr"] = c["card"].map(o["gih_bayes"]).to_numpy() + (p0 - (overall.baseline_gih or p0))
        c["prior_src"] = "carta, tutti i mazzi"
    elif prior is not None:
        c["prior_wr"] = p0 + prior.predict_delta(c)
        c["prior_src"] = "modello"
    else:
        c["prior_wr"] = p0
        c["prior_src"] = "media"
    n, w, mu = c["n_gih"].to_numpy(float), c["w_gih"].to_numpy(float), c["prior_wr"].to_numpy(float)
    tau2, k = estimate_tau2(n, w, mu)
    if tau2 is not None:
        t.tau2_source = f"dati del set ({k} carte con >= {MIN_N_TAU} partite)"
    elif prior is not None:
        tau2 = prior.tau2
        t.tau2_source = "modello dei prior (dati del set insufficienti)"
    else:
        tau2 = 0.03 ** 2
        t.tau2_source = "valore di default (dati insufficienti, nessun prior)"
    post, sd, m = blend(n, w, mu, tau2, p0)
    c["gih_bayes"], c["gih_sd"] = post, sd
    # incertezza sulla win rate dei prossimi giorni: stima + deriva del formato (dal file dei prior)
    drift = prior.drift_sd if prior is not None else 0.0
    c["gih_sd_next"] = np.sqrt(sd ** 2 + drift ** 2)
    c["shrink_weight"] = n / (n + m)          # quanto pesa il dato osservato (0 = solo prior)
    t.tau2 = tau2
    t.baseline_gih = p0
    return t
