from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.services.travelpayouts import FlightOffer


def build_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "travelpayouts_token": "test-token",
        "travelpayouts_marker": "test-marker",
        "travelpayouts_max_retries": 1,
        "whatsapp_phone_number_id": "123456789012345",
        "whatsapp_access_token": "test-whatsapp-token",
        "notification_phone": "+573001234567",
        "database_url": "sqlite+aiosqlite:///:memory:",
    }
    values.update(overrides)
    return Settings(**values)


def build_offer(**overrides: Any) -> FlightOffer:
    values: dict[str, Any] = {
        "origin": "MDE",
        "destination": "MAD",
        "price": 450.0,
        "airline": "AV",
        "flight_number": "123",
        "departure_at": datetime.now(UTC) + timedelta(days=45),
        "return_at": datetime.now(UTC) + timedelta(days=60),
        "link": "/search/MDE1506MAD23061?search_date=10112026",
        "transfers": 0,
    }
    values.update(overrides)
    return FlightOffer.model_validate(values)
