"""Parser incrementale di Player.log per i draft con giocatori umani (Premier, Pick Two).

Messaggi riconosciuti (struttura da fonti non ufficiali, DA VERIFICARE sui log reali):

- busta vista:   ``Draft.Notify {"draftId", "SelfPack", "SelfPick", "PackCards": "id,id,..."}``
- pick fatto:    ``EventPlayerDraftMakePick`` / ``Event_PlayerDraftMakePick`` con
                 ``{"DraftId", "GrpIds": [...] | "GrpId", "Pack", "Pick"}`` (anche dentro ``request``)
- pick + busta:  ``LogBusinessEvents`` con ``{"DraftId", "EventId", "PackNumber", "PickNumber",
                 "PickGrpId" | "PickGrpIds", "CardsInPack": [...]}``
- iscrizione:    ``EventJoin`` / ``Event_Join`` con ``{"EventName"}``
- pool finale:   un oggetto con ``InternalEventName`` e ``CardPool`` (es. risposta di EventGetCoursesV2),
                 oppure la risposta di ``DraftCompleteDraft``
- pick rifiutato: risposta di EventPlayerDraftMakePick con ``IsPickSuccessful: false`` o con ``TableInfo``;
                 il pick viene scartato e conta la richiesta ripresentata
- fine draft:    ``IsPickingCompleted: true`` o ``DraftCompleteDraft``
- evento senza EventJoin: riga ``Entering table draft queue: <SET>_<Tipo>_Draft``, oppure un corso con
                 ``DraftId`` e ``InternalEventName`` (draft ripreso dopo un riavvio di Arena)

Confronto (2026-11-28) con la documentazione di draftomen (``docs/log-format.md``, MIT) e con il codice
di nemedraft-client (MIT), scritti su log veri: stessi campi e numerazione da 1 per Draft.Notify ed
EventPlayerDraftMakePick, in Pick Two 7 pick logici per busta. Nessun codice copiato.

Il riconoscimento si basa sulle chiavi degli oggetti JSON, non sul nome del metodo, cosi' regge
a rinomine dei metodi. Le stringhe che contengono JSON (``request``, ``Payload``) vengono decodificate.

Numerazione di busta e pick: non si assume. Per ogni fonte si sceglie lo schema (base 0 o 1,
pick contati per turno o per carta) che torna con le dimensioni delle buste previste dalla config
del formato e con le carte prese. Se nulla torna si usa base 1 e lo si segnala.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..config import FormatConfig, available_formats, available_sets, format_for, load_format, load_set

_QUEUE = re.compile(r"Entering table draft queue:\s*([A-Za-z0-9]+)_([A-Za-z]+)_Draft\b")
_MARKER = re.compile(r"(?:==>|<==)\s*([A-Za-z_][\w.]*)|\b(Draft\.Notify)\b")
_DECODER = json.JSONDecoder()


# --------------------------------------------------------------------------- osservazioni grezze

@dataclass(frozen=True)
class PackObs:
    source: str              # notify | business
    rep_pack: int
    rep_pick: int
    cards: tuple[int, ...]
    line: int


@dataclass(frozen=True)
class PickObs:
    source: str              # make_pick | business
    rep_pack: int
    rep_pick: int
    grp_ids: tuple[int, ...]
    line: int


# --------------------------------------------------------------------------- risultato

@dataclass
class Pick:
    pack: int                              # 0-based
    pick: int                              # 0-based (turno di pick nella busta)
    pack_cards: list[int] | None = None    # carte viste prima del pick
    picked: list[int] = field(default_factory=list)
    lines: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"pack": self.pack, "pick": self.pick, "pack_cards": self.pack_cards, "picked": self.picked,
                "lines": self.lines}


@dataclass(frozen=True)
class Scheme:
    """Schema di numerazione: indice0 = (riportato - base) / scala."""
    base: int
    scale: int

    def index(self, reported: int) -> int | None:
        q, r = divmod(reported - self.base, self.scale)
        return q if r == 0 and q >= 0 else None

    def __str__(self) -> str:
        return f"base {self.base}" + (f", per carta (x{self.scale})" if self.scale > 1 else "")


@dataclass
class Draft:
    draft_id: str
    first_line: int
    formats: tuple[FormatConfig, ...] = ()
    set_codes: dict[str, str] = field(default_factory=dict)   # token evento (maiuscolo) -> codice set
    event_name: str | None = None
    pool: list[int] | None = None
    pool_line: int | None = None
    pack_obs: list[PackObs] = field(default_factory=list)
    pick_obs: list[PickObs] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)
    completed: bool = False                 # Arena ha segnalato la fine dei pick

    # -- formato e set ------------------------------------------------------------------------
    def _with_set(self, f: FormatConfig) -> FormatConfig:
        """Applica le eccezioni del set (es. buste da 13 carte), se il set e' riconosciuto."""
        code = self.set_code
        return format_for(f.name, code) if code else f

    def format_from_event(self) -> FormatConfig | None:
        if not self.event_name:
            return None
        for f in self.formats:
            if any(self.event_name.startswith(p) for p in f.arena_event_prefixes):
                return self._with_set(f)
        return None

    def guessed_formats(self) -> list[FormatConfig]:
        """Formati compatibili con le dimensioni delle buste e il numero di carte per pick."""
        out = []
        for f in (self._with_set(x) for x in self.formats):
            sizes = {f.pack_size - i * f.picks_per_turn for i in range(f.picks_per_pack)}
            if any(len(o.cards) not in sizes for o in self.pack_obs):
                continue
            if any(len(o.grp_ids) != f.picks_per_turn for o in self.pick_obs):
                continue
            if self.pack_obs or self.pick_obs:
                out.append(f)
        return out

    @property
    def fmt(self) -> FormatConfig | None:
        f = self.format_from_event()
        if f:
            return f
        guesses = self.guessed_formats()
        return guesses[0] if len(guesses) == 1 else None

    @property
    def format_source(self) -> str:
        if self.format_from_event():
            return "nome evento"
        return "dedotto dalle buste" if self.fmt else "sconosciuto"

    @property
    def set_code(self) -> str | None:
        if not self.event_name:
            return None
        for tok in re.split(r"[_\-]", self.event_name):
            if tok.upper() in self.set_codes:
                return self.set_codes[tok.upper()]
        return None

    # -- numerazione ---------------------------------------------------------------------------
    def _schemes(self) -> list[Scheme]:
        fmt = self.fmt
        scales = sorted({1, fmt.picks_per_turn} if fmt else {1})
        return [Scheme(b, s) for b in (1, 0) for s in scales]

    def _pack_scheme(self, source: str) -> tuple[Scheme, bool]:
        """Schema di una fonte di buste, scelto in base alla dimensione delle buste."""
        obs = [o for o in self.pack_obs if o.source == source]
        fmt = self.fmt
        if not obs or not fmt:
            return Scheme(1, 1), False
        votes: Counter[Scheme] = Counter()
        for o in obs:
            q, r = divmod(fmt.pack_size - len(o.cards), fmt.picks_per_turn)
            if r:
                continue
            for s in self._schemes():
                if s.index(o.rep_pick) == q and s.index(o.rep_pack) is not None:
                    votes[s] += 1
        if not votes:
            return Scheme(1, 1), False
        best, n = votes.most_common(1)[0]
        return best, n == len(obs)

    def _pick_scheme(self, source: str, packs: dict[tuple[int, int], list[int]]) -> tuple[Scheme, bool]:
        """Schema di una fonte di pick: quello per cui le carte prese stanno nella busta vista."""
        obs = [o for o in self.pick_obs if o.source == source]
        if not obs or not packs:
            return Scheme(1, 1), False
        votes: Counter[Scheme] = Counter()
        for s in self._schemes():
            for o in obs:
                key = (_idx(Scheme(s.base, 1), o.rep_pack), s.index(o.rep_pick))
                cards = packs.get(key)  # type: ignore[arg-type]
                if cards is not None and not (Counter(o.grp_ids) - Counter(cards)):
                    votes[s] += 1
        if not votes:
            return Scheme(1, 1), False
        best, n = votes.most_common(1)[0]
        return best, n == len(obs)

    # -- ricostruzione -------------------------------------------------------------------------
    def build(self) -> tuple[list[Pick], list[str]]:
        """Ricostruisce i pick (ordinati) e restituisce anche le note sulla numerazione."""
        notes: list[str] = []
        picks: dict[tuple[int, int], Pick] = {}

        fmt = self.fmt

        def get(pack: int, pick: int) -> Pick:
            return picks.setdefault((pack, pick), Pick(pack, pick))

        def outside(key: tuple) -> bool:
            """Coordinate fuori dal formato (Arena a fine draft scrive a volte buste 'fantasma')."""
            return fmt is not None and not (key[0] < fmt.packs and key[1] < fmt.picks_per_pack)

        for source in sorted({o.source for o in self.pack_obs}):
            scheme, ok = self._pack_scheme(source)
            if not ok:
                notes.append(f"buste ({source}): numerazione non confermata dalle dimensioni, uso {scheme}")
            for o in (o for o in self.pack_obs if o.source == source):
                key = (_idx(Scheme(scheme.base, 1), o.rep_pack), scheme.index(o.rep_pick))
                if None in key:
                    notes.append(f"riga {o.line}: busta {o.rep_pack} pick {o.rep_pick} non interpretabile")
                    continue
                if outside(key):
                    notes.append(f"riga {o.line}: busta {o.rep_pack} pick {o.rep_pick} fuori dal formato, ignorata")
                    continue
                p = get(*key)  # type: ignore[arg-type]
                if p.pack_cards is not None and Counter(p.pack_cards) != Counter(o.cards):
                    notes.append(f"riga {o.line}: busta {key[0] + 1} pick {key[1] + 1} diversa da quella "
                                 f"gia' letta alla riga {p.lines[0]}")
                    continue
                if p.pack_cards is None:
                    p.pack_cards = list(o.cards)
                p.lines.append(o.line)

        packs = {k: v.pack_cards for k, v in picks.items() if v.pack_cards is not None}
        for source in sorted({o.source for o in self.pick_obs}):
            scheme, ok = self._pick_scheme(source, packs)  # type: ignore[arg-type]
            if not ok and packs:
                notes.append(f"pick ({source}): numerazione non confermata dalle buste, uso {scheme}")
            for o in (o for o in self.pick_obs if o.source == source):
                key = (_idx(Scheme(scheme.base, 1), o.rep_pack), scheme.index(o.rep_pick))
                if None in key:
                    notes.append(f"riga {o.line}: pick {o.rep_pack}/{o.rep_pick} non interpretabile")
                    continue
                if outside(key):
                    notes.append(f"riga {o.line}: pick {o.rep_pack}/{o.rep_pick} fuori dal formato, ignorato")
                    continue
                p = get(*key)  # type: ignore[arg-type]
                if p.picked and Counter(p.picked) != Counter(o.grp_ids) and source == "make_pick" \
                        and p.lines and p.lines[-1] < o.line:
                    # richiesta ripresentata per lo stesso pick (il primo invio non e' andato a buon fine)
                    notes.append(f"riga {o.line}: busta {key[0] + 1} pick {key[1] + 1} ripresentato, "
                                 f"vale {list(o.grp_ids)} invece di {p.picked}")
                    p.picked = list(o.grp_ids)
                    p.lines.append(o.line)
                    continue
                if p.picked and Counter(p.picked) != Counter(o.grp_ids):
                    notes.append(f"riga {o.line}: busta {key[0] + 1} pick {key[1] + 1}: carte prese "
                                 f"{list(o.grp_ids)} diverse da {p.picked}")
                    continue
                if not p.picked:
                    p.picked = list(o.grp_ids)
                p.lines.append(o.line)
        for p in picks.values():
            p.lines.sort()
        return [picks[k] for k in sorted(picks)], notes + self.parse_warnings

    @property
    def picks(self) -> list[Pick]:
        return self.build()[0]

    def picked_cards(self) -> list[int]:
        return [g for p in self.picks for g in p.picked]

    def to_dict(self) -> dict[str, Any]:
        picks, notes = self.build()
        fmt = self.fmt
        return {
            "draft_id": self.draft_id, "event_name": self.event_name, "set_code": self.set_code,
            "format": fmt.name if fmt else None, "format_source": self.format_source,
            "first_line": self.first_line, "picks": [p.to_dict() for p in picks],
            "pool": self.pool, "notes": notes,
        }


