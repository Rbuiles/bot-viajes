from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.database.db import Database


def test_recent_alerts_use_48_hour_lookback(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'alerts.db'}"

    async def scenario() -> None:
        database = Database(database_url)
        await database.init()
        try:
            now = datetime.now(UTC)
            await database.record_alert(
                deal_id="recent-deal",
                origin="MDE",
                destination="MAD",
                country_code="ES",
                region="europe",
                price_usd=450,
                departure_at=now + timedelta(days=30),
                sent_at=now - timedelta(hours=1),
            )
            await database.record_alert(
                deal_id="old-deal",
                origin="MDE",
                destination="LIS",
                country_code="PT",
                region="europe",
                price_usd=400,
                departure_at=now + timedelta(days=30),
                sent_at=now - timedelta(hours=49),
            )

            recent = await database.recently_notified_ids(
                ["recent-deal", "old-deal", "unknown"],
                within=timedelta(hours=48),
            )
            assert recent == {"recent-deal"}

            recent_within_fifty = await database.recently_notified_ids(
                ["old-deal"],
                within=timedelta(hours=50),
            )
            assert recent_within_fifty == {"old-deal"}
        finally:
            await database.dispose()

    asyncio.run(scenario())


def test_purge_old_alerts(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'purge.db'}"

    async def scenario() -> None:
        database = Database(database_url)
        await database.init()
        try:
            now = datetime.now(UTC)
            await database.record_alert(
                deal_id="old",
                origin="MDE",
                destination="MAD",
                country_code="ES",
                region="europe",
                price_usd=100,
                departure_at=None,
                sent_at=now - timedelta(days=40),
            )
            await database.purge_old_alerts(30)
            recent = await database.recently_notified_ids(
                ["old"],
                within=timedelta(days=60),
            )
            assert recent == set()
        finally:
            await database.dispose()

    asyncio.run(scenario())
