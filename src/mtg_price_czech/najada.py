"""Najada singles search via the Wizardshop JSON catalog API.

Endpoint discovery credit: Robin Vyslouzil's czech-mtg-price-comparator
(https://github.com/xvyslo05/czech-mtg-price-comparator, MIT).

Pagination (verified live): Django REST style. Responses include count, next,
previous, and results. When more pages exist, next is an absolute URL with
offset (e.g. limit=100&offset=100). We follow next up to 20 pages. If count
exceeds accumulated results and next is missing, we raise ShopParseError rather
than silently returning a partial list.

Name fields (verified live): name is the English card name (with shop suffixes).
localized_name matches for English cards; name_cz is often empty. We prefer name,
then localized_name, then name_cz.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from mtg_price_czech.errors import ShopParseError
from mtg_price_czech.http import (
    DEFAULT_RETRY,
    RetryPolicy,
    default_client,
    parse_number,
    request_json,
    require_mapping,
)
from mtg_price_czech.models import BuylistOffer, Offer

SHOP = "najada"
BASE_URL = "https://wizardshop.cz/api/v1/najada2/catalog/mtg-singles/"
BUYLIST_URL = "https://wizardshop.cz/api/v1/najada2/buylist-offers/"
MTG_GAME_ID = "40e972fe-c136-4d66-b61a-89f6be0f93d5"
MAX_PAGES = 20
PAGE_LIMIT = 100
BUYLIST_PRICE_MAX = 10_000_000

_EXTRA_HEADERS = {
    "Origin": "https://najada.games",
    "Referer": "https://najada.games/",
}


async def search_najada(
    name: str,
    *,
    in_stock_only: bool = True,
    client: httpx.AsyncClient | None = None,
    retry: RetryPolicy = DEFAULT_RETRY,
) -> list[Offer]:
    """Search Najada for cards whose name contains ``name`` (substring match)."""
    owns_client = client is None
    if client is None:
        client = default_client()

    try:
        offers: list[Offer] = []
        url: str | None = BASE_URL
        params: dict[str, Any] | None = {"q": name, "limit": PAGE_LIMIT}
        pages = 0
        total_count: int | None = None
        cards_seen = 0

        while url is not None:
            pages += 1
            if pages > MAX_PAGES:
                raise ShopParseError(
                    SHOP,
                    f"exceeded {MAX_PAGES}-page pagination cap while searching {name!r}",
                )

            payload = await request_json(
                SHOP,
                client,
                "GET",
                url,
                retry=retry,
                params=params,
                headers=_EXTRA_HEADERS,
            )
            # After the first page, follow absolute next URLs without re-sending params.
            params = None

            data = require_mapping(SHOP, payload, "response")
            if "results" not in data:
                raise ShopParseError(SHOP, "missing results array (API changed?)")
            results = data["results"]
            if not isinstance(results, list):
                raise ShopParseError(SHOP, "results is not a list (API changed?)")

            if total_count is None and data.get("count") is not None:
                try:
                    total_count = int(data["count"])
                except (TypeError, ValueError) as exc:
                    raise ShopParseError(
                        SHOP, f"unparsable count: {data['count']!r}"
                    ) from exc

            cards_seen += len(results)
            for card in results:
                offers.extend(_offers_from_card(card, in_stock_only=in_stock_only))

            next_url = data.get("next")
            if next_url:
                if not isinstance(next_url, str):
                    raise ShopParseError(SHOP, f"next is not a URL string: {next_url!r}")
                url = next_url
                continue

            if total_count is not None and cards_seen < total_count:
                raise ShopParseError(
                    SHOP,
                    f"count ({total_count}) exceeds accumulated results ({cards_seen}) "
                    "without a next link (API changed?)",
                )
            url = None

        return offers
    finally:
        if owns_client:
            await client.aclose()


def _offers_from_card(card: Any, *, in_stock_only: bool) -> list[Offer]:
    data = require_mapping(SHOP, card, "card")
    card_name = _card_name(data)
    edition, set_code = _edition_fields(data.get("expansion"))
    articles = data.get("articles")
    if articles is None:
        return []
    if not isinstance(articles, list):
        raise ShopParseError(SHOP, f"articles is not a list for {card_name!r} (API changed?)")

    offers: list[Offer] = []
    for article in articles:
        offer = _offer_from_article(
            article,
            card_name=card_name,
            edition=edition,
            set_code=set_code,
            in_stock_only=in_stock_only,
        )
        if offer is not None:
            offers.append(offer)
    return offers


def _card_name(card: Mapping[str, Any]) -> str:
    for key in ("name", "localized_name", "name_cz"):
        value = card.get(key)
        if isinstance(value, str) and value.strip():
            return value
    raise ShopParseError(SHOP, "card missing name fields (API changed?)")


def _edition_fields(expansion: Any) -> tuple[str | None, str | None]:
    if expansion is None:
        return None, None
    data = require_mapping(SHOP, expansion, "expansion")
    edition: str | None = None
    for key in ("localized_name", "name"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            edition = value
            break
    short = data.get("short_code")
    set_code = short.lower() if isinstance(short, str) and short.strip() else None
    return edition, set_code


def _offer_from_article(
    article: Any,
    *,
    card_name: str,
    edition: str | None,
    set_code: str | None,
    in_stock_only: bool,
) -> Offer | None:
    data = require_mapping(SHOP, article, f"article of {card_name!r}")
    price_raw = data.get("effective_price_czk")
    if price_raw is None:
        price_raw = data.get("regular_price_czk")
    if price_raw is None:
        return None
    price = parse_number(SHOP, price_raw, "price", card_name)
    if price <= 0:
        return None

    stock_raw = data.get("total_availability", 0)
    stock = int(parse_number(SHOP, stock_raw, "total_availability", card_name))
    if in_stock_only and stock <= 0:
        return None

    props = data.get("additional_properties") or {}
    if not isinstance(props, dict):
        raise ShopParseError(
            SHOP, f"additional_properties is not an object for {card_name!r} (API changed?)"
        )
    foil = bool(props.get("is_foil"))

    return Offer(
        shop=SHOP,
        card_name=card_name,
        edition=edition,
        set_code=set_code,
        foil=foil,
        price_czk=round(price),
        stock_qty=stock,
    )


async def buylist_najada(
    name: str,
    *,
    buying_only: bool = True,
    client: httpx.AsyncClient | None = None,
    retry: RetryPolicy = DEFAULT_RETRY,
) -> list[BuylistOffer]:
    """Search Najada buylist for printings whose name contains ``name``."""
    owns_client = client is None
    if client is None:
        client = default_client()

    try:
        offers: list[BuylistOffer] = []
        url: str | None = BUYLIST_URL
        params: dict[str, Any] | None = {
            "game_id": MTG_GAME_ID,
            "enabled": "true",
            "q": name,
            "buy_price_min": 0,
            "buy_price_max": BUYLIST_PRICE_MAX,
            "o": "name",
            "limit": PAGE_LIMIT,
            "offset": 0,
        }
        pages = 0
        total_count: int | None = None
        results_seen = 0

        while url is not None:
            pages += 1
            if pages > MAX_PAGES:
                raise ShopParseError(
                    SHOP,
                    f"exceeded {MAX_PAGES}-page pagination cap while buylist-searching {name!r}",
                )

            payload = await request_json(
                SHOP,
                client,
                "GET",
                url,
                retry=retry,
                params=params,
                headers=_EXTRA_HEADERS,
            )
            params = None

            data = require_mapping(SHOP, payload, "response")
            if "results" not in data:
                raise ShopParseError(SHOP, "missing results array (API changed?)")
            results = data["results"]
            if not isinstance(results, list):
                raise ShopParseError(SHOP, "results is not a list (API changed?)")

            if total_count is None and data.get("count") is not None:
                try:
                    total_count = int(data["count"])
                except (TypeError, ValueError) as exc:
                    raise ShopParseError(
                        SHOP, f"unparsable count: {data['count']!r}"
                    ) from exc

            results_seen += len(results)
            for row in results:
                offers.extend(_buylist_from_offer(row, buying_only=buying_only))

            next_url = data.get("next")
            if next_url:
                if not isinstance(next_url, str):
                    raise ShopParseError(SHOP, f"next is not a URL string: {next_url!r}")
                url = next_url
                continue

            if total_count is not None and results_seen < total_count:
                raise ShopParseError(
                    SHOP,
                    f"count ({total_count}) exceeds accumulated results ({results_seen}) "
                    "without a next link (API changed?)",
                )
            url = None

        return offers
    finally:
        if owns_client:
            await client.aclose()


def _buylist_from_offer(row: Any, *, buying_only: bool) -> list[BuylistOffer]:
    data = require_mapping(SHOP, row, "buylist offer")
    product = require_mapping(SHOP, data.get("product"), "buylist product")
    card_name = _card_name(product)
    edition, set_code = _edition_fields(product.get("expansion"))
    foil = bool(data.get("is_foil"))

    want_raw = data.get("available_for_buylist", 0)
    want_qty = int(parse_number(SHOP, want_raw, "available_for_buylist", card_name))
    if buying_only and want_qty <= 0:
        return []

    prices = data.get("prices")
    if prices is None:
        return []
    prices_map = require_mapping(SHOP, prices, f"prices of {card_name!r}")
    czk = prices_map.get("CZK")
    if czk is None:
        return []
    czk_map = require_mapping(SHOP, czk, f"prices.CZK of {card_name!r}")
    en = czk_map.get("en")
    if en is None:
        return []
    en_map = require_mapping(SHOP, en, f"prices.CZK.en of {card_name!r}")

    offers: list[BuylistOffer] = []
    for condition, price_raw in en_map.items():
        if not isinstance(condition, str) or not condition.strip():
            raise ShopParseError(
                SHOP, f"unparsable condition key for {card_name!r}: {condition!r}"
            )
        price = parse_number(SHOP, price_raw, f"price[{condition}]", card_name)
        if price <= 0:
            continue
        offers.append(
            BuylistOffer(
                shop=SHOP,
                card_name=card_name,
                edition=edition,
                set_code=set_code,
                foil=foil,
                condition=condition.upper(),
                price_czk=round(price),
                want_qty=want_qty,
                language="EN",
            )
        )
    return offers
