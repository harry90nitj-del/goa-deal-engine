"""
scoring/
--------
Computes deal-quality scores for each property and writes results to
the `scores` table in Supabase.

Planned modules:
    scorer.py           — Orchestrates the scoring pipeline for a batch of properties.
    signals.py          — Extracts raw signals (days on market, price drops, NRI flag, etc.)
    claude_judge.py     — Sends property context to Claude and parses structured score output.
    weights.py          — Scoring weights and thresholds (edit to tune the model).

Score dimensions (each 0–100):
    seller_motivation_score — NRI owner, long DOM, price drops, heir disputes, forced sale signals
    value_gap_score         — Listed price vs. circle rate vs. comparable sales
    reno_complexity_score   — Inverted: higher score = lower complexity (easier to fix)
    location_score          — Proximity to beach, highway, tourist zones; heritage constraints
    composite_score         — Weighted average of the four dimensions
"""
