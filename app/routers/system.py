"""Endpoints de estado y metadatos del servicio."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import APIRouter, Request

from app.core.config import APP_NAME, APP_VERSION, SUPPORTED_ORIGINS, Settings
from app.scheduler.jobs import FlightAlertJob
from app.scheduler.scheduler import JOB_ID
from app.schemas.system import HealthResponse, RootResponse, ScanStatusResponse

router = APIRouter(tags=["Sistema"])


@router.get("/", response_model=RootResponse)
async def root() -> RootResponse:
    """Devuelve la identidad del servicio y sus rutas principales."""
    return RootResponse(
        service=APP_NAME,
        version=APP_VERSION,
        origins=list(SUPPORTED_ORIGINS),
        docs_url="/docs",
        health_url="/health",
    )


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Expone el estado del planificador y del último escaneo."""
    settings: Settings = request.app.state.settings
    scheduler: AsyncIOScheduler = request.app.state.scheduler
    job: FlightAlertJob = request.app.state.flight_job
    scheduled_job = scheduler.get_job(JOB_ID)
    next_run_at = scheduled_job.next_run_time if scheduled_job else None

    return HealthResponse(
        status="ok" if scheduler.running else "degraded",
        scheduler_running=scheduler.running,
        next_run_at=next_run_at,
        check_interval_minutes=settings.check_interval_minutes,
        only_round_trip=settings.only_round_trip,
        travelpayouts_configured=settings.travelpayouts_configured,
        whatsapp_configured=settings.whatsapp_configured,
        last_scan=ScanStatusResponse.model_validate(job.status.as_dict()),
    )
