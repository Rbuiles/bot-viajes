from __future__ import annotations

from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import Settings
from app.scheduler.jobs import FlightAlertJob

JOB_ID = "flight_alert_scan"


def create_scheduler(
    job: FlightAlertJob,
    settings: Settings,
) -> AsyncIOScheduler:
    """Crea el planificador con una única tarea recurrente."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        job.run,
        trigger=IntervalTrigger(
            minutes=settings.check_interval_minutes,
            timezone="UTC",
        ),
        id=JOB_ID,
        name="Búsqueda de Súper Promociones",
        replace_existing=True,
        # Evita solapamientos y ejecuciones acumuladas tras una pausa.
        max_instances=1,
        coalesce=True,
        misfire_grace_time=max(60, settings.check_interval_minutes * 30),
        next_run_time=datetime.now(UTC),
    )
    return scheduler
