from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.travelpayouts import (
    TravelpayoutsAPIError,
    TravelpayoutsClient,
)
from tests.helpers import build_settings


def _payload(departure_at: datetime) -> dict[str, Any]:
    return {
        "success": True,
        "currency": "usd",
        "data": [
            {
                "origin": "MDE",
                "destination": "MAD",
                "origin_airport": "MDE",
                "destination_airport": "MAD",
                "price": 430,
                "airline": "AV",
                "flight_number": "26",
                "departure_at": departure_at.isoformat(),
                "return_at": (departure_at + timedelta(days=15)).isoformat(),
                "transfers": 0,
                "link": "/search/test",
            },
            {
                "origin": "MDE",
                "destination": "LIS",
                "origin_airport": "MDE",
                "destination_airport": "LIS",
                "price": 250,
                "airline": "TP",
                "flight_number": "42",
                "departure_at": departure_at.isoformat(),
                "transfers": 1,
                "link": "/search/one-way",
            },
            {
                "origin": "MDE",
                "destination": "BCN",
                "origin_airport": "MDE",
                "destination_airport": "BCN",
                "price": 480,
                "airline": "IB",
                "flight_number": "77",
                "departure_at": departure_at.isoformat(),
                "return_date": (departure_at + timedelta(days=20)).isoformat(),
                "transfers": 1,
                "link": "/search/return-date",
            },
            {
                "origin": "MDE",
                "destination": "INVALID",
                "price": 10,
            },
        ],
    }


def test_fetch_offers_uses_round_trip_only_and_parses_response() -> None:
    settings = build_settings(only_round_trip=True)
    departure_at = datetime.now(UTC) + timedelta(days=30)
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json=_payload(departure_at))

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = TravelpayoutsClient(settings, client=http_client)
            offers = await client.fetch_offers_for_origin("mde")
            await client.close()

        assert len(offers) == 2
        assert {offer.destination for offer in offers} == {"MAD", "BCN"}
        assert all(offer.return_at is not None for offer in offers)
        assert offers[0].destination == "MAD"
        assert offers[0].price == 430
        assert offers[0].departure_at == departure_at
        assert offers[0].return_at == departure_at + timedelta(days=15)
        assert offers[1].return_at == departure_at + timedelta(days=20)

    asyncio.run(scenario())

    assert len(captured_requests) == 1
    assert captured_requests[0].url.params["one_way"] == "false"
    for request in captured_requests:
        assert request.headers["X-Access-Token"] == "test-token"
        assert request.url.params["origin"] == "MDE"
        assert request.url.params["currency"] == "usd"
        assert request.url.params["direct"] == "false"
        assert request.url.params["unique"] == "false"


def test_fetch_offers_accepts_round_trip_and_one_way_when_disabled() -> None:
    settings = build_settings(only_round_trip=False)
    departure_at = datetime.now(UTC) + timedelta(days=30)
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json=_payload(departure_at))

    async def scenario() -> list[Any]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = TravelpayoutsClient(settings, client=http_client)
            try:
                return await client.fetch_offers_for_origin("MDE")
            finally:
                await client.close()

    offers = asyncio.run(scenario())

    assert [request.url.params["one_way"] for request in captured_requests] == [
        "false",
        "true",
    ]
    assert {offer.destination for offer in offers} == {"MAD", "LIS", "BCN"}
    one_way_offer = next(offer for offer in offers if offer.destination == "LIS")
    assert one_way_offer.return_at is None


def test_client_retries_transient_errors() -> None:
    settings = build_settings(
        travelpayouts_max_retries=2,
        only_round_trip=True,
    )
    departure_at = datetime.now(UTC) + timedelta(days=30)
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        del request
        attempts += 1
        if attempts == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=_payload(departure_at))

    async def scenario() -> list[Any]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = TravelpayoutsClient(settings, client=http_client)
            return await client.fetch_offers_for_origin("MDE")

    with patch(
        "app.services.travelpayouts.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep_mock:
        offers = asyncio.run(scenario())

    assert attempts == 2
    assert len(offers) == 2
    sleep_mock.assert_awaited_once()


def test_invalid_token_raises_api_error() -> None:
    settings = build_settings(only_round_trip=True)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401)

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = TravelpayoutsClient(settings, client=http_client)
            with pytest.raises(TravelpayoutsAPIError) as exc_info:
                await client.fetch_offers_for_origin("MDE")

        assert exc_info.value.status_code == 401

    asyncio.run(scenario())


def test_non_success_payload_raises_api_error() -> None:
    settings = build_settings(only_round_trip=True)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={"success": False, "error": "invalid token", "data": []},
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            client = TravelpayoutsClient(settings, client=http_client)
            with pytest.raises(TravelpayoutsAPIError):
                await client.fetch_offers_for_origin("MDE")

    asyncio.run(scenario())
