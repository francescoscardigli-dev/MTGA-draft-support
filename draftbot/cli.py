"""Interfaccia a riga di comando: ``python -m draftbot``."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from . import __version__, db
from .config import ConfigError, available_sets, load_format, load_set
from .net import NetError


def _describe_set(code: str) -> str:
    cfg = load_set(code)
    lines = [
        f"draftbot {__version__}",
        f"Set: {cfg.code} - {cfg.name}",
        f"  Codici Scryfall nel draft: {', '.join(c.upper() for c in cfg.scryfall_sets)}",
        f"  Espansione 17Lands: {cfg.seventeenlands_expansion}",
        f"  Fonti ID Arena (priorita'): {', '.join(cfg.arena_id_sources)}",
        "  Formati:",
    ]
    for name in cfg.formats:
        f = load_format(name)
        wheels = ", ".join(str(p + 1) for p in f.wheel_picks()) or "nessuna"
        lines.append(
            f"    - {f.display_name}: tavolo {f.pod_size}, {f.picks_per_turn} carta/e per pick, "
            f"{f.picks_per_pack} pick per busta, pool {f.pool_size} carte, wheel al pick {wheels}"
        )
    lines.append(_data_status(cfg.code))
    return "\n".join(lines)


def _data_status(code: str) -> str:
    con = db.connect()
    n = con.execute("SELECT COUNT(*) FROM cards WHERE set_code=?", (code,)).fetchone()[0]
    if not n:
        return f"  Dati locali: nessuna carta importata (esegui: python -m draftbot add-set {code})"
    ids = con.execute("SELECT COUNT(*) FROM arena_ids WHERE set_code=?", (code,)).fetchone()[0]
    by_set = Counter(r[0] for r in con.execute("SELECT printed_set FROM cards WHERE set_code=?", (code,)))
    issues = Counter(r[0] for r in con.execute("SELECT kind FROM issues WHERE set_code=?", (code,)))
    last = con.execute("SELECT source, kind, MAX(fetched_at) FROM snapshots WHERE set_code=? GROUP BY source, kind",
                       (code,)).fetchall()
    out = [f"  Dati locali: {n} carte ({', '.join(f'{k} {v}' for k, v in by_set.most_common())}), {ids} ID Arena"]
    out.append("  Ultimi download: " + "; ".join(f"{s}/{k} {t[:10]}" for s, k, t in last))
    out.append("  Problemi: " + (", ".join(f"{k} {v}" for k, v in issues.items()) if issues else "nessuno"))
    return "\n".join(out)


def _print_cards(rows: list[sqlite3.Row], verbose: bool) -> None:
    for r in rows:
        pips = "".join(c * (r[f"pip_{c.lower()}"] or 0) for c in "WUBRG")
        print(f"{r['mv']:>4g}  {r['mana_cost'] or '-':<16} {r['name']:<40} {r['rarity'][:1].upper()} "
              f"{r['printed_set']:<4} pip:{pips or '-':<5} {r['type_line']}")
        if verbose:
            print(f"      {(r['oracle_text'] or '').replace(chr(10), ' | ')}")
    print(f"({len(rows)} carte)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="draftbot", description="Assistente per i draft di MTG Arena")
    p.add_argument("--set", dest="set_code", help="codice del set (es. TRK)")
    p.add_argument("--version", action="version", version=f"draftbot {__version__}")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("sets", help="elenca i set configurati")
    st = sub.add_parser("status", help="mostra config e stato dei dati di un set")
    st.add_argument("code")
    add = sub.add_parser("add-set", help="crea la config (se manca) e importa le carte di un set")
    add.add_argument("code")
    add.add_argument("--arena-db", type=Path, help="percorso di Raw_CardDatabase_*.mtga o della cartella Raw")
    add.add_argument("--no-arena-db", action="store_true", help="non leggere il database del client Arena")
    up = sub.add_parser("update-cards", help="riscarica le carte di un set gia' configurato")
    up.add_argument("code")
    up.add_argument("--arena-db", type=Path)
    up.add_argument("--no-arena-db", action="store_true")
    q = sub.add_parser("cards", help="cerca carte per nome, costo, colore, rarita', tipo o ID Arena")
    q.add_argument("code")
    q.add_argument("--name")
    q.add_argument("--mv", type=float)
    q.add_argument("--color", help="colori richiesti dal costo, es. W, UB; C = incolore")
    q.add_argument("--rarity")
    q.add_argument("--type", dest="card_type")
    q.add_argument("--arena-id", type=int)
    q.add_argument("-v", "--verbose", action="store_true")
    iss = sub.add_parser("issues", help="elenca i problemi dell'ultimo import (es. carte senza ID Arena)")
    iss.add_argument("code")
    lw = sub.add_parser("log-where", help="mostra dove il tool cerca Player.log")
    lw.set_defaults(_log=True)
    ld = sub.add_parser("log-drafts", help="elenca i draft trovati in Player.log e ne controlla la coerenza")
    ld.add_argument("logs", nargs="*", type=Path, help="file di log (default: Player-prev.log e Player.log)")
    ld.add_argument("-d", "--detail", action="store_true", help="mostra busta e carte prese pick per pick")
    ld.add_argument("--draft", help="solo il draft con questo ID (anche solo l'inizio)")
    ld.add_argument("--json", type=Path, help="esporta i draft letti in un file JSON")
    lc = sub.add_parser("log-clean", help="crea una copia ripulita del log da salvare come fixture di test")
    lc.add_argument("src", type=Path)
    lc.add_argument("dst", type=Path)
    lc.add_argument("--redact", action="append", default=[], metavar="TESTO",
                    help="testo da cancellare ovunque (es. il tuo nome utente); ripetibile")
    lc.add_argument("--keep-all", action="store_true",
                    help="tiene tutte le righe (ripulite) invece delle sole righe di draft")
    from .statscli import add_parsers
    add_parsers(sub)
    from .enginecli import add_parsers as add_engine
    add_engine(sub)
    from .synergycli import add_parsers as add_synergy
    add_synergy(sub)
    return p


def _import(code: str, args: argparse.Namespace) -> int:
    from .cardbuild import import_set
    cfg = load_set(code)
    res = import_set(cfg, arena_db=args.arena_db, use_arena_db=not args.no_arena_db)
    kinds = Counter(k for k, _ in res.issues)
    print(f"Importate {len(res.cards)} carte e {len(res.arena_ids)} ID Arena per {cfg.code}.")
    if kinds:
        print("Problemi: " + ", ".join(f"{k} {v}" for k, v in kinds.items())
              + f"  (dettagli: python -m draftbot issues {cfg.code})")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "sets":
            print("\n".join(available_sets()))
            return 0
        if args.command == "add-set":
            from .addset import ensure_config
            ensure_config(args.code)
            return _import(args.code.upper(), args)
        if args.command == "update-cards":
            return _import(args.code.upper(), args)
        if args.command == "cards":
            code = load_set(args.code).code
            rows = db.query_cards(db.connect(), code, name=args.name, mv=args.mv, color=args.color,
                                  rarity=args.rarity, card_type=args.card_type, grp_id=args.arena_id)
            _print_cards(rows, args.verbose)
            return 0
        if args.command == "issues":
            code = load_set(args.code).code
            for kind, detail in db.connect().execute(
                    "SELECT kind, detail FROM issues WHERE set_code=? ORDER BY kind, detail", (code,)):
                print(f"{kind:<24} {detail}")
            return 0
        if args.command in ("tag-set", "synergy"):
            from .synergycli import run as run_synergy
            return run_synergy(args)
        if args.command in ("fit-deck-model", "recommend", "backtest", "build", "build-check", "fit-extras", "live",
                            "check-extras"):
            from .enginecli import run as run_engine
            return run_engine(args)
        if args.command in ("update-data", "stats", "fit-prior", "calibrate"):
            from .statscli import run
            return run(args)
        if args.command and args.command.startswith("log-"):
            from .logcli import run_log_command
            return run_log_command(args)
        code = args.code if args.command == "status" else args.set_code
        if not code:
            build_parser().print_help()
            return 1
        print(_describe_set(code))
        return 0
    except ConfigError as e:
        print(f"Errore di configurazione: {e}", file=sys.stderr)
        return 1
    except NetError as e:
        print(f"Errore di rete: {e}", file=sys.stderr)
        return 3
