#!/usr/bin/env python3
"""Enrich a printing+condition tradelist CSV with Najada and Černý Rytíř buylist prices.

Expected input columns: name, set_code, foil, condition
Optional: language (default EN)

Condition codes are shop-native and not remapped (Najada: NM/EX/GD/PL/HP;
Černý Rytíř: NM/LP/PL). A tradelist LP row will match CR but not Najada.

Example:
  python scripts/buylist_tradelist.py tradelist.csv -o enriched.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

from mtg_price_czech import (
    BuylistOffer,
    ShopError,
    buylist_cernyrytir,
    buylist_najada,
)

PACE_S = 1.2


def _parse_foil(raw: str) -> bool:
    value = raw.strip().lower()
    return value in {"1", "true", "yes", "y", "foil"}


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _match_offer(
    offers: list[BuylistOffer],
    *,
    name: str,
    set_code: str,
    foil: bool,
    condition: str,
    language: str,
) -> BuylistOffer | None:
    name_l = name.strip().lower()
    set_l = _norm(set_code)
    cond_l = _norm(condition)
    lang_l = _norm(language) or "en"

    candidates = [
        o
        for o in offers
        if _norm(o.set_code) == set_l
        and o.foil is foil
        and _norm(o.condition) == cond_l
        and (o.language is None or _norm(o.language) == lang_l)
        and name_l in o.card_name.lower()
    ]
    if not candidates:
        return None
    exact = [o for o in candidates if o.card_name.lower() == name_l]
    pool = exact or candidates
    # Prefer higher want_qty, then higher price, for stable deterministic picks.
    pool.sort(key=lambda o: (o.want_qty, o.price_czk), reverse=True)
    return pool[0]


async def _fetch_all(names: list[str]) -> tuple[dict[str, list[BuylistOffer]], dict[str, list[BuylistOffer]]]:
    najada: dict[str, list[BuylistOffer]] = {}
    cerny: dict[str, list[BuylistOffer]] = {}
    for i, name in enumerate(names):
        if i:
            await asyncio.sleep(PACE_S)
        najada[name] = await buylist_najada(name)
        await asyncio.sleep(PACE_S)
        cerny[name] = await buylist_cernyrytir(name)
    return najada, cerny


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="tradelist CSV path")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="enriched CSV output path",
    )
    args = parser.parse_args(argv)

    with args.input.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            print("FAIL: empty CSV", file=sys.stderr)
            return 1
        required = {"name", "set_code", "foil", "condition"}
        missing = required - {h.strip() for h in reader.fieldnames}
        if missing:
            print(f"FAIL: missing columns: {sorted(missing)}", file=sys.stderr)
            return 1
        rows = list(reader)
        fieldnames = list(reader.fieldnames)

    names = sorted({(row.get("name") or "").strip() for row in rows if (row.get("name") or "").strip()})
    try:
        najada_by_name, cerny_by_name = asyncio.run(_fetch_all(names))
    except ShopError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    out_fields = fieldnames + [
        "najada_price_czk",
        "najada_want_qty",
        "cernyrytir_price_czk",
        "cernyrytir_want_qty",
    ]
    with args.output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            name = (row.get("name") or "").strip()
            set_code = row.get("set_code") or ""
            foil = _parse_foil(row.get("foil") or "")
            condition = row.get("condition") or ""
            language = (row.get("language") or "EN").strip() or "EN"

            n_offer = _match_offer(
                najada_by_name.get(name, []),
                name=name,
                set_code=set_code,
                foil=foil,
                condition=condition,
                language=language,
            )
            c_offer = _match_offer(
                cerny_by_name.get(name, []),
                name=name,
                set_code=set_code,
                foil=foil,
                condition=condition,
                language=language,
            )
            out = dict(row)
            out["najada_price_czk"] = "" if n_offer is None else n_offer.price_czk
            out["najada_want_qty"] = "" if n_offer is None else n_offer.want_qty
            out["cernyrytir_price_czk"] = "" if c_offer is None else c_offer.price_czk
            out["cernyrytir_want_qty"] = "" if c_offer is None else c_offer.want_qty
            writer.writerow(out)

    print(f"OK: wrote {len(rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
