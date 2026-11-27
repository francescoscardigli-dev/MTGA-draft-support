"""Test del parser di Player.log su log SINTETICI (struttura documentata, non ancora verificata)."""

import json

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
