"""Segnali: quali colori arrivano aperti dai vicini.

Per ogni busta vista (escluso il primo pick di ogni busta, che non dice nulla sui vicini) si misura
per ogni colore la qualita' disponibile: la somma della qualita' delle 2 migliori carte che richiedono
quel colore (ognuna con il suo valore sopra lo zero). La si confronta con quella attesa allo stesso
turno, stimata sui draft del periodo di stima. Lo scarto si accumula per direzione di passaggio (dalla
config del formato: le buste che vanno a sinistra arrivano dal vicino di destra, e viceversa).

Nel modello degli archetipi (projection.py) il segnale entra come termine aggiuntivo:
    logit_a += g_same * segnale_a(direzione della busta corrente) + g_other * segnale_a(altra direzione)
con segnale_a = somma dei segnali dei due colori della coppia. g_same e g_other si stimano per massima
verosimiglianza sui colori finali, come lambda.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from ..config import FormatConfig
from .book import COLORS, PAIRS, Card

TOP_K = 2


def availability(pack: Sequence[Card]) -> dict[str, float]:
    out = {}
    for col in COLORS:
        vals = sorted((max(c.q, 0.0) for c in pack if col in c.colors and not c.is_land), reverse=True)
        out[col] = float(sum(vals[:TOP_K]))
    return out


@dataclass
class SignalBaseline:
    """Disponibilita' attesa per (busta, turno) e colore."""
    expected: dict[tuple[int, int], dict[str, float]] = field(default_factory=dict)

    @classmethod
    def fit(cls, packs: Iterable[tuple[int, int, Sequence[Card]]]) -> "SignalBaseline":
        acc: dict[tuple[int, int], list[dict[str, float]]] = defaultdict(list)
        for pk, t, pack in packs:
            acc[(pk, t)].append(availability(pack))
        exp = {k: {c: float(np.mean([a[c] for a in v])) for c in COLORS} for k, v in acc.items()}
        return cls(exp)


@dataclass
class SignalState:
    """Segnali accumulati durante un draft, per direzione di passaggio."""
    by_dir: dict[str, dict[str, float]] = field(default_factory=lambda: {"left": dict.fromkeys(COLORS, 0.0),
                                                                          "right": dict.fromkeys(COLORS, 0.0)})
    n: dict[str, int] = field(default_factory=lambda: {"left": 0, "right": 0})

    def observe(self, pack_number: int, pick_number: int, pack: Sequence[Card], fmt: FormatConfig,
                base: SignalBaseline) -> None:
        if pick_number == 0:
            return
        exp = base.expected.get((pack_number, pick_number))
        if exp is None:
            return
        d = fmt.pass_directions[pack_number]
        av = availability(pack)
        for c in COLORS:
            self.by_dir[d][c] += av[c] - exp[c]
        self.n[d] += 1

    def pair_signal(self, direction: str) -> tuple[dict[str, float], dict[str, float]]:
        other = "right" if direction == "left" else "left"
        same = {a: sum(self.by_dir[direction][c] for c in a) for a in PAIRS}
        oth = {a: sum(self.by_dir[other][c] for c in a) for a in PAIRS}
        return same, oth

    def colors_summary(self) -> dict[str, float]:
        return {c: self.by_dir["left"][c] + self.by_dir["right"][c] for c in COLORS}


@dataclass
class SignalModel:
    g_same: float = 0.0
    g_other: float = 0.0
    ll_gain: float = 0.0            # guadagno di log-verosimiglianza media fuori campione (per pool)
    notes: list[str] = field(default_factory=list)


def _samples(projector, drafts, colors: dict[str, str], base: SignalBaseline, every: int = 2):
    """(logit di base senza segnali, segnali stessa direzione, altra direzione, eps, indice finale)."""
    book, fmt = projector.book, projector.fmt
    share = np.array([book.arch_share.get(a, 0.1) for a in PAIRS])
    share = share / share.sum()
    for d in drafts:
        final = colors.get(d.draft_id)
        if not final or final not in PAIRS:
            continue
        st = SignalState()
        for i, p in enumerate(sorted(d.picks, key=lambda p: (p.pack_number, p.pick_number))):
            pack = [book.cards[n] for n in p.pack if n in book.cards]
            st.observe(p.pack_number, p.pick_number, pack, fmt, base)
            if i % every:
                continue
            pool = [book.cards[n] for n in p.pool if n in book.cards]
            vals = projector.arch_values(pool)
            t = len(pool) / fmt.pool_size
            z = np.log(share) + projector.arch.lam(t) * np.array([vals[a] for a in PAIRS])
            same, oth = st.pair_signal(fmt.pass_directions[p.pack_number])
            yield (z, np.array([same[a] for a in PAIRS]), np.array([oth[a] for a in PAIRS]),
                   projector.arch.eps(t), share, PAIRS.index(final))


def _ll(rows, g_same: float, g_other: float) -> float:
    tot = 0.0
    for z, s, o, eps, share, y in rows:
        zz = z + g_same * s + g_other * o
        zz = zz - zz.max()
        p = np.exp(zz)
        p /= p.sum()
        tot += float(np.log((1 - eps) * p[y] + eps * share[y]))
    return tot / max(len(rows), 1)


def fit_signal_model(projector, fit_drafts, test_drafts, colors, base: SignalBaseline,
                     grid=tuple(np.linspace(-40, 40, 41))) -> SignalModel:
    tr = list(_samples(projector, fit_drafts, colors, base))
    te = list(_samples(projector, test_drafts, colors, base, every=1))
    best = max(((gs, go) for gs in grid for go in grid), key=lambda g: _ll(tr, *g))
    gain = _ll(te, *best) - _ll(te, 0.0, 0.0)
    return SignalModel(float(best[0]), float(best[1]), float(gain),
                       [f"stimato su {len(tr)} pool, verificato su {len(te)}",
                        "ll_gain: log-verosimiglianza media dei colori finali, con segnali - senza"])


def extra_logits(model: SignalModel, state: SignalState, direction: str) -> dict[str, float]:
    same, oth = state.pair_signal(direction)
    return {a: model.g_same * same[a] + model.g_other * oth[a] for a in PAIRS}
