"""
scrapers/
---------
Responsible for pulling raw property data from external sources and
writing it into Supabase tables (properties, owners, transactions, listings).

Planned modules:
    igr_scraper.py      — Goa Inspector General of Registration (deed data)
    portal_scraper.py   — MagicBricks / 99acres / Housing.com (listing data)
    maps_enricher.py    — Google Maps API (lat/lng, nearby amenities)

All scrapers should:
    1. Accept a Supabase client instance (from db.get_client()).
    2. Return a list of dicts matching the target table schema.
    3. Use upsert so re-runs are idempotent.
"""
