# mtg-price-czech

Small async Python library that fetches Magic: The Gathering single-card **sell** and **buylist** prices from two Czech shops: [Najada](https://najada.games) and [Černý Rytíř](https://www.cernyrytir.cz).

Unofficial, for personal price checks. Not affiliated with either shop. Use politely and at low volume.

## Install

```bash
pip install "mtg-price-czech @ git+https://github.com/rfrecer/mtg-price-czech"
```

Requires Python 3.10+. Runtime dependency: `httpx`.

## Usage

```python
import asyncio

from mtg_price_czech import (
    ShopError,
    buylist_cernyrytir,
    buylist_najada,
    search_cernyrytir,
    search_najada,
)


async def main() -> None:
    try:
        najada = await search_najada("Coat of Arms")
        cerny = await search_cernyrytir("Coat of Arms")
        najada_buy = await buylist_najada("Coat of Arms")
        cerny_buy = await buylist_cernyrytir("Coat of Arms")
    except ShopError as exc:
        # Network/HTTP/parse failure: never treat this as "no stock" / "not buying".
        raise SystemExit(f"shop failure: {exc}") from exc

    for offer in najada + cerny:
        print(
            offer.shop,
            offer.card_name,
            offer.edition,
            offer.set_code,
            offer.price_czk,
            offer.stock_qty,
            "foil" if offer.foil else "nonfoil",
        )

    for offer in najada_buy + cerny_buy:
        print(
            offer.shop,
            offer.card_name,
            offer.set_code,
            offer.condition,
            offer.price_czk,
            offer.want_qty,
            "foil" if offer.foil else "nonfoil",
        )


asyncio.run(main())
```

Sell connectors (`search_*`) accept:

- `in_stock_only=True`: drop offers with `stock_qty <= 0` (Černý Rytíř also sends this flag to the API).
- `client`: optional `httpx.AsyncClient` used as-is and not closed. If omitted, a short-lived client is created (30s timeout, redirects followed, JSON Accept, library User-Agent).
- `retry`: a `RetryPolicy` (default 4 attempts, exponential backoff).

Buylist connectors (`buylist_*`) share `client` / `retry` and add:

- `buying_only=True`: keep only printings the shop is actively buying. Najada drops `want_qty <= 0`. Černý Rytíř keeps condition rows when any sibling on the same card with the same language+foil has `want_qty > 0` (so LP/PL prices appear when NM is wanted).

## Offer fields (sell)

| Field | Meaning |
| --- | --- |
| `shop` | `"najada"` or `"cernyrytir"` |
| `card_name` | Name as the shop spells it (may include suffixes like `(V.1)` or `(Borderless)`) |
| `edition` | Human-readable set name if given |
| `set_code` | Lowercase set code if given |
| `foil` / `etched` / `borderless` | Finish flags |
| `price_czk` | Whole CZK (`round` of the shop float) |
| `stock_qty` | Units available |

## BuylistOffer fields

One row per **printing + condition** (flat), for joining against a tradelist at that grain.

| Field | Meaning |
| --- | --- |
| `shop` | `"najada"` or `"cernyrytir"` |
| `card_name` / `edition` / `set_code` | Same idea as sell |
| `foil` / `etched` / `borderless` | Finish flags |
| `condition` | Shop-native grade: Najada `NM`/`EX`/`GD`/`PL`/`HP`; CR `NM`/`LP`/`PL` |
| `price_czk` | Buy price in whole CZK |
| `want_qty` | How many copies the shop wants |
| `language` | CR language code (`EN`, `JA`, …); Najada English prices use `"EN"` |

No automatic grade translation between shops. A tradelist `LP` matches Černý Rytíř but not Najada `EX`/`GD`.

Connectors are intentionally dumb. They do not call Scryfall, apply foil/edition policy, pick a cheapest copy, or exact-match names. Both shops do substring search, so results can include other cards whose names contain the query. The caller filters.

### Tradelist CSV helper

[`scripts/buylist_tradelist.py`](scripts/buylist_tradelist.py) reads a CSV with columns `name`, `set_code`, `foil`, `condition` (optional `language`, default `EN`), queries both buylists, and writes an enriched CSV with `najada_price_czk`, `najada_want_qty`, `cernyrytir_price_czk`, `cernyrytir_want_qty`.

```bash
python scripts/buylist_tradelist.py tradelist.csv -o enriched.csv
```

## Empty list vs exceptions

This is the rule that matters:

- An empty list means the shop answered successfully and has nothing matching (after optional in-stock / buying-only filtering).
- Any network error, HTTP error status, invalid JSON, or unexpected payload shape raises a typed exception. Outages must never look like out of stock / not buying.

Hierarchy: `ShopError` (has `.shop`), `ShopRequestError` (transport / HTTP), `ShopParseError` (JSON / shape).

## Retry policy

`RetryPolicy(attempts=4, base_delay_s=0.5, max_delay_s=8.0)` retries `httpx.TransportError` and HTTP 429 / 500 / 502 / 503 / 504. Delay before retry `n` is `base * 2**(n-1)`, capped, and a numeric `Retry-After` is honored (also capped). Other 4xx fail immediately. Invalid JSON after a successful status fails immediately with no retry.

The library does not sleep between successful calls. Callers that batch many names should pace themselves.

## Endpoints

Undocumented shop APIs; they may change, which is why parsing is strict and raises `ShopParseError`:

- Najada sell (Wizardshop catalog): `GET https://wizardshop.cz/api/v1/najada2/catalog/mtg-singles/?q=...&limit=100` with `Origin` / `Referer` for `najada.games`. Pagination is Django REST style (`count` / `next` with `offset`). English card names live in the `name` field.
- Najada buylist: `GET https://wizardshop.cz/api/v1/najada2/buylist-offers/?game_id=<mtg>&enabled=true&q=...&buy_price_min=0&buy_price_max=10000000&o=name&limit=100`. Condition prices come from `prices.CZK.en`.
- Černý Rytíř sell: `POST https://eshop-api.cernyrytir.eu/api/public/card/list?eshop_mode=STANDARD` with a full JSON filter body. Pagination uses `pagination.page` and `pagination.rowsNumber` (100 rows per page).
- Černý Rytíř buylist: same endpoint with `eshop_mode=BUYOUT`; buy fields are `priceBuy`, `wantQty`, and `condition` on each `internalCards` entry.

Every request sends a descriptive User-Agent:
`mtg-price-czech/<version> (+https://github.com/rfrecer/mtg-price-czech; personal price checks, low volume)`.

## Credits

Najada's Wizardshop catalog endpoint was documented in Robin Vyslouzil's
[czech-mtg-price-comparator](https://github.com/xvyslo05/czech-mtg-price-comparator) (MIT). See also `NOTICE`.

## License

MIT. Copyright (c) 2026 Robert Frecer.
