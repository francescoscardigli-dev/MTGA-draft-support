"""Test offline della Fase 3: aggregazione, metriche, blend bayesiano, prior."""

import gzip
import sqlite3

import numpy as np
import pandas as pd
import pytest

from draftbot import db
from draftbot.config import load_format
from draftbot.stats import ingest as ing
from draftbot.stats.prior import fit, load_prior
from draftbot.stats.ratings import Filters, blend, estimate_tau2, ratings, raw_table


def _write_gz(path, df):
    with gzip.open(path, "wt", newline="") as f:
        df.to_csv(f, index=False)


# ------------------------------------------------------------------ aggregazione game data

def test_aggregate_games_counts(tmp_path):
    rows = [
        # carta A in mano iniziale (2 copie: conta una partita), B nel mazzo non vista
        dict(game_time="2026-10-01 10:00:00", main_colors="UW", won=True, user_game_win_rate_bucket=0.62,
             deck_A=2, deck_B=1, opening_hand_A=1, opening_hand_B=0, drawn_A=0, drawn_B=0),
        # A pescata, B pescata, sconfitta
        dict(game_time="2026-10-01 11:00:00", main_colors="WU", won=False, user_game_win_rate_bucket=0.40,
             deck_A=1, deck_B=1, opening_hand_A=0, opening_hand_B=0, drawn_A=1, drawn_B=1),
        # solo B, in mano iniziale, vittoria, giorno dopo
        dict(game_time="2026-10-02 09:00:00", main_colors="BR", won=True, user_game_win_rate_bucket=0.55,
             deck_A=0, deck_B=1, opening_hand_A=0, opening_hand_B=1, drawn_A=0, drawn_B=0),
    ]
    p = tmp_path / "g.csv.gz"
    _write_gz(p, pd.DataFrame(rows))
    by_card, by_deck, n = ing.aggregate_games(p, chunksize=2)   # chunk piccoli: somma tra chunk
    assert n == 3
    tot = by_card.groupby("card")[["n_deck", "w_deck", "n_gih", "w_gih", "n_oh", "w_oh"]].sum()
    assert tot.loc["A"].tolist() == [2, 1, 2, 1, 1, 1]
    assert tot.loc["B"].tolist() == [3, 2, 2, 1, 1, 1]
    assert set(by_deck["colors"]) == {"WU", "BR"}          # colori normalizzati
    assert set(by_deck["tier"]) == {"high", "low", "mid"}
    assert by_deck["n"].sum() == 3 and by_deck["w"].sum() == 2


# ------------------------------------------------------------------ aggregazione draft data (ATA, ALSA)

def test_aggregate_drafts_ata_alsa(tmp_path):
    fmt = load_format("pick_two")      # tavolo da 4, 2 carte per pick, 7 turni per busta
    cards = ["A", "B", "C"]
    rows = []
    # un draft, busta 0: turno 0 vede A,B,C e prende A+B; turno 4 (stessa busta) vede C ancora
    for turn, pack, picks in [(0, {"A": 1, "B": 1, "C": 1}, ("A", "B")), (1, {"C": 1}, ("C", "")),
                              (4, {"C": 1}, ("C", ""))]:
        r = dict(draft_id="12e34", draft_time="2026-10-01 10:00:00", pack_number=0, pick_number=turn,
                 pick=picks[0], pick_2=picks[1], user_game_win_rate_bucket=0.6)
        r.update({f"pack_card_{c}": pack.get(c, 0) for c in cards})
        rows.append(r)
    p = tmp_path / "d.csv.gz"
    _write_gz(p, pd.DataFrame(rows))
    out, n, skipped = ing.aggregate_drafts(p, fmt)
    t = out.groupby("card").sum(numeric_only=True)
    assert n == 3
    assert t.loc["A", "n_taken"] == 1 and t.loc["A", "sum_taken_at"] == 1      # ATA 1
    assert t.loc["C", "n_taken"] == 2 and t.loc["C", "sum_taken_at"] == 2 + 5  # turni 2 e 5
    # A: vista al turno 1, assente al turno 5 -> ultima volta 1
    assert t.loc["A", "n_last"] == 1 and t.loc["A", "sum_last_seen"] == 1
    # C al turno 1 (riga senza busta di ritorno al turno 5 -> esclusa), al turno 5 -> ultima 5
    assert skipped == 1
    assert t.loc["C", "n_last"] == 1 and t.loc["C", "sum_last_seen"] == 5
    # C al turno 0 e' ancora presente al turno 4: non e' "ultima volta"


# ------------------------------------------------------------------ blend bayesiano

def test_blend_limits():
    mu, p0, tau2 = np.array([0.60]), 0.55, 0.0004
    post0, _, m = blend(np.array([0.0]), np.array([0.0]), mu, tau2, p0)
    assert post0[0] == pytest.approx(0.60)                   # nessun dato: solo prior
    big, sd, _ = blend(np.array([1e6]), np.array([0.5e6]), mu, tau2, p0)
    assert big[0] == pytest.approx(0.50, abs=1e-3) and sd[0] < 0.001   # tanti dati: il dato
    assert m == pytest.approx(p0 * (1 - p0) / tau2)


def test_estimate_tau2_recovers_true_spread():
    rng = np.random.default_rng(0)
    true = 0.55 + rng.normal(0, 0.03, 400)
    n = rng.integers(300, 3000, 400).astype(float)
    w = rng.binomial(n.astype(int), true).astype(float)
    tau2, k = estimate_tau2(n, w, np.full(400, 0.55))
    assert k == 400
    assert np.sqrt(tau2) == pytest.approx(0.03, rel=0.15)


# ------------------------------------------------------------------ tabella, prior, ratings su database sintetico

