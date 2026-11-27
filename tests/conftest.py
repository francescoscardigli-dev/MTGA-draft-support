import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """I test non toccano mai la cache reale in ./data."""
    monkeypatch.setenv("DRAFTBOT_DATA", str(tmp_path / "data"))
