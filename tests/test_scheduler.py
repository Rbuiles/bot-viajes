from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.database.db import Database
from app.scheduler.jobs import FlightAlertJob
from app.services.travelpayouts import FlightDeal, FlightOffer
from tests.helpers import build_offer, build_settings


class FakeClient:
    def __init__(self, offers: list[FlightOffer]) -> None:
        self._offers = offers

    async def fetch_all_offers(self) -> list[FlightOffer]:
        return list(self._offers)


class FakeNotifier:
    def __init__(self) -> None:
        self.configuration_error: str | None = None
        self.sent_deals: list[FlightDeal] = []

    async def send_deal_alert(self, deal: FlightDeal) -> bool:
        self.sent_deals.append(deal)
        return True


def test_job_sends_once_and_skips_recent_duplicates(tmp_path: Path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'scheduler.db'}"
    settings = build_settings(database_url=database_url)
    offer = build_offer(destination="MAD", price=450)
    blocked_offer = build_offer(destination="MIA", price=100)
    fake_client = FakeClient([offer, blocked_offer])
    fake_notifier = FakeNotifier()

    async def scenario() -> None:
        database = Database(database_url)
        await database.init()
        try:
            job = FlightAlertJob(
                settings=settings,
                client=fake_client,
                database=database,
                notifier=fake_notifier,
            )

            await job.run()
            first_status: Any = job.status
            assert first_status.success
            assert first_status.alerts_sent == 1
            assert first_status.duplicates_skipped == 0
            assert len(fake_notifier.sent_deals) == 1

            recent = await database.recently_notified_ids(
                [fake_notifier.sent_deals[0].deal_id],
                within=timedelta(hours=48),
            )
            assert len(recent) == 1

            await job.run()
            second_status: Any = job.status
            assert second_status.success
            assert second_status.alerts_sent == 0
            assert second_status.duplicates_skipped == 1
            assert len(fake_notifier.sent_deals) == 1
        finally:
            await database.dispose()

    asyncio.run(scenario())
