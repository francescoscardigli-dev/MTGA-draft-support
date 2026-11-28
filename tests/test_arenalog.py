"""Test del parser di Player.log su log SINTETICI (struttura documentata, non ancora verificata)."""

import json
from collections import Counter

import pytest

from draftbot.arenalog import parse_text, validate
from draftbot.arenalog.sanitize import sanitize_lines
from draftbot.arenalog.synth import simulate
from draftbot.config import load_format

CARDS = list(range(90001, 90120))


@pytest.fixture(params=["premier", "pick_two"])
def fmt(request):
    return load_format(request.param)


def _parse(sd):
    return parse_text("\n".join(sd.lines))


def _check_exact(sd, draft):
    picks = {(p.pack, p.pick): p for p in draft.picks}
    assert set(picks) == set(sd.packs_seen)
    for k, cards in sd.packs_seen.items():
        assert picks[k].pack_cards == cards
        assert picks[k].picked == sd.picked[k]


def test_full_draft_notify(fmt):
    sd = simulate(fmt, CARDS, event_name=f"{fmt.arena_event_prefixes[0]}_TRK_20261113", seed=1)
    p = _parse(sd)
    [d] = p.results()
    assert d.draft_id == sd.draft_id and d.event_name == sd.event_name
    assert d.fmt.name == fmt.name and d.format_source == "nome evento"
    assert d.set_code == "TRK"
    _check_exact(sd, d)
    rep = validate(d)
    assert rep.ok and rep.complete, (rep.errors, rep.warnings)
    assert rep.picks_found == fmt.total_picks
    assert not rep.warnings
    assert d.pool == d.picked_cards()


def test_business_events_and_card_counted_numbering(fmt):
    # pick contati per carta (1, 3, 5... in Pick Two) e base 1: lo schema va dedotto dalle buste
    sd = simulate(fmt, CARDS, event_name=f"{fmt.arena_event_prefixes[0]}_FRA_20260101", seed=2,
                  style="business", pick_scale=fmt.picks_per_turn)
    [d] = _parse(sd).results()
    assert d.set_code == "FRA"
    _check_exact(sd, d)
    assert validate(d).ok


def test_zero_based_numbering_duplicates_and_multiline(fmt):
    sd = simulate(fmt, CARDS, event_name=f"{fmt.arena_event_prefixes[0]}_TRK_20261113", seed=3,
                  pick_base=0, duplicate_every=3, multiline_pick=True)
    [d] = _parse(sd).results()
    _check_exact(sd, d)
    rep = validate(d)
    assert rep.ok and rep.complete, rep.errors


def test_missing_pick_is_reported_not_invented(fmt):
    sd = simulate(fmt, CARDS, event_name=f"{fmt.arena_event_prefixes[0]}_TRK_20261113", seed=4,
                  drop=[(1, 2)])
    [d] = _parse(sd).results()
    rep = validate(d)
    assert not rep.complete
    assert any("P2p3" in w for w in rep.warnings)
    assert any("pool del corso diverso" in w for w in rep.warnings)


def test_format_guessed_without_event_name(fmt):
    sd = simulate(fmt, CARDS, event_name="UnknownEvent_ZZZ", seed=5)
    [d] = _parse(sd).results()
    assert d.fmt.name == fmt.name and d.format_source == "dedotto dalle buste"
    assert d.set_code is None
    assert any("dedotto" in w for w in validate(d).warnings)


def test_wheel_inconsistency_is_an_error():
    fmt = load_format("premier")
    sd = simulate(fmt, CARDS, event_name="PremierDraft_TRK_20261113", seed=6)
    # sostituisce una carta della busta tornata al pick 9 con una mai vista
    lines = []
    for ln in sd.lines:
        if '"SelfPick":9,"SelfPack":1' in ln:
            obj = json.loads(ln.split(" ", 1)[1])
            ids = obj["PackCards"].split(",")
            ids[0] = "99999"
            obj["PackCards"] = ",".join(ids)
            ln = "[UnityCrossThreadLogger]Draft.Notify " + json.dumps(obj)
        lines.append(ln)
    [d] = parse_text("\n".join(lines)).results()
    errors = validate(d).errors
    assert any("P1p9" in e and "tornata" in e for e in errors)


def test_two_drafts_in_one_log():
    a = simulate(load_format("premier"), CARDS, event_name="PremierDraft_TRK_20261113", seed=7,
                 draft_id="aaaaaaaa-1111-4222-8333-444455556666")
    b = simulate(load_format("pick_two"), CARDS, event_name="PickTwoDraft_TRK_20261113", seed=8,
                 draft_id="bbbbbbbb-1111-4222-8333-444455556666", with_personal_data=False)
    ds = parse_text("\n".join(a.lines + b.lines)).results()
    assert [d.fmt.name for d in ds] == ["premier", "pick_two"]
    assert all(validate(d).complete for d in ds)


