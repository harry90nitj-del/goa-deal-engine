"""
scoring/signals.py
------------------
Fetches all properties + listings from Supabase and computes the raw
signals that feed into the four scoring dimensions.

Returns a list of enriched dicts — one per property that has at least
one listing.  The scorer consumes this output directly.
"""

from database.client import get_client


# ── Location weights (keyword matched against village/taluka strings) ─────────
# Higher number = stronger deal location signal (0-100)
LOCATION_SCORES: dict[str, int] = {
    "candolim":   92,
    "calangute":  90,
    "baga":       88,
    "anjuna":     85,
    "vagator":    84,
    "arpora":     83,
    "siolim":     80,
    "panjim":     80,
    "panaji":     80,
    "miramar":    78,
    "porvorim":   76,
    "bardez":     74,
    "caranzalem": 72,
    "reis magos": 72,
    "khobra":     70,
    "nagoa":      68,
    "mapusa":     65,
    "taleigao":   64,
    "guirim":     62,
    "dabolim":    58,
    "vasco":      56,
    "margao":     54,
    "ponda":      50,
    "curchorem":  42,
    "quepem":     40,
    "mopa":       48,
    "cuncolim":   44,
    "bamboli":    50,
    "duler":      55,
}
DEFAULT_LOCATION_SCORE = 52


def location_score(village: str, taluka: str) -> int:
    haystack = f"{village} {taluka}".lower()
    for keyword, score in LOCATION_SCORES.items():
        if keyword in haystack:
            return score
    return DEFAULT_LOCATION_SCORE


# ── DOM scoring curve ─────────────────────────────────────────────────────────
# 0 days  -> 10  (just listed / unknown)
# 30 days -> 35
# 60 days -> 60
# 90 days -> 80
# 180 days -> 100
def dom_score(days: int) -> float:
    if days <= 0:
        return 10.0
    if days >= 180:
        return 100.0
    return min(10 + (days / 180) * 90, 100)


# ── Data fetch ────────────────────────────────────────────────────────────────

def fetch_enriched() -> list[dict]:
    """
    Join properties + listings in one Supabase call, return one record
    per property (latest listing wins when multiple exist).
    """
    client = get_client()

    # Supabase supports embedded selects via FK relationships
    resp = (
        client.table("listings")
        .select(
            "id, listed_price, days_on_market, relisted_flag, "
            "price_drop_count, listing_url, last_seen_at, "
            "properties(id, survey_no, taluka, village, area_sqft, property_type)"
        )
        .order("last_seen_at", desc=True)
        .execute()
    )

    # Deduplicate: keep only the most-recent listing per property
    seen: dict[str, dict] = {}
    for row in resp.data:
        prop = row.get("properties")
        if not prop:
            continue
        prop_id = prop["id"]
        if prop_id not in seen:
            seen[prop_id] = {**prop, "_listing": row}

    return list(seen.values())


# ── Signal computation ────────────────────────────────────────────────────────

def compute_signals(records: list[dict]) -> list[dict]:
    """
    Add signal fields to each record:
        price_per_sqft, taluka_avg_ppsf, value_gap_pct,
        dom_score_val, seller_raw, location_score_val, reno_hint
    """
    # ── 1. price_per_sqft per record ──────────────────────────────────────────
    for rec in records:
        lst   = rec["_listing"]
        price = lst.get("listed_price") or 0
        area  = rec.get("area_sqft") or 0
        rec["_price"]    = price
        rec["_ppsf"]     = round(price / area, 2) if area > 0 else None
        rec["_dom"]      = lst.get("days_on_market") or 0
        rec["_relisted"] = bool(lst.get("relisted_flag"))
        rec["_drops"]    = int(lst.get("price_drop_count") or 0)
        rec["_url"]      = lst.get("listing_url") or ""

    # ── 2. taluka average price_per_sqft ──────────────────────────────────────
    taluka_ppsf: dict[str, list[float]] = {}
    for rec in records:
        if rec["_ppsf"]:
            key = rec.get("taluka", "unknown")
            taluka_ppsf.setdefault(key, []).append(rec["_ppsf"])

    taluka_avg: dict[str, float] = {
        k: sum(v) / len(v) for k, v in taluka_ppsf.items()
    }

    # ── 3. attach computed signals ────────────────────────────────────────────
    for rec in records:
        taluka = rec.get("taluka", "unknown")
        ppsf   = rec["_ppsf"]
        avg    = taluka_avg.get(taluka)

        if ppsf and avg and avg > 0:
            rec["_value_gap_pct"] = round((avg - ppsf) / avg * 100, 1)
        else:
            rec["_value_gap_pct"] = 0.0

        rec["_taluka_avg_ppsf"] = round(avg, 2) if avg else None
        rec["_dom_score"]       = dom_score(rec["_dom"])
        rec["_location_score"]  = location_score(
            rec.get("village", ""), rec.get("taluka", "")
        )

    return records


# ── Public API ────────────────────────────────────────────────────────────────

def get_signals() -> list[dict]:
    """Fetch + enrich all scoreable properties. Entry point for scorer.py."""
    records = fetch_enriched()
    return compute_signals(records)
