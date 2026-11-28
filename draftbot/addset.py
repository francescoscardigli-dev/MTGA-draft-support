"""Comando add-set: crea la config di un set nuovo e importa le carte.

Non sovrascrive mai una config esistente (puo' contenere correzioni fatte a mano).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config import FORMATS_DIR, SETS_DIR, ConfigError, load_format, load_set
from .sources import scryfall, seventeenlands

# Tipi di set Scryfall che di solito sono fogli bonus inseriti nelle buste del set padre.
BONUS_SET_TYPES = {"masterpiece"}


def propose_config(code: str, *, sets: list[dict[str, Any]], datasets: list[seventeenlands.Dataset],
                   formats_dir: Path = FORMATS_DIR, pack_sizes: dict[str, int] | None = None) -> dict[str, Any]:
    """``pack_sizes``: dimensione della busta osservata nei draft data per formato (es. EOE: 13 in
    Premier). Se diversa da quella del formato diventa un'eccezione in ``format_overrides``."""
    """Proposta di config dai metadati delle fonti (funzione pura, testabile offline)."""
    code_l = code.lower()
    main = next((s for s in sets if s["code"] == code_l), None)
    if main is None:
        raise ConfigError(f"set {code} non trovato su Scryfall")
    bonus = [s["code"] for s in sets if s.get("parent_set_code") == code_l and s.get("set_type") in BONUS_SET_TYPES]
    exp = code.upper()
    available = {(d.expansion.upper(), d.event_type) for d in datasets}
    formats = []
    for p in sorted(formats_dir.glob("*.yaml")):
        f = load_format(p.stem, formats_dir)
        formats.append((f.name, (exp, f.seventeenlands_event_type) in available))
    return {
        "code": exp,
        "name": main["name"],
        "scryfall_sets": [code_l, *bonus],
        "seventeenlands_expansion": exp,
        # solo i formati con draft data pubblici; se non ce n'e' nessuno (set appena uscito), tutti
        "formats": [name for name, ok in formats if ok] or [name for name, _ in formats],
        "release_date": main.get("released_at"),
        "arena_id_sources": ["arena_card_database", "seventeenlands", "scryfall"],
        "name_aliases": {},
        "mechanics": [],
        "archetypes": [],
        "synergy_tags_file": f"{exp}.tags.yaml",
        "format_overrides": {name: {"pack_size": n} for name, n in (pack_sizes or {}).items()
                             if n and n != load_format(name, formats_dir).pack_size},
        "_notes": {
            "17lands_draft_data": {name: ok for name, ok in formats},
            "generato_da": "draftbot add-set, da rivedere a mano",
        },
    }


def write_config(cfg: dict[str, Any], sets_dir: Path = SETS_DIR) -> Path:
    path = sets_dir / f"{cfg['code']}.yaml"
    if path.exists():
        raise FileExistsError(path)
    notes = cfg.pop("_notes", {})
    header = (f"# Config del set {cfg['code']} generata da 'draftbot add-set'. Rivedila a mano.\n"
              f"# Draft data 17Lands disponibili per formato: {notes.get('17lands_draft_data')}\n"
              "# Meccaniche, archetipi e tag di sinergia vengono aggiunti nella fase delle sinergie.\n")
    path.write_text(header + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def ensure_config(code: str, log=print) -> tuple[Path, bool]:
    """Crea la config se manca. Restituisce (percorso, creata)."""
    path = SETS_DIR / f"{code.upper()}.yaml"
    if path.exists():
        load_set(code)  # valida
        log(f"Config esistente: {path.relative_to(SETS_DIR.parent)} (non modificata)")
        return path, False
    datasets = seventeenlands.list_datasets()
    sizes = {}
    for p in sorted(FORMATS_DIR.glob("*.yaml")):
        f = load_format(p.stem)
        ds = [d for d in datasets if d.expansion.upper() == code.upper() and d.event_type == f.seventeenlands_event_type
              and d.kind == "draft"]
        if ds:
            sizes[f.name] = seventeenlands.remote_first_pack_size(ds[0].url)
            if sizes[f.name] and sizes[f.name] != f.pack_size:
                log(f"{f.display_name}: buste da {sizes[f.name]} carte nei dati 17Lands (formato: {f.pack_size})")
    cfg = propose_config(code, sets=scryfall.all_sets(), datasets=datasets, pack_sizes=sizes)
    write_config(cfg)
    load_set(code)
    log(f"Config creata: {path.relative_to(SETS_DIR.parent)}")
    return path, True
