"""Costruzione del database carte di un set a partire dalle fonti.

La parte di calcolo (``build``) e' pura e testabile offline; ``import_set`` scarica i dati.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from . import db, mana
from .config import SetConfig, load_format
from .names import keys, normalize
from .sources import scryfall, seventeenlands
from .sources.arena_db import ArenaCard, ArenaDbError, find_database, read_cards
from .paths import raw_dir


@dataclass
class BuildResult:
    cards: list[dict[str, Any]]
    arena_ids: list[tuple[int, str, str, str]]  # grp_id, nome canonico, fonte, espansione di origine
    issues: list[tuple[str, str]] = field(default_factory=list)


def _num(cn: str) -> tuple[int, str]:
    m = re.match(r"(\d+)", cn or "")
    return (int(m.group(1)) if m else 10**6, cn or "")


def _faces(card: dict[str, Any]) -> list[dict[str, Any]]:
    return card.get("card_faces") or [card]


def card_row(set_code: str, card: dict[str, Any]) -> dict[str, Any]:
    """Converte una stampa Scryfall in una riga della tabella cards."""
    faces = _faces(card)
    front = faces[0]
    cost_str = card.get("mana_cost")
    if cost_str is None:  # carte bifronte: costo sulla faccia frontale
        cost_str = front.get("mana_cost", "")
    # Carte a piu' facce lanciabili (split, prepare, adventure...): ogni faccia ha il suo costo.
    # I pip sono il massimo per colore tra le facce, i colori sono l'unione.
    face_costs = [mana.parse(f.get("mana_cost", "")) for f in faces if f.get("mana_cost")]
    if card.get("mana_cost") is None and face_costs:
        face_costs = face_costs[:1]  # bifronte: si lancia solo la faccia frontale
    if not face_costs:
        face_costs = [mana.parse(cost_str)]
    cost = face_costs[0]
    type_line = card.get("type_line") or front.get("type_line", "")
    front_type = front.get("type_line", type_line).split("//")[0]
    oracle = card.get("oracle_text")
    if oracle is None:
        oracle = "\n//\n".join(f.get("oracle_text", "") for f in faces)
    image = (card.get("image_uris") or front.get("image_uris") or {}).get("normal")

    def has(t: str) -> int:
        return int(t in front_type)

    return {
        "set_code": set_code,
        "name": card["name"],
        "norm_name": normalize(card["name"]),
        "scryfall_id": card.get("id"),
        "oracle_id": card.get("oracle_id") or front.get("oracle_id"),
        "printed_set": card.get("set", "").upper(),
        "collector_number": card.get("collector_number"),
        "rarity": card.get("rarity"),
        "layout": card.get("layout"),
        "type_line": type_line,
        "oracle_text": oracle,
        "keywords": json.dumps(card.get("keywords", [])),
        "mana_cost": cost_str,
        "mv": float(card.get("cmc", cost.mv)),
        "colors": "".join(c for c in mana.COLORS if c in (card.get("colors") or front.get("colors") or [])),
        "color_identity": "".join(c for c in mana.COLORS if c in card.get("color_identity", [])),
        "cost_colors": "".join(c for c in mana.COLORS if any(c in fc.colors for fc in face_costs)),
        **{f"pip_{c.lower()}": max(fc.pips.get(c, 0) for fc in face_costs) for c in mana.COLORS},
        "max_pips": max(fc.max_pips() for fc in face_costs),
        "hybrid": json.dumps(sorted({h for fc in face_costs for h in fc.hybrid})),
        "phyrexian": json.dumps(cost.phyrexian),
        "has_x": int(any(fc.x > 0 for fc in face_costs)),
        "is_creature": has("Creature"), "is_land": has("Land"), "is_instant": has("Instant"),
        "is_sorcery": has("Sorcery"), "is_artifact": has("Artifact"), "is_enchantment": has("Enchantment"),
        "is_planeswalker": has("Planeswalker"), "is_battle": has("Battle"),
        "power": front.get("power", card.get("power")),
        "toughness": front.get("toughness", card.get("toughness")),
        "image_uri": image,
    }


def _preferred(prints: list[dict[str, Any]], home_sets: tuple[str, ...]) -> dict[str, Any]:
    """Stampa di riferimento: prima i set del draft, poi le stampe da busta, poi numero piu' basso."""
    def rank(c: dict[str, Any]) -> tuple:
        in_home = c.get("set", "").lower() in home_sets
        home_idx = home_sets.index(c["set"].lower()) if in_home else len(home_sets)
        return (not in_home, home_idx, not c.get("booster", False), c.get("digital", False),
                bool(c.get("frame_effects")), c.get("border_color") != "black", _num(c.get("collector_number", "")))
    return min(prints, key=rank)


