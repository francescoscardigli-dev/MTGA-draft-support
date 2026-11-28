"""Verifica della Fase 7 (segnali e wheel) su draft 17Lands, senza dati futuri.

Stima fino al giorno di taglio: qualita' (prior sui set precedenti), modello del mazzo, lambda degli
archetipi, attese dei segnali e loro pesi, modello di wheel. Verifica sui draft successivi dei giocatori
forti: calibrazione del wheel, verosimiglianza dei colori finali con e senza segnali, accordo dei pick
con quattro varianti del motore (base, + segnali, + wheel, + entrambi).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from ..config import format_for, load_format
from .backtest import Agreement, _cards, archetype_samples, final_colors, pick_agreement, read_drafts
from .projection import Projector, fit_lambda
from .signals import SignalBaseline, SignalModel, fit_signal_model
from .wheel import LogitWheel, calibration, fit_logit_wheel, wheel_samples


@dataclass
class Phase7Result:
    set_code: str
    format: str
    cutoff: str
    signal: SignalModel
    wheel: LogitWheel
    wheel_cal: dict
    agreement: dict[str, Agreement]
    n_test: int


def popularity(con, set_code: str, fmt: str, as_of: str | None) -> dict[str, float]:
    from ..stats.ratings import Filters, ratings
    r = ratings(con, set_code, fmt, Filters(as_of=as_of)).cards.set_index("card")
    return (1 - r["ata_norm"].clip(0, 1)).fillna(0.5).to_dict()


def run(con: sqlite3.Connection, set_code: str, fmt: str, cutoff: str, prior_sets: list[str],
        n_test: int = 200, n_fit: int = 600, log=print) -> Phase7Result:
    from ..stats.prior import fit, load_prior, tables_for
    from .book import load_book
    from .deckmodel import fit_deck_model

    fmt_cfg = format_for(fmt, set_code)
    saved = load_prior(fmt)
    prior = fit(tables_for(con, prior_sets, fmt), fmt)
    prior.half_life = saved.half_life if saved else None
    book = load_book(con, set_code, fmt, prior=prior, as_of=cutoff, half_life=prior.half_life)
    model = fit_deck_model(con, {set_code: book}, fmt, as_of=cutoff)
    proj = Projector(book, model, fmt_cfg)
    colors = final_colors(set_code, fmt)
    fit_d = read_drafts(set_code, fmt, until=cutoff, max_drafts=n_fit, seed=1)
    proj.arch = fit_lambda(proj, archetype_samples(fit_d, colors, book))
    log(f"stima: {len(fit_d)} draft fino al {cutoff}")
    since = (date.fromisoformat(cutoff) + timedelta(days=1)).isoformat()
    test = read_drafts(set_code, fmt, since=since, tiers=("high",), max_drafts=n_test, seed=2)
    log(f"verifica: {len(test)} draft dal {since}")

    base = SignalBaseline.fit((p.pack_number, p.pick_number, _cards(book, p.pack)) for d in fit_d for p in d.picks)
    sig = fit_signal_model(proj, fit_d, test, colors, base)
    log(f"segnali: g_same {sig.g_same:.0f}, g_other {sig.g_other:.0f}, guadagno di log-verosimiglianza "
        f"{sig.ll_gain:+.4f}")
    pop = popularity(con, set_code, fmt, cutoff)
    wm = fit_logit_wheel(list(wheel_samples(fit_d, book, fmt_cfg)), fmt_cfg, pop)
    cal = calibration(wm, list(wheel_samples(test, book, fmt_cfg)), fmt_cfg, pop)
    log(f"wheel: Brier {cal['brier']:.4f} (costante {cal['brier_const']:.4f})")

    ata = {}
    variants = {"base": dict(), "segnali": dict(signals=(base, sig)), "wheel": dict(wheel=(wm, pop)),
                "segnali + wheel": dict(signals=(base, sig), wheel=(wm, pop))}
    agree = {}
    for name, kw in variants.items():
        a = next(x for x in pick_agreement(proj, test, ata, **kw) if x.label == "motore")
        agree[name] = a
        log(f"  {name}: top-1 {a.top1:.1%}, top-3 {a.top3:.1%}")
    return Phase7Result(set_code, fmt, cutoff, sig, wm, cal, agree, len(test))


def format_result(r: Phase7Result) -> str:
    out = [f"# Verifica Fase 7 {r.set_code} {r.format} (taglio {r.cutoff})", "",
           f"draft di verifica: {r.n_test} (giocatori fascia high)", "",
           "## Segnali",
           f"pesi: stessa direzione {r.signal.g_same:.0f}, altra direzione {r.signal.g_other:.0f}",
           f"log-verosimiglianza media dei colori finali, con segnali - senza: {r.signal.ll_gain:+.4f} "
           f"(positivo = i segnali migliorano la previsione)", "",
           "## Wheel",
           f"coefficienti: {', '.join(f'{x:+.2f}' for x in r.wheel.coef)} "
           "(intercetta, posizione relativa, posizione^2, popolarita', dimensione busta)",
           f"Brier {r.wheel_cal['brier']:.4f} contro {r.wheel_cal['brier_const']:.4f} di una probabilita' costante "
           f"({r.wheel_cal['rate']:.0%} delle carte torna; {r.wheel_cal['n']} carte)",
           "prevista -> frequenza reale:"]
    out += [f"  {lo:.1f}-{hi:.1f}: {m:.2f} -> {y:.2f} (n {n})" for lo, hi, n, m, y in r.wheel_cal["bins"]]
    out += ["", "## Accordo con i giocatori forti",
            f"{'variante':<16} {'top-1':>7} {'top-3':>7} {'carte in comune':>16}"]
    for k, a in r.agreement.items():
        ov = f"{a.overlap:16.1%}" if a.overlap is not None else ""
        out.append(f"{k:<16} {a.top1:7.1%} {a.top3:7.1%} {ov}")
    b = r.agreement.get("base")
    if b and b.diff_ci is not None:
        pass
    return "\n".join(out)
