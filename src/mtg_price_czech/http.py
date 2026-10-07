"""Shared HTTP helpers: User-Agent, JSON decode, and loud retries."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from mtg_price_czech.errors import ShopParseError, ShopRequestError


def user_agent() -> str:
    """Build UA at call time so importing this module does not cycle through __init__."""
    from mtg_price_czech import __version__

    return (
        f"mtg-price-czech/{__version__} "
        "(+https://github.com/rfrecer/mtg-price-czech; personal price checks, low volume)"
    )

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    """How many times to try a request and how long to wait between tries."""

    attempts: int = 4
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0


DEFAULT_RETRY = RetryPolicy()


def default_client() -> httpx.AsyncClient:
    """Short-lived client used when the caller does not supply one."""
    return httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        headers={
            "Accept": "application/json",
            "User-Agent": user_agent(),
        },
    )


def _retry_delay(retry: RetryPolicy, attempt_index: int, response: httpx.Response | None) -> float:
    """Delay before retry attempt_index (1-based retry number after a failure)."""
    delay = min(retry.base_delay_s * (2 ** (attempt_index - 1)), retry.max_delay_s)
    if response is not None and response.status_code == 429:
        raw = response.headers.get("Retry-After")
        if raw is not None:
            try:
                delay = min(float(raw), retry.max_delay_s)
            except ValueError:
                pass
    return delay


async def request_json(
    shop: str,
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    retry: RetryPolicy = DEFAULT_RETRY,
    **kwargs: Any,
) -> Any:
    """Perform an HTTP request and return decoded JSON, or raise a typed shop error.

    Retries transport errors and selected server/rate-limit statuses. Invalid JSON
    after a successful status is never retried: that is a parse failure, not a blip.
    """
    attempts = max(1, retry.attempts)
    last_error: str | None = None
    headers = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("User-Agent", user_agent())
    kwargs["headers"] = headers

    for attempt in range(1, attempts + 1):
        response: httpx.Response | None = None
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt >= attempts:
                raise ShopRequestError(
                    shop,
                    f"gave up after {attempts} attempts; last error {last_error}; url={url}",
                ) from exc
            await asyncio.sleep(_retry_delay(retry, attempt, None))
            continue

        status = response.status_code
        if status in RETRYABLE_STATUS:
            last_error = f"HTTP {status}"
            if attempt >= attempts:
                raise ShopRequestError(
                    shop,
                    f"gave up after {attempts} attempts; last error {last_error}; url={url}",
                )
            await asyncio.sleep(_retry_delay(retry, attempt, response))
            continue

        if status >= 400:
            raise ShopRequestError(shop, f"HTTP {status}; url={url}")

        try:
            return response.json()
        except ValueError as exc:
            raise ShopParseError(shop, f"response is not valid JSON; url={url}") from exc

    raise ShopRequestError(
        shop,
        f"gave up after {attempts} attempts; last error {last_error}; url={url}",
    )


def require_mapping(shop: str, value: Any, what: str) -> Mapping[str, Any]:
    """Require a JSON object; raise ShopParseError otherwise."""
    if not isinstance(value, Mapping):
        raise ShopParseError(shop, f"{what} is not an object (API changed?)")
    return value


def parse_number(shop: str, value: Any, field: str, card_name: str) -> float:
    """Parse a numeric field; refuse silent coercion failures."""
    if isinstance(value, bool) or value is None:
        raise ShopParseError(shop, f"unparsable {field} for {card_name!r}: {value!r}")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ShopParseError(shop, f"unparsable {field} for {card_name!r}: {value!r}") from exc
