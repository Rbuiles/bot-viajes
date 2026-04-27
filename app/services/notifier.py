"""Notificaciones mediante Meta WhatsApp Cloud API."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Final
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from app.core.config import Settings
from app.services.travelpayouts import FlightDeal

logger = logging.getLogger(__name__)

API_BASE_URL: Final[str] = "https://graph.facebook.com"
API_VERSION: Final[str] = "v22.0"
MAX_MESSAGE_LENGTH: Final[int] = 4096
MONTHS_ES: Final[tuple[str, ...]] = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def _format_price(price: float) -> str:
    if price.is_integer():
        return f"{int(price):,}"
    return f"{price:,.2f}"


def _format_date(value: datetime | None) -> str:
    if value is None:
        return "No informada"
    return (
        f"{value.day} de {MONTHS_ES[value.month - 1]} de {value.year}, "
        f"{value.strftime('%H:%M')}"
    )


def _format_stops(transfers: int | None) -> str:
    if transfers is None:
        return "No informadas"
    if transfers == 0:
        return "Vuelo directo"
    if transfers == 1:
        return "1 escala"
    return f"{transfers} escalas"


class WhatsAppNotifier:
    """Construye y envía alertas de vuelo por WhatsApp Cloud API."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Inicializa el notificador y difiere la creación del cliente HTTP."""
        self._settings = settings
        self._owns_client = client is None
        self._client = client

    @property
    def is_configured(self) -> bool:
        """Indica si la configuración permite intentar un envío."""
        return self.configuration_error is None

    @property
    def configuration_error(self) -> str | None:
        """Describe el primer dato faltante para enviar mensajes."""
        if not self._settings.whatsapp_phone_number_id:
            return "WHATSAPP_PHONE_NUMBER_ID no está configurado."
        if not self._settings.whatsapp_token:
            return "WHATSAPP_ACCESS_TOKEN no está configurado."
        if not self._settings.notification_phone:
            return "NOTIFICATION_PHONE no está configurado."
        return None

    @property
    def messages_url(self) -> str:
        """Construye la URL del endpoint de mensajes de Meta."""
        phone_number_id = self._settings.whatsapp_phone_number_id
        return f"{API_BASE_URL}/{API_VERSION}/{phone_number_id}/messages"

    def build_booking_url(self, deal: FlightDeal) -> str:
        """Genera un enlace seguro de Aviasales con el marker configurado."""
        base_url = "https://www.aviasales.com"
        raw_link = (deal.offer.link or "").strip()
        if raw_link.startswith("www."):
            raw_link = f"https://{raw_link}"

        candidate = base_url + "/"
        if raw_link.startswith("/"):
            candidate = urljoin(base_url, raw_link)
        elif raw_link:
            parts = urlsplit(raw_link)
            host = parts.netloc.lower()
            allowed_host = host == "aviasales.com" or host.endswith(".aviasales.com")
            if parts.scheme in {"http", "https"} and allowed_host:
                candidate = raw_link
            elif not parts.scheme and not parts.netloc and parts.path:
                candidate = urljoin(base_url + "/", raw_link)

        parts = urlsplit(candidate)
        hostname = (parts.hostname or "").lower()
        if hostname != "aviasales.com" and not hostname.endswith(".aviasales.com"):
            parts = urlsplit(base_url + "/")

        query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key != "marker"
        ]
        marker = self._settings.travelpayouts_marker.strip()
        if marker:
            query.append(("marker", marker))

        return urlunsplit(
            (
                "https",
                parts.netloc or "www.aviasales.com",
                parts.path or "/",
                urlencode(query, doseq=True),
                parts.fragment,
            )
        )

    def build_message_text(self, deal: FlightDeal) -> str:
        """Construye el texto de la alerta según el tipo de viaje."""
        destination = deal.destination_info
        airline = deal.offer.airline or "No informada"
        if deal.offer.flight_number:
            airline = f"{airline} · {deal.offer.flight_number}"
        return_at = deal.offer.return_at
        trip_type = "Ida y vuelta" if return_at is not None else "Solo ida"
        booking_url = self.build_booking_url(deal)

        lines = [
            "*🚨 Súper Promoción de Vuelo*",
            "",
            (
                f"*Ruta:* {deal.origin_city} ({deal.offer.origin}) → "
                f"{destination.city}, {destination.country_name} "
                f"({deal.offer.destination})"
            ),
            f"*Precio:* USD {_format_price(deal.offer.price)}",
            f"*Tipo:* {trip_type}",
            f"*Fecha de ida:* {_format_date(deal.offer.departure_at)}",
        ]
        if return_at is not None:
            lines.append(f"*Fecha de regreso:* {_format_date(return_at)}")

        lines.extend(
            [
                f"*Aerolínea:* {airline}",
                f"*Escalas:* {_format_stops(deal.offer.transfers)}",
                f"*Región:* {deal.region_label}",
                "",
                f"*Reserva:* {booking_url}",
                "",
                (
                    "_Precio detectado por persona. La disponibilidad y la "
                    "tarifa pueden cambiar antes de completar la compra._"
                ),
            ]
        )
        return "\n".join(lines)

    def build_payload(self, deal: FlightDeal) -> dict[str, object]:
        """Construye el payload de texto requerido por WhatsApp Cloud API."""
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self._settings.notification_phone,
            "type": "text",
            "text": {
                "preview_url": True,
                "body": self.build_message_text(deal),
            },
        }

    async def send_deal_alert(self, deal: FlightDeal) -> bool:
        """Envía una alerta y devuelve si Meta aceptó el mensaje."""
        configuration_error = self.configuration_error
        if configuration_error is not None:
            logger.error(
                "No se puede enviar el mensaje de WhatsApp: %s",
                configuration_error,
            )
            return False
        if self._settings.only_round_trip and deal.offer.return_at is None:
            logger.error(
                "No se envía la alerta de %s → %s porque no tiene fecha de regreso.",
                deal.offer.origin,
                deal.offer.destination,
            )
            return False

        payload = self.build_payload(deal)
        text = payload["text"]
        body = text["body"] if isinstance(text, Mapping) else ""
        # Meta rechaza textos libres que superen el límite del canal.
        if not isinstance(body, str) or len(body) > MAX_MESSAGE_LENGTH:
            logger.error(
                "El mensaje de WhatsApp para %s → %s excede el límite de "
                "%d caracteres.",
                deal.offer.origin,
                deal.offer.destination,
                MAX_MESSAGE_LENGTH,
            )
            return False

        headers = {
            "Authorization": f"Bearer {self._settings.whatsapp_token}",
            "Content-Type": "application/json",
        }

        try:
            response = await self._get_client().post(
                self.messages_url,
                headers=headers,
                json=payload,
            )
        except httpx.TimeoutException:
            logger.error(
                "Tiempo de espera agotado enviando WhatsApp para %s → %s.",
                deal.offer.origin,
                deal.offer.destination,
            )
            return False
        except httpx.HTTPError as exc:
            logger.error(
                "Falló la conexión con WhatsApp para %s → %s: %s",
                deal.offer.origin,
                deal.offer.destination,
                exc,
            )
            return False
        except Exception:
            logger.exception(
                "Error inesperado enviando la alerta de WhatsApp de %s → %s.",
                deal.offer.origin,
                deal.offer.destination,
            )
            return False

        # Solo se registra el detalle estructurado; nunca el token de acceso.
        if not response.is_success:
            logger.error(
                "WhatsApp rechazó la alerta de %s → %s con HTTP %d: %s",
                deal.offer.origin,
                deal.offer.destination,
                response.status_code,
                self._extract_error(response),
            )
            return False

        message_id = self._extract_message_id(response)
        logger.info(
            "Alerta de WhatsApp enviada a %s → %s por USD %s (mensaje %s).",
            deal.offer.origin,
            deal.offer.destination,
            _format_price(deal.offer.price),
            message_id or "sin id",
        )
        return True

    async def close(self) -> None:
        """Cierra el cliente HTTP únicamente si pertenece al notificador."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._settings.whatsapp_timeout_seconds),
                headers={"User-Agent": "flight-alerts/1.2"},
            )
        return self._client

    @staticmethod
    def _extract_error(response: httpx.Response) -> str:
        try:
            payload: object = response.json()
        except ValueError:
            return "respuesta sin JSON"
        if not isinstance(payload, Mapping):
            return "respuesta JSON inválida"
        error = payload.get("error")
        if not isinstance(error, Mapping):
            return "error desconocido"
        return (
            f"{error.get('message', 'Error desconocido')} "
            f"(code={error.get('code')}, "
            f"subcode={error.get('error_subcode')}, "
            f"trace={error.get('fbtrace_id')})"
        )

    @staticmethod
    def _extract_message_id(response: httpx.Response) -> str | None:
        try:
            payload: object = response.json()
        except ValueError:
            return None
        if not isinstance(payload, Mapping):
            return None
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return None
        first = messages[0]
        if not isinstance(first, Mapping):
            return None
        message_id = first.get("id")
        return str(message_id) if message_id else None
