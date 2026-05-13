from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlsplit

import httpx

from app.services.notifier import WhatsAppNotifier
from app.services.travelpayouts import (
    DestinationInfo,
    FlightDeal,
    Region,
    select_super_deals,
)
from tests.helpers import build_offer, build_settings


def _build_deal(link: str | None = None):
    settings = build_settings()
    offer = build_offer(
        destination="MAD",
        price=450,
        link=link or "/search/MDE1506MAD23061?search_date=10112026&marker=old",
    )
    return settings, select_super_deals([offer], settings)[0]


def test_booking_url_contains_single_current_marker() -> None:
    settings, deal = _build_deal()
    notifier = WhatsAppNotifier(settings)

    url = notifier.build_booking_url(deal)
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    marker_values = [value for key, value in parse_qsl(parts.query) if key == "marker"]

    assert parts.scheme == "https"
    assert parts.netloc == "www.aviasales.com"
    assert query["search_date"] == "10112026"
    assert marker_values == ["test-marker"]


def test_booking_url_rejects_external_hosts() -> None:
    settings, deal = _build_deal(link="https://example.com/phishing")
    notifier = WhatsAppNotifier(settings)

    url = notifier.build_booking_url(deal)
    protocol_relative_deal = replace(
        deal,
        offer=build_offer(
            destination="MAD",
            price=450,
            link="//evil.example/path",
        ),
    )
    protocol_relative_url = notifier.build_booking_url(protocol_relative_deal)

    assert url.startswith("https://www.aviasales.com/")
    assert "marker=test-marker" in url
    assert "example.com" not in url
    assert protocol_relative_url.startswith("https://www.aviasales.com/")
    assert "evil.example" not in protocol_relative_url


def test_message_and_payload_contain_whatsapp_offer_details() -> None:
    settings, deal = _build_deal()
    notifier = WhatsAppNotifier(settings)

    body = notifier.build_message_text(deal)
    payload = notifier.build_payload(deal)
    text = payload["text"]

    assert body.startswith("*🚨 Súper Promoción de Vuelo*\n\n*Ruta:*")
    assert "Medellín (MDE) → Madrid, España (MAD)" in body
    assert "*Precio:* USD 450" in body
    assert "*Aerolínea:* AV · 123" in body
    assert "marker=test-marker" in body
    assert payload["messaging_product"] == "whatsapp"
    assert payload["recipient_type"] == "individual"
    assert payload["to"] == "+573001234567"
    assert payload["type"] == "text"
    assert isinstance(text, dict)
    assert text["preview_url"] is True
    assert text["body"] == body


def test_message_shows_departure_and_return_dates() -> None:
    settings = build_settings()
    departure_at = datetime(2027, 5, 10, 8, 30, tzinfo=UTC)
    return_at = datetime(2027, 5, 24, 18, 45, tzinfo=UTC)
    offer = build_offer(
        destination="MAD",
        price=450,
        departure_at=departure_at,
        return_at=return_at,
    )
    deal = select_super_deals([offer], settings)[0]
    notifier = WhatsAppNotifier(settings)

    body = notifier.build_message_text(deal)

    assert "*Tipo:* Ida y vuelta" in body
    assert "*Fecha de ida:* 10 de mayo de 2027, 08:30" in body
    assert "*Fecha de regreso:* 24 de mayo de 2027, 18:45" in body


def test_one_way_message_omits_return_and_can_be_sent() -> None:
    settings = build_settings(only_round_trip=False)
    offer = build_offer(destination="MAD", price=100, return_at=None)
    deal = select_super_deals([offer], settings)[0]
    notifier = WhatsAppNotifier(settings)

    body = notifier.build_message_text(deal)

    assert "*Tipo:* Solo ida" in body
    assert "*Fecha de ida:*" in body
    assert "*Fecha de regreso:*" not in body

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"messages": [{"id": "wamid.one-way"}]})

    async def scenario() -> bool:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sender = WhatsAppNotifier(settings, client=client)
            try:
                return await sender.send_deal_alert(deal)
            finally:
                await sender.close()

    assert asyncio.run(scenario()) is True


def test_notifier_refuses_deal_without_return_date() -> None:
    settings = build_settings(only_round_trip=True)
    offer = build_offer(destination="MAD", price=100, return_at=None)
    deal = FlightDeal(
        offer=offer,
        origin_city="Medellín",
        destination_info=DestinationInfo(
            iata="MAD",
            city="Madrid",
            country_code="ES",
            country_name="España",
            region=Region.EUROPE,
        ),
        threshold_usd=550,
    )
    notifier = WhatsAppNotifier(settings)

    assert asyncio.run(notifier.send_deal_alert(deal)) is False


def test_notifier_requires_whatsapp_configuration() -> None:
    without_phone_id = WhatsAppNotifier(build_settings(whatsapp_phone_number_id=""))
    without_token = WhatsAppNotifier(build_settings(whatsapp_access_token=""))
    without_recipient = WhatsAppNotifier(build_settings(notification_phone=""))

    for notifier in (without_phone_id, without_token, without_recipient):
        assert not notifier.is_configured
        assert notifier.configuration_error is not None


def test_marker_is_optional_for_whatsapp_configuration() -> None:
    notifier = WhatsAppNotifier(build_settings(travelpayouts_marker=""))

    assert notifier.is_configured
    assert notifier.configuration_error is None


def test_send_deal_alert_posts_whatsapp_cloud_payload() -> None:
    settings, deal = _build_deal()
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"messages": [{"id": "wamid.test"}]},
        )

    async def scenario() -> bool:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            notifier = WhatsAppNotifier(settings, client=client)
            try:
                return await notifier.send_deal_alert(deal)
            finally:
                await notifier.close()

    assert asyncio.run(scenario()) is True
    request = captured[0]
    payload = json.loads(request.content)

    assert request.method == "POST"
    assert str(request.url) == (
        f"https://graph.facebook.com/v22.0/{settings.whatsapp_phone_number_id}/messages"
    )
    assert request.headers["Authorization"] == "Bearer test-whatsapp-token"
    assert request.headers["Content-Type"] == "application/json"
    assert payload["messaging_product"] == "whatsapp"
    assert payload["recipient_type"] == "individual"
    assert payload["to"] == "+573001234567"
    assert payload["type"] == "text"
    assert payload["text"]["preview_url"] is True
    assert payload["text"]["body"].startswith("*🚨 Súper Promoción de Vuelo*")


def test_send_deal_alert_returns_false_on_whatsapp_error() -> None:
    settings, deal = _build_deal()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "Invalid parameter",
                    "code": 100,
                    "error_subcode": 33,
                    "fbtrace_id": "trace-test",
                }
            },
        )

    async def scenario() -> bool:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            notifier = WhatsAppNotifier(settings, client=client)
            try:
                return await notifier.send_deal_alert(deal)
            finally:
                await notifier.close()

    assert asyncio.run(scenario()) is False
