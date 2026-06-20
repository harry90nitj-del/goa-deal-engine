-- Goa Deal Engine — Database Schema
-- Run against your Supabase project via the SQL editor or psql

-- ─────────────────────────────────────────────────────────────
-- 1. PROPERTIES
-- Core cadastral record. One row per physical parcel.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS properties (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    survey_no       TEXT NOT NULL,
    taluka          TEXT NOT NULL,
    village         TEXT NOT NULL,
    area_sqft       NUMERIC(12, 2),
    property_type   TEXT CHECK (property_type IN ('residential', 'commercial', 'agricultural', 'mixed', 'plot')),
    heritage_flag   BOOLEAN DEFAULT FALSE,   -- true if within a heritage or eco-sensitive zone
    lat             NUMERIC(10, 7),
    lng             NUMERIC(10, 7),
    condition_score INTEGER CHECK (condition_score BETWEEN 1 AND 10),  -- 1 = derelict, 10 = turnkey
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (survey_no, taluka, village)
);

-- ─────────────────────────────────────────────────────────────
-- 2. OWNERS
-- Ownership profile linked to a property.
-- Multiple owners per property allowed (co-owners, heirs).
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS owners (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id     UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    owner_name      TEXT NOT NULL,
    nri_flag        BOOLEAN DEFAULT FALSE,   -- true if owner is Non-Resident Indian
    ownership_since DATE,
    heir_count      INTEGER DEFAULT 0,       -- number of legal heirs (complexity proxy)
    contact_info    JSONB,                   -- { phone, email, address } — store encrypted in prod
    tenure_years    NUMERIC(5, 1)            -- derived: years since ownership_since; can be computed column
);

CREATE INDEX IF NOT EXISTS idx_owners_property_id ON owners(property_id);
CREATE INDEX IF NOT EXISTS idx_owners_nri ON owners(nri_flag) WHERE nri_flag = TRUE;

-- ─────────────────────────────────────────────────────────────
-- 3. TRANSACTIONS
-- Registered sale/transfer deeds pulled from IGR Goa.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transactions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id         UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    transaction_date    DATE NOT NULL,
    price               NUMERIC(15, 2) NOT NULL,
    circle_rate         NUMERIC(15, 2),       -- government ready-reckoner rate at time of sale
    below_circle_flag   BOOLEAN DEFAULT FALSE, -- true if price < circle_rate (distress signal)
    deed_type           TEXT CHECK (deed_type IN ('sale', 'gift', 'partition', 'mortgage', 'lease', 'other')),
    registered_at       TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transactions_property_id ON transactions(property_id);
CREATE INDEX IF NOT EXISTS idx_transactions_below_circle ON transactions(below_circle_flag) WHERE below_circle_flag = TRUE;
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(transaction_date);

-- ─────────────────────────────────────────────────────────────
-- 4. LISTINGS
-- Active or historical portal listings (MagicBricks, 99acres, Housing, etc.)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS listings (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id      UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    portal           TEXT NOT NULL,           -- e.g. 'magicbricks', '99acres', 'housing', 'nobroker'
    listing_url      TEXT,
    listed_price     NUMERIC(15, 2) NOT NULL,
    listed_date      DATE,
    days_on_market   INTEGER DEFAULT 0,
    relisted_flag    BOOLEAN DEFAULT FALSE,   -- true if same property has appeared >1 time
    price_drop_count INTEGER DEFAULT 0,       -- number of price reductions since first listing
    last_seen_at     TIMESTAMPTZ DEFAULT NOW(),
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_listings_property_id ON listings(property_id);
CREATE INDEX IF NOT EXISTS idx_listings_portal ON listings(portal);
CREATE INDEX IF NOT EXISTS idx_listings_days_on_market ON listings(days_on_market);
CREATE INDEX IF NOT EXISTS idx_listings_relisted ON listings(relisted_flag) WHERE relisted_flag = TRUE;

-- ─────────────────────────────────────────────────────────────
-- 5. SCORES
-- AI-derived deal scores. One row per scoring run per property.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scores (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id             UUID NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    seller_motivation_score NUMERIC(5, 2) CHECK (seller_motivation_score BETWEEN 0 AND 100),
    value_gap_score         NUMERIC(5, 2) CHECK (value_gap_score BETWEEN 0 AND 100),
    reno_complexity_score   NUMERIC(5, 2) CHECK (reno_complexity_score BETWEEN 0 AND 100),
    location_score          NUMERIC(5, 2) CHECK (location_score BETWEEN 0 AND 100),
    composite_score         NUMERIC(5, 2) CHECK (composite_score BETWEEN 0 AND 100),
    scored_at               TIMESTAMPTZ DEFAULT NOW(),
    flags                   JSONB          -- array of string flags, e.g. ["nri_seller","long_dom","price_drop_3x"]
);

CREATE INDEX IF NOT EXISTS idx_scores_property_id ON scores(property_id);
CREATE INDEX IF NOT EXISTS idx_scores_composite ON scores(composite_score DESC);
CREATE INDEX IF NOT EXISTS idx_scores_scored_at ON scores(scored_at);

-- ─────────────────────────────────────────────────────────────
-- Convenience view: top deals
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW top_deals AS
SELECT
    p.id,
    p.survey_no,
    p.taluka,
    p.village,
    p.area_sqft,
    p.property_type,
    p.heritage_flag,
    p.lat,
    p.lng,
    s.composite_score,
    s.seller_motivation_score,
    s.value_gap_score,
    s.reno_complexity_score,
    s.location_score,
    s.flags,
    s.scored_at,
    l.portal,
    l.listed_price,
    l.days_on_market,
    l.price_drop_count,
    l.listing_url
FROM properties p
JOIN scores s ON s.property_id = p.id
LEFT JOIN LATERAL (
    SELECT * FROM listings WHERE property_id = p.id ORDER BY last_seen_at DESC LIMIT 1
) l ON TRUE
WHERE s.scored_at = (
    SELECT MAX(scored_at) FROM scores WHERE property_id = p.id
)
ORDER BY s.composite_score DESC;
