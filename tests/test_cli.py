import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*args):
    import os
    return subprocess.run([sys.executable, "-m", "draftbot", *args], cwd=ROOT, capture_output=True, text=True,
                          env=dict(os.environ))


def test_cli_starts_with_set():
    r = run("--set", "TRK")
    assert r.returncode == 0, r.stderr
    assert "Star Trek" in r.stdout
    assert "wheel al pick 9" in r.stdout
    assert "wheel al pick 5" in r.stdout


def test_cli_unknown_set():
    r = run("--set", "XXX")
    assert r.returncode == 1
    assert "non trovato" in r.stderr


def test_cli_reports_missing_local_data():
    r = run("status", "TRK")
    assert r.returncode == 0
    assert "nessuna carta importata" in r.stdout


def test_db_connect_twice_does_not_lock():
    from draftbot import db
    a = db.connect()
    b = db.connect()
    assert b.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    a.close(); b.close()


def test_cli_log_commands(tmp_path):
    from draftbot.arenalog.synth import simulate
    from draftbot.config import load_format
    sd = simulate(load_format("pick_two"), list(range(90001, 90120)), event_name="PickTwoDraft_TRK_20261113")
    raw = tmp_path / "Player.log"
    raw.write_text("\n".join(sd.lines), encoding="utf-8")
    clean = tmp_path / "clean.log"
    r = run("log-clean", str(raw), str(clean), "--redact", "Rossi")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "nessun dato sospetto" in r.stdout
    r = run("log-drafts", str(clean), "-d")
    assert r.returncode == 0, r.stderr
    assert "Pick Two Draft" in r.stdout and "21/21" in r.stdout and "P3p7" in r.stdout