def build(cfg: SetConfig, *, scryfall_cards: list[dict[str, Any]], universe: list[str] | None,
          cards_csv: list[dict[str, str]] | None, arena_db_cards: list[ArenaCard] | None) -> BuildResult:
    """Unisce le fonti. ``universe``: nomi delle carte che compaiono nelle buste (da 17Lands);
    se None si usano tutte le carte non-token dei set Scryfall configurati."""
    issues: list[tuple[str, str]] = []
    aliases = {normalize(k): v for k, v in cfg.name_aliases.items() if v}

    # Indice Scryfall per chiave normalizzata (nome completo e faccia frontale).
    # Le carte rinominate per Arena (es. il set OM1) hanno su Scryfall il nome originale in "name" e il
    # nome di Arena in "printed_name" (o "flavor_name"): si indicizzano anche questi, ma solo per le
    # stampe dei set del draft, e la riga usa il nome visto su Arena.
    by_key: dict[str, list[dict[str, Any]]] = {}
    for c in scryfall_cards:
        if c.get("layout") in ("token", "double_faced_token", "art_series", "emblem"):
            continue
        for k in keys(c["name"]):
            by_key.setdefault(k, []).append(c)
        if c.get("set", "").lower() in cfg.scryfall_sets:
            for alt in {c.get("printed_name"), c.get("flavor_name")} - {None, "", c["name"]}:
                renamed = dict(c, name=alt, original_name=c["name"])
                for k in keys(alt):
                    by_key.setdefault(k, []).append(renamed)

    def lookup(name: str) -> list[dict[str, Any]] | None:
        for k in keys(aliases.get(normalize(name), name)):
            if k in by_key:
                return by_key[k]
        return None

    rows: dict[str, dict[str, Any]] = {}  # nome canonico -> riga
    renamed: dict[str, str] = {}          # nome di Arena -> nome originale (carte rinominate, es. OM1)
    if universe is None:
        universe = sorted({c["name"] for c in scryfall_cards
                           if c.get("set", "").lower() in cfg.scryfall_sets
                           and c.get("layout") not in ("token", "double_faced_token", "art_series", "emblem")})
    for name in universe:
        prints = lookup(name)
        if not prints:
            issues.append(("card_not_on_scryfall", name))
            continue
        best = _preferred(prints, cfg.scryfall_sets)
        rows.setdefault(best["name"], card_row(cfg.code, best))
        if best.get("original_name"):
            renamed[best["name"]] = best["original_name"]

    canon: dict[str, str] = {}
    for n in rows:
        for k in keys(n):
            canon.setdefault(k, n)
    # le fonti degli ID possono usare il nome originale (17Lands: "Spider-Verse" SPM = "Arachnomania" OM1)
    for arena_name, original in renamed.items():
        for k in keys(original):
            canon.setdefault(k, arena_name)
    for alias_src, alias_dst in aliases.items():
        for k in keys(alias_dst):
            if k in canon:
                canon.setdefault(alias_src, canon[k])

    def resolve(name: str) -> str | None:
        for k in keys(name):
            if k in canon:
                return canon[k]
        return None

    # Mappatura grpId in ordine di priorita'. Un grpId gia' assegnato non viene sovrascritto;
    # un conflitto di nome tra fonti viene segnalato.
    candidates: dict[str, list[tuple[int, str, str]]] = {
        "arena_card_database": [(a.grp_id, a.name, a.expansion) for a in (arena_db_cards or [])],
        "seventeenlands": [(int(r["id"]), r["name"], r["expansion"]) for r in (cards_csv or []) if r.get("id")],
        "scryfall": [(int(c["arena_id"]), c["name"], c.get("set", "").upper())
                     for c in scryfall_cards if c.get("arena_id")],
    }
    home = {s.upper() for s in cfg.scryfall_sets} | {cfg.seventeenlands_expansion.upper()}
    # Le carte dei set del draft si mappano solo con ID di quei set; le carte "ospiti"
    # (es. Special Guests) accettano ID di qualsiasi espansione.
    guest = {n for n, r in rows.items() if r["printed_set"] not in home} | set(renamed)
    assigned: dict[int, tuple[str, str, str]] = {}
    for source in cfg.arena_id_sources:
        for grp, name, exp in candidates.get(source, []):
            target = resolve(name)
            if target is None:
                continue  # carta di un altro set: irrilevante per questo draft
            if exp.upper() not in home and target not in guest:
                continue  # ristampa della stessa carta in un altro set
            if grp in assigned:
                if assigned[grp][0] != target:
                    issues.append(("arena_id_conflict",
                                   f"{grp}: {assigned[grp][0]} ({assigned[grp][1]}) vs {target} ({source})"))
                continue
            assigned[grp] = (target, source, exp)

    mapped = {t for t, _, _ in assigned.values()}
    for n in sorted(rows):
        if n not in mapped:
            issues.append(("card_without_arena_id", n))
    # grpId del client che appartengono ai set del draft ma non corrispondono a nessuna carta.
    for a in arena_db_cards or []:
        if a.expansion.upper() in home and a.grp_id not in assigned and resolve(a.name) is None:
            issues.append(("arena_id_without_card", f"{a.grp_id} {a.name} ({a.expansion} #{a.collector_number})"))

    ids = [(g, t, s, e) for g, (t, s, e) in sorted(assigned.items())]
    return BuildResult(list(rows.values()), ids, issues)


