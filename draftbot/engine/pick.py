"""Motore di pick: ``recommend(pool, pack, k)``.

Per ogni opzione (una carta, o una coppia in Pick Two) il valore e' il miglioramento atteso del mazzo
finale, in punti di win rate, mediato sugli archetipi con la loro probabilita' dato il pool:

    valore(o) = somma_a P(a | pool) * [E_a(pool + o) - E_a(pool)]

E_a e' il valore del mazzo finale proiettato nell'archetipo a (vedi projection.py). Il valore di
rimpiazzo e' implicito: una carta vale solo per quanto migliora le 23 carte che finiranno nel mazzo,
contando anche quelle attese dai pick futuri.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Sequence

from .book import Card
from .projection import Projector


@dataclass
class Option:
    cards: tuple[Card, ...]
    value: float                         # punti di win rate attesi (frazione: 0.01 = 1 punto)
    gains: dict[str, float]              # guadagno per archetipo
    probs: dict[str, float] = field(default_factory=dict)
    deck_gain: float = 0.0               # miglioramento atteso del mazzo finale
    secondary: float = 0.0               # contributo se entrasse nel mazzo (spareggio, pesato da tiebreak)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.cards)

    def explain(self, top: int = 3) -> str:
        contrib = sorted(((self.probs[a] * g, a) for a, g in self.gains.items()), reverse=True)[:top]
        parts = [f"{a} {p * 100:.1f}" for p, a in contrib if p > 1e-5]
        q = ", ".join(f"{c.name} q {c.q * 100:+.1f}" for c in self.cards)
        return f"{q}; guadagno sul mazzo {self.deck_gain * 100:+.2f}; per archetipo: {', '.join(parts) or '-'}"


def recommend(projector: Projector, pool: Sequence[Card], pack: Sequence[Card], k: int = 3,
              picks_per_turn: int | None = None) -> tuple[list[Option], dict[str, float]]:
    """Le k opzioni migliori e le probabilita' degli archetipi dato il pool."""
    n = picks_per_turn or projector.fmt.picks_per_turn
    probs = projector.probs(pool)
    # base con gli stessi pick futuri del dopo-pick: il valore e' il guadagno rispetto a una carta
    # che non entra nel mazzo (consumare un pick costa uguale per tutte le opzioni)
    base = projector.arch_values(pool, n_cards=len(pool) + n)
    # in Pick Two: tutte le coppie (con le copie: due carte uguali sono una coppia valida)
    if n == 1:
        options = [(c,) for c in pack]
    else:
        options = list(itertools.combinations(pack, n))
    seen: dict[tuple[str, ...], Option] = {}
    for opt in options:
        key = tuple(sorted(c.name for c in opt))
        if key in seen:
            continue
        v, gains = projector.option_value(pool, opt, base, probs)
        sec = projector.secondary(opt, probs)
        seen[key] = Option(tuple(opt), v + projector.arch.tiebreak * sec, gains, probs, v, sec)
    ranked = sorted(seen.values(), key=lambda o: o.value, reverse=True)
    return ranked[:k], probs
