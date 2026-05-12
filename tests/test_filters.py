from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.travelpayouts import (
    Region,
    resolve_destination,
    select_super_deals,
    threshold_for_region,
)
from tests.helpers import build_offer, build_settings


def test_resolve_allowed_blacklisted_and_unknown_destinations() -> None:
    lisbon = resolve_destination("lis")
    assert lisbon is not None
    assert lisbon.country_code == "PT"
    assert lisbon.region is Region.EUROPE

    assert resolve_destination("JFK") is None
    assert resolve_destination("ICN") is None
    assert resolve_destination("DXB") is None
    assert resolve_destination("XXX") is None


def test_resolve_city_code_fallbacks() -> None:
    buenos_aires = resolve_destination("BUE")
    london = resolve_destination("LON")

    assert buenos_aires is not None
    assert buenos_aires.country_code == "AR"
    assert buenos_aires.region is Region.SOUTH_AMERICA_FAR
    assert london is not None
    assert london.country_code == "GB"


def test_threshold_for_each_region() -> None:
    settings = build_settings()

    assert threshold_for_region(Region.SOUTH_AMERICA_NEAR, settings) == 200
    assert threshold_for_region(Region.SOUTH_AMERICA_FAR, settings) == 300
    assert threshold_for_region(Region.CENTRAL_AMERICA_CARIBBEAN, settings) == 220
    assert threshold_for_region(Region.EUROPE, settings) == 550
    assert threshold_for_region(Region.ASIA, settings) == 950


def test_thresholds_can_be_overridden() -> None:
    settings = build_settings(threshold_europe_usd=100)
    deal = select_super_deals(
        [build_offer(destination="MAD", price=100)],
        settings,
    )
    rejected = select_super_deals(
        [build_offer(destination="MAD", price=100.01)],
        settings,
    )

    assert len(deal) == 1
    assert rejected == []


def test_super_deal_includes_limit_price() -> None:
    settings = build_settings()

    near = select_super_deals(
        [build_offer(destination="LIM", price=200)],
        settings,
    )
    central = select_super_deals(
        [build_offer(destination="MEX", price=220)],
        settings,
    )

    assert len(near) == 1
    assert len(central) == 1


def test_excludes_expensive_blocked_unknown_domestic_and_other_origin() -> None:
    settings = build_settings()
    offers = [
        build_offer(destination="MAD", price=551),
        build_offer(destination="MIA", price=100),
        build_offer(destination="ICN", price=100),
        build_offer(destination="XXX", price=100),
        build_offer(destination="BOG", price=100),
        build_offer(origin="BOG", destination="MAD", price=100),
    ]

    assert select_super_deals(offers, settings) == []


def test_excludes_one_way_offers() -> None:
    settings = build_settings(only_round_trip=True)
    one_way_offer = build_offer(
        destination="MAD",
        price=100,
        return_at=None,
    )
    round_trip_offer = build_offer(destination="LIS", price=450)

    deals = select_super_deals([one_way_offer, round_trip_offer], settings)

    assert select_super_deals([one_way_offer], settings) == []
    assert len(deals) == 1
    assert deals[0].offer.destination == "LIS"
    assert deals[0].offer.return_at is not None


def test_accepts_one_way_offers_when_only_round_trip_is_false() -> None:
    settings = build_settings(only_round_trip=False)
    offer = build_offer(
        destination="MAD",
        price=100,
        departure_at=datetime(2027, 5, 10, 8, 30, tzinfo=UTC),
        return_at=None,
    )

    deals = select_super_deals([offer], settings)

    assert len(deals) == 1
    assert deals[0].offer.return_at is None
    assert deals[0].deal_id == "MDE_MAD_2027-05-10T08:30:00+00:00_100_one_way"


def test_excludes_past_departures() -> None:
    settings = build_settings()
    past_offer = build_offer(
        destination="MAD",
        price=100,
        departure_at=datetime.now(UTC) - timedelta(days=1),
    )

    assert select_super_deals([past_offer], settings) == []


def test_uses_destination_airport_as_country_fallback() -> None:
    settings = build_settings()
    offer = build_offer(
        destination="ZZZ",
        destination_airport="MAD",
        price=450,
    )

    deals = select_super_deals([offer], settings)

    assert len(deals) == 1
    assert deals[0].destination_info.country_code == "ES"


def test_deal_id_matches_required_format() -> None:
    settings = build_settings()
    offer = build_offer(
        origin="MDE",
        destination="LIS",
        price=499.5,
        departure_at=datetime(2027, 5, 10, 8, 30, tzinfo=UTC),
    )
    deal = select_super_deals([offer], settings)[0]

    expected = "MDE_LIS_2027-05-10T08:30:00+00:00_499.5"
    assert deal.deal_id == expected


def test_results_are_sorted_by_price_and_deduplicated() -> None:
    settings = build_settings()
    offer_one = build_offer(destination="MAD", price=400)
    offer_two = build_offer(destination="LIS", price=300)
    offers = [offer_one, offer_one, offer_two]

    deals = select_super_deals(offers, settings)

    assert [deal.offer.price for deal in deals] == [300, 400]


def test_region_labels_are_spanish() -> None:
    settings = build_settings()
    deal = select_super_deals(
        [build_offer(destination="LIS", price=300)],
        settings,
    )[0]

    assert deal.region_label == "Europa"
