"""Backtest del motore su draft reali 17Lands (dati pubblici), senza sbirciare nel futuro.

Tutto viene stimato con i dati fino al giorno di taglio (prior sui set precedenti, qualita' delle
carte, modello del mazzo, lambda degli archetipi) e verificato sui draft successivi.

1. Accordo con i giocatori forti (fascia high): la loro scelta e' la prima del motore (top-1) o tra
   le prime 3 (top-3). Confronto con due riferimenti: la carta di qualita' piu' alta (senza pool) e
   la carta con ATA piu' bassa (la scelta "della media dei giocatori").
2. Valutatore di mazzo fuori campione: sui mazzi dopo il taglio, la win rate reale (al netto del
   giocatore) cresce come previsto dal punteggio del mazzo? Pendenza attesa circa 1.
3. Mazzo proposto contro mazzo reale: dal pool finale, il mazzo migliore secondo il motore (23 carte
   nella coppia di colori migliore) contro quello costruito dal giocatore, con lo stesso valutatore.
"""

from __future__ import annotations

import sqlite3
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np
import pandas as pd

from ..config import format_for, load_format, load_set
from ..paths import raw_dir
from ..stats.ingest import tier_of
from .book import PAIRS, Card, CardBook
from .deckmodel import DECK_SLOTS, CardArrays, DeckModel, deck_matrix_features, load_decks
from .pick import recommend
from .projection import Projector, fit_lambda


@dataclass
class PickRow:
    pack_number: int
    pick_number: int
    pack: list[str]
    pool: list[str]
    picked: list[str]


@dataclass
class DraftRec:
    draft_id: str
    day: str
    tier: str
    picks: list[PickRow] = field(default_factory=list)

    def final_pool(self) -> list[str]:
        last = max(self.picks, key=lambda p: (p.pack_number, p.pick_number))
        return last.pool + last.picked


def draft_data_path(set_code: str, fmt: str):
    cfg, f = load_set(set_code), load_format(fmt)
    p = raw_dir("17lands") / f"draft_data_public.{cfg.seventeenlands_expansion}.{f.seventeenlands_event_type}.csv.gz"
    if not p.is_file():
        raise FileNotFoundError(f"{p.name} non in cache: esegui python -m draftbot update-data {cfg.code}")
    return p


