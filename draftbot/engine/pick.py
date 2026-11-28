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

import numpy as np
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
    wheel_bonus: float = 0.0             # valore atteso della carta migliore che tornera' al giro dopo
    p_strong: float | None = None        # probabilita' che un giocatore forte la scelga (strato di imitazione)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.cards)

    def explain(self, top: int = 3) -> str:
        contrib = sorted(((self.probs[a] * g, a) for a, g in self.gains.items()), reverse=True)[:top]
        parts = [f"{a} {p * 100:.1f}" for p, a in contrib if p > 1e-5]
        q = ", ".join(f"{c.name} q {c.q * 100:+.1f}" for c in self.cards)
        return f"{q}; guadagno sul mazzo {self.deck_gain * 100:+.2f}; per archetipo: {', '.join(parts) or '-'}"


def recommend(projector: Projector, pool: Sequence[Card], pack: Sequence[Card], k: int = 3,
              picks_per_turn: int | None = None, extra_logits: dict[str, float] | None = None,
              wheel=None, shift: dict[str, float] | None = None, imitation=None
              ) -> tuple[list[Option], dict[str, float]]:
    """Le k opzioni migliori e le probabilita' degli archetipi dato il pool.

    ``shift``: spostamento delle carte future per archetipo dai segnali (signals.future_shift).

    ``extra_logits``: segnali dei vicini per archetipo (signals.py).
    ``wheel``: funzione (carte rimaste) -> probabilita' di ritorno; se data, al valore di un'opzione
    si aggiunge il valore atteso della carta migliore che tornera' (vedi wheel_bonus).
    ``imitation``: (ImitationModel, popolarita'); se dato, le opzioni si ordinano per probabilita' di
    scelta di un giocatore forte, stimata dal valore e dalle altre variabili (imitation.py).
    """
    old_shift = projector.shift
    projector.shift = shift or {}
    try:
        if imitation is None:
            return _recommend(projector, pool, pack, k, picks_per_turn, extra_logits, wheel)
        from .imitation import rerank
        opts, probs = _recommend(projector, pool, pack, 10_000, picks_per_turn, extra_logits, wheel)
        fmt = projector.fmt
        n = picks_per_turn or fmt.picks_per_turn
        progress = min((len(pool) / n) / max(fmt.total_picks - 1, 1), 1.0)
        out = []
        for o, pr in rerank(imitation[0], opts, probs, imitation[1], progress)[:k]:
            o.p_strong = pr
            out.append(o)
        return out, probs
    finally:
        projector.shift = old_shift


def _recommend(projector, pool, pack, k, picks_per_turn, extra_logits, wheel):
    n = picks_per_turn or projector.fmt.picks_per_turn
    probs = projector.probs(pool, extra_logits=extra_logits)
    # base con gli stessi pick futuri del dopo-pick: il valore e' il guadagno rispetto a una carta
    # che non entra nel mazzo (consumare un pick costa uguale per tutte le opzioni)
    base = projector.arch_values(pool, n_cards=len(pool) + n)
    # in Pick Two: tutte le coppie (con le copie: due carte uguali sono una coppia valida)
    if n == 1:
        options = [(c,) for c in pack]
    else:
        options = list(itertools.combinations(pack, n))
    single: dict[int, float] = {}
    if wheel is not None:
        for i, c in enumerate(pack):
            single[i], _ = projector.option_value(pool, (c,), base, probs)
    seen: dict[tuple[str, ...], Option] = {}
    for opt in options:
        key = tuple(sorted(c.name for c in opt))
        if key in seen:
            continue
        v, gains = projector.option_value(pool, opt, base, probs)
        sec = projector.secondary(opt, probs)
        o = Option(tuple(opt), v + projector.arch.tiebreak * sec, gains, probs, v, sec)
        if wheel is not None:
            o.wheel_bonus = wheel_bonus(projector, pack, opt, single, wheel)
            o.value += o.wheel_bonus
        seen[key] = o
    ranked = sorted(seen.values(), key=lambda o: o.value, reverse=True)
    return ranked[:k], probs


def wheel_bonus(projector: Projector, pack: Sequence[Card], opt: Sequence[Card], single: dict[int, float],
                wheel) -> float:
    """Valore atteso di cio' che tornera' dalla busta dopo un giro del tavolo, se si prende ``opt``.

    ``single``: valore di ogni carta della busta (per posizione) calcolato una volta con il pool attuale.
    Le carte rimaste si ordinano per valore; la migliore che torna vale v_i con probabilita'
    w_i * prod_{j<i} (1 - w_j). Si conta una carta sola anche in Pick Two (approssimazione). Vale 0 se
    la busta non tornera' (fine busta).
    """
    fmt = projector.fmt
    taken = fmt.pack_size - len(pack)
    if taken // fmt.picks_per_turn + fmt.pod_size >= fmt.picks_per_pack:
        return 0.0
    ids = {id(c) for c in opt}
    rem_idx = [i for i, c in enumerate(pack) if id(c) not in ids]
    if not rem_idx:
        return 0.0
    w = wheel([pack[i] for i in rem_idx])
    vals = np.array([max(single[i], 0.0) for i in rem_idx])
    tot, miss = 0.0, 1.0
    for j in np.argsort(vals)[::-1]:
        tot += vals[j] * w[j] * miss
        miss *= 1 - w[j]
    return float(tot)
