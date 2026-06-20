"""
scrapers/listings_scraper.py
----------------------------
Scrapes MagicBricks Goa property listings and stores results in
Supabase `properties` and `listings` tables.

Usage:
    python -m scrapers.listings_scraper            # default 100
    python -m scrapers.listings_scraper --limit 50
"""

import re
import argparse
from datetime import date, datetime, timezone

from playwright.sync_api import sync_playwright, Page

from database.client import get_client

PORTAL = "magicbricks"

# Rotate through Goa localities so we pull varied inventory when one page hits
# its card ceiling (~40 per search). Each URL targets a distinct sub-area.
SEARCH_PAGES = [
    (
        "https://www.magicbricks.com/property-for-sale/residential-real-estate"
        "?proptype=Multistorey-Apartment,Builder-Floor-Apartment,Penthouse,"
        "Studio-Apartment,Residential-House,Villa,Residential-Plot"
        "&Locality=Goa&cityName=Goa"
    ),
    (
        "https://www.magicbricks.com/property-for-sale/residential-real-estate"
        "?proptype=Multistorey-Apartment,Builder-Floor-Apartment,Penthouse,"
        "Studio-Apartment,Residential-House,Villa,Residential-Plot"
        "&Locality=North+Goa&cityName=Goa"
    ),
    (
        "https://www.magicbricks.com/property-for-sale/residential-real-estate"
        "?proptype=Multistorey-Apartment,Builder-Floor-Apartment,Penthouse,"
        "Studio-Apartment,Residential-House,Villa,Residential-Plot"
        "&Locality=South+Goa&cityName=Goa"
    ),
    (
        "https://www.magicbricks.com/property-for-sale/residential-real-estate"
        "?proptype=Multistorey-Apartment,Builder-Floor-Apartment,Penthouse,"
        "Studio-Apartment,Residential-House,Villa,Residential-Plot"
        "&Locality=Calangute&cityName=Goa"
    ),
    (
        "https://www.magicbricks.com/property-for-sale/residential-real-estate"
        "?proptype=Multistorey-Apartment,Builder-Floor-Apartment,Penthouse,"
        "Studio-Apartment,Residential-House,Villa,Residential-Plot"
        "&Locality=Panjim&cityName=Goa"
    ),
]

PROPERTY_TYPE_MAP = {
    "flat": "residential",
    "apartment": "residential",
    "villa": "residential",
    "house": "residential",
    "bungalow": "residential",
    "penthouse": "residential",
    "studio": "residential",
    "plot": "plot",
    "land": "plot",
    "commercial": "commercial",
    "office": "commercial",
    "shop": "commercial",
    "agricultural": "agricultural",
    "farm": "agricultural",
}


# ── parsers ──────────────────────────────────────────────────────────────────

def parse_price(raw: str) -> float | None:
    raw = raw.replace(",", "").replace("₹", "").replace("\n", " ").strip()
    try:
        if "Cr" in raw:
            return float(re.sub(r"[^\d.]", "", raw.split("Cr")[0].strip())) * 1e7
        if "Lac" in raw or "Lakh" in raw:
            return float(re.sub(r"[^\d.]", "", re.split(r"Lac|Lakh", raw)[0].strip())) * 1e5
        return float(re.sub(r"[^\d.]", "", raw)) if raw else None
    except (ValueError, IndexError):
        return None


def parse_area_sqft(raw: str) -> float | None:
    """Normalise sqft / sqm / sq yards to sqft."""
    raw = raw.replace(",", "").strip()
    m = re.search(r"([\d.]+)\s*(sqm|sq\.m|sqft|sq\.ft|sq ft|sq yards|sq\. yards?)", raw, re.IGNORECASE)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2).lower()
    if "sqm" in unit or "sq.m" in unit:
        val = round(val * 10.7639, 2)
    elif "yard" in unit:
        val = round(val * 9, 2)
    return val


def parse_dom(raw: str) -> int:
    """'Posted 3 days ago' -> 3.  'Posted 2 months ago' -> 60."""
    if not raw:
        return 0
    raw = raw.lower()
    m = re.search(r"(\d+)\s*day", raw)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*month", raw)
    if m:
        return int(m.group(1)) * 30
    m = re.search(r"(\d+)\s*year", raw)
    if m:
        return int(m.group(1)) * 365
    if "today" in raw or "just" in raw or "hour" in raw:
        return 0
    return 0


