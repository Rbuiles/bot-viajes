from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME: Final[str] = "Flight Alerts Colombia"
APP_VERSION: Final[str] = "1.2.0"
BASE_DIR: Final[Path] = Path(__file__).resolve().parents[2]
SUPPORTED_ORIGINS: Final[tuple[str, ...]] = ("MDE", "PEI")
ORIGIN_CITIES: Final[dict[str, str]] = {
    "MDE": "Medellín",
    "PEI": "Pereira",
}
PHONE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\+[1-9]\d{6,14}$")
PHONE_NUMBER_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\d+$")

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Configuración global cargada desde variables de entorno y `.env`."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,
    )

    travelpayouts_token: SecretStr = SecretStr("")
    travelpayouts_marker: str = ""
    travelpayouts_base_url: str = "https://api.travelpayouts.com"
    travelpayouts_market: str = "us"
    travelpayouts_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    travelpayouts_limit: int = Field(default=1000, ge=1, le=1000)
    travelpayouts_max_pages: int = Field(default=1, ge=1, le=10)
    travelpayouts_max_retries: int = Field(default=3, ge=1, le=6)
    only_round_trip: bool = False

    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: SecretStr = SecretStr("")
    notification_phone: str = ""
    whatsapp_timeout_seconds: float = Field(default=20.0, gt=0, le=120)

    check_interval_minutes: int = Field(default=5, ge=1, le=1440)
    deduplication_hours: int = Field(default=48, ge=1, le=8760)
    notification_retention_days: int = Field(default=30, ge=1, le=3650)
    database_url: str = (
        f"sqlite+aiosqlite:///{(BASE_DIR / 'data' / 'flight_alerts.db').as_posix()}"
    )

    threshold_south_america_near_usd: float = Field(default=200.0, gt=0)
    threshold_south_america_far_usd: float = Field(default=300.0, gt=0)
    threshold_central_america_caribbean_usd: float = Field(default=220.0, gt=0)
    threshold_europe_usd: float = Field(default=550.0, gt=0)
    threshold_asia_usd: float = Field(default=950.0, gt=0)

    log_level: LogLevel = "INFO"

    @field_validator(
        "travelpayouts_marker",
        "whatsapp_phone_number_id",
        mode="before",
    )
    @classmethod
    def strip_text_fields(cls, value: object) -> object:
        """Elimina espacios accidentales de campos de texto."""
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("notification_phone", mode="before")
    @classmethod
    def normalize_phone(cls, value: object) -> object:
        """Normaliza el destinatario al formato E.164."""
        if isinstance(value, str):
            normalized = re.sub(r"[\s\-().]", "", value.strip())
            if normalized and not normalized.startswith("+"):
                normalized = f"+{normalized}"
            return normalized
        return value

    @field_validator("whatsapp_phone_number_id")
    @classmethod
    def validate_phone_number_id(cls, value: str) -> str:
        """Valida que el identificador de Meta contenga solo dígitos."""
        if value and not PHONE_NUMBER_ID_PATTERN.fullmatch(value):
            raise ValueError("WHATSAPP_PHONE_NUMBER_ID debe contener solo dígitos.")
        return value

    @field_validator("notification_phone")
    @classmethod
    def validate_notification_phone(cls, value: str) -> str:
        """Valida el número destinatario en formato E.164."""
        if value and not PHONE_PATTERN.fullmatch(value):
            raise ValueError(
                "NOTIFICATION_PHONE debe estar en formato E.164, "
                "por ejemplo +573013201103."
            )
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        """Normaliza el nivel de logs a mayúsculas."""
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @property
    def api_token(self) -> str:
        """Devuelve el token de Travelpayouts sin exponer el contenedor."""
        return self.travelpayouts_token.get_secret_value().strip()

    @property
    def whatsapp_token(self) -> str:
        """Devuelve el token de WhatsApp sin exponer el contenedor."""
        return self.whatsapp_access_token.get_secret_value().strip()

    @property
    def travelpayouts_configured(self) -> bool:
        """Indica si existe un token para consultar Travelpayouts."""
        return bool(self.api_token)

    @property
    def whatsapp_configured(self) -> bool:
        """Indica si WhatsApp tiene todos los datos necesarios para enviar."""
        return bool(
            self.whatsapp_phone_number_id
            and self.whatsapp_token
            and self.notification_phone
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la configuración global cacheada."""
    return Settings()
