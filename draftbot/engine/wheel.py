"""Probabilita' di wheel: una carta della busta tornera' dopo un giro del tavolo?

Modello (parametrico, dalla config del formato): dopo il nostro pick la busta passa per gli altri
``pod_size - 1`` giocatori, ognuno prende ``picks_per_turn`` carte. Ogni giocatore sceglie una carta
con probabilita' proporzionale a un peso w_c = exp(b_q * q_c + b_p * pop_c), dove q e' la qualita'
(punti di win rate) e pop la popolarita' (1 - ATA normalizzata). La sopravvivenza di una carta e':

    s_c = prod_k (1 - n_k * w_c / W_k),   W_k = somma dei pesi attesi rimasti al passaggio k

(approssimazione "in media" del modello di Plackett-Luce, calcolata in avanti passaggio per passaggio).
I parametri si stimano per massima verosimiglianza sugli esiti osservati nei draft 17Lands: la busta
vista al turno t si rivede al turno t + pod_size (vedi la Fase 3 per l'ALSA).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from ..config import FormatConfig
from .book import Card


@dataclass
class WheelModel:
    b_q: float = 0.0
    b_p: float = 0.0
    log_likelihood: float | None = None
    n_samples: int = 0
    notes: list[str] = field(default_factory=list)

    def weights(self, cards: Sequence[Card], pop: dict[str, float]) -> np.ndarray:
        q = np.array([c.q for c in cards])
        p = np.array([pop.get(c.name, 0.5) for c in cards])
        return np.exp(self.b_q * q * 100 / 3 + self.b_p * p)   # q in unita' di 3 punti

    def survival(self, remaining: Sequence[Card], fmt: FormatConfig, pop: dict[str, float]) -> np.ndarray:
        """Probabilita' che ogni carta di ``remaining`` (busta dopo il nostro pick) torni a noi."""
        return survival(self.weights(remaining, pop), fmt.pod_size - 1, fmt.picks_per_turn)


def survival(w: np.ndarray, passes: int, per_pick: int) -> np.ndarray:
    s = np.ones(len(w))
    for _ in range(passes):
        W = float((w * s).sum())
        if W <= 0:
            break
        take = np.clip(per_pick * w / W, 0, 1)
        s = s * (1 - take)
    return s


def wheel_samples(drafts, book, fmt: FormatConfig) -> Iterable[tuple[list[Card], np.ndarray]]:
    """(busta dopo il nostro pick, esito 0/1 per carta) per ogni turno con una busta di ritorno."""
    for d in drafts:
        by = {(p.pack_number, p.pick_number): p for p in d.picks}
        for (pk, t), p in by.items():
            back = by.get((pk, t + fmt.pod_size))
            if back is None:
                continue
            rem = list(p.pack)
            for x in p.picked:
                if x in rem:
                    rem.remove(x)
            cards = [book.cards[n] for n in rem if n in book.cards]
            if len(cards) != len(rem) or not cards:
                continue
            left = list(back.pack)
            out = []
            for c in cards:          # esito per copia: una copia torna se e' ancora nella busta
                if c.name in left:
                    left.remove(c.name)
                    out.append(1)
                else:
                    out.append(0)
            yield cards, np.array(out)


def fit_wheel(samples: list[tuple[list[Card], np.ndarray]], fmt: FormatConfig, pop: dict[str, float],
              grid_q=tuple(np.linspace(0, 3, 13)), grid_p=tuple(np.linspace(0, 8, 17))) -> WheelModel:
    best = None
    for bq in grid_q:
        for bp in grid_p:
            m = WheelModel(bq, bp)
            ll = 0.0
            for cards, y in samples:
                s = np.clip(m.survival(cards, fmt, pop), 1e-6, 1 - 1e-6)
                ll += float((y * np.log(s) + (1 - y) * np.log(1 - s)).sum())
            if best is None or ll > best.log_likelihood:
                best = WheelModel(float(bq), float(bp), ll, sum(len(y) for _, y in samples))
    best.notes.append("griglia su b_q (per 3 punti di qualita') e b_p (popolarita' 1 - ATA normalizzata)")
    return best


@dataclass
class LogitWheel:
    """Regressione logistica dell'esito: posizione di popolarita' nella busta rispetto alle carte che
    verranno prese prima del ritorno, popolarita' e dimensione della busta."""
    coef: list[float] = field(default_factory=lambda: [0.0] * 5)
    log_likelihood: float | None = None
    n_samples: int = 0

    @staticmethod
    def features(cards: Sequence[Card], fmt: FormatConfig, pop: dict[str, float]) -> np.ndarray:
        p = np.array([pop.get(c.name, 0.5) for c in cards])
        n_taken = (fmt.pod_size - 1) * fmt.picks_per_turn
        rank = np.array([(p > x).sum() + 0.5 * ((p == x).sum() - 1) for x in p])   # carte piu' popolari
        rel = (rank - n_taken) / max(n_taken, 1)
        size = len(cards) / fmt.pack_size
        return np.column_stack([np.ones(len(p)), rel, np.clip(rel, -5, 5) ** 2, p, np.full(len(p), size)])

    def survival(self, remaining: Sequence[Card], fmt: FormatConfig, pop: dict[str, float]) -> np.ndarray:
        z = self.features(remaining, fmt, pop) @ np.array(self.coef)
        return 1 / (1 + np.exp(-z))


def fit_logit_wheel(samples, fmt: FormatConfig, pop: dict[str, float], ridge: float = 1.0, iters: int = 25
                    ) -> LogitWheel:
    X = np.vstack([LogitWheel.features(c, fmt, pop) for c, _ in samples])
    y = np.concatenate([y for _, y in samples]).astype(float)
    b = np.zeros(X.shape[1])
    for _ in range(iters):  # Newton (IRLS) con un po' di ridge
        pr = 1 / (1 + np.exp(-(X @ b)))
        W = pr * (1 - pr)
        H = (X * W[:, None]).T @ X + ridge * np.eye(X.shape[1])
        g = X.T @ (y - pr) - ridge * b
        b = b + np.linalg.solve(H, g)
    pr = np.clip(1 / (1 + np.exp(-(X @ b))), 1e-6, 1 - 1e-6)
    ll = float((y * np.log(pr) + (1 - y) * np.log(1 - pr)).sum())
    return LogitWheel([float(x) for x in b], ll, len(y))


def calibration(model, samples, fmt: FormatConfig, pop: dict[str, float]) -> dict:
    """Affidabilita' sui campioni di verifica: per fasce di probabilita' prevista, frequenza reale."""
    pr, ys = [], []
    for cards, y in samples:
        pr.append(model.survival(cards, fmt, pop))
        ys.append(y)
    p = np.concatenate(pr)
    y = np.concatenate(ys)
    bins = [0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0001]
    rows = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (p >= lo) & (p < hi)
        if m.sum():
            rows.append((lo, hi, int(m.sum()), float(p[m].mean()), float(y[m].mean())))
    brier = float(np.mean((p - y) ** 2))
    base = float(np.mean((y.mean() - y) ** 2))
    return {"bins": rows, "brier": brier, "brier_const": base, "n": len(y), "rate": float(y.mean())}
