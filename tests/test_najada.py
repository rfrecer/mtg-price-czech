from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from mtg_price_czech.errors import ShopParseError, ShopRequestError
from mtg_price_czech.http import RetryPolicy
from mtg_price_czech.najada import BASE_URL, search_najada

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.asyncio
@respx.mock
async def test_parse_real_fixture() -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=_load("najada_bolt.json")))
    offers = await search_najada("Lightning Bolt", in_stock_only=True)
    assert offers
    assert all(o.shop == "najada" for o in offers)
    names = {o.card_name for o in offers}
    assert "Lightning Bolt" in names
    foil = [o for o in offers if o.foil]
    assert foil
    assert foil[0].price_czk == 59


@pytest.mark.asyncio
@respx.mock
async def test_foil_flag() -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=_load("najada_bolt.json")))
    offers = await search_najada("Lightning Bolt", in_stock_only=False)
    by_key = {(o.card_name, o.foil, o.stock_qty): o for o in offers}
    assert any(o.foil for o in offers)
    assert any(not o.foil for o in offers)
    assert by_key[("Emeritus of Conflict // Lightning Bolt", True, 1)].price_czk == 59


@pytest.mark.asyncio
@respx.mock
async def test_effective_price_falls_back_to_regular() -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=_load("najada_edges.json")))
    offers = await search_najada("Edge", in_stock_only=False)
    edge = [o for o in offers if o.card_name == "Edge Case"]
    assert any(o.price_czk == 42 for o in edge)


@pytest.mark.asyncio
@respx.mock
async def test_stock_zero_dropped_or_kept() -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=_load("najada_edges.json")))
    only = await search_najada("Edge", in_stock_only=True)
    assert all(o.stock_qty > 0 for o in only)
    assert not any(o.card_name == "Edge Case" and o.stock_qty == 0 for o in only)

    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=_load("najada_edges.json")))
    all_offers = await search_najada("Edge", in_stock_only=False)
    assert any(o.card_name == "Edge Case" and o.stock_qty == 0 for o in all_offers)


@pytest.mark.asyncio
@respx.mock
async def test_null_price_skipped() -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=_load("najada_edges.json")))
    offers = await search_najada("Edge", in_stock_only=False)
    # null effective + null regular is skipped; price<=0 skipped; only priced articles remain
    edge = [o for o in offers if o.card_name == "Edge Case"]
    assert all(o.price_czk > 0 for o in edge)
    assert len(edge) == 2  # fallback 42 and stock-0 foil 10; zero-price dropped


@pytest.mark.asyncio
@respx.mock
async def test_empty_result_returns_empty_list() -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=_load("najada_empty.json")))
    assert await search_najada("xyzzy") == []


@pytest.mark.asyncio
@respx.mock
async def test_set_code_lowercased() -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=_load("najada_edges.json")))
    offers = await search_najada("Upper", in_stock_only=True)
    codes = {o.set_code for o in offers if o.card_name == "Upper Code"}
    assert codes == {"abc"}


@pytest.mark.asyncio
@respx.mock
async def test_pagination_follows_next() -> None:
    page1 = _load("najada_page1.json")
    page2 = _load("najada_page2.json")
    next_url = page1["next"]

    def handler(request: httpx.Request) -> httpx.Response:
        # First page uses BASE_URL + params; later pages use the absolute next URL.
        if str(request.url) == next_url or "offset=100" in str(request.url):
            return httpx.Response(200, json=page2)
        return httpx.Response(200, json=page1)

    respx.get(url__startswith=BASE_URL.rstrip("?")).mock(side_effect=handler)
    offers = await search_najada("Lightning")
    names = [o.card_name for o in offers]
    assert names == ["Page One", "Page Two A", "Page Two B"]


@pytest.mark.asyncio
@respx.mock
async def test_pagination_cap_raises() -> None:
    forever = {
        "count": 9999,
        "next": "https://wizardshop.cz/api/v1/najada2/catalog/mtg-singles/?limit=100&offset=100&q=x",
        "previous": None,
        "results": [
            {
                "name": "X",
                "expansion": {"localized_name": "S", "short_code": "s"},
                "articles": [
                    {
                        "effective_price_czk": 1,
                        "regular_price_czk": 1,
                        "total_availability": 1,
                        "additional_properties": {"is_foil": False},
                    }
                ],
            }
        ],
    }
    respx.get(url__startswith="https://wizardshop.cz/api/v1/najada2/catalog/mtg-singles/").mock(
        return_value=httpx.Response(200, json=forever)
    )
    with pytest.raises(ShopParseError, match="20-page"):
        await search_najada("x")


@pytest.mark.asyncio
@respx.mock
async def test_count_without_next_raises() -> None:
    payload = {
        "count": 5,
        "next": None,
        "previous": None,
        "results": [
            {
                "name": "Only One",
                "expansion": {"localized_name": "S", "short_code": "s"},
                "articles": [
                    {
                        "effective_price_czk": 1,
                        "regular_price_czk": 1,
                        "total_availability": 1,
                        "additional_properties": {"is_foil": False},
                    }
                ],
            }
        ],
    }
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=payload))
    with pytest.raises(ShopParseError, match="without a next link"):
        await search_najada("Only")


@pytest.mark.asyncio
@respx.mock
async def test_missing_results_raises() -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json={"count": 0}))
    with pytest.raises(ShopParseError, match="missing results"):
        await search_najada("x")


@pytest.mark.asyncio
@respx.mock
async def test_non_numeric_price_raises() -> None:
    payload = {
        "count": 1,
        "next": None,
        "previous": None,
        "results": [
            {
                "name": "Bad Price",
                "expansion": {"localized_name": "S", "short_code": "s"},
                "articles": [
                    {
                        "effective_price_czk": "nope",
                        "regular_price_czk": 1,
                        "total_availability": 1,
                        "additional_properties": {"is_foil": False},
                    }
                ],
            }
        ],
    }
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=payload))
    with pytest.raises(ShopParseError, match="unparsable price"):
        await search_najada("Bad")


@pytest.mark.asyncio
@respx.mock
async def test_outage_raises_not_empty(no_sleep: None) -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(ShopRequestError) as exc:
        await search_najada("Coat of Arms", retry=RetryPolicy(attempts=2))
    assert exc.value.shop == "najada"
    # Must not look like an empty stock result.
    assert not isinstance(exc.value, list)


@pytest.mark.asyncio
@respx.mock
async def test_prefers_english_name_field() -> None:
    payload = {
        "count": 1,
        "next": None,
        "previous": None,
        "results": [
            {
                "name": "English Name",
                "localized_name": "Cesky Nazev",
                "name_cz": "Cesky Nazev",
                "expansion": {"localized_name": "S", "short_code": "s"},
                "articles": [
                    {
                        "effective_price_czk": 10,
                        "regular_price_czk": 10,
                        "total_availability": 1,
                        "additional_properties": {"is_foil": False},
                    }
                ],
            }
        ],
    }
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=payload))
    offers = await search_najada("English")
    assert offers[0].card_name == "English Name"


@pytest.mark.asyncio
@respx.mock
async def test_caller_client_not_closed() -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=_load("najada_empty.json")))
    client = httpx.AsyncClient()
    try:
        await search_najada("x", client=client)
        assert not client.is_closed
    finally:
        await client.aclose()
