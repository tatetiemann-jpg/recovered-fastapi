-- ================================================================
-- MIGRATION 2 — Orchestra feature
-- Run this in your Neon SQL console after migrate.sql
-- ================================================================

-- Instrument field for orchestra members (free-text, e.g. "violin")
ALTER TABLE users ADD COLUMN IF NOT EXISTS instrument TEXT;

-- Teacher differentiation: 'vocal' (default) or 'instrumental'
ALTER TABLE users ADD COLUMN IF NOT EXISTS teacher_type TEXT NOT NULL DEFAULT 'vocal';

-- Comma-separated list of instruments a teacher coaches (e.g. "violin,viola")
ALTER TABLE users ADD COLUMN IF NOT EXISTS teacher_instruments TEXT NOT NULL DEFAULT '';

-- Carry teacher type/instruments metadata in the invitation record
-- so the accept-invite page can show the right info and accept_invite() can
-- persist them to the new user row.
ALTER TABLE invitations ADD COLUMN IF NOT EXISTS teacher_type TEXT NOT NULL DEFAULT 'vocal';
ALTER TABLE invitations ADD COLUMN IF NOT EXISTS teacher_instruments TEXT NOT NULL DEFAULT '';

-- Orchestra sections (org-level — same section names across all operas)
-- e.g. "Violin 1" (instrument: "violin"), "Cello" (instrument: "cello")
CREATE TABLE IF NOT EXISTS orchestra_sections (
    id          SERIAL PRIMARY KEY,
    org_id      INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    instrument  TEXT NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0
);

-- Per-opera seat assignments: which orchestra member sits in which chair
-- for a given section in a given production.
CREATE TABLE IF NOT EXISTS orchestra_seats (
    id           SERIAL PRIMARY KEY,
    opera_id     INTEGER NOT NULL REFERENCES operas(id) ON DELETE CASCADE,
    section_id   INTEGER NOT NULL REFERENCES orchestra_sections(id) ON DELETE CASCADE,
    chair_number INTEGER NOT NULL DEFAULT 1,
    member_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE(opera_id, section_id, chair_number)
);

-- Flag on rehearsals to separate vocal rehearsals from orchestra rehearsals
ALTER TABLE rehearsals ADD COLUMN IF NOT EXISTS rehearsal_type TEXT NOT NULL DEFAULT 'vocal';

-- Expand the role check constraint to include orchestra roles.
-- PostgreSQL doesn't support ALTER CONSTRAINT directly, so we drop and recreate it.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (
    role IN ('student', 'teacher', 'admin', 'head_admin', 'system_admin',
             'orchestra_member', 'orchestra_admin')
);
