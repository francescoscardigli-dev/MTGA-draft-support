"""Test della UI web (Fase 8) e della revisione post-draft, con un motore finto."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from draftbot.arenalog.synth import simulate  # noqa: E402
from draftbot.config import load_format  # noqa: E402
from draftbot.engine.review import format_review, review_draft  # noqa: E402
from draftbot.live import LiveSession  # noqa: E402
from draftbot.ui.server import create_app  # noqa: E402

from test_live import _fake_engine  # noqa: E402

IDS = list(range(1000, 1060))


def _session_with_draft(n_lines=None):
    sd = simulate(load_format("premier"), IDS, event_name="PremierDraft_TRK_20261113", seed=4,
                  with_personal_data=False)
    sess = LiveSession()
    sess.engines[("TRK", "premier")] = _fake_engine(IDS)
    return sess, sd, sd.lines if n_lines is None else sd.lines[:n_lines]


def test_review_counts_all_picks_and_loss_is_nonnegative():
    sess, sd, lines = _session_with_draft()
    sess.feed(lines)
    d = sess.parser.results()[0]
    rv = review_draft(sess.engines[("TRK", "premier")], d)
    assert rv["summary"]["n_picks"] == 42
    assert all(p["loss"] is None or p["loss"] >= -1e-12 for p in rv["picks"])
    assert rv["builds"]
    assert "Pick valutati: 42" in format_review(rv)


def test_api_state_drafts_review_and_websocket():
    sess, sd, lines = _session_with_draft()
    app = create_app(None, session=sess)
    with TestClient(app) as client:
        assert "draftbot" in client.get("/").text
        assert client.get("/api/state").json()["pick"] is None
        with client.websocket_connect("/ws") as ws:
            sess.feed(lines[:12])          # alcune buste: arrivano eventi "pick"
            ev = ws.receive_json()
            assert ev["type"] == "pick" and ev["pack"] == 1
        st = client.get("/api/state").json()
        assert st["pick"]["type"] == "pick"
        sess.feed(lines[12:])
        ds = client.get("/api/drafts").json()
        assert len(ds) == 1 and ds[0]["picks"] == 42
        rv = client.get(f"/api/review/{ds[0]['draft_id']}").json()
        assert rv["summary"]["n_picks"] == 42
        assert client.get("/api/review/sconosciuto").status_code == 404
        assert client.get("/api/state").json()["build"]["type"] == "build"
