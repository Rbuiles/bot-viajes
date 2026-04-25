from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, Float, Index, Integer, String, delete, event, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """Devuelve la fecha actual en UTC."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Base declarativa de los modelos ORM de la aplicación."""


class NotifiedDeal(Base):
    """Registro de una oferta ya notificada para evitar duplicados."""

    __tablename__ = "notified_deals"
    __table_args__ = (
        Index("ix_notified_deals_deal_id_sent_at", "deal_id", "sent_at"),
        Index("ix_notified_deals_sent_at", "sent_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    deal_id: Mapped[str] = mapped_column(String(255), nullable=False)
    origin: Mapped[str] = mapped_column(String(3), nullable=False)
    destination: Mapped[str] = mapped_column(String(3), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    region: Mapped[str] = mapped_column(String(50), nullable=False)
    price_usd: Mapped[float] = mapped_column(Float, nullable=False)
    departure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )


class Database:
    """Gestiona la conexión SQLite y las operaciones de alertas."""

    def __init__(self, database_url: str) -> None:
        """Configura el motor asíncrono y los ajustes específicos de SQLite."""
        self._database_url = database_url
        self._url = make_url(database_url)
        connect_args: dict[str, Any] = {}
        if self._url.get_backend_name() == "sqlite":
            connect_args["timeout"] = 30

        self._engine = create_async_engine(
            database_url,
            echo=False,
            connect_args=connect_args,
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

        if self._url.get_backend_name() == "sqlite":
            event.listen(self._engine.sync_engine, "connect", self._set_sqlite_pragmas)

    @staticmethod
    def _set_sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
        del connection_record
        # WAL permite lecturas concurrentes y busy_timeout evita bloqueos cortos.
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    def _ensure_sqlite_directory(self) -> None:
        if self._url.get_backend_name() != "sqlite":
            return

        database_path = self._url.database
        if not database_path or database_path == ":memory:":
            return

        Path(database_path).expanduser().resolve().parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Entrega una sesión con rollback automático ante errores."""
        async with self._session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    async def init(self) -> None:
        """Crea el directorio y las tablas necesarias al arrancar."""
        self._ensure_sqlite_directory()
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def recently_notified_ids(
        self,
        deal_ids: Sequence[str],
        within: timedelta,
    ) -> set[str]:
        """Devuelve los identificadores notificados dentro de la ventana."""
        if not deal_ids:
            return set()

        cutoff = datetime.now(UTC) - within
        found: set[str] = set()
        unique_ids = tuple(dict.fromkeys(deal_ids))

        async with self.session() as session:
            # Consulta por bloques para respetar el límite de parámetros de SQLite.
            for start in range(0, len(unique_ids), 500):
                chunk = unique_ids[start : start + 500]
                result = await session.execute(
                    select(NotifiedDeal.deal_id)
                    .where(
                        NotifiedDeal.deal_id.in_(chunk),
                        NotifiedDeal.sent_at >= cutoff,
                    )
                    .distinct()
                )
                found.update(result.scalars().all())

        return found

    async def record_alert(
        self,
        *,
        deal_id: str,
        origin: str,
        destination: str,
        country_code: str,
        region: str,
        price_usd: float,
        departure_at: datetime | None,
        sent_at: datetime | None = None,
    ) -> None:
        """Registra una alerta enviada para el control de duplicados."""
        async with self.session() as session:
            session.add(
                NotifiedDeal(
                    deal_id=deal_id,
                    origin=origin,
                    destination=destination,
                    country_code=country_code,
                    region=region,
                    price_usd=price_usd,
                    departure_at=departure_at,
                    sent_at=sent_at or datetime.now(UTC),
                )
            )
            await session.commit()

    async def purge_old_alerts(self, retention_days: int) -> None:
        """Elimina registros de alertas fuera de la retención configurada."""
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        async with self.session() as session:
            await session.execute(
                delete(NotifiedDeal).where(NotifiedDeal.sent_at < cutoff)
            )
            await session.commit()

    async def dispose(self) -> None:
        """Cierra el motor de base de datos y libera sus conexiones."""
        await self._engine.dispose()
