"""
reports/
--------
Generates human-readable deal reports from scored properties.

Planned modules:
    generator.py        — Pulls top N deals from Supabase `top_deals` view,
                          formats them, and calls Claude for narrative summaries.
    pdf_export.py       — Renders a PDF deal sheet per property (Pillow + reportlab).
    email_digest.py     — Weekly HTML digest of top opportunities via SMTP or SendGrid.
    dashboard.py        — (optional) Lightweight CLI table printed to stdout via pandas.

Output directory: reports/output/ (gitignored)
"""
