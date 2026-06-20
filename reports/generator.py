"""
reports/generator.py
--------------------
Queries the `top_deals` Supabase view and prints a ranked deal table.

Usage:
    python -m reports.generator           # top 10
    python -m reports.generator --top 25
"""

import argparse
from database.client import get_client


def fetch_top_deals(limit: int = 10) -> list[dict]:
    client = get_client()
    resp = (
        client.table("top_deals")
        .select("*")
        .order("composite_score", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data


def format_price(price) -> str:
    if price is None:
        return "N/A"
    if price >= 1e7:
        return f"{price/1e7:.2f} Cr"
    return f"{price/1e5:.1f} Lac"


def format_ppsf(price, sqft) -> str:
    if not price or not sqft or sqft == 0:
        return "N/A"
    return f"{price/sqft:,.0f}"


def top_flag(flags) -> str:
    if not flags:
        return "-"
    priority = [
        "motivated_seller", "relisted", "long_dom",
        "price_dropped", "below_area_avg", "premium_location", "likely_needs_work",
    ]
    if isinstance(flags, str):
        import json
        try:
            flags = json.loads(flags)
        except Exception:
            return flags[:20]
    for p in priority:
        for f in flags:
            if p in f:
                return f
    return flags[0] if flags else "-"


def print_report(deals: list[dict]) -> None:
    if not deals:
        print("\n  No scored deals found. Run `python -m scoring.scorer` first.\n")
        return

    col = {
        "rank":   4,
        "village": 26,
        "type":    6,
        "price":  10,
        "sqft":    7,
        "ppsf":    9,
        "comp":    6,
        "sm":      5,
        "vg":      5,
        "loc":     5,
        "signal": 22,
    }

    header = (
        f"{'#':<{col['rank']}} "
        f"{'Village':<{col['village']}} "
        f"{'Type':<{col['type']}} "
        f"{'Price':>{col['price']}} "
        f"{'sqft':>{col['sqft']}} "
        f"{'Rs/sqft':>{col['ppsf']}} "
        f"{'Score':>{col['comp']}} "
        f"{'SM':>{col['sm']}} "
        f"{'VG':>{col['vg']}} "
        f"{'Loc':>{col['loc']}} "
        f"{'Top Signal':<{col['signal']}}"
    )
    divider = "-" * len(header)

    print(f"\n{'='*len(header)}")
    print(f"  GOA DEAL ENGINE -- TOP {len(deals)} PROPERTIES BY COMPOSITE SCORE")
    print(f"{'='*len(header)}")
    print(header)
    print(divider)

    for i, d in enumerate(deals, 1):
        village  = (d.get("village") or "?")[:col["village"]]
        ptype    = (d.get("property_type") or "?")[:col["type"]]
        price    = format_price(d.get("listed_price"))
        sqft     = f"{d.get('area_sqft'):.0f}" if d.get("area_sqft") else "N/A"
        ppsf     = format_ppsf(d.get("listed_price"), d.get("area_sqft"))
        comp     = f"{d.get('composite_score', 0):.1f}"
        sm       = f"{d.get('seller_motivation_score', 0):.0f}"
        vg       = f"{d.get('value_gap_score', 0):.0f}"
        loc      = f"{d.get('location_score', 0):.0f}"
        signal   = top_flag(d.get("flags"))

        print(
            f"{i:<{col['rank']}} "
            f"{village:<{col['village']}} "
            f"{ptype:<{col['type']}} "
            f"{price:>{col['price']}} "
            f"{sqft:>{col['sqft']}} "
            f"{ppsf:>{col['ppsf']}} "
            f"{comp:>{col['comp']}} "
            f"{sm:>{col['sm']}} "
            f"{vg:>{col['vg']}} "
            f"{loc:>{col['loc']}} "
            f"{signal:<{col['signal']}}"
        )

    print(divider)
    print(f"  SM=seller_motivation  VG=value_gap  Loc=location  Score=composite (0-100)")
    print(f"{'='*len(header)}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=10, help="Number of deals to show")
    args = parser.parse_args()

    print(f"\nFetching top {args.top} deals from Supabase...")
    deals = fetch_top_deals(args.top)
    print_report(deals)


if __name__ == "__main__":
    main()
