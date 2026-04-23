from __future__ import annotations

import logging
from typing import Final

from app.core.config import Settings

LOG_FORMAT: Final[str] = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def configure_logging(settings: Settings) -> None:
    """Configura el logging global de la aplicación."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format=LOG_FORMAT,
        force=False,
    )
