-- ================================================================
-- MIGRATION: Choir app support
-- Run this once in your Neon SQL console.
-- ================================================================

-- 1. Org type flag — 'opera' (default) keeps existing behaviour
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS org_type TEXT NOT NULL DEFAULT 'opera';

-- 1b. Extend the shared rehearsals table for choir use
--     Choir rehearsals use org_id + the existing start_time/end_time (TIMESTAMPTZ) columns.
ALTER TABLE rehearsals ADD COLUMN IF NOT EXISTS org_id INTEGER REFERENCES organizations(id) ON DELETE CASCADE;
-- Drop lesson_date if it was previously added — start_time already carries the date.
ALTER TABLE rehearsals DROP COLUMN IF EXISTS lesson_date;

-- 2. Section assignment on users (choir singers are assigned to one section)
ALTER TABLE users ADD COLUMN IF NOT EXISTS section_id INTEGER REFERENCES choir_sections(id) ON DELETE SET NULL;

-- NOTE: run steps 3 first, then uncomment step 2 if running manually,
-- because the FK reference must exist before the column is added.
-- Safest order: run steps 3, 4, 5, then 2.

-- 3. Choir sections (per org — e.g. Soprano, Alto, Bass)
CREATE TABLE IF NOT EXISTS choir_sections (
    id         SERIAL PRIMARY KEY,
    org_id     INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

-- 4. Re-run step 2 now that choir_sections exists
-- (idempotent — IF NOT EXISTS on the column means it's safe to re-run)
ALTER TABLE users ADD COLUMN IF NOT EXISTS section_id INTEGER REFERENCES choir_sections(id) ON DELETE SET NULL;

-- 5. Sub roster (no accounts — just contact info)
CREATE TABLE IF NOT EXISTS subs (
    id           SERIAL PRIMARY KEY,
    org_id       INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    section_id   INTEGER NOT NULL REFERENCES choir_sections(id) ON DELETE CASCADE,
    fullname     TEXT NOT NULL,
    email        TEXT NOT NULL,
    phone        TEXT,
    is_preferred BOOLEAN NOT NULL DEFAULT false,
    notes        TEXT,
    active       BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. Which sections are called to a rehearsal (empty = full choir)
CREATE TABLE IF NOT EXISTS rehearsal_sections (
    rehearsal_id INTEGER NOT NULL REFERENCES rehearsals(id) ON DELETE CASCADE,
    section_id   INTEGER NOT NULL REFERENCES choir_sections(id) ON DELETE CASCADE,
    PRIMARY KEY (rehearsal_id, section_id)
);

-- 7. Absence requests (singer marks themselves out for a rehearsal)
CREATE TABLE IF NOT EXISTS absence_requests (
    id           SERIAL PRIMARY KEY,
    rehearsal_id INTEGER NOT NULL REFERENCES rehearsals(id) ON DELETE CASCADE,
    singer_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason       TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(rehearsal_id, singer_id)
);

-- 8. Sub requests (one per absent singer per rehearsal — tracks the hunt)
CREATE TABLE IF NOT EXISTS sub_requests (
    id               SERIAL PRIMARY KEY,
    rehearsal_id     INTEGER NOT NULL REFERENCES rehearsals(id) ON DELETE CASCADE,
    section_id       INTEGER NOT NULL REFERENCES choir_sections(id) ON DELETE CASCADE,
    created_by       INTEGER NOT NULL REFERENCES users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    preferred_sent_at TIMESTAMPTZ,
    all_sent_at      TIMESTAMPTZ,
    status           TEXT NOT NULL DEFAULT 'open',
    -- open | preferred_sent | all_sent | filled | cancelled
    filled_by_sub_id INTEGER REFERENCES subs(id)
);

-- 9. Individual sub contact records (one row per sub per request)
CREATE TABLE IF NOT EXISTS sub_contacts (
    id             SERIAL PRIMARY KEY,
    sub_request_id INTEGER NOT NULL REFERENCES sub_requests(id) ON DELETE CASCADE,
    sub_id         INTEGER NOT NULL REFERENCES subs(id) ON DELETE CASCADE,
    tier           TEXT NOT NULL,          -- 'preferred' | 'regular'
    contacted_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    response       TEXT NOT NULL DEFAULT 'pending',  -- pending | accepted | declined
    responded_at   TIMESTAMPTZ,
    token          TEXT NOT NULL UNIQUE,   -- one-click email link, no login needed
    UNIQUE(sub_request_id, sub_id)
);

-- 10. Mark org as choir type for your choir org (update slug to match yours)
-- UPDATE organizations SET org_type = 'choir' WHERE slug = 'your-choir-slug';
