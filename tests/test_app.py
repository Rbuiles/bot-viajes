from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from tests.helpers import build_settings


def test_health_and_root_endpoints(tmp_path: Path) -> None:
    settings = build_settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'app.db'}",
        travelpayouts_token="",
        whatsapp_access_token="",
        check_interval_minutes=10,
    )

    with (
        patch("app.core.lifespan.get_settings", return_value=settings),
        TestClient(app) as client,
    ):
        root_response = client.get("/")
        health_response = client.get("/health")

    assert root_response.status_code == 200
    assert root_response.json()["origins"] == ["MDE", "PEI"]

    assert health_response.status_code == 200
    health = health_response.json()
    assert health["status"] == "ok"
    assert health["scheduler_running"] is True
    assert health["check_interval_minutes"] == 10
    assert health["only_round_trip"] is False
    assert health["travelpayouts_configured"] is False
    assert health["whatsapp_configured"] is False
    assert health["next_run_at"] is not None
