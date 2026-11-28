"""Comandi CLI della Fase 4: fit-deck-model, recommend, backtest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import db
from .config import load_format, load_set
from .engine.book import PAIRS, Card, load_book
from .engine.deckmodel import fit_deck_model, load_deck_model
from .engine.pick import recommend
from .engine.projection import ArchetypeModel, Projector, fit_lambda
from .sources.seventeenlands import ATTRIBUTION
from .stats.prior import load_prior


def add_parsers(sub: argparse._SubParsersAction) -> None:
    fd = sub.add_parser("fit-deck-model", help="stima il valutatore di mazzo e il modello degli archetipi")
    fd.add_argument("--format", dest="fmt", default="premier")
    fd.add_argument("--sets", nargs="+", required=True, help="set con game data")
    fd.add_argument("--lambda-set", help="set dei cui draft stimare lambda (default: il primo)")
    fd.add_argument("--drafts", type=int, default=1500, help="draft per stimare lambda")

    rc = sub.add_parser("recommend", help="suggerisce il pick: da Player.log o da elenchi di carte")
    rc.add_argument("code")
    rc.add_argument("--format", dest="fmt", default="premier")
    rc.add_argument("--pack", help="carte della busta separate da ';'")
    rc.add_argument("--pool", default="", help="carte gia' prese separate da ';'")
    rc.add_argument("--log", type=Path, nargs="?", const=Path(), help="usa l'ultimo pick di Player.log")
    rc.add_argument("-k", type=int, default=3)
    rc.add_argument("--synergy", action="store_true",
                    help="usa le sinergie nel punteggio (sperimentale: nel backtest peggiorano l'accordo)")

    bd = sub.add_parser("build", help="costruisce 2-3 mazzi dal pool (da elenco o da Player.log)")
    bd.add_argument("code")
    bd.add_argument("--format", dest="fmt", default="premier")
    bd.add_argument("--pool", default="", help="carte del pool separate da ';'")
    bd.add_argument("--log", type=Path, nargs="?", const=Path(), help="pool dell'ultimo draft di Player.log")
    bd.add_argument("-k", type=int, default=3)
    bd.add_argument("--synergy", action="store_true", help="usa le sinergie nel punteggio (sperimentale)")

    bc = sub.add_parser("build-check", help="verifica il costruttore di mazzi sui mazzi reali 17Lands")
    bc.add_argument("code")
    bc.add_argument("--format", dest="fmt", default="premier")
    bc.add_argument("--cutoff", required=True)
    bc.add_argument("--prior-sets", nargs="+", required=True)
    bc.add_argument("--drafts", type=int, default=300)
    bc.add_argument("--out", type=Path)

    fx = sub.add_parser("fit-extras", help="stima wheel e segnali (Fase 7) su un set con game data")
    fx.add_argument("code")
    fx.add_argument("--format", dest="fmt", default="premier")
    fx.add_argument("--drafts", type=int, default=600)

    lv = sub.add_parser("live", help="segue Player.log e suggerisce i pick in tempo reale")
    lv.add_argument("--log", type=Path, help="percorso di Player.log (default: quello standard)")
    lv.add_argument("--set", dest="live_set", help="forza il set (se il nome evento non lo dice)")
    lv.add_argument("--format", dest="fmt", help="forza il formato")
    lv.add_argument("--once", action="store_true", help="legge il file una volta ed esce (prove)")
    lv.add_argument("--from-start", action="store_true", help="rilegge il file dall'inizio")

    p7 = sub.add_parser("check-extras", help="verifica wheel e segnali (Fase 7) su draft reali")
    p7.add_argument("code")
    p7.add_argument("--format", dest="fmt", default="premier")
    p7.add_argument("--cutoff", required=True)
    p7.add_argument("--prior-sets", nargs="+", required=True)
    p7.add_argument("--drafts", type=int, default=150)
    p7.add_argument("--out", type=Path)

    bt = sub.add_parser("backtest", help="accordo con i giocatori forti e verifica del valutatore di mazzo")
    bt.add_argument("code")
    bt.add_argument("--format", dest="fmt", default="premier")
    bt.add_argument("--cutoff", required=True, help="ultimo giorno dei dati usati per stimare (AAAA-MM-GG)")
    bt.add_argument("--prior-sets", nargs="+", required=True)
    bt.add_argument("--drafts", type=int, default=300, help="draft di verifica")
    bt.add_argument("--fit-drafts", type=int, default=1500, help="draft per stimare lambda")
    bt.add_argument("--out", type=Path, help="scrive il report anche in questo file")


def _projector(con, code: str, fmt: str, synergy: bool = False) -> Projector:
    prior = load_prior(fmt)
    model = load_deck_model(fmt)
    if model is None:
        raise SystemExit(f"Modello del mazzo assente: esegui python -m draftbot fit-deck-model --format {fmt} --sets ...")
    book = load_book(con, code, fmt, prior=prior, half_life=prior.half_life if prior else None)
    arch = ArchetypeModel(model.lambdas, mix=model.arch_mix, tiebreak=model.tiebreak) if model.lambdas else None
    proj = Projector(book, model, load_format(fmt), arch)
    # sinergie: solo se misurate sui game data di questo set (i temi cambiano da set a set)
    from .synergy.measure import load_synergy
    from .synergy.runtime import SynergyRuntime
    from .synergy.tags import load_tags
    sm, tags = load_synergy(code, fmt), load_tags(code)
    proj.synergy_info = SynergyRuntime.build(sm, tags, book) if sm is not None and tags is not None else None
    # nel punteggio solo su richiesta: nel backtest su FRA le sinergie peggiorano l'accordo (Fase 5)
    if synergy:
        proj.synergy = proj.synergy_info
    return proj


def _resolve(book, names: list[str]) -> list[Card]:
    from .names import keys
    by_key = {k: c for c in book.cards.values() for k in keys(c.name)}
    out, missing = [], []
    for n in names:
        c = book.cards.get(n) or next((by_key[k] for k in keys(n) if k in by_key), None)
        (out.append(c) if c else missing.append(n))
    if missing:
        print("Carte non trovate: " + ", ".join(missing), file=sys.stderr)
    return out


def _from_log(con, code: str, path: Path, book, final: bool = False) -> tuple[list[Card], list[Card], str] | None:
    from .arenalog import find_logs, parse_files
    logs = [path] if path and str(path) not in ("", ".") else find_logs()
    if not logs:
        print("Player.log non trovato: indica il file con --log <percorso>", file=sys.stderr)
        return None
    drafts = parse_files(logs).results()
    if not drafts:
        print("Nessun draft nel log.", file=sys.stderr)
        return None
    d = drafts[-1]
    names = db.arena_names(con, d.set_code or code)
    picks = d.picks
    last = next((p for p in reversed(picks) if p.pack_cards is not None), None)
    if last is None:
        return None
    if final:  # pool completo: tutte le carte prese (o il pool del corso, se c'e')
        ids = d.pool if d.pool else [g for p in picks for g in p.picked]
        to_cards = lambda ids: [book.cards[names[g]] for g in ids if g in names and names[g] in book.cards]
        return to_cards(ids), [], d.draft_id
    pool_ids = [g for p in picks if (p.pack, p.pick) < (last.pack, last.pick) for g in p.picked]
    to_cards = lambda ids: [book.cards[names[g]] for g in ids if g in names and names[g] in book.cards]
    return to_cards(pool_ids), to_cards(last.pack_cards), f"P{last.pack + 1}p{last.pick + 1} del draft {d.draft_id}"


def run(args: argparse.Namespace) -> int:
    con = db.connect()
    if args.command == "fit-deck-model":
        from .engine.backtest import archetype_samples, final_colors, fit_tiebreak, read_drafts
        prior = load_prior(args.fmt)
        sets = [load_set(s).code for s in args.sets]
        books = {s: load_book(con, s, args.fmt, prior=prior, half_life=prior.half_life if prior else None)
                 for s in sets}
        model = fit_deck_model(con, books, args.fmt)
        lset = load_set(args.lambda_set).code if args.lambda_set else sets[0]
        proj = Projector(books[lset], model, load_format(args.fmt))
        drafts = read_drafts(lset, args.fmt, max_drafts=args.drafts, seed=1)
        arch = fit_lambda(proj, archetype_samples(drafts, final_colors(lset, args.fmt), books[lset]))
        model.lambdas, model.lambda_set, model.arch_mix = arch.lambdas, lset, arch.mix
        proj.arch = arch
        tune = read_drafts(lset, args.fmt, tiers=("high",), max_drafts=60, seed=3)
        model.tiebreak, scores = fit_tiebreak(proj, tune)
        print(f"Spareggio: {model.tiebreak} (top-1 su {len(tune)} draft: "
              + ", ".join(f"{g}: {v:.1%}" for g, v in scores.items()) + ")")
        path = model.save()
        print(f"Modello del mazzo {args.fmt} su {', '.join(sets)} ({model.n_decks} mazzi). Coefficienti:")
        for k, v in model.coef.items():
            print(f"  {k:<13} {v * 100:+.2f} punti per unita'")
        print("  " + "; ".join(model.notes[2:]) if len(model.notes) > 2 else "", end="")
        print(f"\nLambda archetipi ({lset}, {arch.n_samples} pool): " + ", ".join(f"{x:.0f}" for x in arch.lambdas)
              + "; miscela: " + ", ".join(f"{x:.2f}" for x in arch.mix))
        print(f"Scritto: {path}")
        return 0

    if args.command == "backtest":
        from .engine import backtest as bt
        code = load_set(args.code).code
        r = bt.run(con, code, args.fmt, args.cutoff, [load_set(s).code for s in args.prior_sets],
                   n_drafts=args.drafts, n_fit_drafts=args.fit_drafts)
        text = bt.format_result(r)
        print(text)
        if args.out:
            args.out.write_text(text + "\n\n" + ATTRIBUTION + "\n", encoding="utf-8")
        print(ATTRIBUTION)
        return 0

    if args.command == "fit-extras":
        from .engine.extras import fit_extras
        ex = fit_extras(con, load_set(args.code).code, args.fmt, args.drafts)
        path = ex.save()
        print(f"Wheel: coefficienti {', '.join(f'{x:+.2f}' for x in ex.wheel.coef)}")
        print(f"Segnali: g_same {ex.signal.g_same:.0f}, g_other {ex.signal.g_other:.0f}")
        print(f"Scritto: {path}")
        return 0

    if args.command == "live":
        from .live import default_log, run_console
        path = args.log or default_log()
        if path is None:
            print("Percorso di Player.log sconosciuto su questo sistema: usa --log")
            return 1
        return run_console(path, args.live_set, args.fmt, once=args.once, from_start=args.from_start)

    if args.command == "check-extras":
        from .engine import phase7check as p7
        code = load_set(args.code).code
        r = p7.run(con, code, args.fmt, args.cutoff, [load_set(x).code for x in args.prior_sets], n_test=args.drafts)
        text = p7.format_result(r)
        print(text)
        if args.out:
            args.out.write_text(text + "\n\n" + ATTRIBUTION + "\n", encoding="utf-8")
        return 0

    if args.command == "build-check":
        from .engine import buildcheck as bc
        code = load_set(args.code).code
        c = bc.run(con, code, args.fmt, args.cutoff, [load_set(x).code for x in args.prior_sets], args.drafts)
        text = bc.format_check(code, args.fmt, args.cutoff, c)
        print(text)
        if args.out:
            args.out.write_text(text + "\n\n" + ATTRIBUTION + "\n", encoding="utf-8")
        return 0

    if args.command == "build":
        from .engine.build import Builder
        code = load_set(args.code).code
        proj = _projector(con, code, args.fmt, synergy=args.synergy)
        if args.log is not None:
            got = _from_log(con, code, args.log, proj.book, final=True)
            if got is None:
                return 1
            pool, _, where = got
            print(f"Dal log: pool del draft {where}")
        else:
            pool = _resolve(proj.book, [x.strip() for x in args.pool.split(";") if x.strip()])
        builds = Builder(proj.book, proj.model, proj.synergy).best_builds(pool, k=args.k)
        if not builds:
            print("Nessuna build possibile: servono almeno 22 carte giocabili in 2-3 colori.")
            return 1
        best = builds[0].score
        for i, b in enumerate(builds, 1):
            print(f"\n{i}. {b.label}: win rate attesa {b.expected_wr:.1%} (giocatore medio)"
                  + ("" if i == 1 else f", {(b.score - best) * 100:+.2f} punti rispetto alla 1"))
            print(f"   terre: {b.n_lands} = " + ", ".join(f"{k} {n}" for n, k in b.basics.items())
                  + (", " + ", ".join(l.name for l in b.lands) if b.lands else ""))
            print(f"   curva (costo: carte): " + ", ".join(f"{k}{'+' if k == 6 else ''}: {v}" for k, v in b.curve().items())
                  + f"; creature {sum(c.is_creature for c in b.spells)}")
            if b.synergy:
                print(f"   sinergie: {b.synergy * 100:+.2f} punti")
            for n in b.notes:
                print(f"   {n}")
            by_mv: dict[int, list[str]] = {}
            for c in b.spells:
                by_mv.setdefault(min(int(c.mv), 6), []).append(f"{c.name} ({c.q * 100:+.1f})")
            for mv, names in sorted(by_mv.items()):
                print(f"   costo {mv}{'+' if mv == 6 else ''}: " + "; ".join(sorted(names)))
        print(ATTRIBUTION)
        return 0

    # recommend
    code = load_set(args.code).code
    proj = _projector(con, code, args.fmt, synergy=args.synergy)
    if args.log is not None:
        got = _from_log(con, code, args.log, proj.book)
        if got is None:
            return 1
        pool, pack, where = got
        print(f"Dal log: {where}")
    else:
        if not args.pack:
            print("Indica --pack (e --pool) oppure --log", file=sys.stderr)
            return 1
        pack = _resolve(proj.book, [x.strip() for x in args.pack.split(";") if x.strip()])
        pool = _resolve(proj.book, [x.strip() for x in args.pool.split(";") if x.strip()])
    # wheel (Fase 7): se c'e' il modello, conta la probabilita' che le carte tornino al giro dopo.
    # Da elenco di carte la posizione nel draft si deduce dalla dimensione della busta.
    from .engine.extras import load_extras
    ex = load_extras(args.fmt)
    wheel_fn, wprobs = None, {}
    if ex is not None and pack:
        from .engine.phase7check import popularity
        pop = popularity(con, code, args.fmt, None)
        fmt_cfg = load_format(args.fmt)
        wheel_fn = lambda rem: ex.wheel.survival(rem, fmt_cfg, pop)
        wprobs = dict(zip((c.name for c in pack), ex.wheel.survival(pack, fmt_cfg, pop)))
    opts, probs = recommend(proj, pool, pack, k=args.k, wheel=wheel_fn)
    top = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)[:4]
    print(f"Pool: {len(pool)} carte. Archetipi piu' probabili: "
          + ", ".join(f"{a} {p:.0%}" for a, p in top))
    info = proj.synergy_info
    if info is None:
        print("Sinergie: non misurate per questo set (servono i game data: python -m draftbot synergy)")
    elif proj.synergy is None:
        print("Sinergie: mostrate come informazione, non nel punteggio (usa --synergy per includerle)")
    for i, o in enumerate(opts, 1):
        wb = f" (di cui wheel {o.wheel_bonus * 100:+.2f})" if o.wheel_bonus else ""
        print(f"{i}. {' + '.join(o.names)}  ->  {o.value * 100:+.2f} punti di win rate attesa{wb}")
        print(f"   {o.explain()}")
        if info is not None:
            roles = {n: info.roles(n) for n in o.names}
            roles = {n: r for n, r in roles.items() if r}
            if roles:
                print("   temi: " + "; ".join(f"{n}: {', '.join(r)}" for n, r in roles.items()))
    if wprobs and any(o.wheel_bonus for o in opts):
        top = sorted(wprobs.items(), key=lambda kv: -kv[1])[:6]
        print("Probabilita' di ritorno al giro dopo: " + ", ".join(f"{n} {w:.0%}" for n, w in top))
    print(ATTRIBUTION)
    return 0