def _idx(scheme: Scheme, reported: int) -> int | None:
    return scheme.index(reported)


# --------------------------------------------------------------------------- estrazione JSON

def _json_objects(line: str) -> list[Any]:
    """Oggetti JSON presenti nella riga (si prova da ogni '{' non ancora consumato)."""
    out: list[Any] = []
    i = line.find("{")
    while i != -1:
        try:
            obj, end = _DECODER.raw_decode(line, i)
        except ValueError:
            i = line.find("{", i + 1)
            continue
        out.append(obj)
        i = line.find("{", end)
    return out


def _walk(obj: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    """Tutti i dizionari annidati, decodificando le stringhe che contengono JSON."""
    if depth > 12:
        return
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, depth + 1)
    elif isinstance(obj, str):
        s = obj.strip()
        if s[:1] in "{[" and s[-1:] in "}]":
            try:
                yield from _walk(json.loads(s), depth + 1)
            except ValueError:
                pass


def _lower(d: dict[str, Any]) -> dict[str, Any]:
    return {str(k).lower(): v for k, v in d.items()}


def _int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v)
    return None


def _ids(v: Any) -> tuple[int, ...] | None:
    if isinstance(v, str):
        parts = [p for p in re.split(r"[,\s]+", v.strip()) if p]
        v = parts
    if isinstance(v, (int, str)) and not isinstance(v, bool):
        v = [v]
    if not isinstance(v, list):
        return None
    out = [_int(x) for x in v]
    if any(x is None for x in out):
        return None
    return tuple(x for x in out if x is not None)


