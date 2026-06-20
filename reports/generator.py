"""
reports/generator.py
--------------------
Queries the `top_deals` Supabase view and prints a ranked deal table.

Usage:
    python -m reports.generator           # top 10, table format
    python -m reports.generator --top 25
    python -m reports.generator --whatsapp  # WhatsApp-ready text with Claude narratives
"""

import argparse
import os

from dotenv import load_dotenv
import anthropic

from database.client import get_client

load_dotenv()


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


def generate_narrative(deal: dict, client: anthropic.Anthropic) -> str:
    """Call Claude to write a 2-sentence brief for a property deal."""
    village = deal.get("village") or "Unknown location"
    ptype = deal.get("property_type") or "property"
    price = format_price(deal.get("listed_price"))
    sqft = f"{deal.get('area_sqft'):.0f} sqft" if deal.get("area_sqft") else "area unknown"
    ppsf = format_ppsf(deal.get("listed_price"), deal.get("area_sqft"))
    score = deal.get("composite_score", 0)
    sm = deal.get("seller_motivation_score", 0)
    vg = deal.get("value_gap_score", 0)
    loc = deal.get("location_score", 0)
    reno = deal.get("reno_complexity_score", 0)
    dom = deal.get("days_on_market", 0) or 0
    flags_raw = deal.get("flags") or []
    if isinstance(flags_raw, str):
        import json
        try:
            flags_raw = json.loads(flags_raw)
        except Exception:
            flags_raw = [flags_raw]
    flags_str = ", ".join(flags_raw) if flags_raw else "none"

    prompt = (
        f"You are a Goa real estate analyst. Write exactly 2 sentences about this property deal "
        f"for a buyer reading on WhatsApp. Plain English only, no markdown, no emojis.\n\n"
        f"Property: {ptype} in {village}, Goa\n"
        f"Price: {price} | Size: {sqft} | Rs/sqft: {ppsf}\n"
        f"Deal score: {score:.1f}/100 "
        f"(seller motivation={sm:.0f}, value gap={vg:.0f}, location={loc:.0f}, reno={reno:.0f})\n"
        f"Days on market: {dom} | Signals: {flags_str}\n\n"
        f"Sentence 1: What makes this deal interesting.\n"
        f"Sentence 2: What to watch out for."
    )

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=120,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


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


def print_whatsapp_report(deals: list[dict]) -> None:
    """Print a WhatsApp-ready report with Claude-generated narratives per property."""
    if not deals:
        print("\nNo scored deals found. Run `python -m scoring.scorer` first.\n")
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("\n[ERROR] ANTHROPIC_API_KEY not set in .env\n")
        return

    ai = anthropic.Anthropic(api_key=api_key)

    print("\n")
    print("*GOA DEAL ENGINE - TOP 10 PICKS*")
    print("Generated: June 2026")
    print("=" * 40)

    for i, d in enumerate(deals, 1):
        village = d.get("village") or "Unknown"
        ptype = (d.get("property_type") or "property").title()
        price = format_price(d.get("listed_price"))
        sqft = f"{d.get('area_sqft'):.0f} sqft" if d.get("area_sqft") else "N/A"
        ppsf = format_ppsf(d.get("listed_price"), d.get("area_sqft"))
        score = d.get("composite_score", 0)
        dom = d.get("days_on_market") or 0
        url = d.get("listing_url") or ""
        flags_raw = d.get("flags") or []
        if isinstance(flags_raw, str):
            import json
            try:
                flags_raw = json.loads(flags_raw)
            except Exception:
                flags_raw = [flags_raw]

        print(f"\n*#{i} - {village} ({ptype})*")
        print(f"Price: {price}  |  {sqft}  |  Rs {ppsf}/sqft")
        print(f"Deal Score: {score:.1f}/100  |  DOM: {dom} days")
        if flags_raw:
            print(f"Signals: {', '.join(flags_raw)}")
        if url:
            print(f"Link: {url}")

        print("\nAnalysis:")
        try:
            narrative = generate_narrative(d, ai)
            print(narrative)
        except Exception as exc:
            print(f"[narrative unavailable: {exc}]")

        print("-" * 40)

    print("\n*End of Report*")
    print("Scores: SM=seller motivation, VG=value gap, Loc=location (all 0-100)")
    print("Run `python -m scoring.scorer` to refresh scores.\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=10, help="Number of deals to show")
    parser.add_argument("--whatsapp", action="store_true",
                        help="Output WhatsApp-ready format with Claude AI narratives")
    args = parser.parse_args()

    print(f"\nFetching top {args.top} deals from Supabase...")
    deals = fetch_top_deals(args.top)

    if args.whatsapp:
        print(f"Generating WhatsApp report with Claude narratives for {len(deals)} properties...\n")
        print_whatsapp_report(deals)
    else:
        print_report(deals)


if __name__ == "__main__":
    main()
