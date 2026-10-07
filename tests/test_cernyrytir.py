from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from mtg_price_czech.cernyrytir import API_URL, search_cernyrytir
from mtg_price_czech.errors import ShopParseError, ShopRequestError
from mtg_price_czech.http import RetryPolicy

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.asyncio
@respx.mock
async def test_parse_real_fixture() -> None:
    respx.post(API_URL).mock(return_value=httpx.Response(200, json=_load("cr_coat.json")))
    offers = await search_cernyrytir("Coat of Arms")
    assert offers
    assert all(o.shop == "cernyrytir" for o in offers)
    coats = [o for o in offers if o.card_name.startswith("Coat of Arms")]
    assert coats
    assert coats[0].set_code == "lcc"
    assert coats[0].borderless is True


@pytest.mark.asyncio
@respx.mock
async def test_set_code_from_scryfall_present_and_absent() -> None:
    respx.post(API_URL).mock(return_value=httpx.Response(200, json=_load("cr_edges.json")))
    offers = await search_cernyrytir("No", in_stock_only=False)
    no_scry = [o for o in offers if o.card_name == "No Scryfall Card"]
    assert no_scry
    assert no_scry[0].set_code is None
    mixed = [o for o in offers if o.card_name == "Mixed Finish"]
    assert mixed[0].set_code == "exo"


@pytest.mark.asyncio
@respx.mock
async def test_foil_from_card_flag_and_internal_finish() -> None:
    respx.post(API_URL).mock(return_value=httpx.Response(200, json=_load("cr_urza.json")))
    offers = await search_cernyrytir("Urza")
    rage = next(o for o in offers if o.card_name == "Urza's Rage")
    # card.foil True even though internal finish is REGULAR
    assert rage.foil is True
    mine = next(o for o in offers if o.card_name == "Urza's Mine")
    assert mine.foil is True  # internal FOIL


@pytest.mark.asyncio
@respx.mock
async def test_etched_and_borderless_flags() -> None:
    respx.post(API_URL).mock(return_value=httpx.Response(200, json=_load("cr_urza.json")))
    offers = await search_cernyrytir("Urza")
    rage = next(o for o in offers if o.card_name == "Urza's Rage")
    assert rage.etched is True
    assert rage.borderless is True
    avenger = next(o for o in offers if o.card_name == "Urza's Avenger")
    assert avenger.etched is False
    assert avenger.borderless is False


@pytest.mark.asyncio
@respx.mock
async def test_price_le_zero_skipped() -> None:
    respx.post(API_URL).mock(return_value=httpx.Response(200, json=_load("cr_edges.json")))
    offers = await search_cernyrytir("No", in_stock_only=False)
    no_scry = [o for o in offers if o.card_name == "No Scryfall Card"]
    assert len(no_scry) == 1
    assert no_scry[0].price_czk == 16  # round(15.6)


@pytest.mark.asyncio
@respx.mock
async def test_stock_zero_respects_in_stock_only() -> None:
    respx.post(API_URL).mock(return_value=httpx.Response(200, json=_load("cr_edges.json")))
    only = await search_cernyrytir("Mixed", in_stock_only=True)
    assert all(o.stock_qty > 0 for o in only if o.card_name == "Mixed Finish")

    respx.post(API_URL).mock(return_value=httpx.Response(200, json=_load("cr_edges.json")))
    all_offers = await search_cernyrytir("Mixed", in_stock_only=False)
    assert any(o.card_name == "Mixed Finish" and o.stock_qty == 0 for o in all_offers)


@pytest.mark.asyncio
@respx.mock
async def test_empty_result_returns_empty_list() -> None:
    respx.post(API_URL).mock(return_value=httpx.Response(200, json=_load("cr_empty.json")))
    assert await search_cernyrytir("xyzzy") == []


@pytest.mark.asyncio
@respx.mock
async def test_multi_page_pagination() -> None:
    page1 = _load("cr_page1.json")
    page2 = _load("cr_page2.json")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        page = body["pagination"]["page"]
        if page == 1:
            return httpx.Response(200, json=page1)
        if page == 2:
            return httpx.Response(200, json=page2)
        raise AssertionError(f"unexpected page {page}")

    respx.post(API_URL).mock(side_effect=handler)
    offers = await search_cernyrytir("Page")
    assert [o.card_name for o in offers] == ["Page Card A", "Page Card B"]


