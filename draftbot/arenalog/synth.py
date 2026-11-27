"""Log sintetici per i test e le demo, finche' non ci sono log reali ripuliti.

Simula un tavolo secondo la config del formato (giocatori, carte per pick, direzioni di passaggio)
e scrive i messaggi nella forma documentata da fonti non ufficiali. NON prova che il parser
funzioni sui log veri: prova solo che e' coerente con la struttura attesa.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Sequence

from ..config import FormatConfig

TS = "[UnityCrossThreadLogger]11/27/2026 9:15:02 PM"


@dataclass
class SynthDraft:
    lines: list[str]
    draft_id: str
    event_name: str
    packs_seen: dict[tuple[int, int], list[int]]   # (busta, pick) 0-based -> carte
    picked: dict[tuple[int, int], list[int]]


def _q(obj: object) -> str:
    return json.dumps(obj, separators=(",", ":"))


def simulate(fmt: FormatConfig, card_pool: Sequence[int], *, event_name: str, seed: int = 0,
             draft_id: str = "d0d0d0d0-1111-4222-8333-444455556666", style: str = "notify",
             pick_base: int = 1, pick_scale: int = 1, duplicate_every: int = 0,
             drop: Sequence[tuple[int, int]] = (), with_pool: bool = True, with_personal_data: bool = True,
             multiline_pick: bool = False) -> SynthDraft:
    """``style``: ``notify`` (Draft.Notify + EventPlayerDraftMakePick) o ``business`` (LogBusinessEvents)."""
    rng = random.Random(seed)
    seats = fmt.pod_size
    lines: list[str] = []
    if with_personal_data:
        lines += [
            "[Accounts - Login] Logged in successfully. Display Name: MarioRossi#12345",
            r"Mono path[0] = 'C:\Users\MarioRossi\AppData\Local\Programs\MTGA\MTGA_Data\Managed'",
            "[UnityCrossThreadLogger]==> Authenticate " + _q({"id": "9f1c2b3a-0000-4000-8000-aaaaaaaaaaaa",
                "request": _q({"ClientId": "ABCDEFGHJKLMNPQRSTUVWXYZ23", "Ticket": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefg"})}),
            "[UnityCrossThreadLogger]<== Authenticate " + _q({"authenticateResponse": {
                "clientId": "ABCDEFGHJKLMNPQRSTUVWXYZ23", "sessionId": "12345678-aaaa-4bbb-8ccc-1234567890ab",
                "screenName": "MarioRossi#12345", "email": "mario.rossi@example.com"}}),
        ]
    lines += [
        "[UnityCrossThreadLogger]==> EventGetActiveEventsV2 " + _q({"id": "1", "request": "{}"}),
        _q({"Events": [{"InternalEventName": "QuickDraft_XXX_20260101", "EventName": "QuickDraft_XXX_20260101"},
                       {"InternalEventName": event_name}]}),
        "[UnityCrossThreadLogger]==> EventJoin " + _q({"id": "2", "request": _q({
            "EventName": event_name, "EntryCurrencyType": "Gem", "EntryCurrencyPaid": 1500})}),
        f"{TS}",
        "Some unrelated line with {broken json",
    ]
    pool = list(card_pool)
    packs_seen: dict[tuple[int, int], list[int]] = {}
    picked: dict[tuple[int, int], list[int]] = {}
    for pack_i in range(fmt.packs):
        packs = [[rng.choice(pool) for _ in range(fmt.pack_size)] for _ in range(seats)]
        step = 1 if fmt.pass_directions[pack_i] == "left" else -1
        for pick_i in range(fmt.picks_per_pack):
            for seat in range(seats):
                # il sedile s al turno t ha la busta aperta dal sedile (s - step*t)
                owner = (seat - step * pick_i) % seats
                pack = packs[owner]
                take = pack[:fmt.picks_per_turn] if seat == 0 else rng.sample(pack, fmt.picks_per_turn)
                if seat == 0:
                    packs_seen[(pack_i, pick_i)] = list(pack)
                    picked[(pack_i, pick_i)] = list(take)
                    if (pack_i, pick_i) not in drop:
                        rp, rk = pack_i + pick_base, pick_i * pick_scale + pick_base
                        lines += _messages(style, draft_id, event_name, rp, rk, list(pack), list(take),
                                           multiline_pick)
                        n = len(packs_seen)
                        if duplicate_every and n % duplicate_every == 0:
                            lines += _messages(style, draft_id, event_name, rp, rk, list(pack), list(take),
                                               multiline_pick)
                for c in take:
                    pack.remove(c)
        lines.append("[UnityCrossThreadLogger]Client.SceneChange " + _q({"fromSceneName": "Draft"}))
    if with_pool:
        all_picked = [c for k in sorted(picked) for c in picked[k]]
        lines += ["[UnityCrossThreadLogger]<== EventGetCoursesV2(7b0c)",
                  _q({"Courses": [{"CourseId": "c0c0c0c0-1111-4222-8333-444455556666",
                                   "InternalEventName": event_name, "CurrentModule": "DeckSelect",
                                   "CardPool": all_picked, "CurrentWins": 0, "CurrentLosses": 0}]})]
    return SynthDraft(lines, draft_id, event_name, packs_seen, picked)


def _messages(style: str, draft_id: str, event: str, pack: int, pick: int, cards: list[int],
              take: list[int], multiline: bool) -> list[str]:
    if style == "business":
        return ["[UnityCrossThreadLogger]==> LogBusinessEvents " + _q({"id": "3", "request": _q({
            "PlayerId": "ABCDEFGHJKLMNPQRSTUVWXYZ23", "DraftId": draft_id, "EventId": event,
            "PackNumber": pack, "PickNumber": pick, "PickGrpIds": take, "CardsInPack": cards,
            "AutoPick": False, "TimeRemainingOnPick": 41})})]
    out = ["[UnityCrossThreadLogger]Draft.Notify " + _q({
        "draftId": draft_id, "SelfPick": pick, "SelfPack": pack, "PackCards": ",".join(map(str, cards))})]
    body = _q({"id": "4", "request": _q({"DraftId": draft_id, "GrpIds": take, "Pack": pack, "Pick": pick})})
    if multiline:
        out += ["[UnityCrossThreadLogger]==> EventPlayerDraftMakePick", body]
    else:
        out.append("[UnityCrossThreadLogger]==> EventPlayerDraftMakePick " + body)
    return out
