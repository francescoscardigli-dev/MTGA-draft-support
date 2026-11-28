import gzip
import io
import sqlite3

from draftbot import cardbuild, db
from draftbot.config import SetConfig
from draftbot.sources import seventeenlands
from draftbot.sources.arena_db import ArenaCard, ArenaDbError, read_cards

CFG = SetConfig(code="TST", name="Test", scryfall_sets=("tst", "bon"), seventeenlands_expansion="TST",
                formats=("premier",), arena_id_sources=("arena_card_database", "seventeenlands", "scryfall"))


def sf(name, set_="tst", cn="1", cost="{1}{W}", type_line="Creature — Human", rarity="common", **kw):
    d = {"id": f"id-{name}", "name": name, "set": set_, "collector_number": cn, "mana_cost": cost,
         "cmc": kw.pop("cmc", 2.0), "type_line": type_line, "rarity": rarity, "layout": kw.pop("layout", "normal"),
         "colors": kw.pop("colors", ["W"]), "color_identity": ["W"], "oracle_text": "", "booster": True}
    d.update(kw)
    return d


def test_build_maps_names_ids_and_reports_gaps():
    scry = [
        sf("Alpha"), sf("Alpha", cn="301", frame_effects=["showcase"]),
        sf("All Good Things...", cn="2"),
        sf("Beta", cn="3", arena_id=900),
        sf("Bonus Card", set_="bon", cn="1", rarity="mythic"),
        sf("Prep Card // Prep Spell", cn="4", cost="{1}{G} // {G}", cmc=2.0, layout="prepare",
           card_faces=[{"name": "Prep Card", "mana_cost": "{1}{G}", "type_line": "Creature"},
                       {"name": "Prep Spell", "mana_cost": "{G}", "type_line": "Sorcery"}]),
        sf("Guest", set_="old", cn="10"),
    ]
    universe = ["Alpha", "All Good Things . . .", "Beta", "Bonus Card", "Prep Card // Prep Spell", "Guest", "Ghost"]
    csv_rows = [
        {"id": "100", "expansion": "TST", "name": "Alpha"},
        {"id": "101", "expansion": "TST", "name": "Alpha"},        # variante showcase
        {"id": "555", "expansion": "OLD", "name": "Alpha"},        # ristampa altrove: ignorata
        {"id": "102", "expansion": "TST", "name": "All Good Things . . ."},
        {"id": "103", "expansion": "BON", "name": "Bonus Card"},
        {"id": "777", "expansion": "SPG", "name": "Guest"},        # ospite: accettata
    ]
    arena = [ArenaCard(104, "Prep Card", "TST", "4"), ArenaCard(105, "Unknown Thing", "TST", "99"),
             ArenaCard(100, "Beta", "TST", "1")]  # conflitto voluto con 17Lands
    res = cardbuild.build(CFG, scryfall_cards=scry, universe=universe, cards_csv=csv_rows, arena_db_cards=arena)

    names = {c["name"] for c in res.cards}
    assert names == {"Alpha", "All Good Things...", "Beta", "Bonus Card", "Prep Card // Prep Spell", "Guest"}
    alpha = next(c for c in res.cards if c["name"] == "Alpha")
    assert alpha["collector_number"] == "1"  # stampa base, non la showcase
    prep = next(c for c in res.cards if c["name"].startswith("Prep"))
    assert prep["pip_g"] == 1 and prep["cost_colors"] == "G" and prep["mv"] == 2.0

    ids = {g: n for g, n, _, _ in res.arena_ids}
    assert ids[100] == "Beta"           # il database Arena ha la priorita'
    assert ids[101] == "Alpha" and 555 not in ids
    assert ids[102] == "All Good Things..."
    assert ids[103] == "Bonus Card" and ids[104].startswith("Prep") and ids[777] == "Guest"
    assert ids[900] == "Beta"           # da Scryfall

    kinds = {(k, d.split(" ")[0]) for k, d in res.issues}
    assert ("card_not_on_scryfall", "Ghost") in kinds
    assert ("arena_id_conflict", "100:") in kinds
    assert ("arena_id_without_card", "105") in kinds


def test_card_without_arena_id_is_reported():
    res = cardbuild.build(CFG, scryfall_cards=[sf("Alpha")], universe=["Alpha"], cards_csv=[], arena_db_cards=None)
    assert res.issues == [("card_without_arena_id", "Alpha")]