def map_property_type(title: str) -> str:
    title_lower = title.lower()
    for keyword, ptype in PROPERTY_TYPE_MAP.items():
        if keyword in title_lower:
            return ptype
    return "residential"


def extract_location(title: str, summary: dict) -> tuple[str, str]:
    """Return (village, taluka) from the listing title."""
    # Title pattern: "X BHK Flat for Sale in [Society, ]Village, Goa"
    m = re.search(r"(?:for (?:Sale|Rent) in )(.+)", title, re.IGNORECASE)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        # last part is typically "Goa", second-to-last is the area/village
        village = parts[-2] if len(parts) >= 2 else parts[0]
        taluka  = parts[-1] if len(parts) >= 1 else "Goa"
        return village, taluka
    return "Goa", "Goa"


def parse_summary(items: list[str]) -> dict:
    """Convert ['CARPET AREA\\n515 sqft', 'STATUS\\nReady to Move', …] to a dict."""
    out = {}
    for item in items:
        parts = item.split("\n", 1)
        if len(parts) == 2:
            out[parts[0].strip().upper()] = parts[1].strip()
    return out


# ── scrape helpers ───────────────────────────────────────────────────────────

def _card_url(card) -> str:
    """
    MagicBricks renders some card links only after hover/JS interaction,
    so not every card has an <a href>.  When no href is present we fall
    back to the numeric property ID encoded in id="propertiesAction{id}"
    on the summary container and construct a direct property URL from it.
    """
    for a_el in card.query_selector_all("a[href]"):
        href = a_el.get_attribute("href") or ""
        if href and "magicbricks.com" in href:
            return href
        if href.startswith("/") and "-pdpid-" in href:
            return f"https://www.magicbricks.com{href}"
    # fallback: numeric ID from the summary container
    summary_div = card.query_selector("[id^='propertiesAction']")
    if summary_div:
        el_id = summary_div.get_attribute("id") or ""
        prop_id = el_id.replace("propertiesAction", "").strip()
        if prop_id.isdigit():
            return f"https://www.magicbricks.com/property-view/{prop_id}"
    return ""


def _card_dom(card) -> int:
    """
    Days on market.  MagicBricks shows 'Updated 6 days ago' or
    'Posted 3 months ago' in .mb-srp__card__photo__fig--post.
    """
    el = card.query_selector(".mb-srp__card__photo__fig--post")
    return parse_dom(el.inner_text().strip() if el else "")


def _load_page(page: Page, url: str) -> list:
    """Navigate to url, scroll to trigger lazy-load, return all cards."""
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)
    for _ in range(6):
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        page.wait_for_timeout(1000)
    return page.query_selector_all(".mb-srp__card")


def _paginate(page: Page) -> bool:
    """
    Click the Next-page button if it exists and is enabled.
    MagicBricks renders a pagination container; we look for the active
    next-arrow inside it.
    Returns True if navigation happened, False if no next page found.
    """
    for sel in [
        ".mb-srp__pagination .icon-arrow-next",
        "[class*='pagination'] [class*='next']:not([disabled])",
        "[class*='pagination'] li:last-child a",
    ]:
        btn = page.query_selector(sel)
        if btn:
            try:
                btn.click()
                page.wait_for_timeout(4000)
                for _ in range(4):
                    page.evaluate("window.scrollBy(0, window.innerHeight)")
                    page.wait_for_timeout(900)
                return True
            except Exception:
                pass
    return False


# ── scrape ───────────────────────────────────────────────────────────────────

def _extract_cards(cards, seen_urls: set, limit_remaining: int) -> list[dict]:
    results = []
    for card in cards:
        if len(results) >= limit_remaining:
            break
        try:
            def t(sel: str) -> str:
                el = card.query_selector(sel)
                return el.inner_text().strip() if el else ""

            title     = t(".mb-srp__card--title")
            raw_price = t(".mb-srp__card__price--amount")
            if not title:
                continue

            summary_els = card.query_selector_all(".mb-srp__card__summary__list--item")
            summary_raw = [el.inner_text().strip() for el in summary_els]
            summary     = parse_summary(summary_raw)

            area_raw  = summary.get("CARPET AREA") or summary.get("SUPER AREA") or summary.get("PLOT AREA", "")
            listing_url = _card_url(card)

            if listing_url in seen_urls:
                continue
            seen_urls.add(listing_url)

            village, taluka = extract_location(title, summary)

            results.append({
                "title":          title,
                "price":          parse_price(raw_price),
                "area_sqft":      parse_area_sqft(area_raw),
                "property_type":  map_property_type(title),
                "village":        village,
                "taluka":         taluka,
                "days_on_market": _card_dom(card),
                "listing_url":    listing_url,
            })
        except Exception as exc:
            print(f"  [WARN] skipping card: {exc}")
    return results


