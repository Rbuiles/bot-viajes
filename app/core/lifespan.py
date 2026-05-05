"""Gestión del arranque y apagado ordenado de la aplicación."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from app.core.config import SUPPORTED_ORIGINS, Settings, get_settings
from app.core.logging import configure_logging
from app.database.db import Database
from app.scheduler.jobs import FlightAlertJob
from app.scheduler.scheduler import create_scheduler
from app.services.notifier import WhatsAppNotifier
from app.services.travelpayouts import TravelpayoutsClient

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Inicializa y libera base de datos, clientes HTTP y planificador."""
    settings: Settings = get_settings()
    configure_logging(settings)
    database = Database(settings.database_url)
    client: TravelpayoutsClient | None = None
    notifier: WhatsAppNotifier | None = None
    scheduler: AsyncIOScheduler | None = None

    try:
        await database.init()
        client = TravelpayoutsClient(settings)
        notifier = WhatsAppNotifier(settings)
        job = FlightAlertJob(
            settings=settings,
            client=client,
            database=database,
            notifier=notifier,
        )
        scheduler = create_scheduler(job, settings)

        app.state.settings = settings
        app.state.database = database
        app.state.client = client
        app.state.notifier = notifier
        app.state.flight_job = job
        app.state.scheduler = scheduler

        if not settings.travelpayouts_configured:
            logger.warning(
                "Falta TRAVELPAYOUTS_TOKEN: las consultas a la API no "
                "se ejecutarán hasta configurarlo."
            )
        if notifier.configuration_error is not None:
            logger.warning(
                "Las alertas por WhatsApp están deshabilitadas: %s",
                notifier.configuration_error,
            )
        if not settings.travelpayouts_marker:
            logger.warning(
                "TRAVELPAYOUTS_MARKER no está configurado: los enlaces de "
                "reserva no incluirán marker de afiliado."
            )

        scheduler.start()
        logger.info(
            "Agenda activa: escaneo cada %d minutos para los orígenes %s.",
            settings.check_interval_minutes,
            ", ".join(SUPPORTED_ORIGINS),
        )
        yield
    finally:
        # Primero se detiene la agenda para evitar nuevos envíos durante el cierre.
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=False)
        if notifier is not None:
            await notifier.close()
        if client is not None:
            await client.close()
        await database.dispose()
        logger.info("Servicios detenidos y conexiones cerradas.")