def test_live_feed_partial_draft():
    from draftbot.arenalog import LogParser
    sd = simulate(load_format("premier"), CARDS, event_name="PremierDraft_TRK_20261113", seed=9)
    p = LogParser()
    kinds = []
    for ln in sd.lines[:20]:
        kinds += p.feed_line(ln)
    assert "event_join" in kinds and "pack" in kinds
    [d] = p.results()
    assert d.picks[0].pack_cards == sd.packs_seen[(0, 0)]


# ------------------------------------------------------------------ pulizia

def test_sanitize_removes_personal_data_and_keeps_draft():
    fmt = load_format("pick_two")
    sd = simulate(fmt, CARDS, event_name="PickTwoDraft_TRK_20261113", seed=10, style="business")
    out, rep = sanitize_lines(sd.lines, redact=["Rossi"])
    text = "\n".join(out)
    for secret in ("MarioRossi", "Rossi", "12345", "mario.rossi@example.com", "eyJhbGci",
                   "ABCDEFGHJKLMNPQRSTUVWXYZ23", "d0d0d0d0-1111"):
        assert secret not in text, secret
    assert not rep.leftovers
    assert rep.lines_out < rep.lines_in
    # il draft si ricostruisce identico dal log pulito (UUID pseudonimizzati in modo coerente)
    [d] = parse_text(text).results()
    _check_exact(sd, d)
    assert d.pool == d.picked_cards()
    assert validate(d).ok


def test_sanitize_keep_all_still_scrubs():
    sd = simulate(load_format("premier"), CARDS, event_name="PremierDraft_TRK_20261113", seed=11)
    out, rep = sanitize_lines(sd.lines, keep_all=True)
    text = "\n".join(out)
    assert rep.lines_out == rep.lines_in
    assert "MarioRossi" not in text and r"C:\Users\USER" in text
    assert "mario.rossi" not in text


# --- casi descritti da chi ha visto log veri (draftomen docs/log-format.md, nemedraft-client) ---------

def _notify_idx(lines, k):
    """Indice della k-esima riga di richiesta EventPlayerDraftMakePick."""
    return [i for i, ln in enumerate(lines) if "==> EventPlayerDraftMakePick" in ln][k]


def _request(draft_id, pack, pick, ids):
    return "[UnityCrossThreadLogger]==> EventPlayerDraftMakePick " + json.dumps(
        {"id": "9", "request": json.dumps({"DraftId": draft_id, "GrpIds": ids, "Pack": pack, "Pick": pick})})


@pytest.mark.parametrize("with_failure", [True, False])
def test_rejected_pick_is_replaced_by_resubmission(fmt, with_failure):
    sd = simulate(fmt, CARDS, event_name=f"{fmt.arena_event_prefixes[0]}_TRK_20261113", seed=4)
    i = _notify_idx(sd.lines, 2)
    pack = sd.packs_seen[(0, 2)]
    first = sd.picked[(0, 2)]
    other = [c for c in pack if c not in first][:fmt.picks_per_turn]
    # nel log: il primo invio e' il pick "vero" del simulatore; qui lo rifacciamo con altre carte
    extra = []
    if with_failure:
        extra += ["[UnityCrossThreadLogger]<== EventPlayerDraftMakePick(9)",
                  json.dumps({"TableInfo": {"SelfPack": 1, "PickedCards": [], "Players": []},
                              "PickInfo": {"PackCards": ",".join(map(str, pack)), "SelfPack": 1, "SelfPick": 3,
                                           "NumCardsToPick": fmt.picks_per_turn}})]
    extra.append(_request(sd.draft_id, 1, 3, other))
    lines = sd.lines[:i + 1] + extra + sd.lines[i + 1:]
    [d] = parse_text("\n".join(lines), ).results()
    picks = {(p.pack, p.pick): p for p in d.picks}
    assert Counter(picks[(0, 2)].picked) == Counter(other)
    notes = d.build()[1]
    assert any("rifiutato" in n for n in notes) if with_failure else any("ripresentato" in n for n in notes)


def test_event_from_queue_line_without_eventjoin():
    fmt = load_format("pick_two")
    sd = simulate(fmt, CARDS, event_name="PickTwoDraft_TRK_20261113", seed=5, with_pool=False)
    lines = [ln for ln in sd.lines if "EventJoin" not in ln]
    lines.insert(0, "[UnityCrossThreadLogger]Entering table draft queue: TRK_PickTwo_Draft")
    [d] = parse_text("\n".join(lines)).results()
    assert d.set_code == "TRK" and d.fmt.name == "pick_two" and d.format_source == "nome evento"


