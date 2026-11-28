"""Segnali: quali colori arrivano aperti dai vicini.

Offerta di un colore in una busta: somma, sulle carte non terra che richiedono quel colore, della
qualita' sopra la soglia di "carta giocabile" (``PLAYABLE`` = -2 punti). Per ogni busta vista (escluso il
primo pick di ogni busta, che non dice nulla sui vicini) si confronta con l'offerta attesa allo stesso
turno e si accumula lo scarto per direzione di passaggio (dalla config del formato).

Verificato su FRA (1500 draft per formato): lo scarto nei pick 2-8 della busta 1 prevede l'offerta
della busta 3, che arriva dagli stessi vicini (correlazione +0,20 Premier, +0,17 Pick Two), e quasi
non prevede quella della busta 2, che arriva dall'altra parte (+0,03 / +0,05).

Uso nel motore (Fase 7, revisione): le carte future attese di un archetipo si spostano di
``future_shift`` x segnale della direzione da cui arriveranno le buste rimanenti (projection.py), cosi'
i pick favoriscono i colori aperti. ``future_shift`` si stima sui draft: di quanto il segnale sposta la
qualita' del mazzo finale realmente ottenuto rispetto alla proiezione.
Il vecchio uso come termine nelle probabilita' dei colori (g_same, g_other) resta, ma con peso 0:
non migliorava la previsione dei colori finali.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from ..config import FormatConfig
from .book import COLORS, PAIRS, Card

PLAYABLE = -0.02


def availability(pack: Sequence[Card]) -> dict[str, float]:
    """Offerta per colore: qualita' sopra la soglia di giocabilita' delle carte di quel colore."""
    out = dict.fromkeys(COLORS, 0.0)
    for c in pack:
        if c.is_land:
            continue
        w = max(c.q - PLAYABLE, 0.0)
        for col in COLORS:
            if col in c.colors:
                out[col] += w
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
        """Scarto medio per pick per colore (tutte le direzioni): positivo = colore aperto."""
        n = max(self.n["left"] + self.n["right"], 1)
        return {c: (self.by_dir["left"][c] + self.by_dir["right"][c]) / n for c in COLORS}

    def mean(self, direction: str) -> dict[str, float]:
        n = self.n[direction]
        return {c: (self.by_dir[direction][c] / n if n else 0.0) for c in COLORS}


def future_shift(state: SignalState, fmt: FormatConfig, pack_number: int, pick_number: int,
                 delta: float) -> dict[str, float]:
    """Spostamento della qualita' delle carte future per archetipo: delta x media, sui pick rimasti, del
    segnale (scarto medio per pick) della direzione da cui arrivera' ciascuna busta."""
    if not delta:
        return {}
    dirs = [fmt.pass_directions[pack_number]] * max(fmt.picks_per_pack - pick_number - 1, 0)
    for pk in range(pack_number + 1, fmt.packs):
        dirs += [fmt.pass_directions[pk]] * fmt.picks_per_pack
    if not dirs:
        return {}
    means = {d: state.mean(d) for d in set(dirs)}
    out = {}
    for a in PAIRS:
        out[a] = delta * float(np.mean([sum(means[d][c] for c in a) for d in dirs]))
    return out


@dataclass
class SignalModel:
    g_same: float = 0.0
    g_other: float = 0.0
    ll_gain: float = 0.0            # guadagno di log-verosimiglianza media fuori campione (per pool)
    notes: list[str] = field(default_factory=list)
    future_shift: float = 0.0       # delta: spostamento delle carte future per unita' di segnale
    future_shift_se: float = 0.0
    early_temp: float = 1.0         # moltiplicatore di lambda nella prima busta (impegno sui colori)


def fit_future_shift(projector, drafts, colors: dict[str, str], base: SignalBaseline) -> tuple[float, float, int]:
    """Stima delta: di quanto il segnale sposta la qualita' del mazzo finale ottenuto rispetto alla
    proiezione senza segnale. Campioni a fine busta 1 e all'inizio della busta 2, nell'archetipo finale
    del giocatore. Restituisce (delta, errore standard, campioni)."""
    from .deckmodel import DECK_SLOTS
    fmt, book = projector.fmt, projector.book

    def top(vals):
        v = np.sort(np.concatenate([vals, np.full(DECK_SLOTS, projector.profile[-1])]))[::-1][:DECK_SLOTS]
        return float(v.mean())

    ys, zs = [], []
    for d in drafts:
        a = colors.get(d.draft_id)
        if a not in PAIRS:
            continue
        picks = sorted(d.picks, key=lambda p: (p.pack_number, p.pick_number))
        final = [book.cards[n] for n in d.final_pool() if n in book.cards]
        real = top(projector.pool_values(final, a))
        for cut in (fmt.picks_per_pack - 1, fmt.picks_per_pack + 3):
            if cut >= len(picks):
                continue
            st = SignalState()
            for q in picks[:cut + 1]:
                st.observe(q.pack_number, q.pick_number, [book.cards[n] for n in q.pack if n in book.cards], fmt, base)
            p = picks[cut]
            pool = [book.cards[n] for n in p.pool if n in book.cards]
            fut = projector.future_values(len(pool))
            pv = projector.pool_values(pool, a)
            allv = np.concatenate([pv, fut])
            order = np.argsort(-allv)[:DECK_SLOTS]
            mf = int(np.sum(order >= len(pv)))
            sh = future_shift(st, fmt, p.pack_number, p.pick_number, 1.0)[a]
            ys.append(real - top(allv))
            zs.append(mf / DECK_SLOTS * sh)
    y, z = np.array(ys), np.array(zs)
    X = np.column_stack([np.ones(len(z)), z])
    b = np.linalg.lstsq(X, y, rcond=None)[0]
    res = y - X @ b
    se = float(np.sqrt(np.sum(res ** 2) / max(len(z) - 2, 1) / max(np.sum((z - z.mean()) ** 2), 1e-12)))
    return float(b[1]), se, len(z)


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
