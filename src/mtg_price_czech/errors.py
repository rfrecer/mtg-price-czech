"""Typed errors so shop outages never look like empty stock."""

from __future__ import annotations


class ShopError(Exception):
    """Base error for a shop connector failure."""

    def __init__(self, shop: str, message: str) -> None:
        self.shop = shop
        super().__init__(f"{shop}: {message}")


class ShopRequestError(ShopError):
    """Network failure or unexpected HTTP status from a shop."""


class ShopParseError(ShopError):
    """Response body was not valid JSON or had an unexpected shape."""