@pytest.mark.asyncio
@respx.mock
async def test_request_body_has_required_keys_and_page() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append(body)
        return httpx.Response(200, json=_load("cr_empty.json"))

    respx.post(API_URL).mock(side_effect=handler)
    await search_cernyrytir("Coat of Arms", in_stock_only=True)
    assert len(seen) == 1
    body = seen[0]
    ext = body["extendedFilter"]
    assert ext["cardName"] == "Coat of Arms"
    assert ext["inStockOnly"] is True
    assert set(ext["foil"]) == {"regular", "foil", "etched"}
    assert set(ext["rarity"]) >= {"common", "mythic", "token"}
    assert set(ext["condition"]) == {"nm", "lp", "pl"}
    assert "colorCombination" in ext["color"]
    assert ext["price"] == {"min": 0, "max": 0}
    assert "planeswalker" in ext["typeLine"]
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["rowsPerPage"] == 100


@pytest.mark.asyncio
@respx.mock
async def test_20_page_cap_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        page = body["pagination"]["page"]
        return httpx.Response(
            200,
            json={
                "list": [
                    {
                        "name": f"Card {page}",
                        "cardEditionName": "S",
                        "scryfallUri": f"https://scryfall.com/card/lea/{page}/c",
                        "foil": False,
                        "etched": False,
                        "borderColor": "BLACK",
                        "internalCards": [
                            {"priceSell": 1, "availEshopQty": 1, "foil": "REGULAR"}
                        ],
                    }
                ],
                "pagination": {
                    "page": page,
                    "rowsPerPage": 100,
                    "rowsNumber": 5000,
                    "sortBy": "p_asc",
                    "descending": False,
                },
            },
        )

    respx.post(API_URL).mock(side_effect=handler)
    with pytest.raises(ShopParseError, match="20-page"):
        await search_cernyrytir("Card")


@pytest.mark.asyncio
@respx.mock
async def test_missing_list_raises() -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(
            200, json={"pagination": {"rowsNumber": 0, "page": 1, "rowsPerPage": 100}}
        )
    )
    with pytest.raises(ShopParseError, match="missing list"):
        await search_cernyrytir("x")


@pytest.mark.asyncio
@respx.mock
async def test_missing_rows_number_raises() -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(200, json={"list": [], "pagination": {"page": 1}})
    )
    with pytest.raises(ShopParseError, match="rowsNumber"):
        await search_cernyrytir("x")


@pytest.mark.asyncio
@respx.mock
async def test_non_numeric_price_raises() -> None:
    payload = {
        "list": [
            {
                "name": "Bad",
                "cardEditionName": "S",
                "scryfallUri": "https://scryfall.com/card/lea/1/bad",
                "foil": False,
                "etched": False,
                "borderColor": "BLACK",
                "internalCards": [
                    {"priceSell": "nope", "availEshopQty": 1, "foil": "REGULAR"}
                ],
            }
        ],
        "pagination": {
            "page": 1,
            "rowsPerPage": 100,
            "rowsNumber": 1,
            "sortBy": "p_asc",
            "descending": False,
        },
    }
    respx.post(API_URL).mock(return_value=httpx.Response(200, json=payload))
    with pytest.raises(ShopParseError, match="unparsable priceSell"):
        await search_cernyrytir("Bad")


@pytest.mark.asyncio
@respx.mock
async def test_outage_raises_not_empty(no_sleep: None) -> None:
    respx.post(API_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(ShopRequestError) as exc:
        await search_cernyrytir("Coat of Arms", retry=RetryPolicy(attempts=2))
    assert exc.value.shop == "cernyrytir"


@pytest.mark.asyncio
@respx.mock
async def test_multipage_body_increments_page() -> None:
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        page = body["pagination"]["page"]
        pages.append(page)
        fixture = _load("cr_page1.json") if page == 1 else _load("cr_page2.json")
        return httpx.Response(200, json=fixture)

    respx.post(API_URL).mock(side_effect=handler)
    await search_cernyrytir("Page")
    assert pages == [1, 2]
