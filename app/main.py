"""Punto de entrada de la API Flight Alerts Colombia."""

from __future__ import annotations

from fastapi import FastAPI

from app.core.config import APP_NAME, APP_VERSION
from app.core.lifespan import lifespan
from app.routers.system import router

app = FastAPI(
    title=APP_NAME,
    description=(
        "Monitoreo de Súper Promociones de vuelos desde Medellín y Pereira "
        "con alertas por WhatsApp."
    ),
    version=APP_VERSION,
    lifespan=lifespan,
)
app.include_router(router)
