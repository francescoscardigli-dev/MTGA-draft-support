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

    bt = sub.add_parser("backtest", help="accordo con i giocatori forti e verifica del valutatore di mazzo")
    bt.add_argument("code")
    bt.add_argument("--format", dest="fmt", default="premier")
    bt.add_argument("--cutoff", required=True, help="ultimo giorno dei dati usati per stimare (AAAA-MM-GG)")
    bt.add_argument("--prior-sets", nargs="+", required=True)
    bt.add_argument("--drafts", type=int, default=300, help="draft di verifica")
    bt.add_argument("--fit-drafts", type=int, default=1500, help="draft per stimare lambda")
    bt.add_argument("--out", type=Path, help="scrive il report anche in questo file")


def _projector(con, code: str, fmt: str) -> Projector:
    prior = load_prior(fmt)
    model = load_deck_model(fmt)
    if model is None:
        raise SystemExit(f"Modello del mazzo assente: esegui python -m draftbot fit-deck-model --format {fmt} --sets ...")
    book = load_book(con, code, fmt, prior=prior, half_life=prior.half_life if prior else None)
    arch = ArchetypeModel(model.lambdas, mix=model.arch_mix, tiebreak=model.tiebreak) if model.lambdas else None
    return Projector(book, model, load_format(fmt), arch)


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


def _from_log(con, code: str, path: Path, book) -> tuple[list[Card], list[Card], str] | None:
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

    # recommend
    code = load_set(args.code).code
    proj = _projector(con, code, args.fmt)
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
    opts, probs = recommend(proj, pool, pack, k=args.k)
    top = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)[:4]
    print(f"Pool: {len(pool)} carte. Archetipi piu' probabili: "
          + ", ".join(f"{a} {p:.0%}" for a, p in top))
    for i, o in enumerate(opts, 1):
        print(f"{i}. {' + '.join(o.names)}  ->  {o.value * 100:+.2f} punti di win rate attesa")
        print(f"   {o.explain()}")
    print(ATTRIBUTION)
    return 0