def test_resumed_draft_takes_event_from_course():
    fmt = load_format("premier")
    sd = simulate(fmt, CARDS, event_name="PremierDraft_TRK_20261113", seed=6, with_pool=False)
    first = next(i for i, ln in enumerate(sd.lines) if "Draft.Notify" in ln)
    course = json.dumps({"Courses": [
        {"CourseId": "c1", "InternalEventName": "QuickDraft_TRK_20261101", "CurrentModule": "DeckSelect", "CardPool": []},
        {"CourseId": "c2", "InternalEventName": "PremierDraft_TRK_20261113", "CurrentModule": "PlayerDraft",
         "DraftId": sd.draft_id, "CardPool": []}]})
    lines = ["[Accounts - Login] Logged in successfully."] + \
        ["[UnityCrossThreadLogger]<== EventGetCoursesV2(1)", course] + sd.lines[first:]
    [d] = parse_text("\n".join(lines)).results()
    assert d.event_name == "PremierDraft_TRK_20261113" and d.set_code == "TRK"
    assert d.pool is None   # pool vuoto: nessuna build anticipata


def test_draft_complete_response_gives_pool_and_completion(fmt):
    sd = simulate(fmt, CARDS, event_name=f"{fmt.arena_event_prefixes[0]}_TRK_20261113", seed=7, with_pool=False)
    [d0] = parse_text("\n".join(sd.lines)).results()
    pool = d0.picked_cards()
    lines = sd.lines + ["[UnityCrossThreadLogger]<== DraftCompleteDraft(5)",
                        json.dumps({"CourseId": "c3", "CurrentModule": "DeckSelect", "CardPool": pool})]
    [d] = parse_text("\n".join(lines)).results()
    assert d.completed and Counter(d.pool) == Counter(pool)


def test_pool_without_recorded_picks_is_ignored():
    fmt = load_format("premier")
    sd = simulate(fmt, CARDS, event_name="PremierDraft_TRK_20261113", seed=8, with_pool=False)
    lines = sd.lines + ["[UnityCrossThreadLogger]<== EventGetCoursesV2(1)",
                        json.dumps({"Courses": [{"InternalEventName": "PremierDraft_TRK_20261113",
                                                 "CurrentModule": "DeckSelect", "CardPool": [CARDS[0]]}]})]
    [d] = parse_text("\n".join(lines)).results()
    assert d.pool is None and any("pool del corso" in n for n in d.build()[1])


def test_phantom_pack_and_thread_prefix(fmt):
    sd = simulate(fmt, CARDS, event_name=f"{fmt.arena_event_prefixes[0]}_TRK_20261113", seed=9)
    phantom = "[UnityCrossThreadLogger]Draft.Notify " + json.dumps(
        {"draftId": sd.draft_id, "SelfPick": 1, "SelfPack": fmt.packs + 1, "PackCards": "90001,90002"})
    lines = [f"[{i % 7}] {ln}" for i, ln in enumerate(sd.lines + [phantom])]   # file UTC_Log ruotati
    [d] = parse_text("\n".join(lines)).results()
    _check_exact(sd, d)
    assert any("fuori dal formato" in n for n in d.build()[1])


# --- log reale (Premier TRK, ripulito con log-clean e riletto a mano) ----------------------------------

def test_real_premier_log():
    from pathlib import Path
    from draftbot.arenalog import parse_files
    path = Path(__file__).parent / "fixtures" / "logs" / "premier_trk_real.log"
    p = parse_files([path])
    [d] = p.results()
    assert d.event_name.startswith("PremierDraft_TRK") and d.set_code == "TRK" and d.fmt.name == "premier"
    picks, notes = d.build()
    assert len(picks) == 42 and not notes and not p.unrecognized
    assert [len(x.pack_cards) for x in picks[:14]] == list(range(14, 0, -1))
    assert all(len(x.picked) == 1 and x.picked[0] in x.pack_cards for x in picks)
    assert d.completed and Counter(d.pool) == Counter(d.picked_cards())
    rep = validate(d)
    assert rep.ok and rep.complete and not rep.warnings


def test_real_pick_two_log():
    """Draft Pick Two vero (TRK, macOS, Steam, 2026-11-28), ripulito con log-clean."""
    from pathlib import Path
    from draftbot.arenalog import parse_files
    path = Path(__file__).parent / "fixtures" / "logs" / "pick_two_trk_real.log"
    p = parse_files([path])
    [d] = p.results()
    assert d.event_name.startswith("PickTwoDraft_TRK") and d.set_code == "TRK" and d.fmt.name == "pick_two"
    picks, notes = d.build()
    assert len(picks) == 21 and not notes and not p.unrecognized
    # tavolo da 4, 2 carte per pick: buste da 14, 12, ..., 2 carte
    assert [len(x.pack_cards) for x in picks[:7]] == list(range(14, 0, -2))
    assert all(len(x.picked) == 2 and set(x.picked) <= set(x.pack_cards) for x in picks)
    assert d.completed and Counter(d.pool) == Counter(d.picked_cards())
    rep = validate(d)
    assert rep.ok and rep.complete and not rep.warnings
