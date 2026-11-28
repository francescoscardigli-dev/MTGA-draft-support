import pytest

from draftbot.addset import propose_config, write_config
from draftbot.config import load_set
from draftbot.sources.seventeenlands import Dataset

SETS = [
    {"code": "new", "name": "New Set", "set_type": "expansion", "released_at": "2027-01-01"},
    {"code": "nbs", "name": "New Bonus", "set_type": "masterpiece", "parent_set_code": "new"},
    {"code": "tnew", "name": "New Tokens", "set_type": "token", "parent_set_code": "new"},
]


def test_propose_and_write_config(tmp_path):
    ds = [Dataset("NEW", "PremierDraft", "draft", "u", "2027-01-05")]
    cfg = propose_config("new", sets=SETS, datasets=ds)
    assert cfg["scryfall_sets"] == ["new", "nbs"]
    assert cfg["formats"] == ["premier"]          # solo i formati con draft data
    assert set(propose_config("new", sets=SETS, datasets=[])["formats"]) == {"premier", "pick_two"}
    assert cfg["_notes"]["17lands_draft_data"] == {"pick_two": False, "premier": True}
    path = write_config(cfg, tmp_path)
    loaded = load_set("NEW", sets_dir=tmp_path)
    assert loaded.name == "New Set" and loaded.scryfall_sets == ("new", "nbs")
    with pytest.raises(FileExistsError):  # mai sovrascrivere una config esistente
        write_config(propose_config("new", sets=SETS, datasets=ds), tmp_path)
    assert path.read_text().startswith("# Config del set NEW")


def test_pack_size_override_from_data(tmp_path):
    ds = [Dataset("NEW", "PremierDraft", "draft", "u", "2027-01-05")]
    cfg = propose_config("new", sets=SETS, datasets=ds, pack_sizes={"premier": 13})
    assert cfg["format_overrides"] == {"premier": {"pack_size": 13}}
    write_config(cfg, tmp_path)
    from draftbot.config import format_for
    f = format_for("premier", "NEW", sets_dir=tmp_path)
    assert f.pack_size == 13 and f.total_picks == 39 and f.wheel_picks() == [8]
    assert propose_config("new", sets=SETS, datasets=ds, pack_sizes={"premier": 14})["format_overrides"] == {}
