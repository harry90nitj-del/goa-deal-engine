"""
scoring/scorer.py
-----------------
Computes deal scores for every property in Supabase and writes results
to the `scores` table.

Score dimensions (all 0-100):
    seller_motivation  35%  — DOM + relisted + price drops
    value_gap          30%  — price/sqft vs taluka average
    location           20%  — hardcoded taluka desirability ranking
    reno_complexity    15%  — cheap relative to area = likely needs work = opportunity

Usage:
    python -m scoring.scorer
"""

import json
from datetime import datetime, timezone

from database.client import get_client
from scoring.signals import get_signals


# ── Sub-score formulas ────────────────────────────────────────────────────────

def _seller_motivation(dom_score: float, relisted: bool, drops: int) -> float:
    """
    Driven by how long/desperate the seller appears.
    DOM score already normalised to 0-100.
    Relisted adds 20 pts, each price drop adds 15 (capped at 30).
    """
    drop_bonus    = min(drops * 15, 30)
    relisted_bonus = 20 if relisted else 0
    raw = dom_score * 0.5 + relisted_bonus + drop_bonus
    return min(round(raw, 1), 100.0)


def _value_gap(gap_pct: float) -> float:
    """
    gap_pct > 0  → cheaper than taluka average → higher score
    gap_pct < 0  → more expensive than average → lower score
    0% gap       → 50 (neutral)
    +50% gap     → ~100 (screaming deal)
    -50% gap     → ~0  (overpriced)
    """
    score = 50 + gap_pct           # 1 percentage point = 1 score point
    return min(max(round(score, 1), 0.0), 100.0)


def _reno_complexity(price: float, ppsf: float | None, taluka_avg: float | None) -> float:
    """
    Cheap relative to area average → likely needs work → higher reno score
    (higher score = more renovation opportunity, feeds positively into composite).
    Falls back to absolute price band when ppsf is unavailable.
    """
    if ppsf and taluka_avg and taluka_avg > 0:
        below_avg_pct = (taluka_avg - ppsf) / taluka_avg * 100
        score = 50 + below_avg_pct * 0.6
    else:
        # absolute price fallback (INR)
        if price < 4_000_000:       # < 40 Lac
            score = 90
        elif price < 7_500_000:     # < 75 Lac
            score = 72
        elif price < 15_000_000:    # < 1.5 Cr
            score = 52
        elif price < 30_000_000:    # < 3 Cr
            score = 35
        else:
            score = 18
    return min(max(round(score, 1), 0.0), 100.0)


def _composite(sm: float, vg: float, loc: float, reno: float) -> float:
    return round(sm * 0.35 + vg * 0.30 + loc * 0.20 + reno * 0.15, 1)


def _flags(rec: dict, sm: float, vg: float, loc: float, reno: float) -> list[str]:
    f = []
    if rec["_dom"] > 60:
        f.append("long_dom")
    if rec["_relisted"]:
        f.append("relisted")
    if rec["_drops"] > 0:
        f.append(f"price_dropped_{rec['_drops']}x")
    if vg > 65:
        f.append("below_area_avg")
    if vg < 35:
        f.append("above_area_avg")
    if loc >= 85:
        f.append("premium_location")
    if reno > 70:
        f.append("likely_needs_work")
    if sm > 60:
        f.append("motivated_seller")
    return f


# ── Main scoring loop ─────────────────────────────────────────────────────────

def score_all(dry_run: bool = False) -> list[dict]:
    records = get_signals()
    if not records:
        print("  No scoreable properties found.")
        return []

    client   = get_client()
    now_iso  = datetime.now(timezone.utc).isoformat()
    scored   = []

    print(f"  Scoring {len(records)} properties...\n")

    for rec in records:
        prop_id = rec["id"]
        price   = rec["_price"]

        if not price:
            continue

        sm   = _seller_motivation(rec["_dom_score"], rec["_relisted"], rec["_drops"])
        vg   = _value_gap(rec["_value_gap_pct"])
        loc  = float(rec["_location_score"])
        reno = _reno_complexity(price, rec["_ppsf"], rec["_taluka_avg_ppsf"])
        comp = _composite(sm, vg, loc, reno)
        fl   = _flags(rec, sm, vg, loc, reno)

        row = {
            "property_id":             prop_id,
            "seller_motivation_score": sm,
            "value_gap_score":         vg,
            "location_score":          loc,
            "reno_complexity_score":   reno,
            "composite_score":         comp,
            "scored_at":               now_iso,
            "flags":                   fl,
        }

        village = rec.get("village", "?")[:30]
        ppsf_str = f"ppsf={rec['_ppsf']:,.0f}" if rec["_ppsf"] else "no-area"
        print(
            f"  {village:<30}  comp={comp:5.1f}  "
            f"sm={sm:4.0f}  vg={vg:4.0f}  loc={loc:3.0f}  reno={reno:4.0f}  "
            f"{ppsf_str}  {fl}"
        )

        scored.append({**rec, **row})

        if not dry_run:
            try:
                client.table("scores").insert(row).execute()
            except Exception as exc:
                print(f"    [ERR] scores insert: {exc}")

    return scored


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute scores but do not write to DB")
    args = parser.parse_args()

    print(f"\nRunning scorer {'(dry run)' if args.dry_run else '-> writing to Supabase'}...\n")
    results = score_all(dry_run=args.dry_run)
    written = len(results) if not args.dry_run else 0
    print(f"\nDone — {len(results)} properties scored, {written} rows written to `scores`.\n")


if __name__ == "__main__":
    main()
