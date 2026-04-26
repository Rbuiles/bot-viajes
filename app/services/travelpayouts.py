"""Consulta de vuelos y selección de Súper Promociones."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from typing import Any, Final, TypedDict, cast

import httpx
from airportsdata import load as load_airports
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from app.core.config import ORIGIN_CITIES, SUPPORTED_ORIGINS, Settings

logger = logging.getLogger(__name__)

PRICES_PATH: Final[str] = "/aviasales/v3/prices_for_dates"
RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})


class FlightOffer(BaseModel):
    """Oferta normalizada recibida desde Travelpayouts."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    origin: str
    destination: str
    destination_airport: str | None = None
    price: float = Field(gt=0)
    airline: str | None = None
    flight_number: str | None = None
    departure_at: datetime | None = None
    return_at: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("return_at", "return_date"),
    )
    link: str | None = None
    transfers: int | None = None
    return_transfers: int | None = None
    duration: int | None = None
    gate: str | None = None

    @field_validator("origin", "destination", mode="before")
    @classmethod
    def normalize_iata(cls, value: object) -> str:
        """Normaliza y valida los códigos IATA de origen y destino."""
        normalized = str(value).strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("Código IATA inválido")
        return normalized

    @field_validator("destination_airport", mode="before")
    @classmethod
    def normalize_optional_iata(cls, value: object) -> object:
        """Normaliza el aeropuerto alternativo cuando está presente."""
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip().upper()
            if not normalized:
                return None
            if len(normalized) != 3 or not normalized.isalpha():
                raise ValueError("Código IATA de aeropuerto inválido")
            return normalized
        return value

    @field_validator(
        "airline",
        "flight_number",
        "link",
        "gate",
        mode="before",
    )
    @classmethod
    def empty_text_to_none(cls, value: object) -> object:
        """Convierte cadenas vacías en `None`."""
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class TravelpayoutsAPIError(RuntimeError):
    """Error controlado al comunicarse con la API de Travelpayouts."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        """Inicializa el error y conserva el código HTTP si existe."""
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class OfferPage:
    """Página cruda de ofertas y cantidad recibida antes de filtrar."""

    offers: list[FlightOffer]
    raw_count: int


class TravelpayoutsClient:
    """Cliente asíncrono para consultar ofertas según el tipo configurado."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Crea el cliente HTTP reutilizable o adopta uno inyectado."""
        self._settings = settings
        self._owns_client = client is None
        self._endpoint = f"{settings.travelpayouts_base_url.rstrip('/')}{PRICES_PATH}"
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.travelpayouts_timeout_seconds),
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
            ),
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": "flight-alerts/1.0",
            },
        )

    async def fetch_all_offers(
        self,
        origins: Sequence[str] = SUPPORTED_ORIGINS,
    ) -> list[FlightOffer]:
        """Consulta todos los orígenes y tolera fallos individuales."""
        if not self._settings.travelpayouts_configured:
            logger.error(
                "TRAVELPAYOUTS_TOKEN no está configurado; se omite la consulta."
            )
            return []

        normalized_origins = tuple(
            dict.fromkeys(origin.strip().upper() for origin in origins)
        )
        results = await asyncio.gather(
            *(self.fetch_offers_for_origin(origin) for origin in normalized_origins),
            return_exceptions=True,
        )

        offers: list[FlightOffer] = []
        for origin, result in zip(normalized_origins, results, strict=True):
            if isinstance(result, BaseException):
                logger.error(
                    "No se pudieron obtener ofertas para %s: %s",
                    origin,
                    result,
                )
                continue
            logger.info(
                "Travelpayouts devolvió %d ofertas para %s.",
                len(result),
                origin,
            )
            offers.extend(result)

        return offers

    async def fetch_offers_for_origin(self, origin: str) -> list[FlightOffer]:
        """Obtiene y deduplica ofertas para un origen según el modo activo."""
        normalized_origin = origin.strip().upper()
        unique_offers: dict[str, FlightOffer] = {}
        page_size = self._settings.travelpayouts_limit

        # Travelpayouts separa ambos tipos; se pagina cada uno por separado.
        modes: tuple[bool, ...] = (
            (False,) if self._settings.only_round_trip else (False, True)
        )
        for one_way in modes:
            for page in range(1, self._settings.travelpayouts_max_pages + 1):
                result = await self._request_page(
                    normalized_origin,
                    page,
                    one_way=one_way,
                )
                for offer in result.offers:
                    unique_offers.setdefault(self._offer_key(offer), offer)

                if result.raw_count < page_size:
                    break

        return list(unique_offers.values())

    async def _request_page(
        self,
        origin: str,
        page: int,
        *,
        one_way: bool,
    ) -> OfferPage:
        params: dict[str, str | int] = {
            "origin": origin,
            "currency": "usd",
            "sorting": "price",
            "unique": "false",
            "direct": "false",
            "one_way": "true" if one_way else "false",
            "limit": self._settings.travelpayouts_limit,
            "page": page,
        }
        if self._settings.travelpayouts_market:
            params["market"] = self._settings.travelpayouts_market

        headers = {"X-Access-Token": self._settings.api_token}
        max_attempts = self._settings.travelpayouts_max_retries
        last_transport_error: httpx.TransportError | None = None

        # Reintenta errores transitorios con backoff y respeta Retry-After.
        for attempt in range(1, max_attempts + 1):
            try:
                response = await self._client.get(
                    self._endpoint,
                    params=params,
                    headers=headers,
                )
            except httpx.TransportError as exc:
                last_transport_error = exc
                if attempt >= max_attempts:
                    break
                delay = self._backoff_delay(attempt)
                logger.warning(
                    "Error de red consultando Travelpayouts para %s "
                    "(intento %d/%d). Reintento en %.1f s.",
                    origin,
                    attempt,
                    max_attempts,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt >= max_attempts:
                    raise TravelpayoutsAPIError(
                        "Travelpayouts respondió con un error transitorio "
                        f"HTTP {response.status_code}.",
                        status_code=response.status_code,
                    )
                retry_after = self._retry_after_seconds(response)
                delay = retry_after or self._backoff_delay(attempt)
                logger.warning(
                    "Travelpayouts respondió HTTP %d para %s "
                    "(intento %d/%d). Reintento en %.1f s.",
                    response.status_code,
                    origin,
                    attempt,
                    max_attempts,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            if response.status_code in {401, 403}:
                raise TravelpayoutsAPIError(
                    "El token de Travelpayouts es inválido o no tiene permisos.",
                    status_code=response.status_code,
                )

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise TravelpayoutsAPIError(
                    f"Travelpayouts respondió HTTP {response.status_code}.",
                    status_code=response.status_code,
                ) from exc

            try:
                payload: Any = response.json()
            except ValueError as exc:
                raise TravelpayoutsAPIError(
                    "Travelpayouts devolvió una respuesta que no es JSON válido."
                ) from exc

            return self._parse_offers(payload)

        raise TravelpayoutsAPIError(
            "No fue posible comunicarse con Travelpayouts después de "
            f"{max_attempts} intentos."
        ) from last_transport_error

    @staticmethod
    def _offer_key(offer: FlightOffer) -> str:
        departure = (
            offer.departure_at.isoformat()
            if offer.departure_at is not None
            else "sin-fecha"
        )
        return_date = (
            offer.return_at.isoformat() if offer.return_at is not None else "one_way"
        )
        return (
            f"{offer.origin}_{offer.destination}_{departure}_{return_date}_"
            f"{format(offer.price, 'g')}_{offer.airline or ''}"
        )

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float | None:
        raw_value = response.headers.get("Retry-After")
        if raw_value is None:
            return None
        try:
            seconds = float(raw_value)
        except ValueError:
            return None
        return min(max(seconds, 0.0), 30.0)

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        return min(8.0, 2.0 ** (attempt - 1)) + random.uniform(0.0, 0.35)

    def _parse_offers(self, payload: object) -> OfferPage:
        if not isinstance(payload, Mapping):
            raise TravelpayoutsAPIError(
                "Travelpayouts devolvió un formato de respuesta inesperado."
            )

        if payload.get("success") is not True:
            error = payload.get("error") or "respuesta no exitosa"
            raise TravelpayoutsAPIError(f"Error de Travelpayouts: {error}")

        data = payload.get("data")
        if data is None:
            return OfferPage(offers=[], raw_count=0)
        if not isinstance(data, list):
            raise TravelpayoutsAPIError(
                "Travelpayouts devolvió un campo 'data' con formato inválido."
            )

        offers: list[FlightOffer] = []
        invalid_count = 0
        missing_return_count = 0
        for item in data:
            if not isinstance(item, Mapping):
                invalid_count += 1
                continue
            try:
                offer = FlightOffer.model_validate(item)
            except ValidationError:
                invalid_count += 1
                continue
            # En modo estricto solo se aceptan ofertas con fecha de regreso.
            if self._settings.only_round_trip and offer.return_at is None:
                missing_return_count += 1
                continue
            offers.append(offer)

        if invalid_count:
            logger.debug(
                "Se descartaron %d ofertas con datos inválidos de Travelpayouts.",
                invalid_count,
            )
        if missing_return_count:
            logger.debug(
                "Se descartaron %d ofertas sin fecha de regreso.",
                missing_return_count,
            )

        return OfferPage(offers=offers, raw_count=len(data))

    async def close(self) -> None:
        """Cierra el cliente HTTP solo si fue creado internamente."""
        if self._owns_client:
            await self._client.aclose()


# ---------------------------------------------------------------------------
# Dominio de vuelos: resolución de destinos y selección de Súper Promociones
# ---------------------------------------------------------------------------

IATA_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{3}$")


class Region(StrEnum):
    """Regiones comerciales usadas para aplicar umbrales de precio."""

    SOUTH_AMERICA_NEAR = "south_america_near"
    SOUTH_AMERICA_FAR = "south_america_far"
    CENTRAL_AMERICA_CARIBBEAN = "central_america_caribbean"
    EUROPE = "europe"
    ASIA = "asia"


REGION_LABELS: Final[dict[Region, str]] = {
    Region.SOUTH_AMERICA_NEAR: "Sudamérica cercana",
    Region.SOUTH_AMERICA_FAR: "Sudamérica lejana",
    Region.CENTRAL_AMERICA_CARIBBEAN: "Centroamérica, Caribe y México",
    Region.EUROPE: "Europa",
    Region.ASIA: "Asia",
}

REGION_COUNTRIES: Final[dict[Region, frozenset[str]]] = {
    Region.SOUTH_AMERICA_NEAR: frozenset({"EC", "PE", "VE"}),
    Region.SOUTH_AMERICA_FAR: frozenset({"AR", "BO", "BR", "CL", "PY", "UY"}),
    Region.CENTRAL_AMERICA_CARIBBEAN: frozenset(
        {"CR", "PA", "GT", "HN", "SV", "NI", "DO", "JM", "CW", "AW", "MX"}
    ),
    Region.EUROPE: frozenset(
        {
            "ES",
            "FR",
            "IT",
            "DE",
            "PT",
            "NL",
            "BE",
            "CH",
            "AT",
            "GR",
            "SE",
            "NO",
            "DK",
            "FI",
            "PL",
            "CZ",
            "HU",
            "HR",
            "RO",
            "BG",
            "GB",
            "TR",
        }
    ),
    Region.ASIA: frozenset({"TH", "SG", "PH", "MY", "ID", "MV"}),
}

BLOCKED_COUNTRIES: Final[frozenset[str]] = frozenset(
    {
        "AE",
        "QA",
        "SA",
        "IL",
        "JO",
        "LB",
        "OM",
        "KW",
        "BH",
        "EG",
        "KR",
        "KP",
        "US",
        "CA",
        "CN",
        "RU",
        "AU",
        "NZ",
        "IN",
    }
)

COUNTRY_TO_REGION: Final[dict[str, Region]] = {
    country_code: region
    for region, country_codes in REGION_COUNTRIES.items()
    for country_code in country_codes
}

COUNTRY_NAMES_ES: Final[dict[str, str]] = {
    "AR": "Argentina",
    "BO": "Bolivia",
    "BR": "Brasil",
    "CL": "Chile",
    "EC": "Ecuador",
    "PE": "Perú",
    "PY": "Paraguay",
    "UY": "Uruguay",
    "VE": "Venezuela",
    "CR": "Costa Rica",
    "PA": "Panamá",
    "GT": "Guatemala",
    "HN": "Honduras",
    "SV": "El Salvador",
    "NI": "Nicaragua",
    "DO": "República Dominicana",
    "JM": "Jamaica",
    "CW": "Curazao",
    "AW": "Aruba",
    "MX": "México",
    "ES": "España",
    "FR": "Francia",
    "IT": "Italia",
    "DE": "Alemania",
    "PT": "Portugal",
    "NL": "Países Bajos",
    "BE": "Bélgica",
    "CH": "Suiza",
    "AT": "Austria",
    "GR": "Grecia",
    "SE": "Suecia",
    "NO": "Noruega",
    "DK": "Dinamarca",
    "FI": "Finlandia",
    "PL": "Polonia",
    "CZ": "Chequia",
    "HU": "Hungría",
    "HR": "Croacia",
    "RO": "Rumania",
    "BG": "Bulgaria",
    "GB": "Reino Unido",
    "TR": "Turquía",
    "TH": "Tailandia",
    "SG": "Singapur",
    "PH": "Filipinas",
    "MY": "Malasia",
    "ID": "Indonesia",
    "MV": "Maldivas",
}

CITY_CODE_FALLBACKS: Final[dict[str, tuple[str, str]]] = {
    "BUE": ("AR", "Buenos Aires"),
    "SAO": ("BR", "São Paulo"),
    "RIO": ("BR", "Río de Janeiro"),
    "LON": ("GB", "Londres"),
    "PAR": ("FR", "París"),
    "ROM": ("IT", "Roma"),
    "MIL": ("IT", "Milán"),
    "STO": ("SE", "Estocolmo"),
    "BUH": ("RO", "Bucarest"),
    "ANK": ("TR", "Ankara"),
    "JKT": ("ID", "Yakarta"),
}


class AirportRecord(TypedDict, total=False):
    """Subconjunto de datos de aeropuerto utilizado por la aplicación."""

    iata: str
    name: str
    city: str
    country: str


@lru_cache(maxsize=1)
def _airport_index() -> Mapping[str, AirportRecord]:
    return cast(Mapping[str, AirportRecord], load_airports("IATA"))


@dataclass(frozen=True, slots=True)
class DestinationInfo:
    """Datos normalizados del destino de una oferta."""

    iata: str
    city: str
    country_code: str
    country_name: str
    region: Region


@dataclass(frozen=True, slots=True)
class FlightDeal:
    """Oferta que cumple las reglas de negocio de Súper Promoción."""

    offer: FlightOffer
    origin_city: str
    destination_info: DestinationInfo
    threshold_usd: float

    @property
    def deal_id(self) -> str:
        """Identificador estable usado para la deduplicación."""
        return build_deal_id(self.offer)

    @property
    def region_label(self) -> str:
        """Etiqueta en español de la región del destino."""
        return REGION_LABELS[self.destination_info.region]


def resolve_destination(iata: str) -> DestinationInfo | None:
    """Resuelve un código IATA a ciudad, país y región permitidos."""
    normalized = iata.strip().upper()
    if not IATA_PATTERN.fullmatch(normalized):
        return None

    fallback = CITY_CODE_FALLBACKS.get(normalized)
    if fallback is not None:
        country_code, city = fallback
    else:
        record = _airport_index().get(normalized)
        if record is None:
            return None
        country_code = str(record.get("country", "")).upper()
        city = str(record.get("city") or record.get("name") or normalized).strip()

    if not country_code or country_code in BLOCKED_COUNTRIES:
        return None

    region = COUNTRY_TO_REGION.get(country_code)
    if region is None:
        return None

    return DestinationInfo(
        iata=normalized,
        city=city or normalized,
        country_code=country_code,
        country_name=COUNTRY_NAMES_ES.get(country_code, country_code),
        region=region,
    )


def threshold_for_region(region: Region, settings: Settings) -> float:
    """Devuelve el umbral configurado para una región."""
    thresholds: dict[Region, float] = {
        Region.SOUTH_AMERICA_NEAR: settings.threshold_south_america_near_usd,
        Region.SOUTH_AMERICA_FAR: settings.threshold_south_america_far_usd,
        Region.CENTRAL_AMERICA_CARIBBEAN: (
            settings.threshold_central_america_caribbean_usd
        ),
        Region.EUROPE: settings.threshold_europe_usd,
        Region.ASIA: settings.threshold_asia_usd,
    }
    return thresholds[region]


def build_deal_id(offer: FlightOffer) -> str:
    """Construye el identificador e incluye un sufijo para vuelos de solo ida."""
    departure_at = (
        offer.departure_at.isoformat()
        if offer.departure_at is not None
        else "sin-fecha"
    )
    price = format(offer.price, "g")
    deal_id = f"{offer.origin}_{offer.destination}_{departure_at}_{price}"
    if offer.return_at is None:
        return f"{deal_id}_one_way"
    return deal_id


def _is_future_or_undated(departure_at: datetime | None, now: datetime) -> bool:
    if departure_at is None:
        return True
    if departure_at.tzinfo is None:
        departure_at = departure_at.replace(tzinfo=UTC)
    return departure_at >= now


def select_super_deals(
    offers: list[FlightOffer],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> list[FlightDeal]:
    """Filtra ofertas válidas que cumplen origen, país y umbral."""
    reference_now = now or datetime.now(UTC)
    selected: dict[str, FlightDeal] = {}

    for offer in offers:
        origin_city = ORIGIN_CITIES.get(offer.origin)
        if origin_city is None:
            continue

        # En modo estricto se descartan las ofertas sin fecha de regreso.
        if settings.only_round_trip and offer.return_at is None:
            continue

        if not _is_future_or_undated(offer.departure_at, reference_now):
            continue

        destination = resolve_destination(offer.destination)
        if destination is None and offer.destination_airport is not None:
            destination = resolve_destination(offer.destination_airport)
        if destination is None:
            continue

        threshold = threshold_for_region(destination.region, settings)
        if offer.price > threshold:
            continue

        deal = FlightDeal(
            offer=offer,
            origin_city=origin_city,
            destination_info=destination,
            threshold_usd=threshold,
        )
        selected.setdefault(deal.deal_id, deal)

    return sorted(selected.values(), key=lambda deal: deal.offer.price)
