from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from mtg_price_czech.cernyrytir import BUYOUT_URL, buylist_cernyrytir
from mtg_price_czech.errors import ShopParseError, ShopRequestError
from mtg_price_czech.http import RetryPolicy

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.asyncio
@respx.mock
async def test_parse_and_sibling_conditions() -> None:
    respx.post(BUYOUT_URL).mock(
        return_value=httpx.Response(200, json=_load("cr_buylist_coat.json"))
    )
    offers = await buylist_cernyrytir("Coat of Arms", buying_only=True)
    assert offers
    assert all(o.shop == "cernyrytir" for o in offers)

    tenth = [
        o
        for o in offers
        if o.card_name == "Coat of Arms" and o.edition == "10th Edition" and not o.foil
    ]
    assert {o.condition for o in tenth} == {"NM", "LP", "PL"}
    assert all(o.language == "EN" for o in tenth)
    nm = next(o for o in tenth if o.condition == "NM")
    assert nm.price_czk == 210
    assert nm.want_qty == 8
    lp = next(o for o in tenth if o.condition == "LP")
    assert lp.price_czk == 180
    assert lp.want_qty == 0

    # JA has wantQty 0 and no sibling want on JA → dropped when buying_only
    assert not any(o.language == "JA" for o in offers)

    foil_seventh = [
        o for o in offers if o.edition == "7th Edition" and o.foil and o.condition == "NM"
    ]
    assert foil_seventh
    assert foil_seventh[0].price_czk == 3250
    assert foil_seventh[0].set_code == "7ed"


@pytest.mark.asyncio
@respx.mock
async def test_buying_only_false_includes_unwanted() -> None:
    respx.post(BUYOUT_URL).mock(
        return_value=httpx.Response(200, json=_load("cr_buylist_coat.json"))
    )
    offers = await buylist_cernyrytir("Coat of Arms", buying_only=False)
    assert any(o.edition == "Not Wanted" for o in offers)
    assert any(o.language == "JA" for o in offers)
    assert any(o.card_name == "Strength of Arms" for o in offers)


@pytest.mark.asyncio
@respx.mock
async def test_buying_only_drops_printings_with_no_want() -> None:
    respx.post(BUYOUT_URL).mock(
        return_value=httpx.Response(200, json=_load("cr_buylist_coat.json"))
    )
    offers = await buylist_cernyrytir("Coat", buying_only=True)
    assert not any(o.edition == "Not Wanted" for o in offers)
    assert not any(o.card_name == "Strength of Arms" for o in offers)


@pytest.mark.asyncio
@respx.mock
async def test_empty_result_returns_empty_list() -> None:
    respx.post(BUYOUT_URL).mock(return_value=httpx.Response(200, json=_load("cr_empty.json")))
    assert await buylist_cernyrytir("xyzzy") == []


@pytest.mark.asyncio
@respx.mock
async def test_uses_buyout_url() -> None:
    route = respx.post(BUYOUT_URL).mock(
        return_value=httpx.Response(200, json=_load("cr_empty.json"))
    )
    await buylist_cernyrytir("Coat")
    assert route.called
    assert "eshop_mode=BUYOUT" in str(route.calls.last.request.url)


@pytest.mark.asyncio
@respx.mock
async def test_http_error_raises(no_sleep: None) -> None:
    respx.post(BUYOUT_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(ShopRequestError) as exc:
        await buylist_cernyrytir("Coat", retry=RetryPolicy(attempts=2))
    assert exc.value.shop == "cernyrytir"


@pytest.mark.asyncio
@respx.mock
async def test_missing_list_raises() -> None:
    respx.post(BUYOUT_URL).mock(return_value=httpx.Response(200, json={"pagination": {}}))
    with pytest.raises(ShopParseError):
        await buylist_cernyrytir("Bad")
