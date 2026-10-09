from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from mtg_price_czech.errors import ShopParseError, ShopRequestError
from mtg_price_czech.http import RetryPolicy
from mtg_price_czech.najada import BUYLIST_URL, MTG_GAME_ID, buylist_najada

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.asyncio
@respx.mock
async def test_parse_expand_conditions() -> None:
    respx.get(BUYLIST_URL).mock(
        return_value=httpx.Response(200, json=_load("najada_buylist_abeyance.json"))
    )
    offers = await buylist_najada("Abeyance")
    assert len(offers) == 5
    assert all(o.shop == "najada" for o in offers)
    assert all(o.card_name == "Abeyance" for o in offers)
    assert all(o.set_code == "wth" for o in offers)
    assert all(o.foil is False for o in offers)
    assert all(o.language == "EN" for o in offers)
    assert all(o.want_qty == 99 for o in offers)
    by_cond = {o.condition: o.price_czk for o in offers}
    assert by_cond == {"NM": 699, "EX": 629, "GD": 559, "PL": 454, "HP": 350}


@pytest.mark.asyncio
@respx.mock
async def test_query_params_include_game_id() -> None:
    route = respx.get(BUYLIST_URL).mock(
        return_value=httpx.Response(200, json=_load("najada_buylist_empty.json"))
    )
    await buylist_najada("Abeyance")
    assert route.called
    url = str(route.calls.last.request.url)
    qs = parse_qs(urlparse(url).query)
    assert qs["game_id"] == [MTG_GAME_ID]
    assert qs["enabled"] == ["true"]
    assert qs["q"] == ["Abeyance"]


@pytest.mark.asyncio
@respx.mock
async def test_buying_only_drops_zero_want() -> None:
    respx.get(BUYLIST_URL).mock(
        return_value=httpx.Response(200, json=_load("najada_buylist_bolt.json"))
    )
    only = await buylist_najada("Lightning Bolt", buying_only=True)
    assert all(o.want_qty > 0 for o in only)
    assert not any(o.set_code == "nws" for o in only)
    assert any(o.foil for o in only)
    assert any(not o.foil for o in only)

    respx.get(BUYLIST_URL).mock(
        return_value=httpx.Response(200, json=_load("najada_buylist_bolt.json"))
    )
    all_offers = await buylist_najada("Lightning Bolt", buying_only=False)
    assert any(o.set_code == "nws" and o.want_qty == 0 for o in all_offers)


@pytest.mark.asyncio
@respx.mock
async def test_empty_result_returns_empty_list() -> None:
    respx.get(BUYLIST_URL).mock(
        return_value=httpx.Response(200, json=_load("najada_buylist_empty.json"))
    )
    assert await buylist_najada("xyzzy") == []


@pytest.mark.asyncio
@respx.mock
async def test_pagination_follows_next() -> None:
    page1 = _load("najada_buylist_page1.json")
    page2 = _load("najada_buylist_page2.json")
    next_url = page1["next"]

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == next_url or "offset=100" in str(request.url):
            return httpx.Response(200, json=page2)
        return httpx.Response(200, json=page1)

    respx.get(url__startswith=BUYLIST_URL.rstrip("?")).mock(side_effect=handler)
    offers = await buylist_najada("Bolt")
    names = {o.card_name for o in offers}
    assert names == {"Bolt Page1", "Bolt Page2"}


@pytest.mark.asyncio
@respx.mock
async def test_missing_next_with_remaining_count_raises(no_sleep: None) -> None:
    payload = {
        "count": 5,
        "next": None,
        "previous": None,
        "results": _load("najada_buylist_abeyance.json")["results"],
    }
    respx.get(url__startswith="https://wizardshop.cz/api/v1/najada2/buylist-offers/").mock(
        return_value=httpx.Response(200, json=payload)
    )
    with pytest.raises(ShopParseError):
        await buylist_najada("x")


@pytest.mark.asyncio
@respx.mock
async def test_http_error_raises(no_sleep: None) -> None:
    respx.get(BUYLIST_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(ShopRequestError) as exc:
        await buylist_najada("Abeyance", retry=RetryPolicy(attempts=2))
    assert exc.value.shop == "najada"