def test_universe_from_scryfall_when_no_17lands():
    scry = [sf("Alpha"), sf("Token", layout="token"), sf("Other", set_="zzz")]
    res = cardbuild.build(CFG, scryfall_cards=scry, universe=None, cards_csv=None, arena_db_cards=None)
    assert [c["name"] for c in res.cards] == ["Alpha"]


def test_db_roundtrip_and_query(tmp_path):
    res = cardbuild.build(CFG, scryfall_cards=[sf("Alpha"), sf("Beta", cn="2", cost="{3}{U}{U}", cmc=5.0,
                                                            colors=["U"])],
                          universe=None, cards_csv=[{"id": "1", "expansion": "TST", "name": "Beta"}],
                          arena_db_cards=None)
    con = db.connect(tmp_path / "t.sqlite")
    db.replace_set(con, "TST", res.cards, res.arena_ids, res.issues)
    assert [r["name"] for r in db.query_cards(con, "TST", mv=5)] == ["Beta"]
    assert [r["name"] for r in db.query_cards(con, "TST", color="U")] == ["Beta"]
    assert [r["name"] for r in db.query_cards(con, "TST", name="alp")] == ["Alpha"]
    assert [r["name"] for r in db.query_cards(con, "TST", grp_id=1)] == ["Beta"]
    assert db.query_cards(con, "TST", name="beta")[0]["pip_u"] == 2


def test_header_with_commas_in_names():
    buf = io.BytesIO()
    with gzip.open(buf, "wt", newline="") as g:
        g.write('draft_id,pick,"pack_card_Arni, Humble Scribe",pack_card_Beta,"pool_Arni, Humble Scribe"\n1,x,0,1,0\n')
    buf.seek(0)
    header = seventeenlands.header_from_gzip_stream(buf)
    assert seventeenlands.card_names_from_header(header) == ["Arni, Humble Scribe", "Beta"]


def _fake_arena_db(path, cards_cols="GrpId, TitleId, ExpansionCode, CollectorNumber"):
    con = sqlite3.connect(path)
    con.execute(f"CREATE TABLE Cards ({cards_cols})")
    con.execute("CREATE TABLE Localizations_enUS (LocId, Formatted, Loc)")
    con.executemany("INSERT INTO Cards VALUES (?,?,?,?)", [(1, 10, "TST", "1"), (2, 11, "OLD", "5")])
    con.executemany("INSERT INTO Localizations_enUS VALUES (?,?,?)",
                    [(10, 1, "<i>Alpha</i>"), (10, 0, "Alpha"), (11, 0, "Beta")])
    con.commit()
    con.close()


def test_arena_db_reader(tmp_path):
    p = tmp_path / "Raw_CardDatabase_x.mtga"
    _fake_arena_db(p)
    cards = read_cards(p, {"TST"})
    assert [(c.grp_id, c.name, c.expansion) for c in cards] == [(1, "Alpha", "TST")]


def test_arena_db_unknown_schema_is_reported(tmp_path):
    p = tmp_path / "Raw_CardDatabase_y.mtga"
    _fake_arena_db(p, "Id, Title, Exp, Num")
    try:
        read_cards(p)
    except ArenaDbError as e:
        assert "non riconosciuto" in str(e)
    else:
        raise AssertionError("schema sconosciuto non segnalato")


def test_arena_renamed_cards_match_printed_name():
    # set solo-Arena (es. OM1): Scryfall ha il nome originale in "name" e quello di Arena in "printed_name"
    scry = [sf("Spider-Verse", cn="7", printed_name="Arachnomania"),
            sf("Spider-Verse", set_="old", cn="1")]            # stampa originale in un altro set
    res = cardbuild.build(CFG, scryfall_cards=scry, universe=["Arachnomania"],
                          cards_csv=[{"id": "300", "expansion": "TST", "name": "Arachnomania"}], arena_db_cards=None)
    names = [c["name"] for c in res.cards]
    assert names == ["Arachnomania"]
    assert not [i for i in res.issues if i[0] == "card_not_on_scryfall"]
    assert [(g, n) for g, n, _, _ in res.arena_ids] == [(300, "Arachnomania")]
