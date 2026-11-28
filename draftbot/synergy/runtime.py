"""Sinergie nel motore: termine aggiuntivo del valore del mazzo (proiettato o finale).

    sinergia = calibrazione * somma_t coef_t * x_t

con x_t come in measure.py (interazione enabler x payoff / 10, o densita' per i temi di keyword) e
``calibrazione`` = pendenza fuori campione misurata (circa 0,5 su FRA: le stime della ridge sono
il doppio dell'effetto reale). A meta' draft i conteggi includono le carte attese dai pick futuri,
con la frequenza del tema tra le carte del set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..engine.book import Card, CardBook
from .measure import INTER_SCALE, SynergyModel
from .tags import TagSet


@dataclass
class SynergyRuntime:
    coefs: dict[str, float]                       # gia' moltiplicati per la calibrazione
    enablers: dict[str, set[str]]
    payoffs: dict[str, set[str]]
    kinds: dict[str, str]
    rate_e: dict[str, float]                      # frequenza tra le carte non base del set
    rate_p: dict[str, float]

    @classmethod
    def build(cls, model: SynergyModel, tags: TagSet, book: CardBook) -> "SynergyRuntime | None":
        cal = min(1.0, max(0.0, model.oos_slope))
        coefs = {t: c * cal for t, c in model.effects.items() if t in tags.themes and c * cal > 0}
        if not coefs:
            return None
        names = [c.name for c in book.cards.values() if not c.is_basic]
        en, pay, kinds, re_, rp = {}, {}, {}, {}, {}
        for t in coefs:
            d = tags.themes[t]
            e = set(d.get("enablers", []))
            p = set(d.get("payoffs", []))
            kind = "interazione" if p and not t.startswith("kw:") else "densita'"
            if t.startswith("kw:"):
                e |= p
            en[t], pay[t], kinds[t] = e, p, kind
            re_[t] = sum(n in e for n in names) / max(len(names), 1)
            rp[t] = sum(n in p for n in names) / max(len(names), 1)
        return cls(coefs, en, pay, kinds, re_, rp)

    def value(self, cards: Sequence[Card], n_future: float = 0.0, scale: float = 1.0) -> float:
        """Sinergia (frazione di win rate) di un insieme di carte giocabili, piu' le carte future attese.

        ``scale`` (<= 1) riduce i conteggi quando le carte giocabili sono piu' dei posti nel mazzo."""
        tot = 0.0
        names = [c.name for c in cards if not c.is_basic]
        for t, coef in self.coefs.items():
            e = (sum(n in self.enablers[t] for n in names) + n_future * self.rate_e[t]) * scale
            if self.kinds[t] == "interazione":
                p = (sum(n in self.payoffs[t] for n in names) + n_future * self.rate_p[t]) * scale
                x = e * p / INTER_SCALE
            else:
                x = e * max(e - 1, 0) / (2 * INTER_SCALE)
            tot += coef * x
        return tot

    def roles(self, name: str) -> list[str]:
        out = []
        for t in self.coefs:
            if name in self.payoffs[t]:
                out.append(f"{t} (payoff)")
            elif name in self.enablers[t]:
                out.append(f"{t}")
        return out
