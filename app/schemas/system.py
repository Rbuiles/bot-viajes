"""Modelos de respuesta de los endpoints del sistema."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ScanStatusResponse(BaseModel):
    """Estado serializable del último escaneo de vuelos."""

    started_at: datetime | None = None
    finished_at: datetime | None = None
    offers_received: int = 0
    super_deals_found: int = 0
    duplicates_skipped: int = 0
    alerts_sent: int = 0
    alerts_failed: int = 0
    success: bool = True
    error: str | None = None


class HealthResponse(BaseModel):
    """Estado operativo del planificador y de los servicios externos."""

    status: Literal["ok", "degraded"]
    scheduler_running: bool
    next_run_at: datetime | None = None
    check_interval_minutes: int
    only_round_trip: bool
    travelpayouts_configured: bool
    whatsapp_configured: bool
    last_scan: ScanStatusResponse | None = None


class RootResponse(BaseModel):
    """Información básica y enlaces del servicio."""

    service: str
    version: str
    origins: list[str]
    docs_url: str
    health_url: str
