"""Test-wide setup: never read or write the developer's real privacy settings."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("CSC398_SETTINGS", str(tmp_path / "privacy-settings.json"))
