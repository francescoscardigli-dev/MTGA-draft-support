"""Lettura live di Player.log (Fase 7): consiglio a ogni nuova busta, build a fine draft.

- ``LogTailer`` segue il file e gestisce la rotazione (al riavvio Arena rinomina Player.log in
  Player-prev.log e ne crea uno nuovo: se il file si accorcia si riparte dall'inizio).
- ``LiveSession`` passa le righe al parser della Fase 2. Quando arriva una busta del draft in corso
  calcola il consiglio (pick, con wheel e segnali se disponibili) e produce un evento JSON; a draft
  completo produce le build. Gli eventi vanno ai callback registrati (console, UI web della Fase 8).

Gli eventi sono dizionari con ``type``: ``status`` | ``pick`` | ``build`` | ``error``.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import db
from .arenalog import LogParser
from .config import load_format, load_set


class LogTailer:
    def __init__(self, path: Path, from_start: bool = True):
        self.path = Path(path)
        self.pos = 0
        self.inode = None
        self.buf = ""
        if not from_start and self.path.exists():
            self.pos = self.path.stat().st_size

    def read_new(self) -> list[str]:
        if not self.path.exists():
            return []
        st = self.path.stat()
        ino = getattr(st, "st_ino", None)
        if st.st_size < self.pos or (self.inode is not None and ino != self.inode):
            self.pos, self.buf = 0, ""          # file ruotato o troncato
        self.inode = ino
        if st.st_size == self.pos:
            return []
        with self.path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(self.pos)
            data = fh.read()
            self.pos = fh.tell()
        data = self.buf + data
        lines = data.split("\n")
        self.buf = lines.pop()                  # ultima riga forse incompleta
        return [ln.rstrip("\r") for ln in lines]


@dataclass
class Engine:
    """Componenti del motore per un set e formato, caricati una volta."""
    set_code: str
    format: str
    projector: object
    builder: object
    names: dict[int, str]
    wheel: object = None
    pop: dict[str, float] = field(default_factory=dict)
    signal_base: object = None
    signal_model: object = None


def load_engine(con, set_code: str, fmt: str) -> Engine:
    from .engine.build import Builder
    from .enginecli import _projector
    from .engine.extras import load_extras, signal_baseline
    proj = _projector(con, set_code, fmt)
    eng = Engine(set_code, fmt, proj, Builder(proj.book, proj.model, proj.synergy), db.arena_names(con, set_code))
    ex = load_extras(fmt)
    if ex is not None:
        from .engine.phase7check import popularity
        eng.wheel = ex.wheel
        eng.pop = popularity(con, set_code, fmt, None)
        if ex.signal is not None and (ex.signal.g_same or ex.signal.g_other):
            eng.signal_model = ex.signal
            eng.signal_base = signal_baseline(con, set_code, fmt, proj.book)
    return eng


class LiveSession:
    def __init__(self, set_code: str | None = None, fmt: str | None = None, k: int = 3):
        self.parser = LogParser()
        self.con = db.connect()
        self.k = k
        self.forced = (set_code, fmt)
        self.engines: dict[tuple[str, str], Engine] = {}
        self.callbacks: list[Callable[[dict], None]] = []
        self.done: set[tuple[str, int, int]] = set()
        self.built: set[str] = set()
        self.last_event: dict | None = None

    def on_event(self, cb: Callable[[dict], None]) -> None:
        self.callbacks.append(cb)

    def emit(self, ev: dict) -> None:
        self.last_event = ev if ev.get("type") in ("pick", "build") else self.last_event
        for cb in self.callbacks:
            cb(ev)

    def engine(self, set_code: str, fmt: str) -> Engine | None:
        key = (set_code, fmt)
        if key not in self.engines:
            try:
                self.engines[key] = load_engine(self.con, set_code, fmt)
            except Exception as e:  # dati del set mancanti: lo si dice invece di fermarsi
                self.emit({"type": "error", "message": f"motore non disponibile per {set_code} {fmt}: {e}"})
                return None
        return self.engines[key]

    def feed(self, lines: list[str]) -> None:
        changed = False
        for ln in lines:
            kinds = self.parser.feed_line(ln)
            if "pack" in kinds or "pool" in kinds or "pick" in kinds:
                changed = True
        if changed:
            self.update()

    def update(self) -> None:
        drafts = self.parser.results()
        if not drafts:
            return
        d = drafts[-1]
        set_code = self.forced[0] or d.set_code
        fmt = self.forced[1] or (d.fmt.name if d.fmt else None)
        if not set_code or not fmt:
            self.emit({"type": "status", "message": f"draft {d.draft_id}: set o formato non riconosciuti "
                                                      f"(evento: {d.event_name}); usa --set e --format"})
            return
        set_code = load_set(set_code).code
        eng = self.engine(set_code, fmt)
        if eng is None:
            return
        picks = d.picks
        fmt_cfg = load_format(fmt)
        last = next((p for p in reversed(picks) if p.pack_cards is not None and not p.picked), None)
        if last is not None and (d.draft_id, last.pack, last.pick) not in self.done:
            self.done.add((d.draft_id, last.pack, last.pick))
            self.emit(self.pick_event(eng, d, last, picks, fmt_cfg))
        total = sum(len(p.picked) for p in picks)
        if (total >= fmt_cfg.pool_size or d.pool) and d.draft_id not in self.built:
            self.built.add(d.draft_id)
            self.emit(self.build_event(eng, d))

    def _cards(self, eng: Engine, ids: list[int]):
        book = eng.projector.book
        out, missing = [], []
        for g in ids:
            n = eng.names.get(g)
            if n and n in book.cards:
                out.append(book.cards[n])
            else:
                missing.append(g)
        return out, missing

    def pick_event(self, eng: Engine, d, last, picks, fmt_cfg) -> dict:
        from .engine.pick import recommend
        from .engine.signals import SignalState, extra_logits
        pool, _ = self._cards(eng, [g for p in picks if (p.pack, p.pick) < (last.pack, last.pick) for g in p.picked])
        pack, missing = self._cards(eng, last.pack_cards)
        xl, sig_summary = None, None
        if eng.signal_model is not None:
            st = SignalState()
            for p in picks:
                if p.pack_cards is not None and (p.pack, p.pick) <= (last.pack, last.pick):
                    st.observe(p.pack, p.pick, self._cards(eng, p.pack_cards)[0], fmt_cfg, eng.signal_base)
            xl = extra_logits(eng.signal_model, st, fmt_cfg.pass_directions[last.pack])
            sig_summary = st.colors_summary()
        wheel_fn = (lambda rem: eng.wheel.survival(rem, fmt_cfg, eng.pop)) if eng.wheel is not None else None
        opts, probs = recommend(eng.projector, pool, pack, k=self.k, extra_logits=xl, wheel=wheel_fn)
        wheel_probs = {}
        if eng.wheel is not None and pack:
            for c, w in zip(pack, eng.wheel.survival(pack, fmt_cfg, eng.pop)):
                wheel_probs[c.name] = float(w)
        info = getattr(eng.projector, "synergy_info", None)
        return {
            "type": "pick", "draft_id": d.draft_id, "set": eng.set_code, "format": eng.format,
            "pack": last.pack + 1, "pick": last.pick + 1, "pool_size": len(pool),
            "wheel_possible": last.pick + fmt_cfg.pod_size < fmt_cfg.picks_per_pack,
            "options": [{"cards": list(o.names), "value": o.value, "deck_gain": o.deck_gain,
                         "wheel_bonus": o.wheel_bonus, "explain": o.explain(),
                         "themes": {n: info.roles(n) for n in o.names} if info else {}} for o in opts],
            "pack_cards": [{"name": c.name, "q": c.q, "colors": c.colors, "mv": c.mv, "rarity": c.rarity,
                            "wheel": wheel_probs.get(c.name)} for c in sorted(pack, key=lambda c: -c.q)],
            "archetypes": dict(sorted(probs.items(), key=lambda kv: -kv[1])),
            "signals": sig_summary,
            "pool": [c.name for c in pool],
            "unknown_ids": missing,
        }

    def build_event(self, eng: Engine, d) -> dict:
        ids = d.pool if d.pool else d.picked_cards()
        pool, missing = self._cards(eng, ids)
        builds = eng.builder.best_builds(pool, k=3)
        return {"type": "build", "draft_id": d.draft_id, "set": eng.set_code, "format": eng.format,
                "builds": [{"label": b.label, "expected_wr": b.expected_wr, "score": b.score,
                            "lands": b.n_lands, "basics": b.basics, "nonbasic_lands": [l.name for l in b.lands],
                            "curve": b.curve(), "notes": b.notes,
                            "spells": [{"name": c.name, "q": c.q, "mv": c.mv} for c in b.spells]}
                           for b in builds],
                "unknown_ids": missing}


def run_console(path: Path, set_code: str | None, fmt: str | None, poll: float = 0.5,
                once: bool = False, from_start: bool = False) -> int:
    """Segue il log e stampa i consigli. ``once``: legge il file una volta ed esce (per prove)."""
    sess = LiveSession(set_code, fmt)
    sess.on_event(print_event)
    tail = LogTailer(path, from_start=from_start or once)
    print(f"In ascolto su {path} (Ctrl+C per uscire)" if not once else f"Lettura di {path}")
    try:
        while True:
            lines = tail.read_new()
            if lines:
                sess.feed(lines)
            if once:
                return 0
            time.sleep(poll)
    except KeyboardInterrupt:
        return 0


def print_event(ev: dict) -> None:
    t = ev.get("type")
    if t in ("status", "error"):
        print(f"[{t}] {ev['message']}")
    elif t == "pick":
        arch = ", ".join(f"{a} {p:.0%}" for a, p in list(ev["archetypes"].items())[:3])
        print(f"\n{ev['set']} {ev['format']} P{ev['pack']}p{ev['pick']} (pool {ev['pool_size']}; {arch})")
        for i, o in enumerate(ev["options"], 1):
            wb = f", wheel {o['wheel_bonus'] * 100:+.2f}" if o["wheel_bonus"] else ""
            print(f"  {i}. {' + '.join(o['cards'])}  {o['value'] * 100:+.2f} punti{wb}")
        wh = [f"{c['name']} {c['wheel']:.0%}" for c in ev["pack_cards"] if c.get("wheel") is not None]
        if ev["wheel_possible"] and wh:
            print("  probabilita' di ritorno: " + ", ".join(wh[:6]))
        if ev.get("signals"):
            print("  segnali (qualita' in piu' del previsto, punti): " +
                  ", ".join(f"{c} {v * 100:+.1f}" for c, v in ev["signals"].items()))
    elif t == "build":
        print(f"\nDraft finito: build proposte")
        for b in ev["builds"]:
            print(f"  {b['label']}: win rate attesa {b['expected_wr']:.1%}, terre {b['lands']} {b['basics']}")


def default_log() -> Path | None:
    from .arenalog import default_log_paths
    paths = default_log_paths()
    return paths[-1] if paths else None
