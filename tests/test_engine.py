"""Test offline del motore (Fase 4) con un catalogo di carte finto."""

import numpy as np
import pytest

from draftbot.config import load_format
from draftbot.engine.backtest import propose_deck
from draftbot.engine.book import PAIRS, Card, CardBook
from draftbot.engine.deckmodel import DECK_SLOTS, DeckModel
from draftbot.engine.pick import recommend
from draftbot.engine.projection import ArchetypeModel, Projector, fit_lambda


def card(name, q, colors, mv=2, creature=True):
    return Card(name, q, 0.01, colors, mv, False, False, creature, "common")


def make_book(cards):
    return CardBook("XXX", "premier", {c.name: c for c in cards}, 0.56,
                    arch_delta={p: 0.0 for p in PAIRS}, arch_share={p: 0.1 for p in PAIRS})


def make_model():
    profile = list(np.linspace(0.06, -0.01, DECK_SLOTS))
    return DeckModel("premier", 0.0, {"mean_q": 1.3, "top3": 1.4, "arch": 0.5, "n_le2": 0, "n_ge5": 0,
                                       "lands": 0, "splash": -0.03, "extra_colors": -0.03},
                     0.95, profile, 0.55, ["XXX"], 0, None, "2026-11-27")


def projector(cards, fmt="premier", lambdas=None):
    return Projector(make_book(cards), make_model(), load_format(fmt),
                     ArchetypeModel(lambdas or [100.0] * 10))


def test_empty_pool_prefers_stronger_card():
    a, b = card("Bomb", 0.08, "R"), card("Filler", 0.0, "R")
    opts, probs = recommend(projector([a, b]), [], [b, a], k=2)
    assert opts[0].names == ("Bomb",)
    assert opts[0].value > opts[1].value
    assert sum(probs.values()) == pytest.approx(1.0)


def test_flexible_mono_color_beats_equal_gold_card_early():
    mono, gold = card("Mono", 0.03, "W"), card("Gold", 0.03, "WU")
    opts, _ = recommend(projector([mono, gold]), [], [gold, mono], k=2)
    assert opts[0].names == ("Mono",)       # giocabile in 4 archetipi su 10


def test_committed_pool_prefers_on_color_card_late():
    pool = [card(f"WU{i}", 0.02, "W" if i % 2 else "U") for i in range(14)]
    on, off = card("OnColor", 0.02, "U"), card("OffColor", 0.035, "R")
    p = projector(pool + [on, off], lambdas=[800.0] * 10)   # giocatore molto impegnato sui colori
    opts, probs = recommend(p, pool, [off, on], k=2)
    assert max(probs, key=probs.get) == "WU"
    assert opts[0].names == ("OnColor",)


def test_card_below_replacement_is_worth_little():
    pool = [card(f"G{i}", 0.05, "G") for i in range(23)]       # 23 carte forti: il mazzo e' pieno
    weak = card("Weak", -0.02, "G")
    opts, _ = recommend(projector(pool + [weak]), pool, [weak], k=1)
    assert abs(opts[0].value) < 1e-9


def test_pick_two_evaluates_pairs():
    cs = [card("A", 0.05, "W"), card("B", 0.04, "W"), card("C", 0.045, "B"), card("D", -0.01, "G")]
    p = projector(cs, fmt="pick_two")
    opts, _ = recommend(p, [], cs, k=6)
    assert all(len(o.cards) == 2 for o in opts)
    assert len(opts) == 6                     # C(4,2) coppie
    assert set(opts[0].names) == {"A", "C"} or set(opts[0].names) == {"A", "B"}
    assert opts[-1].names.count("D") == 1


def test_future_values_shrink_as_draft_progresses():
    p = projector([card("A", 0.0, "W")])
    assert len(p.future_values(0)) == DECK_SLOTS
    assert len(p.future_values(30)) < len(p.future_values(10))
    assert len(p.future_values(42)) == 0


def test_fit_lambda_recovers_true_value():
    rng = np.random.default_rng(0)
    cards = [card(f"{c}{i}", rng.normal(0, 0.03), c) for c in "WUBRG" for i in range(40)]
    p = projector(cards, lambdas=[0.0] * 10)
    true_lam = 150.0
    samples = []
    for _ in range(600):
        pool = list(rng.choice(cards, 20, replace=False))
        v = p.arch_values(pool)
        z = np.array([true_lam * v[a] for a in PAIRS])
        pr = np.exp(z - z.max())
        pr /= pr.sum()
        samples.append((pool, PAIRS[rng.choice(len(PAIRS), p=pr)]))
    m = fit_lambda(p, samples, grid=np.linspace(0, 400, 81))
    fitted = m.lambdas[4]                     # pool di 20 carte su 42 -> quinto decimo
    assert 90 <= fitted <= 220


def test_propose_deck_picks_best_pair():
    cs = [card(f"W{i}", 0.03, "W") for i in range(15)] + [card(f"U{i}", 0.02, "U") for i in range(15)] + \
         [card(f"R{i}", -0.02, "R") for i in range(15)]
    book = make_book(cs)
    pair, counts = propose_deck(book, make_model(), cs)
    assert pair == "WU"
    assert sum(counts.values()) == DECK_SLOTS        # niente terre base nel catalogo finto


def test_bad_card_never_has_negative_value():
    # a fine draft il mazzo proiettato non e' pieno: una carta pessima non deve togliere valore
    pool = [card(f"W{i}", 0.01, "W") for i in range(10)]
    awful = card("Awful", -0.10, "W")
    p = projector(pool + [awful])
    opts, _ = recommend(p, pool + [card(f"X{i}", 0.0, "G") for i in range(30)], [awful], k=1)
    assert opts[0].deck_gain >= -1e-12


def test_deck_model_roundtrip_with_lambdas(tmp_path):
    from draftbot.engine.deckmodel import load_deck_model
    m = make_model()
    m.lambdas, m.tiebreak = [float(x) for x in np.linspace(100, 200, 10)], 0.0
    m.save(tmp_path / "d.yaml")
    assert load_deck_model("premier", tmp_path / "d.yaml").lambdas == m.lambdas


def test_mixture_caps_archetype_confidence():
    pool = [card(f"W{i}", 0.05, "W" if i % 2 else "U") for i in range(20)]
    p = projector(pool, lambdas=[2000.0] * 10)
    p.arch.mix = [0.2] * 10
    probs = p.probs(pool)
    assert max(probs.values()) <= 0.8 + 0.2 * 0.1 + 1e-9
    assert min(probs.values()) >= 0.2 * 0.1 - 1e-9
