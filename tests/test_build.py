"""Test offline del costruttore di mazzi (Fase 6)."""

import numpy as np

from draftbot.engine.book import PAIRS, Card, CardBook
from draftbot.engine.build import Builder, land_count
from draftbot.engine.deckmodel import DECK_SLOTS, DeckModel


def card(name, q, colors, mv=2, pips=None, land=False, produces=""):
    return Card(name, q, 0.0, colors, mv, land, False, not land, "common", pips if pips is not None else colors,
                produces)


def model():
    return DeckModel("premier", 0.2, {"mean_q": 1.5, "top3": 1.4, "arch": 0.0, "n_le2": 0.0, "n_ge5": 0.0,
                                      "lands": -0.009, "splash": -0.03, "extra_colors": -0.035},
                     0.95, list(np.linspace(0.06, -0.01, DECK_SLOTS)), 0.55, ["X"], 0, None, "x")


def book(cards):
    basics = [Card(n, 0.0, 0.0, "", 0, True, True, False, "common") for n in
              ("Plains", "Island", "Swamp", "Mountain", "Forest")]
    return CardBook("X", "premier", {c.name: c for c in cards + basics}, 0.56,
                    {p: 0.0 for p in PAIRS}, {p: 0.1 for p in PAIRS})


def test_two_color_build_with_17_lands_and_proportional_basics():
    pool = [card(f"W{i}", 0.02, "W", mv=3, pips="WW") for i in range(14)] + \
           [card(f"U{i}", 0.01, "U", mv=3) for i in range(12)] + [card(f"R{i}", -0.03, "R") for i in range(10)]
    b = Builder(book(pool), model()).best_builds(pool, k=1)[0]
    assert b.main == "WU" and not b.splash
    assert len(b.spells) + b.n_lands == 40 and b.n_lands == 17
    assert b.basics["Plains"] > b.basics["Island"]        # piu' simboli bianchi


def test_splash_cards_and_enough_sources():
    pool = [card(f"W{i}", 0.01, "W", mv=3) for i in range(12)] + [card(f"U{i}", 0.01, "U", mv=3) for i in range(11)] + \
           [card("Bomb", 0.12, "R", mv=4, pips="R"), card("Heavy", 0.12, "R", mv=4, pips="RR")]
    builds = Builder(book(pool), model()).best_builds(pool, k=3)
    sp = [b for b in builds if b.splash == "R"]
    assert sp, "la build con splash della bomba deve comparire"
    names = {c.name for c in sp[0].spells}
    assert "Bomb" in names
    # le carte con due simboli sono ammesse (il costo e' nel valutatore) ma vogliono 4 fonti
    assert sp[0].basics.get("Mountain", 0) >= (4 if "Heavy" in names else 3)


def test_land_count_rule():
    low = [card(f"a{i}", 0, "W", mv=2) for i in range(24)]
    high = [card(f"b{i}", 0, "W", mv=4) for i in range(23)]
    assert land_count(low) == 16 and land_count(high) == 17


def test_not_enough_playables_returns_no_build():
    pool = [card(f"W{i}", 0.01, "W") for i in range(8)] + [card(f"U{i}", 0.01, "U") for i in range(5)]
    assert Builder(book(pool), model()).best_builds(pool) == []


def test_three_colors_need_enough_cards_per_color():
    pool = [card(f"W{i}", 0.02, "W") for i in range(12)] + [card(f"U{i}", 0.02, "U") for i in range(10)] + \
           [card("B1", 0.03, "B")]
    builds = Builder(book(pool), model()).best_builds(pool, k=5)
    assert all(len(b.main) == 2 for b in builds)       # WUB con una sola carta nera non e' a tre colori
