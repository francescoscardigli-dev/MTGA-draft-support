import pytest

from draftbot import mana
from draftbot.names import keys, normalize


@pytest.mark.parametrize("cost,mv,pips,colors", [
    ("{2}{W}{W}", 4, {"W": 2}, "W"),
    ("{X}{R}", 1, {"R": 1}, "R"),
    ("{2/W}{2/W}", 4, {"W": 2}, "W"),
    ("{W/U}{B/P}", 2, {}, "WUB"),
    ("{C}{C}", 2, {}, ""),
    ("", 0, {}, ""),
    ("{10}", 10, {}, ""),
])
def test_parse(cost, mv, pips, colors):
    m = mana.parse(cost)
    assert (m.mv, m.pips, m.colors) == (mv, pips, colors)


def test_unknown_symbol():
    with pytest.raises(ValueError):
        mana.parse("{Q}")


def test_normalize_variants():
    assert normalize("All Good Things . . .") == normalize("All Good Things...")
    assert normalize("Urza’s Saga") == normalize("Urza's Saga")
    assert keys("Fire // Ice") == ["fire // ice", "fire"]
