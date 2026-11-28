"""Verifica dello strato di imitazione su draft 17Lands, senza dati futuri.

Tutto e' stimato fino al giorno di taglio (qualita', modello del mazzo, archetipi, wheel, flusso dei
colori, coefficienti dello strato); la verifica e' sui draft successivi dei giocatori forti. Le variabili
di ogni pick si calcolano una volta e si salvano in data/, cosi' le varianti si confrontano in fretta.
"""

from __future__ import annotations

import pickle
from datetime import date, timedelta

import numpy as np

from ..config import format_for
from ..paths import data_dir
from .backtest import _cards, _phase, archetype_samples, final_colors, read_drafts
from .imitation import FEATURES, PickFeatures, agreement, fit, option_features, paired_ci
from .pick import recommend
from .projection import Projector, fit_lambda
from .signals import SignalBaseline, SignalModel, SignalState, fit_future_shift, future_shift
from .wheel import fit_logit_wheel, wheel_samples


def setup(con, set_code: str, fmt: str, cutoff: str, prior_sets: list[str], n_fit: int = 600, log=print):
    """Motore come in uso (wheel e flusso dei colori attivi), con tutto stimato fino al taglio."""
    from ..stats.prior import fit as fit_prior, load_prior, tables_for
    from .book import load_book
    from .deckmodel import fit_deck_model
    from .phase7check import popularity

    fmt_cfg = format_for(fmt, set_code)
    saved = load_prior(fmt)
    prior = fit_prior(tables_for(con, prior_sets, fmt), fmt)
    prior.half_life = saved.half_life if saved else None
    book = load_book(con, set_code, fmt, prior=prior, as_of=cutoff, half_life=prior.half_life)
    model = fit_deck_model(con, {set_code: book}, fmt, as_of=cutoff)
    proj = Projector(book, model, fmt_cfg)
    fit_d = read_drafts(set_code, fmt, until=cutoff, max_drafts=n_fit, seed=1)
    colors = final_colors(set_code, fmt)
    proj.arch = fit_lambda(proj, archetype_samples(fit_d, colors, book))
    base = SignalBaseline.fit((p.pack_number, p.pick_number, _cards(book, p.pack)) for d in fit_d for p in d.picks)
    delta, _, _ = fit_future_shift(proj, fit_d, colors, base)
    flow = SignalModel(0.0, 0.0, 0.0, [], future_shift=max(delta, 0.0))
    pop = popularity(con, set_code, fmt, cutoff)
    wm = fit_logit_wheel(list(wheel_samples(fit_d, book, fmt_cfg)), fmt_cfg, pop)
    log(f"{set_code} {fmt}: motore stimato fino al {cutoff} ({len(fit_d)} draft), flusso delta {delta:+.3f}")
    return proj, base, flow, wm, pop


def collect(proj: Projector, drafts, base, flow, wm, pop) -> list[PickFeatures]:
    fmt = proj.fmt
    n_turn = fmt.picks_per_turn
    wheel_fn = lambda rem: wm.survival(rem, fmt, pop)
    out: list[PickFeatures] = []
    for di, d in enumerate(drafts):
        st = SignalState()
        for p in sorted(d.picks, key=lambda p: (p.pack_number, p.pick_number)):
            pack = _cards(proj.book, p.pack)
            st.observe(p.pack_number, p.pick_number, pack, fmt, base)
            sh = future_shift(st, fmt, p.pack_number, p.pick_number, flow.future_shift)
            if len(pack) < len(p.pack) or len(p.picked) != n_turn or len(pack) < n_turn:
                continue
            pool = _cards(proj.book, p.pool)
            opts, probs = recommend(proj, pool, pack, k=10_000, wheel=wheel_fn, shift=sh)
            keys = [tuple(sorted(o.names)) for o in opts]
            human = tuple(sorted(p.picked))
            if human not in keys:
                continue
            progress = (p.pack_number * fmt.picks_per_pack + p.pick_number) / max(fmt.total_picks - 1, 1)
            X = option_features(opts, probs, pop, progress)
            out.append(PickFeatures(X, keys.index(human), di, _phase(p.pick_number, fmt.picks_per_pack), keys))
    return out


def cached(name: str, make):
    path = data_dir() / "imitation" / f"{name}.pkl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        return pickle.loads(path.read_bytes())
    obj = make()
    path.write_bytes(pickle.dumps(obj))
    return obj


def run(con, set_code: str, fmt: str, cutoff: str, prior_sets: list[str], n_train: int = 400,
        n_test: int = 300, log=print) -> dict:
    tag = f"{set_code}_{fmt}_{cutoff}"

    def make():
        proj, base, flow, wm, pop = setup(con, set_code, fmt, cutoff, prior_sets, log=log)
        since = (date.fromisoformat(cutoff) + timedelta(days=1)).isoformat()
        tr = read_drafts(set_code, fmt, until=cutoff, tiers=("high",), max_drafts=n_train, seed=5)
        te = read_drafts(set_code, fmt, since=since, tiers=("high",), max_drafts=n_test, seed=2)
        log(f"stima su {len(tr)} draft fino al {cutoff}, verifica su {len(te)} dal {since}")
        return collect(proj, tr, base, flow, wm, pop), collect(proj, te, base, flow, wm, pop)

    train, test = cached(tag, make)
    return evaluate(train, test, log=log) | {"set": set_code, "format": fmt, "cutoff": cutoff,
                                             "n_train": len(train), "n_test": len(test)}


def evaluate(train, test, log=print) -> dict:
    vi = FEATURES.index("value")
    eng1, eng3, eng_pd = agreement(test, lambda p: p.X[:, vi])
    res = {"engine": (eng1, eng3)}
    log(f"motore: top-1 {eng1:.1%}, top-3 {eng3:.1%} ({len(test)} pick)")
    variants = {
        "valore + qualita' + popolarita'": [FEATURES.index(f) for f in ("value", "q", "pop")],
        "+ fit e carte": [FEATURES.index(f) for f in ("value", "q", "pop", "fit", "rare", "gold", "land")],
        "completo (con fase del draft)": list(range(len(FEATURES))),
    }
    models = {}
    for name, cols in variants.items():
        m = fit(train, cols=cols)
        t1, t3, pd_ = agreement(test, lambda p: m.scores(p.X))
        d, lo, hi = paired_ci(pd_, eng_pd)
        ll = float(np.mean([np.log(m.probs(p.X)[p.chosen]) for p in test]))
        res[name] = (t1, t3, d, lo, hi, ll)
        models[name] = m
        log(f"{name}: top-1 {t1:.1%} ({d * 100:+.1f} [{lo * 100:+.1f}, {hi * 100:+.1f}]), top-3 {t3:.1%}, "
            f"log-verosimiglianza media {ll:.3f}")
    full = models["completo (con fase del draft)"]
    log("coefficienti (completo): " + ", ".join(f"{f} {c:+.3f}" for f, c in zip(FEATURES, full.coef)))
    by = {}
    for ph in sorted({p.phase for p in test}):
        sub = [p for p in test if p.phase == ph]
        by[ph] = (agreement(sub, lambda p: p.X[:, vi])[0], agreement(sub, lambda p: full.scores(p.X))[0], len(sub))
    log("per fase (motore -> strato): " + "; ".join(f"{k} {a:.1%} -> {b:.1%}" for k, (a, b, _) in by.items()))
    res["by_phase"] = by
    res["models"] = models
    return res
