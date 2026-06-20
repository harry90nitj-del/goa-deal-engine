"""
scrapers/igr_scraper.py
-----------------------
Scrapes recent property registrations from the NGDRS Goa portal
(https://ngdrsgoa.gov.in) and stores them in the `transactions` table.

NETWORK NOTE
------------
ngdrsgoa.gov.in is hosted on the Goa State Data Centre and is only
reachable from within India.  Running this from an Indian IP / VPN works
fine.  Outside India it returns ERR_CONNECTION_TIMED_OUT.

Usage:
    python -m scrapers.igr_scraper                     # North Goa, last 30 days
    python -m scrapers.igr_scraper --district south --days 60
    python -m scrapers.igr_scraper --preview            # print 5 rows, no DB write
"""

import re
import time
import argparse
from datetime import date, datetime, timedelta, timezone

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from database.client import get_client

# ── Constants ────────────────────────────────────────────────────────────────

IGR_BASE      = "https://ngdrsgoa.gov.in"
SEARCH_PATH   = "/AxisSoft/CivilSearch"   # known working path on the portal
SEARCH_URL    = IGR_BASE + SEARCH_PATH

DISTRICT_MAP = {
    "north": "North Goa",
    "south": "South Goa",
}

DEED_TYPE_MAP = {
    "sale deed":         "sale",
    "deed of sale":      "sale",
    "gift deed":         "gift",
    "deed of gift":      "gift",
    "partition deed":    "partition",
    "mortgage deed":     "mortgage",
    "lease deed":        "lease",
    "agreement to sell": "sale",
}

# ── Parsers ──────────────────────────────────────────────────────────────────

def normalise_deed_type(raw: str) -> str:
    raw_lower = raw.lower().strip()
    for pattern, dtype in DEED_TYPE_MAP.items():
        if pattern in raw_lower:
            return dtype
    return "other"


def parse_registration_price(raw: str) -> float | None:
    """Convert 'Rs. 45,00,000' or '4500000' to float."""
    raw = re.sub(r"[^\d.]", "", raw)
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def parse_reg_date(raw: str) -> str | None:
    """Accept dd/mm/yyyy, dd-mm-yyyy, or yyyy-mm-dd → ISO yyyy-mm-dd."""
    raw = raw.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def parse_survey_no(raw: str) -> str:
    """Normalise survey/CTS numbers — strip whitespace, upper-case."""
    return re.sub(r"\s+", " ", raw.strip()).upper()


# ── Scrape ───────────────────────────────────────────────────────────────────

def scrape_igr(district: str = "north", days_back: int = 30, limit: int = 50) -> list[dict]:
    """
    Submit the NGDRS search form for recent sale deeds in the specified
    district and return a list of parsed transaction dicts.
    """
    from_date = (date.today() - timedelta(days=days_back)).strftime("%d/%m/%Y")
    to_date   = date.today().strftime("%d/%m/%Y")
    district_label = DISTRICT_MAP.get(district.lower(), "North Goa")

    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()

        print(f"  Connecting to {SEARCH_URL} …")
        try:
            page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=20000)
        except PWTimeout:
            print(
                "  [NETWORK] ngdrsgoa.gov.in timed out.\n"
                "  This site is only reachable from within India.\n"
                "  Connect via an Indian VPN and re-run."
            )
            browser.close()
            return []

        page.wait_for_timeout(3000)

        # ── Fill the search form ──────────────────────────────────────────────
        # The form fields and their names vary by NGDRS version.
        # We try the most common selectors used by the Goa deployment.
        try:
            # district / taluka dropdown
            for sel in ["select[name='districtId']", "select[name='district']", "#district"]:
                el = page.query_selector(sel)
                if el:
                    page.select_option(sel, label=district_label)
                    page.wait_for_timeout(800)
                    break

            # date range
            for sel in ["input[name='fromDate']", "input[name='dateFrom']", "#fromDate"]:
                if page.query_selector(sel):
                    page.fill(sel, from_date)
                    break
            for sel in ["input[name='toDate']", "input[name='dateTo']", "#toDate"]:
                if page.query_selector(sel):
                    page.fill(sel, to_date)
                    break

            # submit
            for sel in ["button[type='submit']", "input[type='submit']", "#searchBtn"]:
                if page.query_selector(sel):
                    page.click(sel)
                    break

            page.wait_for_timeout(5000)
        except Exception as exc:
            print(f"  [WARN] form interaction failed: {exc}")

        # ── Parse results table ───────────────────────────────────────────────
        rows = page.query_selector_all("table tbody tr")
        print(f"  Table rows found: {len(rows)}")

        for row in rows[:limit]:
            try:
                cells = [td.inner_text().strip() for td in row.query_selector_all("td")]
                if len(cells) < 5:
                    continue

                # Column order observed on ngdrsgoa portal (may shift by version):
                # 0: Sr, 1: Reg No, 2: Reg Date, 3: Taluka, 4: Village,
                # 5: Survey No, 6: Deed Type, 7: Consideration Amount, 8: Parties
                reg_no      = cells[1] if len(cells) > 1 else ""
                reg_date    = parse_reg_date(cells[2]) if len(cells) > 2 else None
                taluka      = cells[3].strip() if len(cells) > 3 else ""
                village     = cells[4].strip() if len(cells) > 4 else ""
                survey_raw  = cells[5] if len(cells) > 5 else ""
                deed_raw    = cells[6] if len(cells) > 6 else ""
                price_raw   = cells[7] if len(cells) > 7 else ""

                results.append({
                    "reg_no":      reg_no,
                    "survey_no":   parse_survey_no(survey_raw) if survey_raw else reg_no,
                    "taluka":      taluka,
                    "village":     village,
                    "deed_type":   normalise_deed_type(deed_raw),
                    "price":       parse_registration_price(price_raw),
                    "reg_date":    reg_date,
                    "raw_deed":    deed_raw,
                })
            except Exception as exc:
                print(f"  [WARN] skipping row: {exc}")

        browser.close()

    return results


