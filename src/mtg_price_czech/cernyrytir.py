"""Černý Rytíř singles search via the public eshop JSON API.

The shop front-end is a SPA; HTML scraping of the old PHP pages no longer works.
Empty searches return list=[] with pagination.rowsNumber=0 (verified live).
Pagination is page-based with rowsPerPage=100 and rowsNumber as the total.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

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

SHOP = "cernyrytir"
API_URL = "https://eshop-api.cernyrytir.eu/api/public/card/list?eshop_mode=STANDARD"
BUYOUT_URL = "https://eshop-api.cernyrytir.eu/api/public/card/list?eshop_mode=BUYOUT"
MAX_PAGES = 20
ROWS_PER_PAGE = 100


def _filter_body(name: str, *, in_stock_only: bool, page: int) -> dict[str, Any]:
    """Full filter payload: the API expects every key, even when unused."""
    return {
        "extendedFilter": {
            "cardName": name,
            "inStockOnly": in_stock_only,
            "foil": {"regular": False, "foil": False, "etched": False},
            "rarity": {
                k: False
                for k in (
                    "common",
                    "uncommon",
                    "rare",
                    "special",
                    "mythic",
                    "bonus",
                    "token",
                    "land",
                )
            },
            "condition": {"nm": False, "lp": False, "pl": False},
            "color": {
                k: False
                for k in (
                    "white",
                    "blue",
                    "black",
                    "red",
                    "green",
                    "colorless",
                    "multicolor",
                    "colorCombination",
                    "onlySelectedColors",
                )
            },
            "price": {"min": 0, "max": 0},
            "typeLine": {
                k: False
                for k in (
                    "artifact",
                    "battle",
                    "creature",
                    "enchantment",
                    "instant",
                    "land",
                    "sorcery",
                    "planeswalker",
                )
            },
        },
        "pagination": {
            "page": page,
            "rowsPerPage": ROWS_PER_PAGE,
            "rowsNumber": 0,
            "sortBy": "p_asc",
            "descending": False,
        },
    }


async def search_cernyrytir(
    name: str,
    *,
    in_stock_only: bool = True,
    client: httpx.AsyncClient | None = None,
    retry: RetryPolicy = DEFAULT_RETRY,
) -> list[Offer]:
    """Search Černý Rytíř for cards whose name contains ``name`` (substring match)."""
    owns_client = client is None
    if client is None:
        client = default_client()

    try:
        offers: list[Offer] = []
        page = 1
        rows_number: int | None = None

        while True:
            if page > MAX_PAGES:
                raise ShopParseError(
                    SHOP,
                    f"exceeded {MAX_PAGES}-page pagination cap while searching {name!r}",
                )

            payload = await request_json(
                SHOP,
                client,
                "POST",
                API_URL,
                retry=retry,
                json=_filter_body(name, in_stock_only=in_stock_only, page=page),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            data = require_mapping(SHOP, payload, "response")
            if "list" not in data:
                raise ShopParseError(SHOP, "missing list (API changed?)")
            cards = data["list"]
            if not isinstance(cards, list):
                raise ShopParseError(SHOP, "list is not a list (API changed?)")

            pagination = data.get("pagination")
            pag = require_mapping(SHOP, pagination, "pagination")
            if "rowsNumber" not in pag:
                raise ShopParseError(SHOP, "missing pagination.rowsNumber (API changed?)")
            try:
                rows_number = int(pag["rowsNumber"])
            except (TypeError, ValueError) as exc:
                raise ShopParseError(
                    SHOP, f"unparsable pagination.rowsNumber: {pag['rowsNumber']!r}"
                ) from exc

            if not cards:
                break

            for card in cards:
                offers.extend(_offers_from_card(card, in_stock_only=in_stock_only))

            if page * ROWS_PER_PAGE >= rows_number:
                break
            page += 1

        return offers
    finally:
        if owns_client:
            await client.aclose()


def _offers_from_card(card: Any, *, in_stock_only: bool) -> list[Offer]:
    data = require_mapping(SHOP, card, "card")
    if "name" not in data or not isinstance(data["name"], str) or not data["name"].strip():
        raise ShopParseError(SHOP, "card missing name (API changed?)")
    card_name = data["name"]

    edition = data.get("cardEditionName")
    if edition is not None and not isinstance(edition, str):
        raise ShopParseError(SHOP, f"cardEditionName is not a string for {card_name!r}")
    if isinstance(edition, str) and not edition.strip():
        edition = None

    set_code = _set_code_from_scryfall(data.get("scryfallUri"))
    card_foil = bool(data.get("foil"))
    etched = bool(data.get("etched"))
    border = data.get("borderColor")
    borderless = isinstance(border, str) and border.upper() == "BORDERLESS"

    internals = data.get("internalCards")
    if internals is None:
        return []
    if not isinstance(internals, list):
        raise ShopParseError(
            SHOP, f"internalCards is not a list for {card_name!r} (API changed?)"
        )

    offers: list[Offer] = []
    for article in internals:
        offer = _offer_from_internal(
            article,
            card_name=card_name,
            edition=edition,
            set_code=set_code,
            card_foil=card_foil,
            etched=etched,
            borderless=borderless,
            in_stock_only=in_stock_only,
        )
        if offer is not None:
            offers.append(offer)
    return offers


def _set_code_from_scryfall(uri: Any) -> str | None:
    if uri is None or uri == "":
        return None
    if not isinstance(uri, str):
        raise ShopParseError(SHOP, f"scryfallUri is not a string: {uri!r}")
    path = urlparse(uri).path.strip("/").split("/")
    # Expected: card/<set>/<num>/<slug>
    try:
        card_idx = path.index("card")
    except ValueError:
        return None
    if card_idx + 1 >= len(path):
        return None
    code = path[card_idx + 1]
    return code.lower() if code else None


def _offer_from_internal(
    article: Any,
    *,
    card_name: str,
    edition: str | None,
    set_code: str | None,
    card_foil: bool,
    etched: bool,
    borderless: bool,
    in_stock_only: bool,
) -> Offer | None:
    data = require_mapping(SHOP, article, f"internalCards entry of {card_name!r}")
    price = parse_number(SHOP, data.get("priceSell"), "priceSell", card_name)
    if price <= 0:
        return None

    stock = int(parse_number(SHOP, data.get("availEshopQty", 0), "availEshopQty", card_name))
    if in_stock_only and stock <= 0:
        return None

    finish = data.get("foil", "")
    if finish is None:
        finish = ""
    if not isinstance(finish, str):
        raise ShopParseError(SHOP, f"unparsable foil finish for {card_name!r}: {finish!r}")
    foil = card_foil or finish.upper() not in {"REGULAR", ""}

    return Offer(
        shop=SHOP,
        card_name=card_name,
        edition=edition,
        set_code=set_code,
        foil=foil,
        price_czk=round(price),
        stock_qty=stock,
        etched=etched,
        borderless=borderless,
    )


async def buylist_cernyrytir(
    name: str,
    *,
    buying_only: bool = True,
    client: httpx.AsyncClient | None = None,
    retry: RetryPolicy = DEFAULT_RETRY,
) -> list[BuylistOffer]:
    """Search Černý Rytíř buylist for printings whose name contains ``name``."""
    owns_client = client is None
    if client is None:
        client = default_client()

    try:
        offers: list[BuylistOffer] = []
        page = 1
        rows_number: int | None = None

        while True:
            if page > MAX_PAGES:
                raise ShopParseError(
                    SHOP,
                    f"exceeded {MAX_PAGES}-page pagination cap while buylist-searching {name!r}",
                )

            payload = await request_json(
                SHOP,
                client,
                "POST",
                BUYOUT_URL,
                retry=retry,
                json=_filter_body(name, in_stock_only=False, page=page),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            data = require_mapping(SHOP, payload, "response")
            if "list" not in data:
                raise ShopParseError(SHOP, "missing list (API changed?)")
            cards = data["list"]
            if not isinstance(cards, list):
                raise ShopParseError(SHOP, "list is not a list (API changed?)")

            pagination = data.get("pagination")
            pag = require_mapping(SHOP, pagination, "pagination")
            if "rowsNumber" not in pag:
                raise ShopParseError(SHOP, "missing pagination.rowsNumber (API changed?)")
            try:
                rows_number = int(pag["rowsNumber"])
            except (TypeError, ValueError) as exc:
                raise ShopParseError(
                    SHOP, f"unparsable pagination.rowsNumber: {pag['rowsNumber']!r}"
                ) from exc

            if not cards:
                break

            for card in cards:
                offers.extend(_buylist_from_card(card, buying_only=buying_only))

            if page * ROWS_PER_PAGE >= rows_number:
                break
            page += 1

        return offers
    finally:
        if owns_client:
            await client.aclose()


def _buylist_from_card(card: Any, *, buying_only: bool) -> list[BuylistOffer]:
    data = require_mapping(SHOP, card, "card")
    if "name" not in data or not isinstance(data["name"], str) or not data["name"].strip():
        raise ShopParseError(SHOP, "card missing name (API changed?)")
    card_name = data["name"]

    edition = data.get("cardEditionName")
    if edition is not None and not isinstance(edition, str):
        raise ShopParseError(SHOP, f"cardEditionName is not a string for {card_name!r}")
    if isinstance(edition, str) and not edition.strip():
        edition = None

    set_code = _set_code_from_scryfall(data.get("scryfallUri"))
    card_foil = bool(data.get("foil"))
    etched = bool(data.get("etched"))
    border = data.get("borderColor")
    borderless = isinstance(border, str) and border.upper() == "BORDERLESS"

    internals = data.get("internalCards")
    if internals is None:
        return []
    if not isinstance(internals, list):
        raise ShopParseError(
            SHOP, f"internalCards is not a list for {card_name!r} (API changed?)"
        )

    parsed: list[tuple[Mapping[str, Any], bool, str | None, int, float]] = []
    wanted_keys: set[tuple[bool, str | None]] = set()

    for article in internals:
        entry = require_mapping(SHOP, article, f"internalCards entry of {card_name!r}")
        price_raw = entry.get("priceBuy")
        if price_raw is None:
            continue
        price = parse_number(SHOP, price_raw, "priceBuy", card_name)
        if price <= 0:
            continue

        finish = entry.get("foil", "")
        if finish is None:
            finish = ""
        if not isinstance(finish, str):
            raise ShopParseError(
                SHOP, f"unparsable foil finish for {card_name!r}: {finish!r}"
            )
        foil = card_foil or finish.upper() not in {"REGULAR", ""}

        language = entry.get("language")
        if language is not None and not isinstance(language, str):
            raise ShopParseError(
                SHOP, f"language is not a string for {card_name!r}: {language!r}"
            )
        if isinstance(language, str) and not language.strip():
            language = None

        want = int(parse_number(SHOP, entry.get("wantQty", 0), "wantQty", card_name))
        if want > 0:
            wanted_keys.add((foil, language))

        parsed.append((entry, foil, language, want, price))

    offers: list[BuylistOffer] = []
    for entry, foil, language, want, price in parsed:
        if buying_only and (foil, language) not in wanted_keys:
            continue

        condition = entry.get("condition")
        if not isinstance(condition, str) or not condition.strip():
            raise ShopParseError(
                SHOP, f"missing condition for {card_name!r}: {condition!r}"
            )

        offers.append(
            BuylistOffer(
                shop=SHOP,
                card_name=card_name,
                edition=edition,
                set_code=set_code,
                foil=foil,
                condition=condition.upper(),
                price_czk=round(price),
                want_qty=want,
                etched=etched,
                borderless=borderless,
                language=language.upper() if isinstance(language, str) else None,
            )
        )
    return offers