def read_drafts(set_code: str, fmt: str, *, since: str | None = None, until: str | None = None,
                tiers: tuple[str, ...] | None = None, max_drafts: int = 500, seed: int = 0,
                chunksize: int = 40_000) -> list[DraftRec]:
    """Draft completi nel periodo indicato. Se sono piu' di ``max_drafts`` se ne sceglie un campione
    casuale (riproducibile): per ogni draft id si usa un hash, cosi' non serve tenerli tutti in memoria."""
    path = draft_data_path(set_code, fmt)
    h = pd.read_csv(path, nrows=0).columns
    pack_cols = [c for c in h if c.startswith("pack_card_")]
    pool_cols = [c for c in h if c.startswith("pool_")]
    from .deckmodel import canonical_names
    cards = canonical_names(set_code, [c[len("pack_card_"):] for c in pack_cols])
    pool_names = canonical_names(set_code, [c[len("pool_"):] for c in pool_cols])
    canon = dict(zip([c[len("pack_card_"):] for c in pack_cols], cards))
    meta = ["draft_id", "draft_time", "pack_number", "pick_number", "pick", "user_game_win_rate_bucket"]
    if "pick_2" in h:
        meta.append("pick_2")
    dtypes = {c: np.int8 for c in pack_cols + pool_cols}
    dtypes.update({m: str for m in meta})
    drafts: dict[str, DraftRec] = {}
    salt = seed
    # soglia sull'hash: si tengono piu' draft del necessario e si sfoltisce alla fine
    keep_frac = 1.0
    for ch in pd.read_csv(path, usecols=meta + pack_cols + pool_cols, dtype=dtypes, chunksize=chunksize):
        day = ch["draft_time"].str[:10]
        m = pd.Series(True, index=ch.index)
        if since:
            m &= day >= since
        if until:
            m &= day <= until
        if tiers:
            m &= tier_of(ch["user_game_win_rate_bucket"]).isin(tiers)
        ch = ch[m]
        if not len(ch):
            continue
        hv = ch["draft_id"].map(lambda s: _h(s, salt) % 10_000 / 10_000)
        ch = ch[hv < keep_frac]
        P = ch[pack_cols].to_numpy()
        Q = ch[pool_cols].to_numpy()
        for i, r in enumerate(ch.itertuples(index=False)):
            d = drafts.get(r.draft_id)
            if d is None:
                d = drafts[r.draft_id] = DraftRec(r.draft_id, r.draft_time[:10],
                                                  str(tier_of(pd.Series([r.user_game_win_rate_bucket])).iloc[0]))
            pack = [cards[j] for j in np.nonzero(P[i])[0] for _ in range(int(P[i, j]))]
            pool = [pool_names[j] for j in np.nonzero(Q[i])[0] for _ in range(int(Q[i, j]))]
            picked = [canon.get(x, x) for x in (r.pick, getattr(r, "pick_2", None)) if isinstance(x, str) and x]
            d.picks.append(PickRow(int(r.pack_number), int(r.pick_number), pack, pool, picked))
        if len(drafts) > 4 * max_drafts:  # troppi: si alza la selettivita' dell'hash
            keep_frac /= 2
            drafts = {k: v for k, v in drafts.items() if _h(k, salt) % 10_000 / 10_000 < keep_frac}
    fmt_cfg = format_for(fmt, set_code)
    full = [d for d in drafts.values() if len(d.picks) == fmt_cfg.total_picks]
    full.sort(key=lambda d: _h(d.draft_id, salt))
    return full[:max_drafts]


def _h(s: str, salt: int) -> int:
    return zlib.crc32(f"{salt}:{s}".encode())


def _cards(book: CardBook, names: list[str]) -> list[Card]:
    return [book.cards[n] for n in names if n in book.cards]


# --------------------------------------------------------------------------- archetipi

def archetype_samples(drafts: list[DraftRec], colors: dict[str, str], book: CardBook, every: int = 2
                      ) -> Iterator[tuple[list[Card], str]]:
    for d in drafts:
        final = colors.get(d.draft_id)
        if not final or len(final) != 2:
            continue
        for i, p in enumerate(sorted(d.picks, key=lambda p: (p.pack_number, p.pick_number))):
            if i % every == 0:
                yield _cards(book, p.pool), final


def final_colors(set_code: str, fmt: str) -> dict[str, str]:
    _, info = load_decks(set_code, fmt)
    info = info.reset_index()
    info = info[info["build_index"].astype(str) == "0"]
    mc = info["colors"].fillna("").map(lambda s: "".join(c for c in "WUBRG" if c in s))
    return dict(zip(info["draft_id"], mc))


# --------------------------------------------------------------------------- 1. accordo

@dataclass
class Agreement:
    label: str
    n: int
    top1: float
    top3: float
    by_phase: dict[str, tuple[int, float, float]]
    overlap: float | None = None    # Pick Two: quota delle carte scelte presenti nella prima coppia
    diff_ci: dict[str, tuple[float, float, float]] = field(default_factory=dict)  # top-1 motore - altro
    per_draft: list[list[int]] = field(default_factory=list)   # per draft: [pick valutati, top-1 giusti]


def _phase(pick_number: int, picks_per_pack: int) -> str:
    third = picks_per_pack / 3
    return "inizio busta" if pick_number < third else ("meta' busta" if pick_number < 2 * third else "fine busta")


