"""Comandi CLI legati a Player.log: log-where, log-drafts, log-clean."""

from __future__ import annotations

import argparse
import json
from collections import Counter

from . import db
from .arenalog import default_log_paths, find_logs, parse_files, validate
from .arenalog.parser import Draft, Pick
from .arenalog.sanitize import sanitize_file

_HINT = ("Ricorda: in Arena va attivato Opzioni > Account > 'Detailed Logs (Plugin Support)', "
         "altrimenti i messaggi del draft non vengono scritti nel log.")


_NAMES: dict[str, dict[int, str]] = {}


def _names(code: str | None) -> dict[int, str]:
    """grpId -> nome; vuoto se il set non e' importato (si mostrano gli ID)."""
    if not code:
        return {}
    if code not in _NAMES:
        _NAMES[code] = db.arena_names(db.connect(), code)
    return _NAMES[code]


def _fmt_cards(ids: list[int] | None, names: dict[int, str]) -> str:
    if ids is None:
        return "(non nel log)"
    return ", ".join(names.get(i, f"#{i}") for i in ids)


def _print_draft(d: Draft, detail: bool) -> None:
    fmt = d.fmt
    rep = validate(d)
    names = _names(d.set_code)
    print(f"Draft {d.draft_id}")
    print(f"  Evento: {d.event_name or '(non trovato)'}  set: {d.set_code or '?'}  "
          f"formato: {fmt.display_name if fmt else '?'} ({d.format_source})")
    exp = rep.picks_expected if rep.picks_expected is not None else "?"
    print(f"  Pick letti: {rep.picks_found}/{exp}  completo: {'si' if rep.complete else 'no'}  "
          f"pool del corso: {len(d.pool) if d.pool is not None else 'non trovato'}")
    if d.set_code and not names:
        print(f"  (nomi non disponibili: esegui python -m draftbot add-set {d.set_code})")
    else:
        unknown = {g for p in d.picks for g in (p.pack_cards or []) + p.picked if g not in names}
        if names and unknown:
            print(f"  ATTENZIONE: {len(unknown)} ID senza corrispondenza nel database: "
                  + ", ".join(map(str, sorted(unknown)[:15])))
    for e in rep.errors:
        print(f"  ERRORE: {e}")
    for w in rep.warnings:
        print(f"  nota: {w}")
    if detail:
        for p in d.picks:
            _print_pick(p, names)
    if names and d.picks:
        cnt = Counter(names.get(g, f"#{g}") for g in d.picked_cards())
        print("  Carte prese: " + ", ".join(f"{n}" + (f" x{c}" if c > 1 else "") for n, c in cnt.items()))


def _print_pick(p: Pick, names: dict[int, str]) -> None:
    print(f"   P{p.pack + 1}p{p.pick + 1:<2} presa: {_fmt_cards(p.picked or None, names)}")
    print(f"          busta ({len(p.pack_cards) if p.pack_cards is not None else '?'}): "
          f"{_fmt_cards(p.pack_cards, names)}")


def run_log_command(args: argparse.Namespace) -> int:
    if args.command == "log-where":
        paths = default_log_paths()
        if not paths:
            print("Nessun percorso standard per questo sistema: passa il file a mano o imposta DRAFTBOT_PLAYER_LOG.")
        for p in paths:
            print(f"{'trovato  ' if p.is_file() else 'mancante '} {p}")
        print(_HINT)
        return 0

    if args.command == "log-clean":
        rep = sanitize_file(args.src, args.dst, redact=args.redact, keep_all=args.keep_all)
        print(rep.summary())
        print(f"Scritto: {args.dst}")
        if not rep.kinds:
            print("Nessun messaggio di draft riconosciuto nel log. " + _HINT)
        return 2 if rep.leftovers else 0

    # log-drafts
    logs = args.logs or find_logs()
    if not logs:
        print("Nessun Player.log trovato. Indica il file: python -m draftbot log-drafts <percorso>")
        print(_HINT)
        return 1
    missing = [p for p in logs if not p.is_file()]
    if missing:
        print("File non trovato: " + ", ".join(map(str, missing)))
        return 1
    parser = parse_files(logs)
    drafts = parser.results()
    if args.draft:
        drafts = [d for d in drafts if d.draft_id.startswith(args.draft)]
    print(f"Letti: {', '.join(map(str, logs))} ({parser.line_no} righe). Draft trovati: {len(drafts)}")
    if not drafts:
        print(_HINT)
    for d in drafts:
        print()
        _print_draft(d, args.detail)
    if parser.unrecognized:
        print("\nMessaggi di draft non interpretati (utile per adattare il parser): "
              + ", ".join(f"{k} x{v}" for k, v in parser.unrecognized.most_common()))
    if args.json:
        args.json.write_text(json.dumps([d.to_dict() for d in drafts], indent=1), encoding="utf-8")
        print(f"\nEsportato: {args.json}")
    return 0