# ── DB write ─────────────────────────────────────────────────────────────────

def store_transactions(records: list[dict]) -> int:
    """
    For each IGR record:
      1. Upsert a property stub (survey_no + taluka + village).
      2. Insert into transactions.
    Returns number of rows written.
    """
    client   = get_client()
    written  = 0
    now_iso  = datetime.now(timezone.utc).isoformat()

    for rec in records:
        if not rec.get("price") or not rec.get("reg_date"):
            continue

        try:
            prop_resp = (
                client.table("properties")
                .upsert(
                    {
                        "survey_no":     rec["survey_no"],
                        "taluka":        rec["taluka"] or "Unknown",
                        "village":       rec["village"] or "Unknown",
                        "property_type": "residential",
                    },
                    on_conflict="survey_no,taluka,village",
                )
                .execute()
            )
            property_id = prop_resp.data[0]["id"]
        except Exception as exc:
            print(f"  [ERR] property upsert: {exc}")
            continue

        try:
            client.table("transactions").insert(
                {
                    "property_id":      property_id,
                    "transaction_date": rec["reg_date"],
                    "price":            rec["price"],
                    "deed_type":        rec["deed_type"],
                    "registered_at":    now_iso,
                }
            ).execute()
            written += 1
        except Exception as exc:
            print(f"  [ERR] transaction insert: {exc}")

    return written


# ── Preview printer ───────────────────────────────────────────────────────────

def print_preview(records: list[dict], n: int = 5) -> None:
    print(f"\n{'='*90}")
    print(f"  IGR DATA PREVIEW — first {min(n, len(records))} rows")
    print(f"{'='*90}")
    hdr = f"{'Survey No':<20} {'Taluka':<14} {'Village':<16} {'Type':<8} {'Price (INR)':>14}  {'Reg Date'}"
    print(hdr)
    print("-" * 90)
    for rec in records[:n]:
        price = rec["price"]
        price_str = f"{price:,.0f}" if price else "N/A"
        print(
            f"{rec['survey_no'][:19]:<20} {rec['taluka'][:13]:<14} "
            f"{rec['village'][:15]:<16} {rec['deed_type']:<8} "
            f"{price_str:>14}  {rec['reg_date'] or 'N/A'}"
        )
    print(f"{'='*90}\n")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape NGDRS Goa IGR registrations")
    parser.add_argument("--district", default="north", choices=["north", "south"],
                        help="Goa district to query (default: north)")
    parser.add_argument("--days", type=int, default=30,
                        help="Look-back window in days (default: 30)")
    parser.add_argument("--limit", type=int, default=50,
                        help="Max rows to scrape per run (default: 50)")
    parser.add_argument("--preview", action="store_true",
                        help="Print first 5 rows and exit without writing to DB")
    args = parser.parse_args()

    print(f"\nScraping NGDRS Goa — {args.district.title()} Goa, last {args.days} days\n")
    records = scrape_igr(district=args.district, days_back=args.days, limit=args.limit)

    if not records:
        print("No records returned (likely network issue — see NETWORK NOTE in module docstring).")
        return

    print_preview(records, n=5)

    if args.preview:
        print(f"Preview mode — {len(records)} rows fetched, nothing written to DB.")
        return

    print(f"Writing {len(records)} transactions to Supabase…")
    written = store_transactions(records)
    print(f"Done — {written} rows inserted into `transactions` table.")


if __name__ == "__main__":
    main()
