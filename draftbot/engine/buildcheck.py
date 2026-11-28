"""Verifica del costruttore di mazzi (Fase 6) sui mazzi reali 17Lands, senza dati futuri.

Modelli stimati fino al giorno di taglio; verifica sui draft successivi dei giocatori forti, unendo
il pool finale (draft data) al mazzo giocato (game data, prima build) con il ``draft_id``.

Misure:
- accordo con il giocatore: stessi colori principali, stessa scelta di splash, quota delle magie
  della nostra build presenti nel suo mazzo, stesso numero di terre;
- differenza di punteggio tra la nostra build e il mazzo reale, con lo stesso valutatore (a favore
  della nostra build per costruzione: e' una verifica di coerenza, non di qualita');
- test sui risultati: win rate reale (al netto del giocatore) meno la win rate prevista per la NOSTRA
  build, separando chi ha giocato gli stessi colori da chi ha scelto diversamente. Se il costruttore
  sceglie bene, chi ha scelto diversamente rende meno della nostra previsione.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import load_format
from .backtest import _cards, read_drafts
from .book import CardBook
from .build import Builder
from .deckmodel import CardArrays, DeckModel, deck_matrix_features, load_decks


@dataclass
class BuildCheck:
    n: int
    same_main: float
    same_splash: float
    spell_overlap: float
    same_lands: float
    gap_mean: float                 # nostra build - reale (punti, frazione)
    regret_same: tuple[float, float, int]     # media, errore standard, n
    regret_diff: tuple[float, float, int]
    our_splash_rate: float
    real_splash_rate: float
    our_three_color_rate: float
    real_three_color_rate: float


def _norm(s) -> str:
    s = s if isinstance(s, str) else ""      # NaN nei game data = nessun colore
    return "".join(c for c in "WUBRG" if c in s)


def check_builds(model: DeckModel, book: CardBook, set_code: str, fmt: str, since: str, n_drafts: int = 300,
                 synergy=None) -> BuildCheck:
    deck, info = load_decks(set_code, fmt)
    ids = info.index.get_level_values(0)
    builds = info.index.get_level_values(1).astype(str)
    first = {did: i for i, (did, b) in enumerate(zip(ids, builds)) if b == "0"}
    drafts = [d for d in read_drafts(set_code, fmt, since=since, tiers=("high",), max_drafts=n_drafts * 2, seed=5)
              if d.draft_id in first][:n_drafts]
    ca = CardArrays.from_book(list(deck.columns), book)
    builder = Builder(book, model, synergy)
    rows = []
    for d in drafts:
        i = first[d.draft_id]
        row = info.iloc[i]
        ours = builder.best_builds(_cards(book, d.final_pool()), k=1)
        if not ours:
            continue
        b = ours[0]
        real_counts = deck.iloc[i]
        F = deck_matrix_features(real_counts.to_numpy()[None, :], ca, pd.Series([row["colors"]]),
                                 pd.Series([row["splash"]]), book).iloc[0].to_dict()
        real_score = model.score(F)
        real_spells = {n for n, k in real_counts.items() if k > 0 and book.get(n) and not book.get(n).is_land}
        real_lands = int(sum(k for n, k in real_counts.items() if k > 0 and book.get(n) and book.get(n).is_land))
        our_main = _norm(b.main)
        real_main = _norm(row["colors"])
        player = row["player"] if row["player"] == row["player"] else 0.55
        actual = row["wins"] / row["games"] - model.control_coef * (player - builder.player_wr)
        rows.append(dict(
            same_main=our_main == real_main, same_splash=bool(b.splash) == bool(_norm(row["splash"])),
            overlap=np.mean([c.name in real_spells for c in b.spells]), same_lands=b.n_lands == real_lands,
            gap=b.score - real_score, regret=actual - b.expected_wr, games=row["games"],
            our_splash=bool(b.splash), real_splash=bool(_norm(row["splash"])),
            our3=len(our_main) >= 3, real3=len(real_main) >= 3))
    df = pd.DataFrame(rows)

    def grp(mask):
        g = df[mask]
        if not len(g):
            return (float("nan"), float("nan"), 0)
        w = g["games"].to_numpy(float)
        m = float(np.average(g["regret"], weights=w))
        se = float(np.sqrt(0.25 / w.sum()))
        return (m, se, len(g))

    return BuildCheck(len(df), df["same_main"].mean(), df["same_splash"].mean(), df["overlap"].mean(),
                      df["same_lands"].mean(), df["gap"].mean(), grp(df["same_main"]), grp(~df["same_main"]),
                      df["our_splash"].mean(), df["real_splash"].mean(), df["our3"].mean(), df["real3"].mean())


def run(con: sqlite3.Connection, set_code: str, fmt: str, cutoff: str, prior_sets: list[str], n_drafts: int = 300,
        log=print) -> BuildCheck:
    from datetime import date, timedelta

    from ..stats.prior import fit, load_prior, tables_for
    from .book import load_book
    from .deckmodel import fit_deck_model

    saved = load_prior(fmt)
    prior = fit(tables_for(con, prior_sets, fmt), fmt)
    prior.half_life = saved.half_life if saved else None
    book = load_book(con, set_code, fmt, prior=prior, as_of=cutoff, half_life=prior.half_life)
    model = fit_deck_model(con, {set_code: book}, fmt, as_of=cutoff)
    since = (date.fromisoformat(cutoff) + timedelta(days=1)).isoformat()
    log(f"modelli fino al {cutoff}; verifica sui draft dal {since}")
    load_format(fmt)
    return check_builds(model, book, set_code, fmt, since, n_drafts)


def format_check(set_code: str, fmt: str, cutoff: str, c: BuildCheck) -> str:
    rs, rd = c.regret_same, c.regret_diff
    return "\n".join([
        f"# Verifica del costruttore di mazzi {set_code} {fmt} (taglio {cutoff})", "",
        f"draft: {c.n} (giocatori fascia high, pool finale + prima build giocata)",
        f"stessi colori principali: {c.same_main:.0%}; stessa scelta di splash: {c.same_splash:.0%}; "
        f"magie della nostra build nel mazzo reale: {c.spell_overlap:.0%}; stesse terre: {c.same_lands:.0%}",
        f"splash: nostro {c.our_splash_rate:.0%}, reale {c.real_splash_rate:.0%}; tre colori principali: "
        f"nostro {c.our_three_color_rate:.0%}, reale {c.real_three_color_rate:.0%}",
        f"punteggio nostra build - mazzo reale: {c.gap_mean * 100:+.2f} punti (a favore della nostra per costruzione)",
        "",
        "Test sui risultati: win rate reale (al netto del giocatore) - win rate prevista per la nostra build",
        f"  stessi colori della nostra build: {rs[0] * 100:+.2f} +- {rs[1] * 100:.2f} punti (n {rs[2]})",
        f"  colori diversi:                    {rd[0] * 100:+.2f} +- {rd[1] * 100:.2f} punti (n {rd[2]})",
        "  (se il costruttore sceglie bene, il secondo valore e' piu' basso del primo)",
    ])