# --------------------------------------------------------------------------- parser

class LogParser:
    """Parser incrementale: ``feed_line`` riga per riga (servira' anche per la lettura live)."""

    def __init__(self, formats: Iterable[FormatConfig] | None = None, set_codes: dict[str, str] | None = None):
        if formats is None:
            formats = [load_format(n) for n in available_formats()]
        if set_codes is None:
            set_codes = {}
            for code in available_sets():
                cfg = load_set(code)
                set_codes[cfg.code.upper()] = cfg.code
                set_codes[cfg.seventeenlands_expansion.upper()] = cfg.code
        self.formats = tuple(formats)
        self.set_codes = dict(set_codes)
        self.drafts: dict[str, Draft] = {}
        self.current_event: str | None = None
        self.line_no = 0
        self.unrecognized: Counter[str] = Counter()   # metodi legati al draft non interpretati
        self._pending_marker: str | None = None
        self._seen: set[tuple] = set()
        self._last_pick: tuple[Draft, PickObs, tuple] | None = None   # ultimo pick inviato
        self._draft_events: dict[str, str] = {}                        # draftId -> evento (dai corsi)

    # -- API -----------------------------------------------------------------------------------
    def feed_line(self, line: str) -> list[str]:
        """Elabora una riga. Restituisce i tipi di messaggio riconosciuti (per lettura live e pulizia)."""
        self.line_no += 1
        m = _MARKER.search(line)
        marker = (m.group(1) or m.group(2)) if m else None
        q = _QUEUE.search(line)
        if q:
            set_tok, kind = q.group(1), q.group(2)
            prefix = f"{kind}Draft"
            cur = self.current_event or ""
            if not (cur.startswith(prefix) and set_tok.upper() in cur.upper().split("_")):
                self.current_event = f"{prefix}_{set_tok}"
            return ["event_queue"]
        objs = _json_objects(line)
        if not objs:
            if marker:
                self._pending_marker = marker
            return []
        if marker is None:
            marker = self._pending_marker
        self._pending_marker = None
        kinds: list[str] = []
        for obj in objs:
            for d in _walk(obj):
                k = self._classify(_lower(d), marker)
                if k:
                    kinds.append(k)
        if not kinds and marker and "draft" in marker.lower():
            self.unrecognized[marker] += 1
        return kinds

    def feed(self, lines: Iterable[str]) -> "LogParser":
        for ln in lines:
            self.feed_line(ln)
        return self

    def results(self) -> list[Draft]:
        return sorted(self.drafts.values(), key=lambda d: d.first_line)

    # -- classificazione -----------------------------------------------------------------------
    def _draft(self, draft_id: str) -> Draft:
        d = self.drafts.get(draft_id)
        if d is None:
            d = Draft(draft_id, self.line_no, self.formats, self.set_codes,
                      event_name=self._draft_events.get(draft_id, self.current_event))
            self.drafts[draft_id] = d
        return d

    def _dedup(self, key: tuple) -> bool:
        """True se il messaggio e' gia' stato visto identico (righe duplicate nel log)."""
        if key in self._seen:
            return True
        self._seen.add(key)
        return False

    def _classify(self, d: dict[str, Any], marker: str | None) -> str | None:
        mk = (marker or "").lower().replace("_", "")
        draft_id = d.get("draftid")
        draft_id = str(draft_id) if isinstance(draft_id, str) and draft_id else None

        # esito di un pick: rifiutato (si scarta, la richiesta ripresentata lo sostituisce) o fine dei pick
        if "eventplayerdraftmakepick" in mk and ("ispicksuccessful" in d or "tableinfo" in d):
            last = self._last_pick
            if (d.get("ispicksuccessful") is False or "tableinfo" in d) and last is not None:
                dr, obs, key = last
                if obs in dr.pick_obs:
                    dr.pick_obs.remove(obs)
                    dr.parse_warnings.append(f"riga {obs.line}: pick rifiutato da Arena, scartato")
                self._seen.discard(key)
                self._last_pick = None
            elif d.get("ispickingcompleted") is True and last is not None:
                last[0].completed = True
            return "pick_result"

        # fine del draft (richiesta o risposta di DraftCompleteDraft)
        if "draftcompletedraft" in mk and ("cardpool" in d or "eventname" in d or "internaleventname" in d):
            ev = d.get("internaleventname") or d.get("eventname")
            if d.get("isbotdraft") is True:
                return None
            dr = self._latest_draft(ev if isinstance(ev, str) else None)
            if dr is None:
                return None
            dr.completed = True
            pool = _ids(d.get("cardpool")) if "cardpool" in d else None
            if pool:
                self._set_pool(dr, pool)
            return "complete"

        # corso di un draft in corso (es. draft ripreso dopo un riavvio): lega draftId all'evento
        ev = d.get("internaleventname")
        if draft_id and isinstance(ev, str) and ev:
            self._draft_events[draft_id] = ev
            if draft_id in self.drafts and not self.drafts[draft_id].event_name:
                self.drafts[draft_id].event_name = ev

        # busta + pick dal log degli eventi di business
        if draft_id and "cardsinpack" in d:
            pack, pick = _int(d.get("packnumber")), _int(d.get("picknumber"))
            cards = _ids(d.get("cardsinpack"))
            picked = _ids(d.get("pickgrpids", d.get("pickgrpid")))
            if pack is None or pick is None or cards is None:
                return None
            dr = self._draft(draft_id)
            ev = d.get("eventid") or d.get("eventname")
            if isinstance(ev, str) and ev:
                dr.event_name = ev
            if not self._dedup(("bp", draft_id, pack, pick, cards)):
                dr.pack_obs.append(PackObs("business", pack, pick, cards, self.line_no))
            if picked and not self._dedup(("bk", draft_id, pack, pick, picked)):
                dr.pick_obs.append(PickObs("business", pack, pick, picked, self.line_no))
            return "pack"

        # busta vista
        if draft_id and "packcards" in d:
            pack, pick = _int(d.get("selfpack")), _int(d.get("selfpick"))
            cards = _ids(d.get("packcards"))
            if pack is None or pick is None or cards is None:
                return None
            if not self._dedup(("np", draft_id, pack, pick, cards)):
                self._draft(draft_id).pack_obs.append(PackObs("notify", pack, pick, cards, self.line_no))
            return "pack"

        # pick fatto
        if draft_id and ("grpids" in d or "grpid" in d) and "pack" in d and "pick" in d:
            pack, pick = _int(d.get("pack")), _int(d.get("pick"))
            ids = _ids(d.get("grpids", d.get("grpid")))
            if pack is None or pick is None or not ids:
                return None
            key = ("mp", draft_id, pack, pick, ids)
            if not self._dedup(key):
                dr = self._draft(draft_id)
                obs = PickObs("make_pick", pack, pick, ids, self.line_no)
                dr.pick_obs.append(obs)
                self._last_pick = (dr, obs, key)
            return "pick"

        # iscrizione a un evento (solo nel contesto di EventJoin: altre risposte elencano tutti gli eventi)
        if "eventjoin" in mk and isinstance(d.get("eventname"), str):
            self.current_event = d["eventname"]
            return "event_join"

        # pool del corso
        ev = d.get("internaleventname")
        if isinstance(ev, str) and "cardpool" in d:
            pool = _ids(d.get("cardpool"))
            if pool:
                for dr in sorted(self.drafts.values(), key=lambda x: x.first_line, reverse=True):
                    if dr.event_name == ev:
                        self._set_pool(dr, pool)
                        return "pool"
            return None
        return None

    def _latest_draft(self, event: str | None) -> Draft | None:
        drafts = sorted(self.drafts.values(), key=lambda x: x.first_line, reverse=True)
        if event:
            for dr in drafts:
                if dr.event_name == event:
                    return dr
        return drafts[0] if drafts else None

    def _set_pool(self, dr: Draft, pool: tuple[int, ...]) -> None:
        """Pool del corso: lo si tiene solo se contiene tutte le carte prese lette nel log."""
        picked = Counter(dr.picked_cards())
        if picked - Counter(pool):
            if dr.pool is None:
                dr.parse_warnings.append(f"riga {self.line_no}: pool del corso senza alcune carte prese, ignorato")
            return
        if dr.pool is None or len(pool) >= len(dr.pool):
            dr.pool, dr.pool_line = list(pool), self.line_no


# --------------------------------------------------------------------------- funzioni comode

def read_lines(path: Path) -> Iterator[str]:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            yield ln.rstrip("\r\n")


def parse_text(text: str, **kw: Any) -> LogParser:
    return LogParser(**kw).feed(text.splitlines())


def parse_files(paths: Iterable[Path], **kw: Any) -> LogParser:
    """Legge piu' file in ordine (es. Player-prev.log e Player.log) con un solo stato."""
    p = LogParser(**kw)
    for path in paths:
        p.feed(read_lines(path))
    return p
