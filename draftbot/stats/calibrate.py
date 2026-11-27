"""Verifiche delle statistiche (calibrazione), da eseguire sui set con game data (es. FRA).

1. Prior, leave-one-set-out: si stima il prior senza il set S e lo si prova su S.
2. Divisione temporale: con i dati dei primi k giorni si prevede la GIH WR dei giorni successivi,
   confrontando dato grezzo, solo prior e blend bayesiano. Si misura anche se gli intervalli di
   incertezza sono tarati (copertura dell'intervallo all'80%).
3. Decadimento temporale: con i dati fino a un giorno, diverse emivite per prevedere il periodo dopo.

Errore "corretto per il rumore": sqrt(max(0, MSE - rumore medio del periodo di verifica)). Toglie la
parte di errore dovuta al fatto che anche il dato di verifica e' una stima con poche partite.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .prior import FEATURE_SETS, PriorModel, fit, tables_for
from .ratings import Filters, blend, estimate_tau2, raw_table

Z80 = 1.2816
MIN_N_EVAL = 300


@dataclass
class Score:
    label: str
    rmse: float          # corretto per il rumore, in punti percentuali
    corr: float
    n_cards: int
    coverage80: float | None = None

    def row(self) -> str:
        cov = f"{self.coverage80:5.0%}" if self.coverage80 is not None else "    -"
        return f"{self.label:<34} {self.rmse * 100:6.2f}  {self.corr:6.3f}  {cov}  {self.n_cards:>5}"


HEADER = f"{'stimatore':<34} {'RMSE pp':>6}  {'corr':>6}  {'cop80':>5}  {'carte':>5}"


def _score(label: str, pred: np.ndarray, truth: np.ndarray, n_truth: np.ndarray, sd: np.ndarray | None = None
           ) -> Score:
    noise = truth * (1 - truth) / n_truth
    mse = float(np.average((pred - truth) ** 2, weights=n_truth))
    noise_m = float(np.average(noise, weights=n_truth))
    rmse = float(np.sqrt(max(0.0, mse - noise_m)))
    corr = float(np.corrcoef(pred, truth)[0, 1]) if len(pred) > 2 and np.ptp(pred) > 1e-9 else float("nan")
    cov = None
    if sd is not None:
        cov = float(np.mean(np.abs(pred - truth) <= Z80 * np.sqrt(sd ** 2 + noise)))
    return Score(label, rmse, corr, len(pred), cov)


# --------------------------------------------------------------------------- 1. prior LOSO

def loso(con: sqlite3.Connection, sets: list[str], fmt: str) -> dict[str, list[Score]]:
    tables = tables_for(con, sets, fmt)
    out: dict[str, list[Score]] = {}
    for held in sets:
        t = tables[held]
        if t.baseline_gih is None:
            continue
        c = t.cards[t.cards["n_gih"] >= MIN_N_EVAL]
        truth, n = c["gih_wr"].to_numpy(), c["n_gih"].to_numpy(float)
        scores = [_score("media del set", np.full(len(c), t.baseline_gih), truth, n)]
        others = {k: v for k, v in tables.items() if k != held}
        for fs in FEATURE_SETS:
            try:
                m = fit(others, fmt, feature_set=fs)
            except ValueError:
                continue
            scores.append(_score(f"prior: {fs}", t.baseline_gih + m.predict_delta(c), truth, n))
        out[held] = scores
    return out


# --------------------------------------------------------------------------- 2. divisione temporale

def _days(con: sqlite3.Connection, set_code: str, fmt: str) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT DISTINCT day FROM stat_decks WHERE set_code=? AND format=? AND snapshot_id="
        "(SELECT MAX(snapshot_id) FROM stat_decks WHERE set_code=? AND format=?) ORDER BY day",
        (set_code, fmt, set_code, fmt))]


def estimate_drift(con: sqlite3.Connection, sets: list[str], fmt: str, half_life: float | None,
                   cutoffs: tuple[int, ...] = (7, 14)) -> tuple[float, list[str]]:
    """Deriva: parte dello scarto tra stima e win rate futura non spiegata dall'incertezza binomiale.

    drift^2 = media(scarto^2 - sd_stima^2 - rumore del periodo futuro), su divisioni temporali.
    """
    vals, notes = [], []
    for s in sets:
        days = _days(con, s, fmt)
        for k in cutoffs:
            if k >= len(days):
                continue
            early = raw_table(con, s, fmt, Filters(as_of=days[k - 1], half_life=half_life))
            late = raw_table(con, s, fmt, Filters(since=days[k])).cards.set_index("card")
            late = late[late["n_gih"] >= MIN_N_EVAL]
            a = _blend_table(early, None, False).reindex(late.index)
            truth, n_t = late["gih_wr"].to_numpy(), late["n_gih"].to_numpy(float)
            e2 = (a["post"].to_numpy() - truth) ** 2 - a["sd"].to_numpy() ** 2 - truth * (1 - truth) / n_t
            d2 = float(max(0.0, np.average(e2, weights=n_t)))
            vals.append(d2)
            notes.append(f"{s} primi {k} giorni: deriva {np.sqrt(d2) * 100:.2f} punti")
    return (float(np.sqrt(np.mean(vals))) if vals else 0.0), notes


def _blend_table(t, prior: PriorModel | None, use_prior: bool) -> pd.DataFrame:
    """GIH WR bayesiana di tutte le carte di una tabella grezza (tau^2 stimato sulla tabella)."""
    c = t.cards.copy()
    p0 = t.baseline_gih
    mu = p0 + prior.predict_delta(c) if (use_prior and prior) else np.full(len(c), p0)
    n, w = c["n_gih"].to_numpy(float), c["w_gih"].to_numpy(float)
    tau2, _ = estimate_tau2(n, w, mu)
    if tau2 is None:
        tau2 = prior.tau2 if (use_prior and prior) else 0.03 ** 2
    post, sd, _ = blend(n, w, mu, tau2, p0)
    drift = prior.drift_sd if prior is not None else 0.0
    c["mu"], c["post"], c["sd"] = mu, post, np.sqrt(sd ** 2 + drift ** 2)
    c["raw"] = np.where(n > 0, w / np.maximum(n, 1), p0)
    return c.set_index("card")


def time_split(con: sqlite3.Connection, set_code: str, fmt: str, prior: PriorModel | None,
               cutoffs: tuple[int, ...] = (2, 4, 7, 14)) -> dict[str, list[Score]]:
    """``prior`` va stimato SENZA ``set_code`` (altrimenti il confronto e' truccato). Si usano la sua
    emivita (sui dati iniziali) e la sua deriva (negli intervalli)."""
    days = _days(con, set_code, fmt)
    out: dict[str, list[Score]] = {}
    for k in cutoffs:
        if k >= len(days):
            continue
        last_early, first_late = days[k - 1], days[k]
        hl = prior.half_life if prior is not None else None
        early = raw_table(con, set_code, fmt, Filters(as_of=last_early, half_life=hl))
        late = raw_table(con, set_code, fmt, Filters(since=first_late)).cards.set_index("card")
        late = late[late["n_gih"] >= MIN_N_EVAL]
        truth, n_t = late["gih_wr"].to_numpy(), late["n_gih"].to_numpy(float)
        a = _blend_table(early, prior, False).reindex(late.index)  # stessa deriva del prior, se c'e'
        label = (f"primi {k} giorni (fino al {last_early}, {early.n_games:.0f} partite"
                 + (f" pesate, emivita {hl:g} giorni)" if hl else ")"))
        scores = [_score("dato grezzo", a["raw"].to_numpy(), truth, n_t),
                  _score("media del set", np.full(len(truth), early.baseline_gih), truth, n_t),
                  _score("blend verso la media", a["post"].to_numpy(), truth, n_t, a["sd"].to_numpy())]
        if prior is not None:
            b = _blend_table(early, prior, True).reindex(late.index)
            scores += [_score("solo prior (modello)", b["mu"].to_numpy(), truth, n_t,
                              np.full(len(truth), np.sqrt(prior.tau2 + prior.drift_sd ** 2))),
                       _score("blend verso il prior (modello)", b["post"].to_numpy(), truth, n_t, b["sd"].to_numpy())]
        out[label] = scores
    return out


# --------------------------------------------------------------------------- 3. decadimento

def decay_test(con: sqlite3.Connection, set_code: str, fmt: str, cutoff: int = 21,
               half_lives: tuple[float | None, ...] = (None, 14, 7, 3)) -> tuple[str, list[Score]]:
    days = _days(con, set_code, fmt)
    if cutoff >= len(days):
        cutoff = len(days) * 2 // 3
    last_early, first_late = days[cutoff - 1], days[cutoff]
    late = raw_table(con, set_code, fmt, Filters(since=first_late)).cards.set_index("card")
    late = late[late["n_gih"] >= MIN_N_EVAL]
    truth, n_t = late["gih_wr"].to_numpy(), late["n_gih"].to_numpy(float)
    scores = []
    for hl in half_lives:
        e = raw_table(con, set_code, fmt, Filters(as_of=last_early, half_life=hl))
        c = e.cards.set_index("card").reindex(late.index)
        n_e, w_e = c["n_gih"].fillna(0).to_numpy(float), c["w_gih"].fillna(0).to_numpy(float)
        mu = np.full(len(truth), e.baseline_gih)
        tau2, _ = estimate_tau2(n_e, w_e, mu)
        post, sd, _ = blend(n_e, w_e, mu, tau2 or 0.03 ** 2, e.baseline_gih)
        scores.append(_score(f"emivita {hl:g} giorni" if hl else "nessun decadimento", post, truth, n_t, sd))
    return f"dati fino al {last_early}, verifica dal {first_late}", scores


def format_report(title: str, blocks: dict[str, list[Score]]) -> str:
    out = [f"## {title}", ""]
    for k, scores in blocks.items():
        out += [f"{k}", HEADER] + [s.row() for s in scores] + [""]
    return "\n".join(out)