def pick_agreement(projector: Projector, drafts: list[DraftRec], ata: dict[str, float],
                   signals=None, wheel=None, imitation=None) -> list[Agreement]:
    """``signals``: (SignalBaseline, SignalModel) da usare durante il draft; ``wheel``: modello di wheel
    con metodo survival(carte, fmt, pop) e il dizionario pop (tupla (modello, pop))."""
    fmt = projector.fmt
    n_turn = fmt.picks_per_turn
    book = projector.book
    stats = {k: defaultdict(lambda: [0, 0, 0, 0.0]) for k in ("motore", "qualita'", "ATA")}
    per_draft: dict[str, list[list[int]]] = {k: [] for k in stats}   # per draft: [pick, top-1]

    def rank_simple(pack: list[Card], key) -> list[tuple[str, ...]]:
        # coppie (o carte) ordinate per somma della chiave
        import itertools
        opts = {tuple(sorted(c.name for c in o)): sum(key(c) for c in o)
                for o in (itertools.combinations(pack, n_turn) if n_turn > 1 else ((c,) for c in pack))}
        return [k for k, _ in sorted(opts.items(), key=lambda kv: kv[1], reverse=True)]

    from .signals import SignalState, extra_logits, future_shift
    wheel_fn = None
    if wheel is not None:
        wm, pop = wheel
        wheel_fn = lambda rem: wm.survival(rem, fmt, pop)
    for d in drafts:
        for k in per_draft:
            per_draft[k].append([0, 0])
        st = SignalState()
        for p in sorted(d.picks, key=lambda p: (p.pack_number, p.pick_number)):
            pack = _cards(book, p.pack)
            xl, sh = None, None
            if signals is not None:
                st.observe(p.pack_number, p.pick_number, pack, fmt, signals[0])
                xl = extra_logits(signals[1], st, fmt.pass_directions[p.pack_number])
                sh = future_shift(st, fmt, p.pack_number, p.pick_number, signals[1].future_shift)
            if len(pack) < len(p.pack) or len(p.picked) != n_turn or len(pack) < n_turn:
                continue
            human = tuple(sorted(p.picked))
            pool = _cards(book, p.pool)
            opts, _ = recommend(projector, pool, pack, k=3, extra_logits=xl, wheel=wheel_fn, shift=sh,
                                imitation=imitation)
            ranked = {"motore": [tuple(sorted(o.names)) for o in opts],
                      "qualita'": rank_simple(pack, lambda c: c.q)[:3],
                      "ATA": rank_simple(pack, lambda c: -ata.get(c.name, 99.0))[:3]}
            ph = _phase(p.pick_number, fmt.picks_per_pack)
            for k, r in ranked.items():
                for key in ("tutti", ph):
                    s = stats[k][key]
                    s[0] += 1
                    s[1] += r[0] == human
                    s[2] += human in r
                    s[3] += len(set(r[0]) & set(human)) / n_turn if n_turn > 1 else float(r[0] == human)
                per_draft[k][-1][0] += 1
                per_draft[k][-1][1] += r[0] == human
    # intervallo al 95% della differenza di top-1 (bootstrap sui draft: i pick di un draft non sono indipendenti)
    rng = np.random.default_rng(0)
    eng = np.array(per_draft["motore"], dtype=float)
    cis = {}
    if len(eng):
        idx = rng.integers(0, len(eng), size=(1000, len(eng)))
        for k in ("qualita'", "ATA"):
            oth = np.array(per_draft[k], dtype=float)
            diff = lambda ii: eng[ii, 1].sum(-1) / eng[ii, 0].sum(-1) - oth[ii, 1].sum(-1) / oth[ii, 0].sum(-1)
            boot = diff(idx)
            cis[k] = (float(diff(np.arange(len(eng)))), float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5)))
    out = []
    for k, st in stats.items():
        a = st["tutti"]
        if not a[0]:
            continue
        phases = {ph: (v[0], v[1] / v[0], v[2] / v[0]) for ph, v in st.items() if ph != "tutti" and v[0]}
        out.append(Agreement(k, a[0], a[1] / a[0], a[2] / a[0], phases, a[3] / a[0] if n_turn > 1 else None,
                             cis if k == "motore" else {}, per_draft[k]))
    return out


