"""Test offline della Fase 5: tag dei temi e sinergie nel motore."""

import numpy as np

from draftbot import db
from draftbot.engine.book import PAIRS, Card, CardBook
from draftbot.synergy.measure import SynergyModel, theme_features, theme_matrices
from draftbot.synergy.runtime import SynergyRuntime
from draftbot.synergy.tags import TagSet, generate


def _card_row(con, name, type_line, text, keywords="[]"):
    con.execute("INSERT INTO cards(set_code, name, norm_name, type_line, oracle_text, keywords, is_land) "
                "VALUES ('ZZZ', ?, ?, ?, ?, ?, 0)", (name, name.lower(), type_line, text, keywords))


def test_generate_finds_generic_and_set_themes():
    con = db.connect()
    for i in range(6):
        _card_row(con, f"Healer{i}", "Creature — Cleric", "When this creature enters, you gain 2 life.")
        _card_row(con, f"Zap{i}", "Instant", "Zap deals 2 damage to any target.")
        _card_row(con, f"Bot{i}", "Artifact Creature — Robot", "Gizmo (It does a thing.)", '["Gizmo"]')
    _card_row(con, "Angel", "Creature — Angel", "Whenever you gain life, put a +1/+1 counter on target creature.")
    _card_row(con, "Mage", "Creature — Wizard", "Whenever you cast an instant or sorcery spell, scry 1.")
    _card_row(con, "Boss", "Creature — Robot", "Other Robots you control get +1/+1.")
    _card_row(con, "Forge", "Artifact", "Robot spells you cast cost {1} less to cast.")   # 2 payoff: tema di tipo
    _card_row(con, "Fan", "Creature", "Whenever a creature with gizmo attacks, draw a card.")
    ts = generate(con, "ZZZ")
    assert "Angel" in ts.themes["lifegain"]["payoffs"] and "Healer0" in ts.themes["lifegain"]["enablers"]
    assert "Mage" in ts.themes["spells"]["payoffs"] and "Zap3" in ts.themes["spells"]["enablers"]
    assert "type:Robot" in ts.themes and "Boss" in ts.themes["type:Robot"]["payoffs"]
    assert "kw:Gizmo" in ts.themes and "Fan" in ts.themes["kw:Gizmo"]["payoffs"]
    # il testo di richiamo tra parentesi non conta
    assert "Bot0" not in ts.themes["kw:Gizmo"]["payoffs"]


def test_theme_features_interaction_and_density():
    tags = TagSet("ZZZ", {"lifegain": {"enablers": ["A", "B"], "payoffs": ["C"]},
                          "kw:Gizmo": {"enablers": ["D"], "payoffs": []}})
    names = ["A", "B", "C", "D"]
    mats = theme_matrices(names, tags)
    D = np.array([[2, 1, 1, 3]])
    f = theme_features(D, mats).iloc[0]
    assert f["lifegain|e"] == 3 and f["lifegain|p"] == 1 and f["lifegain|x"] == 0.3
    assert f["kw:Gizmo|x"] == 3 * 2 / 20


def _c(name, q=0.0, colors="W"):
    return Card(name, q, 0.0, colors, 2, False, False, True, "common")


def test_runtime_value_grows_with_enablers_and_uses_calibration():
    tags = TagSet("ZZZ", {"lifegain": {"enablers": [f"E{i}" for i in range(10)], "payoffs": ["P"]}})
    cards = {n: _c(n) for n in [f"E{i}" for i in range(10)] + ["P", "X"]}
    book = CardBook("ZZZ", "premier", cards, 0.56, {p: 0.0 for p in PAIRS}, {p: 0.1 for p in PAIRS})
    sm = SynergyModel("ZZZ", "premier", {"lifegain": 0.02}, [], None, 0.0, 0.0, "x", oos_slope=0.5)
    rt = SynergyRuntime.build(sm, tags, book)
    assert rt.coefs["lifegain"] == 0.01
    few = rt.value([cards["P"], cards["E0"]])
    many = rt.value([cards["P"]] + [cards[f"E{i}"] for i in range(6)])
    assert many > few > 0
    assert rt.value([cards["X"]]) == 0
    assert rt.roles("P") == ["lifegain (payoff)"]
    # pendenza fuori campione negativa: nessuna sinergia usata
    sm.oos_slope = -0.1
    assert SynergyRuntime.build(sm, tags, book) is None
