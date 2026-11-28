"""Costruzione del mazzo dal pool (Fase 6): 2-3 build alternative con terre e splash.

Per ogni coppia di colori principali, senza splash e con ogni possibile colore di splash:
1. candidate: le carte non base giocabili nei colori (le carte di splash solo se richiedono un solo
   simbolo del colore di splash, al massimo ``MAX_SPLASH`` carte);
2. scelta delle magie: si parte dalle migliori per qualita' e si migliora con scambi uno-a-uno
   finche' il punteggio del valutatore di mazzo (Fase 4: qualita', top 3, curva, terre, splash) piu'
   le sinergie (Fase 5) cresce;
3. terre: 17, oppure 16 se la curva e' bassa (vedi ``land_count``); le terre non base nei colori del
   pool entrano per prime; le base si dividono in proporzione ai simboli colorati, con almeno
   ``SPLASH_SOURCES`` fonti per il colore di splash.

Le regole su terre e fonti di splash sono euristiche dichiarate, non stimate dai dati (il valutatore
di mazzo da' a ogni terra oltre le 17 circa -0,9 punti, ma l'effetto e' confuso con la curva).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Sequence

from .book import COLORS, PAIRS, Card, CardBook
from .deckmodel import DeckModel, deck_features

MAX_SPLASH = 3
SPLASH_MIN_Q = 0.02      # uno splash si propone solo per carte forti (euristica: +2 punti di qualita')
SPLASH_SOURCES = 3
MIN_MAIN_CARDS = 4       # con tre colori principali: almeno 4 carte per colore (altrimenti e' uno splash)
MIN_MAIN_SOURCES = 3     # fonti minime per ogni colore principale
BASIC_NAMES = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
DECK_SIZE = 40


@dataclass
class Build:
    main: str
    splash: str
    spells: list[Card]
    lands: list[Card]                 # terre non base scelte
    basics: dict[str, int]
    score: float                      # punteggio del valutatore (relativo) + sinergie
    synergy: float
    expected_wr: float                # win rate attesa per un giocatore medio (indicativa)
    features: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.main + (f" + splash {self.splash}" if self.splash else "")

    @property
    def n_lands(self) -> int:
        return len(self.lands) + sum(self.basics.values())

    def counts(self) -> Counter:
        c = Counter(x.name for x in self.spells) + Counter(x.name for x in self.lands)
        for n, k in self.basics.items():
            c[n] += k
        return c

    def curve(self) -> dict[int, int]:
        out: Counter = Counter()
        for c in self.spells:
            out[min(int(c.mv), 6)] += 1
        return dict(sorted(out.items()))


def land_count(spells: Sequence[Card]) -> int:
    """17 terre; 16 con curva bassa (costo medio <= 2,75 e al massimo 3 carte di costo >= 5)."""
    if not spells:
        return 17
    avg = sum(c.mv for c in spells) / len(spells)
    heavy = sum(c.mv >= 5 for c in spells)
    return 16 if avg <= 2.75 and heavy <= 3 else 17


def _allowed(c: Card, main: str, splash: str) -> bool:
    if c.is_basic or c.is_land:
        return False
    if c.castable_in(main):
        return True
    if not splash or not c.castable_in(main + splash):
        return False
    return c.pips.count(splash) <= 1


def _land_ok(c: Card, colors: str) -> bool:
    prod = c.produces or ""
    return c.is_land and not c.is_basic and (not prod or all(x in colors for x in prod)) and \
        (c.q >= 0 or any(x in colors for x in prod))


class Builder:
    def __init__(self, book: CardBook, model: DeckModel, synergy=None, player_wr: float = 0.55):
        self.book, self.model, self.synergy, self.player_wr = book, model, synergy, player_wr

    # -- punteggio -----------------------------------------------------------------------------
    def evaluate(self, spells: Sequence[Card], lands: Sequence[Card], basics: dict[str, int], main: str,
                 splash: str) -> tuple[float, float, dict[str, float]]:
        counts: Counter = Counter(c.name for c in spells) + Counter(c.name for c in lands)
        for n, k in basics.items():
            if n in self.book.cards:
                counts[n] += k
        x = deck_features(dict(counts), self.book, main, splash)
        # le terre base mancanti nel catalogo contano comunque come terre
        missing = sum(k for n, k in basics.items() if n not in self.book.cards)
        x["lands"] = x.get("lands", 0.0) + missing
        syn = self.synergy.value(list(spells) + list(lands)) if self.synergy is not None else 0.0
        return self.model.score(x) + syn, syn, x

    def _basics(self, spells: Sequence[Card], lands: Sequence[Card], n_lands: int, main: str, splash: str
                ) -> dict[str, int]:
        n_basic = max(0, n_lands - len(lands))
        pips = Counter(p for c in spells for p in c.pips if p in main + splash)
        sources = Counter(p for l in lands for p in (l.produces or "") if p in main + splash)
        out: Counter = Counter()
        if splash:
            need = max(0, SPLASH_SOURCES - sources[splash])
            out[splash] = min(need, n_basic)
            n_basic -= out[splash]
        # prima il minimo di fonti per colore principale, poi il resto in proporzione ai simboli
        for c in main:
            need = max(0, MIN_MAIN_SOURCES - sources[c])
            give = min(need, n_basic)
            out[c] += give
            n_basic -= give
        weights = {c: pips[c] + 0.5 for c in main}
        tot = sum(weights.values())
        alloc = {c: n_basic * w / tot for c, w in weights.items()}
        for c in main:
            out[c] += int(alloc[c])
        rest = n_basic - sum(int(alloc[c]) for c in main)
        for c in sorted(main, key=lambda c: alloc[c] - int(alloc[c]), reverse=True)[:rest]:
            out[c] += 1
        return {BASIC_NAMES[c]: k for c, k in out.items() if k > 0}

    # -- costruzione ---------------------------------------------------------------------------
    def build(self, pool: Sequence[Card], main: str, splash: str = "", passes: int = 3) -> Build | None:
        cands = [c for c in pool if _allowed(c, main, splash)]
        splash_cards = sorted((c for c in cands if not c.castable_in(main)), key=lambda c: c.q, reverse=True)
        splash_cards = [c for c in splash_cards if c.q >= SPLASH_MIN_Q]
        if splash and not splash_cards:
            return None
        cands = [c for c in cands if c.castable_in(main)] + splash_cards[:MAX_SPLASH]
        lands = [c for c in pool if _land_ok(c, main + splash)]
        cands.sort(key=lambda c: c.q, reverse=True)
        n_lands = 17
        n_spells = DECK_SIZE - n_lands
        if len(cands) < n_spells - 1:      # serve quasi un mazzo intero di carte giocabili
            return None
        chosen = cands[:n_spells]
        n_lands = land_count(chosen)
        n_spells = DECK_SIZE - n_lands
        chosen = cands[:n_spells]
        lands = lands[: max(0, n_lands - 4)]       # almeno qualche base per colore
        basics = self._basics(chosen, lands, n_lands, main, splash)
        best, syn, x = self.evaluate(chosen, lands, basics, main, splash)
        bench = [c for c in cands if c not in chosen]
        for _ in range(passes):
            improved = False
            for i in range(len(chosen)):
                for j in range(len(bench)):
                    trial = chosen[:i] + [bench[j]] + chosen[i + 1:]
                    s, sy, xx = self.evaluate(trial, lands, basics, main, splash)
                    if s > best + 1e-9:
                        chosen[i], bench[j] = bench[j], chosen[i]
                        best, syn, x = s, sy, xx
                        improved = True
            if not improved:
                break
        # terre ricalcolate sulla scelta finale
        n_new = land_count(chosen)
        if n_new != n_lands:
            n_lands = n_new
            n_spells = DECK_SIZE - n_lands
            chosen = sorted(chosen, key=lambda c: c.q, reverse=True)
            pool_rest = sorted(bench, key=lambda c: c.q, reverse=True)
            chosen = (chosen + pool_rest)[:n_spells]
        if len(chosen) < n_spells:
            return None
        if len(main) >= 3 and any(sum(col in c.colors for c in chosen) < MIN_MAIN_CARDS for col in main):
            return None
        basics = self._basics(chosen, lands, n_lands, main, splash)
        best, syn, x = self.evaluate(chosen, lands, basics, main, splash)
        wr = self.model.intercept + self.model.control_coef * self.player_wr + best
        notes = []
        if splash:
            sc = [c.name for c in chosen if not c.castable_in(main)]
            notes.append(f"splash {splash}: {', '.join(sc)}")
        return Build(main, splash, sorted(chosen, key=lambda c: (c.mv, c.name)), lands, basics, best, syn, wr, x,
                     notes)

    def best_builds(self, pool: Sequence[Card], k: int = 3) -> list[Build]:
        """Le k build migliori tra: coppie, coppie con splash, tre colori principali."""
        import itertools
        out = []
        for main in PAIRS:
            b = self.build(pool, main)
            if b:
                out.append(b)
            for s in COLORS:
                if s not in main:
                    b = self.build(pool, main, s)
                    if b:
                        out.append(b)
        for tri in itertools.combinations(COLORS, 3):
            b = self.build(pool, "".join(tri))
            if b:
                out.append(b)
        out.sort(key=lambda b: b.score, reverse=True)
        # build distinte: stesso insieme di magie = stessa build
        seen, uniq = set(), []
        for b in out:
            key = frozenset(c.name for c in b.spells)
            if key not in seen:
                seen.add(key)
                uniq.append(b)
        return uniq[:k]