def fit_tiebreak(projector: Projector, drafts: list[DraftRec], grid=(0.0, 0.05, 0.1, 0.2, 0.5, 1.0)
                 ) -> tuple[float, dict[float, float]]:
    """Peso dello spareggio che massimizza l'accordo top-1 sui draft indicati (periodo di stima)."""
    scores = {}
    for g in grid:
        projector.arch.tiebreak = g
        a = next(x for x in pick_agreement(projector, drafts, {}) if x.label == "motore")
        scores[g] = a.top1
    best = float(max(scores, key=scores.get))
    projector.arch.tiebreak = best
    return best, scores


# --------------------------------------------------------------------------- 2-3. mazzi

@dataclass
class DeckCheck:
    n_decks: int
    slope: float
    slope_se: float
    corr: float
    proposed_minus_real: float | None = None    # punti di win rate attesi
    share_better: float | None = None
    n_compared: int = 0
    arch_same: float | None = None


def deck_checks(model: DeckModel, book: CardBook, set_code: str, fmt: str, since: str,
                drafts: list[DraftRec], projector: Projector) -> DeckCheck:
    deck, info = load_decks(set_code, fmt)
    keep = (info["day"] >= since).to_numpy()
    deck, info = deck[keep], info[keep]
    ca = CardArrays.from_book(list(deck.columns), book)
    F = deck_matrix_features(deck.to_numpy(), ca, info["colors"], info["splash"], book)
    score = F.apply(lambda r: model.score(r.to_dict()), axis=1).to_numpy()
    pl = info["player"].fillna(info["player"].mean()).to_numpy()
    y = (info["wins"] / info["games"]).to_numpy() - model.control_coef * pl
    w = info["games"].to_numpy(float)
    X = np.column_stack([np.ones(len(score)), score])
    W = np.sqrt(w)
    beta, *_ = np.linalg.lstsq(X * W[:, None], y * W, rcond=None)
    resid = y - X @ beta
    s2 = np.average(resid ** 2, weights=w)
    cov = s2 * np.linalg.inv((X * w[:, None]).T @ X)
    corr = float(np.corrcoef(score, y)[0, 1])
    res = DeckCheck(len(score), float(beta[1]), float(np.sqrt(cov[1, 1])), corr)

    # mazzo proposto contro reale
    ids = info.index.get_level_values(0)
    builds = info.index.get_level_values(1).astype(str)
    real = {did: (i, info.iloc[i]) for i, (did, b) in enumerate(zip(ids, builds)) if b == "0"}
    diffs, same = [], []
    for d in drafts:
        if d.draft_id not in real:
            continue
        i, row = real[d.draft_id]
        prop = propose_deck(book, model, _cards(book, d.final_pool()))
        if prop is None:
            continue
        pair, counts = prop
        names = list(counts)
        pca = CardArrays.from_book(names, book)
        pf = deck_matrix_features(np.array([[counts[n] for n in names]]), pca, pd.Series([pair]),
                                  pd.Series([""]), book).iloc[0].to_dict()
        pf["lands"] = 0.0
        diffs.append(model.score(pf) - score[i])
        mc = "".join(c for c in "WUBRG" if c in (row["colors"] or ""))
        same.append(mc == pair)
    if diffs:
        res.proposed_minus_real = float(np.mean(diffs))
        res.share_better = float(np.mean(np.array(diffs) >= 0))
        res.n_compared = len(diffs)
        res.arch_same = float(np.mean(same))
    return res


def propose_deck(book: CardBook, model: DeckModel, pool: list[Card]) -> tuple[str, Counter] | None:
    """Mazzo semplice: le 23 carte migliori nella coppia di colori con il punteggio migliore.
    La costruzione vera (curva, terre, splash, 2-3 build) e' la Fase 6."""
    best = None
    for a in PAIRS:
        cs = sorted((c for c in pool if not c.is_basic and c.castable_in(a)), key=lambda c: c.q, reverse=True)
        if len(cs) < DECK_SLOTS:
            continue
        cs = cs[:DECK_SLOTS]
        v = np.array([c.q for c in cs])
        s = model.coef["mean_q"] * v.mean() + model.coef["top3"] * v[:3].mean() + model.coef["arch"] * book.arch_delta.get(a, 0)
        if best is None or s > best[0]:
            best = (s, a, Counter(c.name for c in cs))
    if best is None:
        return None
    counts = best[2]
    # terre base: 17, divise tra i due colori (non influisce sul punteggio)
    for col, basic in zip(best[1], _basics(book, best[1])):
        if basic:
            counts[basic] += 17 // 2 + (1 if col == best[1][0] else 0)
    return best[1], counts


