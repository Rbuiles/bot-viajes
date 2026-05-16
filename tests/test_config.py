from __future__ import annotations

import pytest

from app.core.config import Settings


def test_only_round_trip_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ONLY_ROUND_TRIP", raising=False)

    settings = Settings(_env_file=None)

    assert settings.only_round_trip is False


def test_only_round_trip_reads_true_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ONLY_ROUND_TRIP", "true")

    settings = Settings(_env_file=None)

    assert settings.only_round_trip is True
