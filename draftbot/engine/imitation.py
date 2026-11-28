"""Strato di scelta stimato sui pick dei giocatori forti (logit condizionale).

Il motore ordina le opzioni per valore atteso sul mazzo finale. Qui si stima, sui pick reali dei
giocatori della fascia alta, quanto pesano nella loro scelta il valore del motore e poche altre
grandezze note al momento del pick:

- ``value``: valore del motore (punti, wheel compreso);
- ``q``: qualita' delle carte (punti);
- ``pop``: popolarita' (1 - ATA normalizzata), cioe' quanto presto la carta viene presa in media;
- ``fit``: quanto le carte stanno negli archetipi probabili del pool (somma su archetipi di P(a) per le
  carte giocabili in a);
- ``rare``: carte rare o mitiche; ``gold``: carte multicolore; ``land``: terre non base;
- le interazioni di valore, qualita', popolarita' e fit con ``early`` = 1 - avanzamento del draft.

P(opzione) = softmax(X beta) sulle opzioni della busta (in Pick Two: tutte le coppie). La stima e'
per massima verosimiglianza con una piccola penalita' L2 (Newton). Le variabili non dipendono dal set,
quindi i coefficienti si possono stimare su un set e usare su un altro.

Imitare non e' lo stesso che vincere: lo strato si tiene solo se l'accordo fuori campione migliora.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np
import yaml

from ..config import ROOT
from .book import Card
from .pick import Option

FEATURES = ("value", "q", "pop", "fit", "rare", "gold", "land",
            "value_early", "q_early", "pop_early", "fit_early")


@dataclass
class PickFeatures:
    X: np.ndarray            # opzioni x variabili
    chosen: int              # indice dell'opzione scelta dal giocatore
    draft: int               # indice del draft (per il bootstrap)
    phase: str
    keys: list[tuple[str, ...]] = field(default_factory=list)


@dataclass
class ImitationModel:
    coef: np.ndarray
    features: tuple[str, ...] = FEATURES
    l2: float = 1.0
    n_picks: int = 0
    loglik: float = 0.0
    notes: list[str] = field(default_factory=list)

    def scores(self, X: np.ndarray) -> np.ndarray:
        return X @ self.coef

    def probs(self, X: np.ndarray) -> np.ndarray:
        s = self.scores(X)
        e = np.exp(s - s.max())
        return e / e.sum()

    # -- salvataggio ---------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"features": list(self.features), "coef": [float(x) for x in self.coef], "l2": float(self.l2),
                "n_picks": int(self.n_picks), "loglik": float(self.loglik), "notes": list(self.notes)}

    @classmethod
    def from_dict(cls, d: dict) -> "ImitationModel":
        feats = tuple(d["features"])
        if feats != FEATURES:
            raise ValueError(f"variabili del modello salvato diverse da quelle del codice: {feats}")
        return cls(np.array(d["coef"], dtype=float), feats, d.get("l2", 1.0), d.get("n_picks", 0),
                   d.get("loglik", 0.0), list(d.get("notes", [])))


def model_path(fmt: str) -> Path:
    return ROOT / "priors" / f"{fmt}_imitation.yaml"


def save(model: ImitationModel, fmt: str) -> Path:
    p = model_path(fmt)
    p.write_text(yaml.safe_dump(model.to_dict(), sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p


def load(fmt: str) -> ImitationModel | None:
    p = model_path(fmt)
    if not p.is_file():
        return None
    return ImitationModel.from_dict(yaml.safe_load(p.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- variabili

def _fits(card: Card, pair: str) -> bool:
    return card.is_land or set(card.colors) <= set(pair)


def option_features(options: Sequence[Option], probs: dict[str, float], pop: dict[str, float],
                    progress: float) -> np.ndarray:
    early = 1.0 - progress
    rows = []
    for o in options:
        v = o.value * 100
        q = sum(c.q for c in o.cards) * 100
        pp = sum(pop.get(c.name, 0.5) for c in o.cards)
        fit = sum(sum(p for a, p in probs.items() if _fits(c, a)) for c in o.cards if not c.is_basic)
        rare = sum(c.rarity in ("rare", "mythic") for c in o.cards)
        gold = sum(len(c.colors) > 1 for c in o.cards)
        land = sum(c.is_land and not c.is_basic for c in o.cards)
        rows.append([v, q, pp, fit, rare, gold, land, v * early, q * early, pp * early, fit * early])
    return np.array(rows, dtype=float)


def rerank(model: ImitationModel, options: list[Option], probs: dict[str, float], pop: dict[str, float],
           progress: float) -> list[tuple[Option, float]]:
    """Opzioni riordinate per probabilita' di scelta di un giocatore forte."""
    if not options:
        return []
    p = model.probs(option_features(options, probs, pop, progress))
    order = np.argsort(-p, kind="stable")
    return [(options[i], float(p[i])) for i in order]


# --------------------------------------------------------------------------- stima

def fit(picks: Sequence[PickFeatures], l2: float = 1.0, iters: int = 50, cols: Sequence[int] | None = None
        ) -> ImitationModel:
    """Logit condizionale per Newton. ``cols``: sottoinsieme di variabili (le altre a 0)."""
    k = len(FEATURES)
    idx = np.arange(k) if cols is None else np.array(cols)
    # scala delle variabili per una penalita' sensata
    allX = np.vstack([p.X[:, idx] for p in picks])
    sd = allX.std(0)
    sd[sd < 1e-9] = 1.0
    b = np.zeros(len(idx))
    ll = 0.0
    for _ in range(iters):
        g = -l2 * b
        H = -l2 * np.eye(len(idx))
        ll = -0.5 * l2 * float(b @ b)
        for p in picks:
            Z = p.X[:, idx] / sd
            s = Z @ b
            s -= s.max()
            e = np.exp(s)
            w = e / e.sum()
            m = w @ Z
            ll += float(s[p.chosen] - np.log(e.sum()))
            g += Z[p.chosen] - m
            H -= (Z * w[:, None]).T @ Z - np.outer(m, m)
        step = np.linalg.solve(H, g)
        b = b - step
        if np.abs(step).max() < 1e-6:
            break
    coef = np.zeros(k)
    coef[idx] = b / sd
    return ImitationModel(coef, FEATURES, l2, len(picks), ll)


def agreement(picks: Sequence[PickFeatures], score) -> tuple[float, float, np.ndarray]:
    """top-1, top-3 e, per draft, [pick, top-1 giusti] secondo la funzione di punteggio data."""
    n_d = max((p.draft for p in picks), default=-1) + 1
    per = np.zeros((n_d, 2))
    t1 = t3 = 0
    for p in picks:
        s = score(p)
        order = np.argsort(-s, kind="stable")
        t1 += order[0] == p.chosen
        t3 += p.chosen in order[:3]
        per[p.draft, 0] += 1
        per[p.draft, 1] += order[0] == p.chosen
    n = max(len(picks), 1)
    return t1 / n, t3 / n, per


def paired_ci(a: np.ndarray, b: np.ndarray, B: int = 1000, seed: int = 0) -> tuple[float, float, float]:
    """Differenza di top-1 a - b, con intervallo al 95% (bootstrap sui draft)."""
    keep = a[:, 0] > 0
    a, b = a[keep], b[keep]
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(B, len(a)))
    f = lambda ii: a[ii, 1].sum(-1) / a[ii, 0].sum(-1) - b[ii, 1].sum(-1) / b[ii, 0].sum(-1)
    lo, hi = np.percentile(f(idx), [2.5, 97.5])
    return float(f(np.arange(len(a)))), float(lo), float(hi)
