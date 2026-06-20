# Goa Deal Engine

An automated deal discovery engine for Goa real estate. It scrapes public property registrations, active portal listings, and owner signals, scores every property across four deal-quality dimensions using Claude, and surfaces the highest-opportunity assets in a ranked report.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        EXTERNAL SOURCES                         │
│  IGR Goa (deed registry)  │  Portals (MB/99acres)  │  Maps API  │
└────────────┬──────────────┴──────────┬─────────────┴─────┬──────┘
             │                         │                   │
             ▼                         ▼                   ▼
┌─────────────────────────────────────────────────────────────────┐
│                         scrapers/                               │
│  igr_scraper.py   portal_scraper.py   maps_enricher.py         │
│                                                                 │
│  • Playwright / requests + BS4 for HTML portals                 │
│  • Writes raw rows into Supabase via upsert (idempotent)        │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Supabase (PostgreSQL)                      │
│                                                                 │
│   properties   owners   transactions   listings   scores        │
│                                                                 │
│   top_deals view — joins latest score with property + listing   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                         scoring/                                │
│  signals.py  →  claude_judge.py  →  scorer.py                  │
│                                                                 │
│  1. signals.py pulls features per property (DOM, price drops,   │
│     NRI flag, gap to circle rate, heritage zone, heir count)    │
│  2. claude_judge.py formats a structured prompt and calls       │
│     Claude (claude-sonnet-4-6) with tool_use for JSON output   │
│  3. scorer.py writes the result into the `scores` table         │
│                                                                 │
│  Score dimensions (each 0–100):                                 │
│    seller_motivation — urgency signals from owner profile       │
│    value_gap         — listed price vs. circle rate vs. comps   │
│    reno_complexity   — inverted difficulty (higher = easier)    │
│    location          — beach/highway proximity, zone overlays   │
│    composite         — weighted average → the ranking key       │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                         reports/                                │
│  generator.py   pdf_export.py   email_digest.py                │
│                                                                 │
│  • Queries top_deals view (top N by composite_score)            │
│  • Claude writes a 3-sentence investment narrative per deal     │
│  • PDF deal sheet rendered with Pillow                          │
│  • Weekly HTML digest sent by email                             │
└─────────────────────────────────────────────────────────────────┘
```

---

## Scoring Logic

| Dimension | Key Inputs | Weight |
|---|---|---|
| `seller_motivation_score` | NRI flag, years of ownership, heir count, days on market, price drop count, relisted flag | 35% |
| `value_gap_score` | `(circle_rate - listed_price) / circle_rate`, comparable recent transactions | 30% |
| `reno_complexity_score` | `condition_score`, heritage flag, property type | 20% |
| `location_score` | Distance to beach / NH-66 / Panjim, eco-sensitive zone overlap | 15% |

A `composite_score` above **70** is considered a high-priority opportunity.

---

## Setup

```bash
# 1. Clone and install
git clone <repo>
cd goa-deal-engine
pip install -r requirements.txt
playwright install chromium

# 2. Configure environment
cp .env.example .env
# Fill in SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY,
# ANTHROPIC_API_KEY, GOOGLE_MAPS_API_KEY

# 3. Apply the database schema
# Paste database/schema.sql into the Supabase SQL editor and run it.

# 4. Run a scrape
python -m scrapers.igr_scraper
python -m scrapers.portal_scraper

# 5. Score all unscored properties
python -m scoring.scorer

# 6. Generate a report
python -m reports.generator
```

---

## Project Structure

```
goa-deal-engine/
├── .env.example          # Environment variable template
├── .gitignore
├── README.md
├── requirements.txt
├── database/
│   └── schema.sql        # PostgreSQL schema for Supabase
├── scrapers/
│   └── __init__.py       # Module docs + planned submodules
├── scoring/
│   └── __init__.py       # Module docs + score dimensions
└── reports/
    └── __init__.py       # Module docs + output formats
```

---

## Data Flow Summary

1. **Scrapers** pull deed registrations from IGR Goa and listings from portals → stored in `properties`, `owners`, `transactions`, `listings`.
2. **Scorer** reads those tables, builds a feature vector per property, sends it to Claude via the Anthropic API, and writes structured scores to `scores`.
3. **Reports** query the `top_deals` view (composite_score DESC), generate PDF deal sheets and an email digest.
4. A `schedule` job (or cron) re-runs steps 1–3 nightly so the database stays current.
