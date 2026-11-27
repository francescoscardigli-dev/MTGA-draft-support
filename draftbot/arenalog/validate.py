"""Controlli di coerenza di un draft letto dal log, usando solo la config del formato."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .parser import Draft


@dataclass
class Report:
    complete: bool
    picks_found: int
    picks_expected: int | None
    errors: list[str] = field(default_factory=list)     # incoerenze vere
    warnings: list[str] = field(default_factory=list)   # dati mancanti o non verificabili

    @property
    def ok(self) -> bool:
        return not self.errors


def validate(draft: Draft) -> Report:
    picks, notes = draft.build()
    fmt = draft.fmt
    rep = Report(False, len(picks), fmt.total_picks if fmt else None, warnings=list(notes))
    if fmt is None:
        guesses = [f.name for f in draft.guessed_formats()]
        rep.warnings.append("formato non riconosciuto" + (f" (compatibili: {', '.join(guesses)})" if guesses else ""))
        return rep
    if draft.format_source != "nome evento":
        rep.warnings.append(f"formato {fmt.name} dedotto dalle buste: nome evento assente o non riconosciuto")

    by_key = {(p.pack, p.pick): p for p in picks}
    missing = [(a, b) for a in range(fmt.packs) for b in range(fmt.picks_per_pack) if (a, b) not in by_key]
    extra = [k for k in by_key if not (0 <= k[0] < fmt.packs and 0 <= k[1] < fmt.picks_per_pack)]
    for a, b in extra:
        rep.errors.append(f"busta {a + 1} pick {b + 1}: fuori dalla struttura del formato {fmt.name}")
    if missing:
        rep.warnings.append("pick mancanti: " + ", ".join(f"P{a + 1}p{b + 1}" for a, b in missing))

    for (a, b), p in sorted(by_key.items()):
        if (a, b) in extra:
            continue
        tag = f"P{a + 1}p{b + 1}"
        if p.pack_cards is not None and len(p.pack_cards) != fmt.pack_size_at(b):
            rep.errors.append(f"{tag}: busta di {len(p.pack_cards)} carte, attese {fmt.pack_size_at(b)}")
        if p.picked and len(p.picked) != fmt.picks_per_turn:
            rep.errors.append(f"{tag}: prese {len(p.picked)} carte, attese {fmt.picks_per_turn}")
        if p.pack_cards is not None and p.picked and Counter(p.picked) - Counter(p.pack_cards):
            rep.errors.append(f"{tag}: carta presa non presente nella busta")
        if p.pack_cards is None:
            rep.warnings.append(f"{tag}: busta non trovata nel log")
        if not p.picked:
            rep.warnings.append(f"{tag}: pick non trovato nel log")
        # wheel: la busta che torna e' un sottoinsieme di quella vista pod_size pick prima, meno i pick fatti
        first = fmt.first_seen_pick(b)
        prev = by_key.get((a, first)) if first is not None else None
        if prev and prev.pack_cards is not None and p.pack_cards is not None:
            left = Counter(prev.pack_cards) - Counter(prev.picked)
            if Counter(p.pack_cards) - left:
                rep.errors.append(f"{tag}: la busta tornata contiene carte non presenti al P{a + 1}p{first + 1}")

    if draft.pool is not None:
        diff_log = Counter(draft.picked_cards()) - Counter(draft.pool)
        diff_pool = Counter(draft.pool) - Counter(draft.picked_cards())
        if diff_log or diff_pool:
            rep.warnings.append(f"pool del corso diverso dai pick letti: {sum(diff_log.values())} carte solo "
                                f"nei pick, {sum(diff_pool.values())} solo nel pool")
    rep.complete = not missing and all(p.pack_cards is not None and p.picked for p in picks)
    return rep
