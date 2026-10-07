from __future__ import annotations

import httpx
import pytest
import respx

from mtg_price_czech.errors import ShopParseError, ShopRequestError
from mtg_price_czech.http import RetryPolicy, default_client, request_json, user_agent


URL = "https://example.test/api"


@pytest.mark.asyncio
@respx.mock
async def test_retry_then_success(no_sleep: None) -> None:
    route = respx.get(URL)
    route.side_effect = [
        httpx.Response(503),
        httpx.Response(503),
        httpx.Response(200, json={"ok": True}),
    ]
    async with httpx.AsyncClient() as client:
        data = await request_json("test", client, "GET", URL, retry=RetryPolicy(attempts=4))
    assert data == {"ok": True}
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_500_x4_raises_with_attempts(no_sleep: None) -> None:
    respx.get(URL).mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        with pytest.raises(ShopRequestError, match="gave up after 4 attempts") as exc:
            await request_json("test", client, "GET", URL, retry=RetryPolicy(attempts=4))
    assert exc.value.shop == "test"
    assert "HTTP 500" in str(exc.value)
    assert URL in str(exc.value)


@pytest.mark.asyncio
@respx.mock
async def test_404_raises_immediately_without_retry(no_sleep: None) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        with pytest.raises(ShopRequestError, match="HTTP 404"):
            await request_json("test", client, "GET", URL, retry=RetryPolicy(attempts=4))
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_429_honors_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    delays: list[float] = []

    async def capture(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("mtg_price_czech.http.asyncio.sleep", capture)
    route = respx.get(URL)
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(200, json={"ok": 1}),
    ]
    async with httpx.AsyncClient() as client:
        await request_json(
            "test",
            client,
            "GET",
            URL,
            retry=RetryPolicy(attempts=4, base_delay_s=0.5, max_delay_s=8.0),
        )
    assert delays == [3.0]


@pytest.mark.asyncio
@respx.mock
async def test_transport_error_retried_then_raised(no_sleep: None) -> None:
    route = respx.get(URL)
    route.side_effect = httpx.ConnectError("boom")
    async with httpx.AsyncClient() as client:
        with pytest.raises(ShopRequestError, match="gave up after 3 attempts") as exc:
            await request_json("test", client, "GET", URL, retry=RetryPolicy(attempts=3))
    assert "ConnectError" in str(exc.value)
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_invalid_json_raises_parse_error_no_retry(no_sleep: None) -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, text="not-json"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(ShopParseError, match="not valid JSON"):
            await request_json("test", client, "GET", URL, retry=RetryPolicy(attempts=4))
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_caller_supplied_client_is_not_closed() -> None:
    client = httpx.AsyncClient()
    assert not client.is_closed
    # Connectors close only clients they create; exercise via request_json path using
    # a supplied client through search would be ideal, but http itself never closes.
    with respx.mock:
        respx.get(URL).mock(return_value=httpx.Response(200, json={}))
        await request_json("test", client, "GET", URL)
    assert not client.is_closed
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_user_agent_header_sent() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json={}))
    async with default_client() as client:
        await request_json("test", client, "GET", URL)
    assert route.calls.last.request.headers["User-Agent"] == user_agent()
    assert "mtg-price-czech/" in user_agent()
