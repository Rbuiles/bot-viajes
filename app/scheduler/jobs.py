from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.database.db import Database
from app.services.notifier import WhatsAppNotifier
from app.services.travelpayouts import (
    TravelpayoutsClient,
    select_super_deals,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ScanStatus:
    """Resumen del estado y resultado de un escaneo de vuelos."""

    started_at: datetime | None = None
    finished_at: datetime | None = None
    offers_received: int = 0
    super_deals_found: int = 0
    duplicates_skipped: int = 0
    alerts_sent: int = 0
    alerts_failed: int = 0
    success: bool = True
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Convierte el estado en un diccionario serializable."""
        return asdict(self)


class FlightAlertJob:
    """Coordina la consulta de ofertas, el filtrado y las notificaciones."""

    def __init__(
        self,
        settings: Settings,
        client: TravelpayoutsClient,
        database: Database,
        notifier: WhatsAppNotifier,
    ) -> None:
        """Recibe las dependencias necesarias para ejecutar el escaneo."""
        self._settings = settings
        self._client = client
        self._database = database
        self._notifier = notifier
        self._running = False
        self.status = ScanStatus()

    async def run(self) -> None:
        """Ejecuta un ciclo completo de búsqueda y envío de alertas."""
        if self._running:
            logger.warning(
                "El escaneo anterior sigue en ejecución; se omite este ciclo."
            )
            return

        self._running = True
        status = ScanStatus(started_at=datetime.now(UTC))
        self.status = status

        try:
            if not self._settings.travelpayouts_configured:
                status.success = False
                status.error = "TRAVELPAYOUTS_TOKEN no está configurado."
                logger.error(status.error)
                return

            notifier_error = self._notifier.configuration_error
            if notifier_error is not None:
                status.success = False
                status.error = notifier_error
                logger.error(
                    "No se puede ejecutar el escaneo porque WhatsApp no está "
                    "configurado: %s",
                    notifier_error,
                )
                return

            offers = await self._client.fetch_all_offers()
            status.offers_received = len(offers)

            # El selector aplica país, umbral, origen y el modo de viaje.
            deals = select_super_deals(offers, self._settings)
            status.super_deals_found = len(deals)

            if not deals:
                logger.info(
                    "Escaneo completado sin Súper Promociones (%d ofertas revisadas).",
                    len(offers),
                )
                return

            recent_ids = await self._database.recently_notified_ids(
                [deal.deal_id for deal in deals],
                within=timedelta(hours=self._settings.deduplication_hours),
            )
            status.duplicates_skipped = sum(
                1 for deal in deals if deal.deal_id in recent_ids
            )

            for deal in deals:
                if deal.deal_id in recent_ids:
                    logger.info(
                        "Oferta omitida por duplicado reciente: %s → %s (USD %s).",
                        deal.offer.origin,
                        deal.offer.destination,
                        deal.offer.price,
                    )
                    continue

                sent = await self._notifier.send_deal_alert(deal)
                if not sent:
                    status.alerts_failed += 1
                    continue

                status.alerts_sent += 1
                try:
                    await self._database.record_alert(
                        deal_id=deal.deal_id,
                        origin=deal.offer.origin,
                        destination=deal.offer.destination,
                        country_code=deal.destination_info.country_code,
                        region=deal.destination_info.region.value,
                        price_usd=deal.offer.price,
                        departure_at=deal.offer.departure_at,
                    )
                except Exception:
                    status.success = False
                    status.error = (
                        "Se envió un mensaje de WhatsApp, pero no se pudo "
                        "registrar la oferta para el control de duplicados."
                    )
                    logger.exception(
                        "No se pudo registrar la alerta %s en SQLite.",
                        deal.deal_id,
                    )
        except Exception as exc:
            status.success = False
            status.error = f"{type(exc).__name__}: {exc}"
            logger.exception("El escaneo de vuelos falló.")
        finally:
            status.finished_at = datetime.now(UTC)
            self._running = False
            logger.info(
                "Escaneo finalizado: %d ofertas, %d súper promociones, "
                "%d duplicadas omitidas, %d alertas enviadas, %d fallidas.",
                status.offers_received,
                status.super_deals_found,
                status.duplicates_skipped,
                status.alerts_sent,
                status.alerts_failed,
            )

            try:
                await self._database.purge_old_alerts(
                    self._settings.notification_retention_days
                )
            except (SQLAlchemyError, OSError) as exc:
                logger.warning(
                    "No se pudieron purgar alertas antiguas de SQLite: %s",
                    exc,
                )
