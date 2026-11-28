"""Proiezione del mazzo finale a meta' draft e probabilita' degli archetipi.

Per ogni archetipo a (coppia di colori) il mazzo finale atteso e' formato dalle migliori 23 carte tra:
- le carte del pool giocabili in a;
- le carte attese dai pick futuri: pick rimasti x quota di pick che finisce nel mazzo, con la qualita'
  del profilo di un mazzo tipico (in proporzione: se arriveranno m carte, si prendono m posti del
  profilo distribuiti uniformemente);
- se non basta, carte di riempimento con la qualita' dell'ultimo posto del profilo.

Il valore E_a del mazzo proiettato usa i coefficienti del valutatore di mazzo per qualita' media,
top 3 e forza dell'archetipo. Curva, terre e splash non sono proiettabili a meta' draft e qui non
entrano (contano nella costruzione del mazzo).

Probabilita' degli archetipi:  P(a | pool) proporzionale a  quota_a * exp(lambda(t) * E_a),
con lambda che cresce col procedere del draft (t = carte nel pool / carte totali), stimato per massima
verosimiglianza sui colori finali dei mazzi reali (vedi fit_lambda).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from ..config import FormatConfig
from .book import PAIRS, Card, CardBook
from .deckmodel import DECK_SLOTS, DeckModel

N_BUCKETS = 10


@dataclass
class ArchetypeModel:
    lambdas: list[float]                      # per decimo di avanzamento del draft
    mix: list[float] | None = None            # quota "cambio di strada" per decimo (miscela con le quote)
    tiebreak: float = 0.0                     # peso del termine secondario (vedi Projector.secondary)
    log_likelihood: float | None = None
    n_samples: int = 0
    notes: list[str] = field(default_factory=list)

    def lam(self, progress: float) -> float:
        i = min(int(progress * N_BUCKETS), N_BUCKETS - 1)
        return self.lambdas[i]

    def eps(self, progress: float) -> float:
        if not self.mix:
            return 0.0
        return self.mix[min(int(progress * N_BUCKETS), N_BUCKETS - 1)]


class Projector:
    def __init__(self, book: CardBook, model: DeckModel, fmt: FormatConfig, arch: ArchetypeModel | None = None):
        self.book, self.model, self.fmt = book, model, fmt
        self.arch = arch or ArchetypeModel([0.0] * N_BUCKETS)
        self.profile = np.asarray(model.profile, dtype=float)
        self.c_mean = model.coef["mean_q"]
        self.c_top = model.coef["top3"]
        self.c_arch = model.coef["arch"]

    # -- valore del mazzo proiettato -----------------------------------------------------------
    def future_values(self, n_pool: int) -> np.ndarray:
        remaining = max(0, self.fmt.pool_size - n_pool)
        m = remaining * self.model.playable_share
        if m <= 0:
            return np.empty(0)
        if m >= DECK_SLOTS:
            return self.profile.copy()
        k = int(np.floor(m))
        frac = m - k
        pos = ((np.arange(k) + 0.5) * DECK_SLOTS / max(m, 1e-9)).astype(int).clip(0, DECK_SLOTS - 1)
        vals = list(self.profile[pos])
        if frac > 0:  # parte frazionaria: una carta "a meta'" (valore pesato verso il riempimento)
            vals.append(frac * self.profile[min(int((k + 0.5) * DECK_SLOTS / m), DECK_SLOTS - 1)]
                        + (1 - frac) * self.profile[-1])
        return np.array(vals)

    def deck_value(self, values: np.ndarray, pair: str) -> float:
        # una carta peggiore del riempimento non si gioca: si prendono le 23 migliori tra carte e riempimento
        v = np.sort(np.concatenate([values, np.full(DECK_SLOTS, self.profile[-1])]))[::-1][:DECK_SLOTS]
        return self.c_mean * v.mean() + self.c_top * v[:3].mean() + self.c_arch * self.book.arch_delta.get(pair, 0.0)

    def pool_values(self, pool: Sequence[Card], pair: str) -> np.ndarray:
        return np.array([c.q for c in pool if not c.is_basic and c.castable_in(pair)], dtype=float)

    def arch_values(self, pool: Sequence[Card], extra: Sequence[Card] = (), n_cards: int | None = None
                    ) -> dict[str, float]:
        """``n_cards``: carte nel pool da usare per i pick futuri (default: pool + extra)."""
        n = len(pool) + len(extra) if n_cards is None else n_cards
        fut = self.future_values(n)
        out = {}
        for a in PAIRS:
            vals = np.concatenate([self.pool_values(pool, a), self.pool_values(extra, a), fut])
            out[a] = self.deck_value(vals, a)
        return out

    # -- archetipi -----------------------------------------------------------------------------
    def probs(self, pool: Sequence[Card], values: dict[str, float] | None = None) -> dict[str, float]:
        values = values or self.arch_values(pool)
        t = len(pool) / self.fmt.pool_size
        lam, eps = self.arch.lam(t), self.arch.eps(t)
        share = np.array([self.book.arch_share.get(a, 1 / len(PAIRS)) for a in PAIRS])
        share = share / share.sum()
        logits = np.log(share) + lam * np.array([values[a] for a in PAIRS])
        p = np.exp(logits - logits.max())
        p = (1 - eps) * p / p.sum() + eps * share
        return dict(zip(PAIRS, p))

    # -- valore di un'opzione ------------------------------------------------------------------
    def option_value(self, pool: Sequence[Card], option: Sequence[Card], base: dict[str, float],
                     probs: dict[str, float]) -> tuple[float, dict[str, float]]:
        """Miglioramento atteso del mazzo finale (in punti di win rate), per archetipo e pesato."""
        after = self.arch_values(pool, option)
        gains = {a: after[a] - base[a] for a in PAIRS}
        return float(sum(probs[a] * gains[a] for a in PAIRS)), gains


    def secondary(self, option: Sequence[Card], probs: dict[str, float]) -> float:
        """Contributo che l'opzione darebbe se entrasse nel mazzo, nei colori probabili.

        Serve quando nessuna opzione entra nel mazzo proiettato (tipico a fine busta): una carta poco
        sotto la soglia ha comunque qualche probabilita' di finire nel mazzo. Stessa scala del valore
        principale (una carta su 23, coefficiente della qualita' media).
        """
        tot = 0.0
        for a, p in probs.items():
            tot += p * sum(c.q for c in option if not c.is_basic and c.castable_in(a))
        return self.c_mean * tot / DECK_SLOTS


def fit_lambda(projector: Projector, samples: Iterable[tuple[list[Card], str]],
               grid: Sequence[float] = tuple(np.linspace(0, 800, 41)),
               mix_grid: Sequence[float] = (0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5)) -> ArchetypeModel:
    """Stima lambda(t) e la miscela eps(t) per massima verosimiglianza sui colori finali.

    P(a) = (1 - eps) * softmax(log quota_a + lambda * E_a) + eps * quota_a. La miscela rappresenta i
    giocatori che cambiano strada anche con un pool ben definito: senza, le probabilita' alte sono
    troppo sicure (verificato su FRA: 97% previsto contro circa 75% reale).
    """
    by_bucket: dict[int, list[tuple[np.ndarray, int]]] = {i: [] for i in range(N_BUCKETS)}
    share = np.array([projector.book.arch_share.get(a, 0.1) for a in PAIRS])
    share = share / share.sum()
    logshare = np.log(share)
    for pool, final in samples:
        if final not in PAIRS:
            continue
        vals = projector.arch_values(pool)
        v = np.array([vals[a] for a in PAIRS])
        b = min(int(len(pool) / projector.fmt.pool_size * N_BUCKETS), N_BUCKETS - 1)
        by_bucket[b].append((v - v.max(), PAIRS.index(final)))
    lambdas, mixes, total_ll, n = [], [], 0.0, 0
    for b in range(N_BUCKETS):
        rows = by_bucket[b]
        if not rows:
            lambdas.append(lambdas[-1] if lambdas else 0.0)
            mixes.append(mixes[-1] if mixes else 0.0)
            continue
        V = np.array([r[0] for r in rows])
        y = np.array([r[1] for r in rows])
        best = None
        for lam in grid:
            z = logshare + lam * V
            z = z - z.max(1, keepdims=True)
            soft = np.exp(z)
            soft /= soft.sum(1, keepdims=True)
            ps = soft[np.arange(len(y)), y]
            for eps in mix_grid:
                ll = float(np.log((1 - eps) * ps + eps * share[y]).sum())
                if best is None or ll > best[2]:
                    best = (lam, eps, ll)
        lambdas.append(float(best[0]))
        mixes.append(float(best[1]))
        total_ll += best[2]
        n += len(rows)
    return ArchetypeModel(lambdas=[float(x) for x in lambdas], mix=mixes, log_likelihood=float(total_ll),
                          n_samples=n, notes=["lambda e miscela per decimo di avanzamento"])
