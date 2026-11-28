"""Test offline della Fase 7: wheel, segnali, lettura live del log."""

import numpy as np

from draftbot.arenalog.synth import simulate
from draftbot.config import load_format
from draftbot.engine.book import PAIRS, Card, CardBook
from draftbot.engine.build import Builder
from draftbot.engine.deckmodel import DECK_SLOTS, DeckModel
from draftbot.engine.projection import ArchetypeModel, Projector
from draftbot.engine.signals import SignalBaseline, SignalState, availability
from draftbot.engine.wheel import LogitWheel, fit_logit_wheel, survival
from draftbot.live import Engine, LiveSession, LogTailer


def card(name, q, colors):
    return Card(name, q, 0.0, colors, 2, False, False, True, "common", colors)


def test_survival_decreases_with_weight_and_passes():
    w = np.array([10.0, 1.0, 1.0, 1.0, 1.0])
    s = survival(w, passes=3, per_pick=1)
    assert s[0] < s[1]
    assert np.all(survival(w, 7, 1) <= s)


def test_logit_wheel_learns_popularity():
    fmt = load_format("premier")
    rng = np.random.default_rng(0)
    cards = [card(f"C{i}", 0.0, "W") for i in range(40)]
    pop = {c.name: float(i) / 40 for i, c in enumerate(cards)}
    samples = []
    for _ in range(300):
        rem = list(rng.choice(cards, 10, replace=False))
        # le 7 piu' popolari vengono prese, le altre tornano
        order = np.argsort([-pop[c.name] for c in rem])
        y = np.ones(10)
        y[order[:7]] = 0
        samples.append((rem, y))
    m = fit_logit_wheel(samples, fmt, pop)
    s = m.survival(samples[0][0], fmt, pop)
    y = samples[0][1]
    assert s[y == 1].min() > s[y == 0].max()


def test_signals_accumulate_by_direction():
    fmt = load_format("premier")
    base = SignalBaseline({(0, 1): dict.fromkeys("WUBRG", 0.0), (1, 1): dict.fromkeys("WUBRG", 0.0)})
    st = SignalState()
    st.observe(0, 0, [card("X", 0.05, "W")], fmt, base)          # primo pick: ignorato
    assert st.n["left"] == 0
    st.observe(0, 1, [card("A", 0.05, "W"), card("B", -0.02, "U")], fmt, base)
    st.observe(1, 1, [card("C", 0.04, "R")], fmt, base)
    assert st.by_dir["left"]["W"] == 0.05 and st.by_dir["left"]["U"] == 0.0
    assert st.by_dir["right"]["R"] == 0.04
    same, other = st.pair_signal("left")
    assert same["WU"] == 0.05 and other["BR"] == 0.04
    assert availability([card("A", 0.05, "W"), card("B", 0.03, "W"), card("C", 0.02, "W")])["W"] == 0.08


def test_tailer_handles_partial_lines_and_rotation(tmp_path):
    p = tmp_path / "Player.log"
    p.write_text("riga 1\nriga 2 incompl")
    t = LogTailer(p)
    assert t.read_new() == ["riga 1"]
    with p.open("a") as fh:
        fh.write("eta\nriga 3\n")
    assert t.read_new() == ["riga 2 incompleta", "riga 3"]
    p.write_text("nuova sessione\n")              # file ricreato e piu' corto
    assert t.read_new() == ["nuova sessione"]


def _fake_engine(ids):
    cards = {f"N{g}": card(f"N{g}", 0.01 * ((g * 7) % 9 - 4), "WUBRG"[g % 5]) for g in ids}
    for n in ("Plains", "Island", "Swamp", "Mountain", "Forest"):
        cards[n] = Card(n, 0.0, 0.0, "", 0, True, True, False, "common")
    book = CardBook("TRK", "premier", cards, 0.56, {p: 0.0 for p in PAIRS}, {p: 0.1 for p in PAIRS})
    model = DeckModel("premier", 0.2, {"mean_q": 1.5, "top3": 1.4, "arch": 0.0, "n_le2": 0, "n_ge5": 0,
                                       "lands": -0.009, "splash": -0.03, "extra_colors": -0.035},
                      0.95, list(np.linspace(0.06, -0.01, DECK_SLOTS)), 0.55, ["X"], 0, None, "x")
    proj = Projector(book, model, load_format("premier"), ArchetypeModel([200.0] * 10))
    eng = Engine("TRK", "premier", proj, Builder(book, model), {g: f"N{g}" for g in ids})
    eng.wheel = LogitWheel([-1.0, 1.0, 0.0, -1.0, 2.0])
    return eng


def test_live_session_emits_pick_and_build_events():
    ids = list(range(1000, 1060))
    sd = simulate(load_format("premier"), ids, event_name="PremierDraft_TRK_20261113", seed=3,
                  with_personal_data=False)
    sess = LiveSession()
    sess.engines[("TRK", "premier")] = _fake_engine(ids)
    events = []
    sess.on_event(events.append)
    # riga per riga, come in diretta: un evento per ogni busta prima del pick
    for ln in sd.lines:
        sess.feed([ln])
    picks = [e for e in events if e["type"] == "pick"]
    builds = [e for e in events if e["type"] == "build"]
    assert len(picks) == 42
    assert picks[0]["pack"] == 1 and picks[0]["pick"] == 1 and picks[0]["pool_size"] == 0
    assert picks[-1]["pool_size"] == 41
    assert all(len(e["options"]) >= 1 for e in picks)
    assert picks[0]["wheel_possible"] and not picks[10]["wheel_possible"]
    assert len(builds) == 1
