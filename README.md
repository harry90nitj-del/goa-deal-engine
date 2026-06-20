# Goa Deal Engine

An automated deal discovery engine for Goa real estate. It scrapes active property listings from MagicBricks, scores every property across four deal-quality dimensions, calls the Claude AI API to write a plain-English brief on each top deal, and outputs a ranked report you can read on WhatsApp.

---

## What It Does

1. **Scrapes** live listings from MagicBricks across Goa (North Goa, South Goa, Calangute, Panjim) — captures price, area, location, days on market, and listing URL.
2. **Stores** everything in a Supabase (PostgreSQL) database across 5 tables.
3. **Scores** every property on 4 dimensions using a weighted formula — no AI needed for the score itself, it is deterministic math.
4. **Reports** the top 10 deals as a clean WhatsApp-ready text with a 2-sentence Claude AI brief per property — what makes it interesting and what to watch out for.

---

## How the Scoring Works

Every property gets a score from 0 to 100 on four dimensions. These are combined into a single `composite_score` that ranks all properties.

### Dimension 1 — Seller Motivation (35% weight)

Answers: *How desperate is the seller to offload this property?*

| Signal | Points |
|---|---|
| Days on market (DOM) | 0 days = 10 pts, 180+ days = 100 pts (linear curve) |
| Property was relisted | +20 pts |
| Each price drop | +15 pts each, capped at +30 pts total |

Formula:
```
seller_motivation = (dom_score × 0.5) + relisted_bonus + drop_bonus
```

A property sitting unsold for 6+ months with two price drops will score near 100 — the seller wants out.

---

### Dimension 2 — Value Gap (30% weight)

Answers: *Is this property cheap relative to others in the same area?*

We compute the average price-per-sqft for all properties in the same taluka (district). Then:

```
value_gap_pct = (taluka_avg_ppsf - this_ppsf) / taluka_avg_ppsf × 100
value_gap_score = 50 + value_gap_pct
```

- 50 = priced exactly at the area average (neutral)
- 80 = priced 30% below the area average (good deal)
- 20 = priced 30% above the area average (overpriced)

---

### Dimension 3 — Location (20% weight)

Answers: *How desirable is this location for rental income or resale?*

Hardcoded scores based on local knowledge of Goa's market:

| Location | Score |
|---|---|
| Candolim | 92 |
| Calangute | 90 |
| Baga | 88 |
| Anjuna | 85 |
| Vagator | 84 |
| Panjim / Panaji | 80 |
| Porvorim | 76 |
| Mapusa | 65 |
| Vasco | 56 |
| Margao | 54 |
| Unknown / other | 52 |

---

### Dimension 4 — Renovation Complexity (15% weight)

Answers: *Does this property look like a fixer-upper that can be bought cheap and improved?*

A higher score means it looks like a renovation opportunity (cheap relative to area = likely needs work = upside potential).

```
If ppsf data is available:
    reno_score = 50 + ((taluka_avg_ppsf - this_ppsf) / taluka_avg_ppsf × 100 × 0.6)

If ppsf is not available (fallback by price band):
    < 40 Lac  → 90
    40-75 Lac → 72
    75 Lac - 1.5 Cr → 52
    1.5-3 Cr  → 35
    > 3 Cr    → 18
```

---

### Composite Score

```
composite = (seller_motivation × 0.35)
          + (value_gap        × 0.30)
          + (location         × 0.20)
          + (reno_complexity  × 0.15)
```

Score above 70 = high-priority opportunity. Score below 40 = skip it.

---

### Deal Flags

The scorer also tags each property with plain-English signals:

| Flag | Meaning |
|---|---|
| `long_dom` | On market 60+ days |
| `relisted` | Was taken off and re-listed |
| `price_dropped_Nx` | Price was dropped N times |
| `below_area_avg` | Value gap score above 65 |
| `above_area_avg` | Value gap score below 35 |
| `premium_location` | Location score 85 or above |
| `likely_needs_work` | Reno complexity score above 70 |
| `motivated_seller` | Seller motivation score above 60 |

---

## Claude AI Narratives

When you run the WhatsApp report, the engine calls the Claude `claude-opus-4-8` model once per property. It sends the deal data (price, area, scores, flags, days on market) and asks Claude to write exactly 2 sentences:

- Sentence 1: What makes this deal interesting
- Sentence 2: What to watch out for

Output is plain English with no markdown or emoji so it reads cleanly in WhatsApp.

---

## Database Schema

Five tables in Supabase:

| Table | What it stores |
|---|---|
| `properties` | One row per property — survey number, village, taluka, area sqft, type |
| `owners` | Owner name, NRI flag, purchase year (for IGR-sourced data) |
| `transactions` | Registered sale deeds from IGR Goa — price, date, deed type |
| `listings` | Active portal listings — price, days on market, drops, URL, last seen |
| `scores` | Computed scores per property — one row per scoring run |

A `top_deals` view joins the latest score with property info and the most recent listing.

---

## IGR Goa Scraper (India-only)

`scrapers/igr_scraper.py` scrapes actual registered sale deeds from the Goa government's NGDRS portal (`ngdrsgoa.gov.in`). This site is only accessible from within India. If you are outside India, use an Indian VPN or hand this script to someone in India.

```bash
python -m scrapers.igr_scraper --district north --days 30
```

---

## Setup

```bash
# 1. Clone and install
git clone https://github.com/harry90nitj-del/goa-deal-engine.git
cd goa-deal-engine
pip install -r requirements.txt
playwright install chromium

# 2. Configure environment
cp .env.example .env
# Fill in SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, ANTHROPIC_API_KEY

# 3. Apply the database schema
# Open database/schema.sql and run it in the Supabase SQL editor

# 4. Test your connection
python -m database.test_connection

# 5. Scrape listings
python -m scrapers.listings_scraper --limit 100

# 6. Score all properties
python -m scoring.scorer

# 7. Run the report
python -m reports.generator                   # table format in terminal
python -m reports.generator --whatsapp        # WhatsApp format with Claude AI briefs
python -m reports.generator --whatsapp > reports/output/whatsapp_report.txt  # save to file
```

---

## Project Structure

```
goa-deal-engine/
├── .env.example                # Environment variable template (never commit .env)
├── requirements.txt
├── database/
│   ├── client.py               # Supabase connection helper
│   ├── schema.sql              # PostgreSQL schema — run once in Supabase
│   └── test_connection.py      # Verifies all 5 tables are reachable
├── scrapers/
│   ├── listings_scraper.py     # MagicBricks Goa scraper (Playwright, 100 listings)
│   └── igr_scraper.py          # NGDRS Goa sale deed scraper (India network only)
├── scoring/
│   ├── signals.py              # Fetches enriched data + computes raw signals
│   └── scorer.py               # Applies 4-dimension formula, writes to scores table
└── reports/
    ├── generator.py            # Ranked deal table + WhatsApp format with Claude briefs
    └── output/                 # Saved report files (gitignored)
```

---

## Environment Variables

| Variable | Where to get it |
|---|---|
| `SUPABASE_URL` | Supabase project Settings > API |
| `SUPABASE_ANON_KEY` | Supabase project Settings > API |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase project Settings > API |
| `ANTHROPIC_API_KEY` | console.anthropic.com |
