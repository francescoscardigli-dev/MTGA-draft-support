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
