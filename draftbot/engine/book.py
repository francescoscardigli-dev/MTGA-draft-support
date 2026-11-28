"""Catalogo delle carte per il motore: qualita' (dalla Fase 3), colori, tipo, costo.

La qualita' ``q`` di una carta e' la sua GIH WR bayesiana meno la GIH WR media del set: e' in
"punti di win rate" e vale 0 per una carta nella media.
"""

from __future__ import annotations

import itertools
import sqlite3
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..stats.prior import PriorModel
from ..stats.ratings import Filters, ratings

COLORS = "WUBRG"
PAIRS = tuple("".join(p) for p in itertools.combinations(COLORS, 2))   # 10 archetipi a due colori


@dataclass(frozen=True)
class Card:
    name: str
    q: float                 # qualita' (punti di win rate sopra la media)
    q_sd: float              # incertezza della qualita'
    colors: str              # colori richiesti dal costo (per le terre: identita' di colore)
    mv: float
    is_land: bool
    is_basic: bool
    is_creature: bool
    rarity: str

    def castable_in(self, pair: str) -> bool:
        return all(c in pair for c in self.colors)


@dataclass
class CardBook:
    set_code: str
    format: str
    cards: dict[str, Card]
    baseline_gih: float
    arch_delta: dict[str, float] = field(default_factory=dict)   # win rate dell'archetipo - media
    arch_share: dict[str, float] = field(default_factory=dict)   # quota di mazzi per archetipo
    as_of: str | None = None

    def get(self, name: str) -> Card | None:
        return self.cards.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self.cards


def _arch_stats(con: sqlite3.Connection, set_code: str, fmt: str, f: Filters, shrink: float = 2000.0
                ) -> tuple[dict[str, float], dict[str, float]]:
    """Win rate (ristretta verso la media) e quota dei mazzi a due colori principali."""
    sql = ("SELECT colors, SUM(n), SUM(w) FROM stat_decks WHERE set_code=? AND format=? AND snapshot_id="
           "(SELECT MAX(snapshot_id) FROM stat_decks WHERE set_code=? AND format=?)")
    args: list[object] = [set_code, fmt, set_code, fmt]
    if f.as_of:
        sql += " AND day <= ?"
        args.append(f.as_of)
    rows = {r[0]: (r[1], r[2]) for r in con.execute(sql + " GROUP BY colors", args)}
    n_all = sum(n for n, _ in rows.values())
    if not n_all:
        return {p: 0.0 for p in PAIRS}, {p: 1 / len(PAIRS) for p in PAIRS}
    p0 = sum(w for _, w in rows.values()) / n_all
    delta, share = {}, {}
    n_pairs = sum(rows.get(p, (0, 0))[0] for p in PAIRS)
    for p in PAIRS:
        n, w = rows.get(p, (0, 0))
        delta[p] = (w + shrink * p0) / (n + shrink) - p0
        share[p] = (n + 50) / (n_pairs + 50 * len(PAIRS))
    return delta, share


def load_book(con: sqlite3.Connection, set_code: str, fmt: str, *, prior: PriorModel | None,
              as_of: str | None = None, half_life: float | None = None) -> CardBook:
    f = Filters(as_of=as_of, half_life=half_life)
    t = ratings(con, set_code, fmt, f, prior=prior)
    meta = pd.read_sql_query(
        "SELECT name AS card, cost_colors, color_identity, mv, is_land, is_creature, type_line, rarity "
        "FROM cards WHERE set_code=?", con, params=[set_code]).set_index("card")
    q = (t.cards.set_index("card")["gih_bayes"] - t.baseline_gih).to_dict()
    sd = t.cards.set_index("card")["gih_sd"].to_dict()
    cards = {}
    for name, m in meta.iterrows():
        basic = "Basic Land" in (m["type_line"] or "")
        land = bool(m["is_land"])
        cols = (m["color_identity"] if land else m["cost_colors"]) or ""
        qq = q.get(name, np.nan)
        cards[name] = Card(name, 0.0 if basic or qq != qq else float(qq),
                           0.0 if basic else float(sd.get(name, 0.0) or 0.0), "" if basic else cols,
                           float(m["mv"] or 0), land, basic, bool(m["is_creature"]), m["rarity"] or "")
    delta, share = _arch_stats(con, set_code, fmt, f)
    return CardBook(set_code, fmt, cards, t.baseline_gih or 0.0, delta, share, as_of)
