"""Shared offer models returned by shop connectors."""

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


@dataclass(frozen=True)
class BuylistOffer:
    """One buylist price for a printing at a single condition."""

    shop: str
    card_name: str
    edition: str | None
    set_code: str | None
    foil: bool
    condition: str
    price_czk: int
    want_qty: int
    etched: bool = False
    borderless: bool = False
    language: str | None = None