def scrape_listings(limit: int = 100) -> list[dict]:
    results: list[dict] = []
    seen_urls: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-IN",
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = ctx.new_page()

        for page_url in SEARCH_PAGES:
            if len(results) >= limit:
                break
            print(f"  Loading: {page_url[-60:]}")
            cards = _load_page(page, page_url)
            print(f"  Cards on page: {len(cards)}")

            batch = _extract_cards(cards, seen_urls, limit - len(results))
            results.extend(batch)
            print(f"  Extracted {len(batch)} new -> running total {len(results)}")

            # try one level of in-page pagination before moving to next URL
            if len(results) < limit:
                if _paginate(page):
                    extra_cards = page.query_selector_all(".mb-srp__card")
                    extra = _extract_cards(extra_cards, seen_urls, limit - len(results))
                    results.extend(extra)
                    print(f"  +{len(extra)} from next page -> total {len(results)}")

        browser.close()

    print(f"\n  Total unique listings scraped: {len(results)}")
    return results


# ── database ─────────────────────────────────────────────────────────────────

def upsert_to_supabase(listings: list[dict]) -> list[dict]:
    client = get_client()
    inserted = []

    for item in listings:
        if not item["price"]:
            print(f"  [SKIP] no price — {item['title'][:60]}")
            continue

        # Survey number stub — unique per listing URL or title+price combo
        url_key = item["listing_url"] or f"{item['title']}|{item['price']}"
        survey_stub = f"MB-{abs(hash(url_key)) % 10**9}"

        # Upsert property stub
        try:
            prop_resp = (
                client.table("properties")
                .upsert(
                    {
                        "survey_no":     survey_stub,
                        "taluka":        item["taluka"],
                        "village":       item["village"],
                        "area_sqft":     item["area_sqft"],
                        "property_type": item["property_type"],
                        "heritage_flag": False,
                    },
                    on_conflict="survey_no,taluka,village",
                )
                .execute()
            )
            property_id = prop_resp.data[0]["id"]
        except Exception as exc:
            print(f"  [ERR] property upsert failed for {item['title'][:50]}: {exc}")
            continue

        # Insert or update listing (deduplicate by listing_url)
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            existing = (
                client.table("listings")
                .select("id")
                .eq("listing_url", item["listing_url"])
                .execute()
            )
            listing_payload = {
                "property_id":    property_id,
                "portal":         PORTAL,
                "listing_url":    item["listing_url"],
                "listed_price":   item["price"],
                "listed_date":    date.today().isoformat(),
                "days_on_market": item["days_on_market"],
                "last_seen_at":   now_iso,
            }

            if existing.data:
                listing_id = existing.data[0]["id"]
                client.table("listings").update(listing_payload).eq("id", listing_id).execute()
                row = {**listing_payload, "id": listing_id, "_action": "updated"}
            else:
                listing_resp = client.table("listings").insert(listing_payload).execute()
                row = {**listing_resp.data[0], "_action": "inserted"}

            row["_title"]    = item["title"]
            row["_location"] = f"{item['village']}, {item['taluka']}"
            inserted.append(row)

            price_str = f"{item['price']/1e7:.2f} Cr" if item["price"] >= 1e7 else f"{item['price']/1e5:.1f} Lac"
            area_str  = f"{item['area_sqft']:.0f} sqft" if item["area_sqft"] else "area N/A"
            print(
                f"  [{row['_action'].upper()[:3]}] {item['title'][:52]:52s} | "
                f"{price_str:>9} | {area_str:>10} | {item['village']}"
            )

        except Exception as exc:
            print(f"  [ERR] listing save failed for {item['title'][:50]}: {exc}")

    return inserted


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape MagicBricks Goa listings into Supabase")
    parser.add_argument("--limit", type=int, default=100, help="Max listings to scrape")
    args = parser.parse_args()

    print(f"\nScraping MagicBricks Goa — target {args.limit} listings\n")
    raw = scrape_listings(args.limit)
    print(f"\nParsed {len(raw)} listings. Writing to Supabase...\n")
    inserted = upsert_to_supabase(raw)
    print(f"\nDone — {len(inserted)} rows written to `listings` + `properties` tables.\n")


if __name__ == "__main__":
    main()