def _basics(book: CardBook, pair: str) -> list[str | None]:
    names = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
    return [names[c] if names[c] in book.cards else None for c in pair]


# --------------------------------------------------------------------------- orchestrazione

@dataclass
class BacktestResult:
    set_code: str
    format: str
    cutoff: str
    prior_sets: list[str]
    n_fit_drafts: int
    agreement: list[Agreement]
    deck: DeckCheck
    model: DeckModel
    lambdas: list[float]
    tiebreak: float = 0.0
    mix: list[float] | None = None
    agreement_syn: list[Agreement] | None = None
    synergy_summary: str = ""


def run(con: sqlite3.Connection, set_code: str, fmt: str, cutoff: str, prior_sets: list[str], *,
        n_drafts: int = 300, n_fit_drafts: int = 1500, synergy: bool = True, log=print) -> BacktestResult:
    from datetime import date, timedelta

    from ..stats.calibrate import estimate_drift
    from ..stats.prior import fit, load_prior, tables_for
    from ..stats.ratings import Filters, ratings
    from .book import load_book
    from .deckmodel import fit_deck_model

    fmt_cfg = format_for(fmt, set_code)
    saved = load_prior(fmt)
    prior = fit(tables_for(con, prior_sets, fmt), fmt)
    prior.half_life = saved.half_life if saved else None
    prior.drift_sd, _ = estimate_drift(con, prior_sets, fmt, prior.half_life)
    log(f"prior stimato su {', '.join(prior_sets)}; qualita' delle carte con i dati fino al {cutoff}")
    book = load_book(con, set_code, fmt, prior=prior, as_of=cutoff, half_life=prior.half_life)
    model = fit_deck_model(con, {set_code: book}, fmt, as_of=cutoff)
    log(f"modello del mazzo: {model.n_decks} mazzi fino al {cutoff}")
    projector = Projector(book, model, fmt_cfg)
    fit_drafts = read_drafts(set_code, fmt, until=cutoff, max_drafts=n_fit_drafts, seed=1)
    projector.arch = fit_lambda(projector, archetype_samples(fit_drafts, final_colors(set_code, fmt), book))
    log(f"archetipi su {projector.arch.n_samples} pool di {len(fit_drafts)} draft: lambda "
        + ", ".join(f"{x:.0f}" for x in projector.arch.lambdas) + "; miscela "
        + ", ".join(f"{x:.2f}" for x in projector.arch.mix or []))
    tune = read_drafts(set_code, fmt, until=cutoff, tiers=("high",), max_drafts=max(30, n_drafts // 3), seed=3)
    best, scores = fit_tiebreak(projector, tune)
    log(f"spareggio stimato su {len(tune)} draft prima del taglio: {best} "
        f"(top-1: {', '.join(f'{g}: {v:.1%}' for g, v in scores.items())})")
    since = (date.fromisoformat(cutoff) + timedelta(days=1)).isoformat()
    test = read_drafts(set_code, fmt, since=since, tiers=("high",), max_drafts=n_drafts, seed=2)
    log(f"draft di verifica: {len(test)} (giocatori fascia high, dal {since})")
    t = ratings(con, set_code, fmt, Filters(as_of=cutoff))
    ata = t.cards.set_index("card")["ata"].to_dict()
    agreement = pick_agreement(projector, test, ata)
    deck = deck_checks(model, book, set_code, fmt, since, test, projector)
    agreement_syn, syn_summary = None, ""
    if synergy:
        from ..synergy.measure import measure
        from ..synergy.runtime import SynergyRuntime
        from ..synergy.tags import generate, load_tags
        tags = load_tags(set_code) or generate(con, set_code)
        sm = measure(model, book, tags, set_code, fmt, cutoff)
        rt = SynergyRuntime.build(sm, tags, book)
        syn_summary = (f"temi tenuti: {', '.join(f'{t} {c * 100:+.2f}' for t, c in sm.effects.items()) or 'nessuno'}; "
                       f"pendenza fuori campione {sm.oos_slope:.2f} +- {sm.oos_slope_se:.2f}")
        log("sinergie: " + syn_summary)
        if rt is not None:
            projector.synergy = rt
            agreement_syn = pick_agreement(projector, test, ata)
            projector.synergy = None
    return BacktestResult(set_code, fmt, cutoff, prior_sets, len(fit_drafts), agreement, deck, model,
                          projector.arch.lambdas, projector.arch.tiebreak, projector.arch.mix, agreement_syn,
                          syn_summary)


def format_result(r: BacktestResult) -> str:
    out = [f"# Backtest {r.set_code} {r.format} (taglio {r.cutoff}, prior {'+'.join(r.prior_sets)})", "",
           f"lambda archetipi: {', '.join(f'{x:.0f}' for x in r.lambdas)}; miscela: "
           f"{', '.join(f'{x:.2f}' for x in (r.mix or []))}; spareggio: {r.tiebreak}", ""]
    out.append("## Accordo con i giocatori forti")
    out.append(f"{'metodo':<10} {'pick':>6} {'top-1':>7} {'top-3':>7} {'carte in comune':>16}")
    for a in r.agreement:
        ov = f"{a.overlap:16.1%}" if a.overlap is not None else " " * 16
        out.append(f"{a.label:<10} {a.n:>6} {a.top1:7.1%} {a.top3:7.1%} {ov}")
    if r.synergy_summary:
        out.append(f"Sinergie (Fase 5): {r.synergy_summary}")
    if r.agreement_syn:
        a = next(x for x in r.agreement_syn if x.label == "motore")
        ov = f", carte in comune {a.overlap:.1%}" if a.overlap is not None else ""
        out.append(f"motore + sinergie: top-1 {a.top1:.1%}, top-3 {a.top3:.1%}{ov}")
    eng = next((a for a in r.agreement if a.label == "motore"), None)
    if eng and eng.diff_ci:
        out.append("Differenza di top-1 del motore (intervallo al 95%, bootstrap sui draft): " + "; ".join(
            f"contro {k}: {d * 100:+.1f} punti [{lo * 100:+.1f}, {hi * 100:+.1f}]" for k, (d, lo, hi) in eng.diff_ci.items()))
    out.append("")
    out.append("Per fase della busta (top-1 / top-3):")
    for a in r.agreement:
        out.append(f"  {a.label:<10} " + "; ".join(f"{ph}: {t1:.0%} / {t3:.0%} (n {n})"
                                                   for ph, (n, t1, t3) in sorted(a.by_phase.items())))
    d = r.deck
    out += ["", "## Valutatore di mazzo (mazzi dopo il taglio)",
            f"mazzi: {d.n_decks}; pendenza win rate reale su punteggio: {d.slope:.2f} +- {d.slope_se:.2f} "
            f"(atteso 1); correlazione {d.corr:.3f}"]
    if d.proposed_minus_real is not None:
        out.append(f"mazzo proposto contro reale ({d.n_compared} draft): {d.proposed_minus_real * 100:+.2f} punti "
                   f"di win rate attesa in media; proposto >= reale nel {d.share_better:.0%} dei casi; "
                   f"stessa coppia di colori nel {d.arch_same:.0%}")
    out += ["", "Coefficienti del modello del mazzo (punti di win rate per unita'):"]
    out += [f"  {k:<13} {v * 100:+.2f}" for k, v in r.model.coef.items()]
    out.append(f"  (controllo giocatore: {r.model.control_coef:+.2f} per punto di win rate del giocatore)")
    return "\n".join(out)
