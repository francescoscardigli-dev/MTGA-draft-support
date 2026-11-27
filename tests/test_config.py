from pathlib import Path

import pytest

from draftbot.config import ConfigError, FormatConfig, available_sets, load_format, load_set


def test_trk_config_loads():
    cfg = load_set("trk")
    assert cfg.code == "TRK"
    assert cfg.scryfall_sets == ("trk", "sds")
    assert set(cfg.formats) == {"premier", "pick_two"}


def test_premier_structure():
    f = load_format("premier")
    assert f.picks_per_pack == 14
    assert f.pack_size_at(0) == 14 and f.pack_size_at(13) == 1
    assert f.wheel_picks() == [8]  # pick 9 in numerazione 1-based
    assert f.first_seen_pick(8) == 0
    assert f.pool_size == 42


def test_pick_two_structure():
    # Valori verificati sul dataset 17Lands TRK PickTwoDraft (Fase 0)
    f = load_format("pick_two")
    assert f.picks_per_pack == 7
    assert [f.pack_size_at(i) for i in range(7)] == [14, 12, 10, 8, 6, 4, 2]
    assert f.wheel_picks() == [4]
    assert f.first_seen_pick(4) == 0
    assert f.pool_size == 42


def test_eight_player_pick_two_has_no_wheel():
    f = FormatConfig("x", "X", "X", ("X",), 8, 3, 14, 2, ("left", "right", "left"), 1)
    assert f.wheel_picks() == []


def test_invalid_format_rejected():
    with pytest.raises(ConfigError):
        FormatConfig("x", "X", "X", ("X",), 4, 3, 15, 2, ("left", "right", "left"), 1)


def test_missing_set_is_reported():
    with pytest.raises(ConfigError):
        load_set("NOPE")


def test_available_sets():
    assert "TRK" in available_sets()


def test_engine_has_no_hardcoded_set_codes():
    """Vincolo: nessun dato specifico di set nel codice del motore."""
    pkg = Path(__file__).resolve().parent.parent / "draftbot"
    codes = {c.upper() for c in available_sets()}
    for cfg_code in list(codes):
        codes.update(s.upper() for s in load_set(cfg_code).scryfall_sets)
    for py in pkg.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for code in codes:
            assert f'"{code}"' not in text and f"'{code}'" not in text, f"{code} in {py.name}"
            assert f'"{code.lower()}"' not in text and f"'{code.lower()}'" not in text, f"{code} in {py.name}"
