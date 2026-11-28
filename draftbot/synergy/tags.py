"""Tag dei temi di sinergia per carta.

Fonti, in ordine:
1. temi generici di Magic (``tags/themes.yaml``): regex sul testo Oracle e tipi di carta;
2. temi automatici ricavati dai dati del set:
   - ``kw:<Keyword>``: keyword non sempreverdi presenti in almeno ``min_theme_cards`` carte. Le carte
     con la keyword sono "enabler"; quelle che la nominano nel testo senza averla sono "payoff";
   - ``type:<Tipo>``: tipi o sottotipi citati nei testi ("if you control a planeswalker",
     "other Humans you control"...). Le carte di quel tipo sono enabler, quelle che lo citano payoff.

Il risultato va in ``sets/<CODICE>.tags.yaml``, che si **rivede a mano** e non viene mai sovrascritto:
una nuova generazione scrive ``<CODICE>.tags.new.yaml`` accanto, per confrontarli.
Nessun dato di set nel codice: tutto viene dai testi delle carte e dal file di regole.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from ..config import ROOT, SETS_DIR, load_set

RULES_PATH = ROOT / "tags" / "themes.yaml"
_REMINDER = re.compile(r"\([^)]*\)")
# frasi che indicano che un tipo e' "contato" o "richiesto" da una carta
_TYPE_PAYOFF = (r"(if you control (an?|two or more|three or more) {t}|for each {t}|other {t}s? you control|"
                r"{t}s? you control|whenever (an?|another) {t}\b|number of {t}s|{t} spells? you cast)")


@dataclass
class TagSet:
    set_code: str
    themes: dict[str, dict[str, list[str]]]          # tema -> {"enablers": [...], "payoffs": [...]}
    generated: str = ""
    reviewed: bool = False
    notes: list[str] = field(default_factory=list)

    def card_roles(self) -> dict[str, dict[str, set[str]]]:
        """carta -> {"enabler": {temi}, "payoff": {temi}}"""
        out: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"enabler": set(), "payoff": set()})
        for t, d in self.themes.items():
            for n in d.get("enablers", []):
                out[n]["enabler"].add(t)
            for n in d.get("payoffs", []):
                out[n]["payoff"].add(t)
        return out

    def save(self, path: Path) -> Path:
        head = ("# Tag dei temi di sinergia, generati da 'python -m draftbot tag-set' con regole sul testo.\n"
                "# RIVEDERE A MANO: togliere i falsi positivi, aggiungere quelli mancanti, poi reviewed: true.\n"
                "# Questo file non viene mai sovrascritto da add-set o tag-set.\n")
        data = {"set_code": self.set_code, "generated": self.generated, "reviewed": self.reviewed,
                "notes": self.notes,
                "themes": {t: {"enablers": sorted(d["enablers"]), "payoffs": sorted(d["payoffs"])}
                           for t, d in sorted(self.themes.items())}}
        path.write_text(head + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120),
                        encoding="utf-8")
        return path


def load_rules(path: Path = RULES_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _text(oracle: str | None) -> str:
    return _REMINDER.sub("", (oracle or "").lower())


def _types(type_line: str | None) -> set[str]:
    tl = (type_line or "").replace("—", " ").replace("//", " ")
    return {w for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", tl)} - {"Basic", "Legendary", "Snow", "World"}


def generate(con: sqlite3.Connection, set_code: str, rules: dict | None = None) -> TagSet:
    rules = rules or load_rules()
    rows = con.execute("SELECT name, type_line, oracle_text, keywords, is_land FROM cards WHERE set_code=?",
                       (set_code,)).fetchall()
    cards = [(r[0], r[1] or "", _text(r[2]), json.loads(r[3] or "[]"), bool(r[4])) for r in rows
             if "Basic Land" not in (r[1] or "")]
    min_n = int(rules.get("min_theme_cards", 6))
    themes: dict[str, dict[str, set[str]]] = {}

    # 1. temi generici
    for t, spec in (rules.get("themes") or {}).items():
        en_re = [re.compile(x) for x in spec.get("enabler", [])]
        pay_re = [re.compile(x) for x in spec.get("payoff", [])]
        en_types = set(spec.get("enabler_types", []))
        en, pay = set(), set()
        for name, tl, txt, _, _ in cards:
            if any(r.search(txt) for r in en_re) or (en_types & _types(tl)):
                en.add(name)
            if any(r.search(txt) for r in pay_re):
                pay.add(name)
        themes[t] = {"enablers": en, "payoffs": pay}

    # 2a. keyword del set
    evergreen = {k.lower() for k in rules.get("evergreen", [])}
    kw_count = Counter(k for _, _, _, kws, _ in cards for k in kws if k.lower() not in evergreen)
    for kw, n in kw_count.items():
        if n < min_n:
            continue
        en = {name for name, _, _, kws, _ in cards if kw in kws}
        word = re.compile(r"\b" + re.escape(kw.lower()))
        pay = {name for name, _, txt, kws, _ in cards if kw not in kws and word.search(txt)}
        themes[f"kw:{kw}"] = {"enablers": en, "payoffs": pay}

    # 2b. tipi citati nei testi
    type_count = Counter(t for _, tl, _, _, _ in cards for t in _types(tl))
    for typ, n in type_count.items():
        if n < min_n or typ in ("Creature", "Land", "Instant", "Sorcery"):
            continue
        pat = re.compile(_TYPE_PAYOFF.format(t=re.escape(typ.lower())))
        pay = {name for name, _, txt, _, _ in cards if pat.search(txt)}
        if len(pay) >= 2:
            en = {name for name, tl, _, _, _ in cards if typ in _types(tl)}
            themes[f"type:{typ}"] = {"enablers": en, "payoffs": pay}

    # i temi generici e di tipo servono payoff espliciti; le keyword del set valgono anche come temi di
    # densita' (le carte con la keyword rendono di piu' insieme), quindi bastano gli enabler
    themes = {t: d for t, d in themes.items()
              if len(d["enablers"]) >= min_n and (len(d["payoffs"]) >= 1 or t.startswith("kw:"))}
    return TagSet(set_code, {t: {k: sorted(v) for k, v in d.items()} for t, d in themes.items()},
                  date.today().isoformat(), False,
                  ["generato con regole sul testo Oracle (tags/themes.yaml) e temi automatici del set"])


def tags_path(set_code: str) -> Path:
    cfg = load_set(set_code)
    return SETS_DIR / (cfg.synergy_tags_file or f"{cfg.code}.tags.yaml")


def load_tags(set_code: str, path: Path | None = None) -> TagSet | None:
    path = path or tags_path(set_code)
    if not path.is_file():
        return None
    d = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return TagSet(d.get("set_code", set_code), d.get("themes") or {}, d.get("generated", ""),
                  bool(d.get("reviewed")), d.get("notes") or [])


def write_tags(con: sqlite3.Connection, set_code: str) -> tuple[Path, bool]:
    """Scrive il file dei tag se manca; altrimenti scrive <CODICE>.tags.new.yaml. True = file nuovo."""
    ts = generate(con, set_code)
    path = tags_path(set_code)
    if path.exists():
        return ts.save(path.with_name(path.name.replace(".tags.yaml", ".tags.new.yaml"))), False
    return ts.save(path), True