# --------------------------------------------------------------------------- download


def _json_cache(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def import_set(cfg: SetConfig, *, arena_db: Path | None = None, use_arena_db: bool = True,
               log=print) -> BuildResult:
    con = db.connect()
    today = date.today().isoformat()

    # 1. Scryfall: carte dei set configurati.
    sf_cards: list[dict[str, Any]] = []
    for code in cfg.scryfall_sets:
        cards = scryfall.set_cards(code)
        log(f"Scryfall {code.upper()}: {len(cards)} stampe")
        sf_cards.extend(cards)
        _json_cache(raw_dir("scryfall") / f"{code}-{today}.json", cards)
        db.add_snapshot(con, cfg.code, "scryfall", "cards", url=f"e:{code}", info={"count": len(cards)})

    # 2. 17Lands: elenco delle carte nelle buste (intestazione dei draft data) e cards.csv.
    universe: list[str] | None = None
    datasets = [d for d in seventeenlands.list_datasets()
                if d.expansion.upper() == cfg.seventeenlands_expansion.upper()]
    event_types = [load_format(f).seventeenlands_event_type for f in cfg.formats]
    draft_ds = [d for d in datasets if d.kind == "draft" and d.event_type in event_types]
    names: dict[str, None] = {}
    for d in draft_ds:
        header = seventeenlands.remote_header(d.url)
        found = seventeenlands.card_names_from_header(header, "draft")
        names.update(dict.fromkeys(found))
        log(f"17Lands {d.event_type} draft data ({d.last_updated}): {len(found)} carte")
        db.add_snapshot(con, cfg.code, "17lands", "draft_header", url=d.url, last_updated=d.last_updated,
                        info={"cards": len(found)})
    if names:
        universe = list(names)
    else:
        log("17Lands: nessun draft data pubblico per questo set; uso le carte dei set Scryfall")

    csv_path = raw_dir("17lands") / f"cards-{today}.csv"
    if not csv_path.exists():
        seventeenlands.download(seventeenlands.CARDS_CSV_URL, csv_path)
    cards_csv = seventeenlands.read_cards_csv(csv_path)
    db.add_snapshot(con, cfg.code, "17lands", "cards_csv", url=seventeenlands.CARDS_CSV_URL,
                    info={"rows": len(cards_csv)})

    # 3. Carte nelle buste che non appartengono ai set configurati (es. Special Guests): per nome.
    if universe:
        known = {k for c in sf_cards for n in (c["name"], c.get("printed_name"), c.get("flavor_name")) if n
                 for k in keys(n)}
        extra = [n for n in universe if not any(k in known for k in keys(n))]
        if extra:
            found, missing = scryfall.cards_by_name(extra)
            log(f"Scryfall: {len(found)} carte extra risolte per nome, {len(missing)} non trovate")
            sf_cards.extend(found)

    # 4. Database del client Arena (facoltativo, solo sul PC dove e' installato Arena).
    arena_cards: list[ArenaCard] | None = None
    if use_arena_db and "arena_card_database" in cfg.arena_id_sources:
        path = arena_db or find_database()
        if path is None:
            log("Database del client Arena non trovato: mappatura da 17Lands e Scryfall")
        else:
            try:
                arena_cards = read_cards(path)
                log(f"Database Arena {path.name}: {len(arena_cards)} carte")
                db.add_snapshot(con, cfg.code, "arena_db", "cards", url=str(path), info={"count": len(arena_cards)})
            except ArenaDbError as e:
                log(f"Database Arena non utilizzabile: {e}")

    result = build(cfg, scryfall_cards=sf_cards, universe=universe, cards_csv=cards_csv,
                   arena_db_cards=arena_cards)
    db.replace_set(con, cfg.code, result.cards, result.arena_ids, result.issues)
    con.commit()
    con.close()
    return result