def _fake_set(con, code, rng, n_cards=120, games_scale=1.0, slope=-0.06):
    """Carte la cui GIH WR vera dipende dall'ATA (piu' presa presto = piu' forte)."""
    rar = rng.choice(["common", "uncommon", "rare", "mythic"], n_cards, p=[.55, .3, .12, .03])
    ata = np.clip(rng.uniform(1, 7, n_cards) - 1.5 * (rar == "rare") - 2 * (rar == "mythic"), 1, 7)
    true = 0.56 + slope * ((ata - 1) / 6 - 0.5) + rng.normal(0, 0.015, n_cards)
    for i in range(n_cards):
        con.execute("INSERT INTO cards(set_code, name, norm_name, rarity, type_line, mv, cost_colors) "
                    "VALUES (?,?,?,?,?,?,?)", (code, f"{code}{i}", f"{code.lower()}{i}", rar[i], "Creature", 2, "W"))
    sid_g = con.execute("INSERT INTO snapshots(set_code, source, kind, fetched_at) VALUES (?,?,?,?)",
                        (code, "17lands", "game_stats", "x")).lastrowid
    sid_d = con.execute("INSERT INTO snapshots(set_code, source, kind, fetched_at) VALUES (?,?,?,?)",
                        (code, "17lands", "draft_stats", "x")).lastrowid
    for day in ("2026-10-01", "2026-10-02"):
        for i in range(n_cards):
            n = int(rng.integers(200, 2000) * games_scale)
            wins = int(rng.binomial(n, true[i]))
            con.execute("INSERT INTO stat_games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sid_g, code, "premier", "WU", day, "mid", f"{code}{i}", n * 2, wins * 2 - n // 10,
                         n, wins, n // 2, wins // 2))
            taken = 50
            con.execute("INSERT INTO stat_picks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (sid_d, code, "premier", day, "mid", f"{code}{i}", 100, taken,
                         int(round(ata[i] * taken)), taken, int(round(ata[i] * taken))))
        con.execute("INSERT INTO stat_decks VALUES (?,?,?,?,?,?,?,?)", (sid_g, code, "premier", "WU", day, "mid",
                                                                       10000, 5500))
    con.commit()
    return true


def test_prior_fit_and_ratings(tmp_path):
    con = db.connect()
    rng = np.random.default_rng(1)
    _fake_set(con, "AAA", rng)
    _fake_set(con, "BBB", rng)
    true_c = _fake_set(con, "CCC", rng, games_scale=0.02)    # pochissimi dati: il prior conta
    tables = {s: raw_table(con, s, "premier") for s in ("AAA", "BBB")}
    m = fit(tables, "premier")
    assert m.train_sets == ["AAA", "BBB"]
    assert m.coef["ata_norm"] < 0                               # presa tardi = piu' debole
    assert np.sqrt(m.tau2) < 0.03
    m.save(tmp_path / "p.yaml")
    m2 = load_prior("premier", tmp_path / "p.yaml")
    assert m2.coef == m.coef

    t_prior = ratings(con, "CCC", "premier", prior=m2)
    t_flat = ratings(con, "CCC", "premier")
    c, cf = t_prior.cards.set_index("card"), t_flat.cards.set_index("card")
    names = [f"CCC{i}" for i in range(len(true_c))]
    err = lambda col, df: float(np.sqrt(np.mean((df.loc[names, col].to_numpy() - true_c) ** 2)))
    assert err("gih_bayes", c) < err("gih_wr", c)               # il blend batte il dato grezzo
    assert err("gih_bayes", c) < err("gih_bayes", cf)           # e il prior batte la media
    assert ((c["shrink_weight"] > 0) & (c["shrink_weight"] < 1)).all()


def test_ratings_without_game_data_uses_prior_only(tmp_path):
    con = db.connect()
    rng = np.random.default_rng(2)
    _fake_set(con, "AAA", rng)
    m = fit({"AAA": raw_table(con, "AAA", "premier")}, "premier")
    _fake_set(con, "DDD", rng)
    con.execute("DELETE FROM stat_games WHERE set_code='DDD'")
    con.execute("DELETE FROM stat_decks WHERE set_code='DDD'")
    t = ratings(con, "DDD", "premier", prior=m)
    assert t.n_games == 0
    assert np.allclose(t.cards["gih_bayes"], t.cards["prior_wr"])
    assert "modello dei prior" in t.tau2_source


def test_filters_as_of_and_decay():
    con = db.connect()
    _fake_set(con, "AAA", np.random.default_rng(3))
    full = raw_table(con, "AAA", "premier")
    first = raw_table(con, "AAA", "premier", Filters(as_of="2026-10-01"))
    assert first.n_games == full.n_games / 2
    assert first.days == ("2026-10-01", "2026-10-01")
    dec = raw_table(con, "AAA", "premier", Filters(half_life=1))
    assert dec.n_games == pytest.approx(10000 * 0.5 + 10000)    # il giorno prima pesa meta'
    assert raw_table(con, "AAA", "premier", Filters(colors="UW")).n_games == full.n_games
    assert raw_table(con, "AAA", "premier", Filters(colors="BR")).n_games == 0


def test_archetype_ratings_start_from_overall_estimate():
    con = db.connect()
    _fake_set(con, "AAA", np.random.default_rng(4))
    overall = ratings(con, "AAA", "premier").cards.set_index("card")
    arch = ratings(con, "AAA", "premier", Filters(colors="UW")).cards.set_index("card")
    assert (arch["prior_src"] == "carta, tutti i mazzi").all()
    # nel set finto tutti i mazzi sono WU: la stima per archetipo coincide con quella complessiva
    assert np.allclose(arch["gih_bayes"], overall["gih_bayes"], atol=1e-3)
