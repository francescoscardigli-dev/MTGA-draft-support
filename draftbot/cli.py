"""Interfaccia a riga di comando: ``python -m draftbot``."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import ConfigError, available_sets, load_format, load_set


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
    lines.append("  Dati locali: non ancora importati (Fase 1-3)")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="draftbot", description="Assistente per i draft di MTG Arena")
    p.add_argument("--set", dest="set_code", help="codice del set (es. TRK)")
    p.add_argument("--version", action="version", version=f"draftbot {__version__}")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("sets", help="elenca i set configurati")
    st = sub.add_parser("status", help="mostra config e stato dei dati di un set")
    st.add_argument("code")
    add = sub.add_parser("add-set", help="crea config e dati per un set nuovo (Fase 1)")
    add.add_argument("code")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "sets":
            print("\n".join(available_sets()))
            return 0
        if args.command == "add-set":
            print("add-set non ancora implementato (Fase 1).", file=sys.stderr)
            return 2
        code = args.code if args.command == "status" else args.set_code
        if not code:
            build_parser().print_help()
            return 1
        print(_describe_set(code))
        return 0
    except ConfigError as e:
        print(f"Errore di configurazione: {e}", file=sys.stderr)
        return 1
