"""Fetch MTG single-card prices from Najada and Černý Rytíř."""

from __future__ import annotations

from mtg_price_czech.cernyrytir import buylist_cernyrytir, search_cernyrytir
from mtg_price_czech.errors import ShopError, ShopParseError, ShopRequestError
from mtg_price_czech.http import DEFAULT_RETRY, RetryPolicy
from mtg_price_czech.models import BuylistOffer, Offer
from mtg_price_czech.najada import buylist_najada, search_najada

__version__ = "0.1.0"

__all__ = [
    "Offer",
    "BuylistOffer",
    "RetryPolicy",
    "DEFAULT_RETRY",
    "ShopError",
    "ShopRequestError",
    "ShopParseError",
    "search_najada",
    "search_cernyrytir",
    "buylist_najada",
    "buylist_cernyrytir",
    "__version__",
]
