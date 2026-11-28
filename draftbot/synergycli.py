"""Comandi CLI della Fase 5: tag-set, synergy."""

from __future__ import annotations

import argparse

from . import db
from .config import load_format, load_set
from .sources.seventeenlands import ATTRIBUTION


def add_parsers(sub: argparse._SubParsersAction) -> None:
    ts = sub.add_parser("tag-set", help="genera i tag dei temi di sinergia (file da rivedere a mano)")
    ts.add_argument("code")
    sy = sub.add_parser("synergy", help="misura le sinergie sui game data del set e salva gli effetti")
    sy.add_argument("code")
    sy.add_argument("--format", dest="fmt", default="premier")
    sy.add_argument("--cutoff", help="giorno di taglio per la verifica (default: al 60%% dei giorni di dati)")


def run(args: argparse.Namespace) -> int:
    con = db.connect()
    code = load_set(args.code).code
    if args.command == "tag-set":
        from .synergy.tags import write_tags, load_tags
        path, new = write_tags(con, code)
        ts = load_tags(code, path)
        print(("Creato " if new else "Il file dei tag esiste gia' (non toccato). Nuova versione in ") + str(path))
        for t, d in sorted(ts.themes.items()):
            print(f"  {t:<24} enabler {len(d['enablers']):3d}  payoff {len(d['payoffs']):3d}")
        print("Rivedi il file a mano e imposta reviewed: true.")
        return 0

    from .engine.book import load_book
    from .engine.deckmodel import load_deck_model
    from .stats.prior import load_prior
    from .synergy.measure import measure
    from .synergy.tags import load_tags
    load_format(args.fmt)
    tags = load_tags(code)
    if tags is None:
        print(f"Tag mancanti: esegui prima python -m draftbot tag-set {code}")
        return 1
    model = load_deck_model(args.fmt)
    prior = load_prior(args.fmt)
    book = load_book(con, code, args.fmt, prior=prior, half_life=prior.half_life if prior else None)
    cutoff = args.cutoff
    if not cutoff:
        days = [r[0] for r in con.execute(
            "SELECT DISTINCT day FROM stat_decks WHERE set_code=? AND format=? ORDER BY day", (code, args.fmt))]
        if not days:
            print(f"Nessun game data per {code} {args.fmt}: le sinergie non si possono misurare.")
            return 1
        cutoff = days[int(len(days) * 0.6)]
    sm = measure(model, book, tags, code, args.fmt, cutoff)
    path = sm.save()
    print(f"Sinergie {code} {args.fmt} (stima fino al {cutoff}, verifica dopo); tag rivisti: "
          f"{'si' if tags.reviewed else 'no'}")
    print(f"{'tema':<24} {'tipo':<12} {'prima':>8} {'+-':>6} {'dopo':>8} {'mazzi':>7}")
    for d in sm.details:
        mark = " *" if d["theme"] in sm.effects else ""
        print(f"{d['theme']:<24} {d['kind']:<12} {d['coef'] * 100:+8.2f} {d['se'] * 100:6.2f} "
              f"{d['coef_late'] * 100:+8.2f} {d['n_decks_with']:7d}{mark}")
    print(f"(* = tenuti) pendenza fuori campione: {sm.oos_slope:.2f} +- {sm.oos_slope_se:.2f} "
          f"(gli effetti si usano moltiplicati per questa calibrazione)")
    print(f"Scritto: {path}")
    print(ATTRIBUTION)
    return 0
