"""Shared offer model returned by shop connectors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Offer:
    """One sellable article from a Czech shop search result."""

    shop: str
    card_name: str
    edition: str | None
    set_code: str | None
    foil: bool
    price_czk: int
    stock_qty: int
    etched: bool = False
    borderless: bool = False
