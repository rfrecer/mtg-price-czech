#!/usr/bin/env python3
"""Live smoke check against both shops (not part of the pytest suite)."""

from __future__ import annotations

import asyncio
import sys

from mtg_price_czech import (
    ShopError,
    buylist_cernyrytir,
    buylist_najada,
    search_cernyrytir,
    search_najada,
)


async def main() -> int:
    name = "Coat of Arms"
    try:
        najada = await search_najada(name)
        await asyncio.sleep(1.2)
        cerny = await search_cernyrytir(name)
        await asyncio.sleep(1.2)
        najada_buy = await buylist_najada(name)
        await asyncio.sleep(1.2)
        cerny_buy = await buylist_cernyrytir(name)
    except ShopError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"najada: {len(najada)} offers")
    for o in najada[:5]:
        print(f"  {o.card_name!r} {o.edition} {o.set_code} {o.price_czk} CZK x{o.stock_qty} foil={o.foil}")
    print(f"cernyrytir: {len(cerny)} offers")
    for o in cerny[:5]:
        print(
            f"  {o.card_name!r} {o.edition} {o.set_code} {o.price_czk} CZK "
            f"x{o.stock_qty} foil={o.foil} etched={o.etched} borderless={o.borderless}"
        )
    print(f"najada buylist: {len(najada_buy)} rows")
    for o in najada_buy[:5]:
        print(
            f"  {o.card_name!r} {o.edition} {o.set_code} {o.condition} "
            f"{o.price_czk} CZK want={o.want_qty} foil={o.foil}"
        )
    print(f"cernyrytir buylist: {len(cerny_buy)} rows")
    for o in cerny_buy[:5]:
        print(
            f"  {o.card_name!r} {o.edition} {o.set_code} {o.condition} "
            f"{o.price_czk} CZK want={o.want_qty} foil={o.foil} lang={o.language}"
        )

    if not najada or not cerny:
        print("FAIL: expected non-empty sell results from both shops", file=sys.stderr)
        return 1
    if not najada_buy or not cerny_buy:
        print("FAIL: expected non-empty buylist results from both shops", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
