"""
Coaching Scheduler — FastAPI backend.

All users (students, teachers, admins) live in one `users` table,
distinguished by the `role` column. Organizations are stored in `organizations`
and referenced by org_id. For now we hardcode the org to "boa" since the
app supports one festival at a time.
"""
from fastapi import FastAPI, Request, Query, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta, time as dtime
import os
import re
import html as html_mod
import hashlib
import secrets
import bcrypt
import psycopg2
from psycopg2 import errors as pg_errors
from psycopg2 import pool as pg_pool
import pytz
from dotenv import load_dotenv


load_dotenv()

DEFAULT_ORG_SLUG = "boa"
EST = pytz.timezone("US/Eastern")

# All role values that are considered "admin-level"
ADMIN_ROLES = frozenset({"admin", "head_admin", "system_admin", "orchestra_admin"})

OPERA_ADMIN_ROLES = frozenset({"director", "assistant_director", "stage_manager", "assistant_stage_manager"})
ORCHESTRA_ADMIN_ROLES = frozenset({"conductor", "assistant_conductor", "orchestra_manager"})
TEACHER_ROLES = frozenset({"teacher", "studio_teacher"})


# ========================================================
# LIFESPAN (startup/shutdown)
# ========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        _init_pool()
        with db_cursor() as cur:
            cur.execute("SELECT 1;")
        print(f"✅ Neon connection pool initialized (max {_connection_pool.maxconn} connections).")
        with db_cursor(commit=True) as cur:
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS calendar_token TEXT UNIQUE;")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS instrument VARCHAR(100);")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS admin_role VARCHAR(50);")
            cur.execute("ALTER TABLE invitations ADD COLUMN IF NOT EXISTS instrument VARCHAR(100);")
            cur.execute("ALTER TABLE invitations ADD COLUMN IF NOT EXISTS admin_role VARCHAR(50);")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS role_covers (
                    id SERIAL PRIMARY KEY,
                    opera_id INT NOT NULL REFERENCES operas(id) ON DELETE CASCADE,
                    cast_id INT NOT NULL REFERENCES casts(id) ON DELETE CASCADE,
                    role_name VARCHAR(200) NOT NULL,
                    student_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    UNIQUE (cast_id, role_name, student_id)
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS staff_messages (
                    id SERIAL PRIMARY KEY,
                    org_id INT NOT NULL,
                    opera_id INT REFERENCES operas(id) ON DELETE CASCADE,
                    sender_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    body TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS staff_message_recipients (
                    message_id INT NOT NULL REFERENCES staff_messages(id) ON DELETE CASCADE,
                    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    PRIMARY KEY (message_id, user_id)
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS staff_board_views (
                    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    scope VARCHAR(60) NOT NULL,
                    last_viewed_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (user_id, scope)
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    org_id INT NOT NULL,
                    sender_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    body TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT 'direct',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS message_recipients (
                    id SERIAL PRIMARY KEY,
                    message_id INT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    read_at TIMESTAMPTZ,
                    UNIQUE (message_id, user_id)
                );
            """)
            cur.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS external_recipient_names TEXT;")
            cur.execute("ALTER TABLE subs ADD COLUMN IF NOT EXISTS preferred_rank INT;")
            cur.execute("ALTER TABLE rehearsals ADD COLUMN IF NOT EXISTS choir_type VARCHAR(20) DEFAULT 'choir';")
            cur.execute("ALTER TABLE rehearsals ADD COLUMN IF NOT EXISTS materials_url TEXT;")
            cur.execute("ALTER TABLE absence_requests ADD COLUMN IF NOT EXISTS note TEXT;")
            cur.execute("ALTER TABLE absence_requests ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'approved';")
            cur.execute("ALTER TABLE absence_requests ADD COLUMN IF NOT EXISTS contact_preferred_on_approval BOOLEAN NOT NULL DEFAULT FALSE;")
            cur.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS org_type TEXT DEFAULT 'opera';")
            cur.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS lessons_enabled BOOLEAN DEFAULT FALSE;")
            cur.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS lesson_duration_min INTEGER DEFAULT 30;")
            cur.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS lesson_durations TEXT DEFAULT '30';")
            cur.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS lesson_max_per_day INTEGER DEFAULT 1;")
            cur.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS lesson_booking_open_hour INTEGER DEFAULT 21;")
            cur.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS lesson_booking_close_hour INTEGER DEFAULT 18;")
            cur.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS lesson_cancellation_notice_min INTEGER DEFAULT 60;")
            cur.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS lesson_has_lunch_break BOOLEAN DEFAULT TRUE;")
            cur.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS lesson_max_per_teacher INTEGER DEFAULT 5;")
            cur.execute("ALTER TABLE organizations ADD COLUMN IF NOT EXISTS logo_url TEXT;")
            cur.execute("ALTER TABLE lessons ALTER COLUMN student_id DROP NOT NULL;")
            cur.execute("ALTER TABLE lessons ADD COLUMN IF NOT EXISTS external_name TEXT;")
            cur.execute("ALTER TABLE lessons ADD COLUMN IF NOT EXISTS external_email TEXT;")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS recurring_lessons (
                    id SERIAL PRIMARY KEY,
                    teacher_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    student_id INT REFERENCES users(id) ON DELETE SET NULL,
                    external_name TEXT,
                    external_email TEXT,
                    weekday INT NOT NULL,
                    lesson_time TIME NOT NULL,
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(teacher_id, weekday, lesson_time)
                );
            """)
            cur.execute("ALTER TABLE lessons ADD COLUMN IF NOT EXISTS recurring_lesson_id INT REFERENCES recurring_lessons(id) ON DELETE SET NULL;")
            cur.execute("ALTER TABLE lessons ADD COLUMN IF NOT EXISTS duration_min INTEGER;")
            cur.execute("ALTER TABLE recurring_lessons ADD COLUMN IF NOT EXISTS duration_min INTEGER DEFAULT 30;")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rehearsal_members (
                    rehearsal_id INT REFERENCES rehearsals(id) ON DELETE CASCADE,
                    user_id INT REFERENCES users(id) ON DELETE CASCADE,
                    PRIMARY KEY (rehearsal_id, user_id)
                );
            """)
            # Studio teacher infrastructure
            cur.execute("ALTER TABLE lessons ADD COLUMN IF NOT EXISTS zoom_link TEXT;")
            cur.execute("ALTER TABLE lessons ADD COLUMN IF NOT EXISTS attendance TEXT;")
            cur.execute("ALTER TABLE lessons ADD COLUMN IF NOT EXISTS payment_overrun BOOLEAN DEFAULT FALSE;")
            # studio_student_id links lessons to studio_students registry (set after studio_students table exists)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS studio_families (
                    id SERIAL PRIMARY KEY,
                    teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    family_name TEXT NOT NULL,
                    parent_name TEXT,
                    parent_email TEXT
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS studio_students (
                    id SERIAL PRIMARY KEY,
                    teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    email TEXT,
                    parent_name TEXT,
                    parent_email TEXT
                );
            """)
            cur.execute("ALTER TABLE studio_students ADD COLUMN IF NOT EXISTS family_id INTEGER REFERENCES studio_families(id) ON DELETE SET NULL;")
            cur.execute("ALTER TABLE lessons ADD COLUMN IF NOT EXISTS studio_student_id INTEGER REFERENCES studio_students(id) ON DELETE SET NULL;")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS studio_payment_pools (
                    id SERIAL PRIMARY KEY,
                    teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    family_id INTEGER REFERENCES studio_families(id) ON DELETE CASCADE,
                    student_id INTEGER REFERENCES studio_students(id) ON DELETE CASCADE,
                    duration_min INTEGER NOT NULL,
                    lessons_paid INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uidx_payment_pool_family
                ON studio_payment_pools (teacher_id, family_id, duration_min)
                WHERE family_id IS NOT NULL;
            """)
            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uidx_payment_pool_student
                ON studio_payment_pools (teacher_id, student_id, duration_min)
                WHERE student_id IS NOT NULL;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS studio_teacher_settings (
                    teacher_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    payment_zelle   TEXT,
                    payment_venmo   TEXT,
                    payment_cashapp TEXT,
                    payment_paypal  TEXT,
                    lesson_rates    JSONB NOT NULL DEFAULT '[]'::jsonb,
                    cancel_hours    INTEGER,
                    cancel_charge   BOOLEAN NOT NULL DEFAULT FALSE,
                    free_cancels_per_student INTEGER NOT NULL DEFAULT 0
                );
            """)
            cur.execute("ALTER TABLE studio_teacher_settings ADD COLUMN IF NOT EXISTS free_cancels_per_student INTEGER NOT NULL DEFAULT 0;")
            cur.execute("ALTER TABLE studio_students ADD COLUMN IF NOT EXISTS free_cancels_used INTEGER NOT NULL DEFAULT 0;")
            cur.execute("ALTER TABLE studio_teacher_settings ADD COLUMN IF NOT EXISTS packages_enabled BOOLEAN NOT NULL DEFAULT FALSE;")
            cur.execute("ALTER TABLE studio_teacher_settings ADD COLUMN IF NOT EXISTS package_size INTEGER NOT NULL DEFAULT 4;")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS studio_payment_transactions (
                    id SERIAL PRIMARY KEY,
                    teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    family_id  INTEGER REFERENCES studio_families(id) ON DELETE CASCADE,
                    student_id INTEGER REFERENCES studio_students(id) ON DELETE CASCADE,
                    duration_min  INTEGER NOT NULL,
                    lessons_count INTEGER NOT NULL DEFAULT 1,
                    is_package    BOOLEAN NOT NULL DEFAULT FALSE,
                    package_size  INTEGER,
                    amount_cents  INTEGER,
                    note          TEXT,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            # Migrate existing pool balances into transactions once (skip if already done)
            cur.execute("""
                INSERT INTO studio_payment_transactions
                    (teacher_id, family_id, student_id, duration_min, lessons_count, note, created_at)
                SELECT p.teacher_id, p.family_id, p.student_id, p.duration_min, p.lessons_paid,
                       'Opening balance', NOW()
                FROM studio_payment_pools p
                WHERE p.lessons_paid > 0
                  AND NOT EXISTS (
                      SELECT 1 FROM studio_payment_transactions t
                      WHERE t.teacher_id = p.teacher_id
                        AND t.duration_min = p.duration_min
                        AND (t.family_id  IS NOT DISTINCT FROM p.family_id)
                        AND (t.student_id IS NOT DISTINCT FROM p.student_id)
                  )
            """)
            # Cancel future booked lessons whose recurring slot has been deactivated
            cur.execute("""
                UPDATE lessons
                SET status = 'cancelled', cancelled_at = NOW()
                WHERE recurring_lesson_id IS NOT NULL
                  AND lesson_date >= CURRENT_DATE
                  AND status = 'booked'
                  AND recurring_lesson_id IN (
                      SELECT id FROM recurring_lessons WHERE active = FALSE
                  )
            """)
            # Delete any non-booked lessons (today or future) for ACTIVE recurring slots
            # (NULL-status rows from before explicit status='booked' was added,
            # and cancelled rows left behind before the re-assign path used DELETE)
            cur.execute("""
                DELETE FROM lessons
                WHERE recurring_lesson_id IS NOT NULL
                  AND lesson_date >= CURRENT_DATE
                  AND (status IS NULL OR status = 'cancelled')
                  AND recurring_lesson_id IN (
                      SELECT id FROM recurring_lessons WHERE active = TRUE
                  )
            """)
            # Recreate booked lessons for every active recurring slot so the
            # student sees them without the teacher having to re-add the assignment
            cur.execute("""
                SELECT id, teacher_id, student_id, external_name, external_email,
                       weekday, lesson_time, COALESCE(duration_min, 30)
                FROM recurring_lessons WHERE active = TRUE
            """)
            active_slots = cur.fetchall()
            today_d = datetime.now().date()
            now_t = datetime.now().time()
            for rid, t_id, s_id, ext_name, ext_email, wday, l_time, dur in active_slots:
                days_ahead = (wday - today_d.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7  # recurring assignments always start from next occurrence
                first_d = today_d + timedelta(days=days_ahead)
                for i in range(12):
                    lesson_date = first_d + timedelta(weeks=i)
                    cur.execute("""
                        INSERT INTO lessons
                            (teacher_id, student_id, lesson_date, lesson_time,
                             external_name, external_email, recurring_lesson_id,
                             duration_min, status)
                        SELECT %s, %s, %s, %s, %s, %s, %s, %s, 'booked'
                        WHERE NOT EXISTS (
                            SELECT 1 FROM lessons
                            WHERE teacher_id = %s
                              AND lesson_date = %s
                              AND lesson_time = %s
                        )
                    """, (t_id, s_id, lesson_date, l_time,
                          ext_name, ext_email, rid, dur,
                          t_id, lesson_date, l_time))

            # No-account contacts for casting / seating / staff assignments —
            # mirrors the lessons.external_name/external_email pattern so
            # rehearsal notifications can still reach someone without a login.
            cur.execute("ALTER TABLE student_roles ALTER COLUMN student_id DROP NOT NULL;")
            cur.execute("ALTER TABLE student_roles ADD COLUMN IF NOT EXISTS external_name TEXT;")
            cur.execute("ALTER TABLE student_roles ADD COLUMN IF NOT EXISTS external_email TEXT;")
            cur.execute("ALTER TABLE orchestra_seats ADD COLUMN IF NOT EXISTS external_name TEXT;")
            cur.execute("ALTER TABLE orchestra_seats ADD COLUMN IF NOT EXISTS external_email TEXT;")
            cur.execute("ALTER TABLE opera_staff ALTER COLUMN teacher_id DROP NOT NULL;")
            cur.execute("ALTER TABLE opera_staff ADD COLUMN IF NOT EXISTS external_name TEXT;")
            cur.execute("ALTER TABLE opera_staff ADD COLUMN IF NOT EXISTS external_email TEXT;")

            # ── Orchestra Manager tables ─────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orchestra_members (
                    id           SERIAL PRIMARY KEY,
                    org_id       INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    fullname     TEXT NOT NULL,
                    email        TEXT,
                    phone        TEXT,
                    instrument   TEXT,
                    section_family TEXT,
                    section_id   INTEGER REFERENCES orchestra_sections(id),
                    user_id      INTEGER REFERENCES users(id),
                    notes        TEXT,
                    active       BOOLEAN NOT NULL DEFAULT true,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS concert_pieces (
                    id           SERIAL PRIMARY KEY,
                    opera_id     INTEGER NOT NULL REFERENCES operas(id) ON DELETE CASCADE,
                    title        TEXT NOT NULL,
                    composer     TEXT,
                    opus         TEXT,
                    duration_min INTEGER,
                    sort_order   INTEGER NOT NULL DEFAULT 0,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS piece_seats (
                    id           SERIAL PRIMARY KEY,
                    piece_id     INTEGER NOT NULL REFERENCES concert_pieces(id) ON DELETE CASCADE,
                    section_id   INTEGER NOT NULL REFERENCES orchestra_sections(id) ON DELETE CASCADE,
                    chair_number INTEGER NOT NULL,
                    part_number  INTEGER NOT NULL DEFAULT 1,
                    member_id    INTEGER REFERENCES orchestra_members(id),
                    external_name  TEXT,
                    external_email TEXT,
                    UNIQUE(piece_id, section_id, chair_number, part_number)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orchestra_attendance (
                    id           SERIAL PRIMARY KEY,
                    rehearsal_id INTEGER NOT NULL REFERENCES rehearsals(id) ON DELETE CASCADE,
                    member_id    INTEGER NOT NULL REFERENCES orchestra_members(id) ON DELETE CASCADE,
                    status       TEXT NOT NULL DEFAULT 'attended',
                    notes        TEXT,
                    UNIQUE(rehearsal_id, member_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orchestra_subs (
                    id           SERIAL PRIMARY KEY,
                    org_id       INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    section_id   INTEGER NOT NULL REFERENCES orchestra_sections(id) ON DELETE CASCADE,
                    fullname     TEXT NOT NULL,
                    email        TEXT NOT NULL,
                    phone        TEXT,
                    is_preferred BOOLEAN NOT NULL DEFAULT false,
                    preferred_rank INTEGER,
                    notes        TEXT,
                    active       BOOLEAN NOT NULL DEFAULT true,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orchestra_sub_requests (
                    id               SERIAL PRIMARY KEY,
                    rehearsal_id     INTEGER NOT NULL REFERENCES rehearsals(id) ON DELETE CASCADE,
                    section_id       INTEGER NOT NULL REFERENCES orchestra_sections(id) ON DELETE CASCADE,
                    created_by       INTEGER NOT NULL REFERENCES users(id),
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    preferred_sent_at TIMESTAMPTZ,
                    all_sent_at      TIMESTAMPTZ,
                    status           TEXT NOT NULL DEFAULT 'open',
                    filled_by_sub_id INTEGER REFERENCES orchestra_subs(id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orchestra_sub_contacts (
                    id             SERIAL PRIMARY KEY,
                    sub_request_id INTEGER NOT NULL REFERENCES orchestra_sub_requests(id) ON DELETE CASCADE,
                    sub_id         INTEGER NOT NULL REFERENCES orchestra_subs(id) ON DELETE CASCADE,
                    tier           TEXT NOT NULL,
                    contacted_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    response       TEXT NOT NULL DEFAULT 'pending',
                    responded_at   TIMESTAMPTZ,
                    token          TEXT NOT NULL UNIQUE,
                    UNIQUE(sub_request_id, sub_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orchestra_rehearsal_sections (
                    rehearsal_id INTEGER NOT NULL REFERENCES rehearsals(id) ON DELETE CASCADE,
                    section_id   INTEGER NOT NULL REFERENCES orchestra_sections(id) ON DELETE CASCADE,
                    PRIMARY KEY (rehearsal_id, section_id)
                )
            """)
            cur.execute("ALTER TABLE orchestra_members ADD COLUMN IF NOT EXISTS part_label TEXT;")
            cur.execute("ALTER TABLE orchestra_members ADD COLUMN IF NOT EXISTS doublings TEXT;")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS doublings TEXT;")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orchestra_absence_requests (
                    id           SERIAL PRIMARY KEY,
                    rehearsal_id INTEGER NOT NULL REFERENCES rehearsals(id) ON DELETE CASCADE,
                    member_id    INTEGER NOT NULL REFERENCES orchestra_members(id) ON DELETE CASCADE,
                    reason       TEXT,
                    note         TEXT,
                    status       VARCHAR(20) NOT NULL DEFAULT 'pending',
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    reviewed_at  TIMESTAMPTZ,
                    reviewed_by  INTEGER REFERENCES users(id),
                    UNIQUE(rehearsal_id, member_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orchestra_section_coverage_contacts (
                    id                  SERIAL PRIMARY KEY,
                    absence_request_id  INTEGER NOT NULL REFERENCES orchestra_absence_requests(id) ON DELETE CASCADE,
                    member_id           INTEGER NOT NULL REFERENCES orchestra_members(id) ON DELETE CASCADE,
                    token               TEXT UNIQUE NOT NULL,
                    contacted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    response            VARCHAR(20) NOT NULL DEFAULT 'pending',
                    responded_at        TIMESTAMPTZ,
                    UNIQUE(absence_request_id, member_id)
                )
            """)
            cur.execute("ALTER TABLE orchestra_sub_requests ADD COLUMN IF NOT EXISTS section_contacted_at TIMESTAMPTZ;")
            cur.execute("ALTER TABLE orchestra_sub_requests ADD COLUMN IF NOT EXISTS absence_request_id INTEGER REFERENCES orchestra_absence_requests(id);")
    except Exception as e:
        print("❌ Neon connection failed:", e)
        raise
    yield
    if _connection_pool is not None:
        _connection_pool.closeall()
    print("🔻 App shutting down.")


# ========================================================
# APP SETUP
# ========================================================

app = FastAPI(lifespan=lifespan)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import traceback
    print(f"[ERROR] {request.method} {request.url.path}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Something went wrong. Please try again."},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://countrpnt.com",
        "https://www.countrpnt.com"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ========================================================
# DB CONNECTION
# ========================================================

# Connection pool — created once at startup, reused for every request.
# minconn=2 keeps a couple of connections warm; maxconn=20 caps total.
# Neon's free tier allows ~100 concurrent connections, so 20 is comfortable.
_connection_pool = None


def _init_pool():
    """Initialize the connection pool. Called from lifespan startup."""
    global _connection_pool
    _connection_pool = pg_pool.ThreadedConnectionPool(
        minconn=2,
        maxconn=80,
        dbname=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        host=os.getenv("POSTGRES_HOST"),
        port=os.getenv("POSTGRES_PORT"),
        sslmode=os.getenv("POSTGRES_SSL"),
        connect_timeout=10,      # fail fast if Neon is cold-starting
        keepalives=1,            # TCP keepalives so the OS detects dead connections
        keepalives_idle=60,      # start probing after 60 s of inactivity
        keepalives_interval=10,  # retry every 10 s
        keepalives_count=5,      # give up after 5 failed probes
    )


def get_conn():
    """
    Borrow a connection from the pool. Release via release_conn().
    If the pool is exhausted, retries a few times with small backoff
    before giving up — prevents instant 500s during brief traffic spikes.
    """
    import time
    if _connection_pool is None:
        _init_pool()

    for attempt in range(5):
        try:
            return _connection_pool.getconn()
        except pg_pool.PoolError:
            if attempt == 4:
                raise
            time.sleep(0.1 * (attempt + 1))  # 100ms, 200ms, 300ms, 400ms


def release_conn(conn):
    """Return a connection to the pool."""
    if _connection_pool is not None:
        _connection_pool.putconn(conn)


@contextmanager
def db_cursor(commit: bool = False):
    """
    Borrow a connection from the pool, yield a cursor, return connection
    when done. Commits only if requested; rolls back on exceptions.
    Stale/broken connections (Neon idle-timeout) are discarded so the pool
    never re-serves a dead connection.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()
        yield cur
        if commit:
            conn.commit()
        else:
            # Important: always end the transaction even for read-only queries.
            # Otherwise the connection stays in "idle in transaction" state.
            conn.rollback()
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        # Connection is broken — discard it from the pool so it is never reused.
        try:
            conn.rollback()
            _connection_pool.putconn(conn, close=True)
            conn = None
        except Exception:
            pass
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        if conn is not None:
            release_conn(conn)


# ========================================================
# HELPERS
# ========================================================

# Password hashing: bcrypt for new passwords, SHA-256 kept only to verify legacy hashes
# on login so we can transparently upgrade them.

def hash_password_bcrypt(pw: str) -> str:
    """Hash a password with bcrypt. Cost=12 is the sensible default in 2026."""
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(pw: str, stored_hash: str, pw_version: str) -> bool:
    """
    Check a plaintext password against whatever hash we have on file.
    Works with both bcrypt (new) and sha256 (legacy).
    """
    if pw_version == "bcrypt":
        try:
            return bcrypt.checkpw(pw.encode(), stored_hash.encode())
        except (ValueError, TypeError):
            return False
    # Legacy SHA-256 path
    return hashlib.sha256(pw.encode()).hexdigest() == stored_hash


# Sessions
SESSION_DURATION_DAYS = 7


def create_session(user_id: int, user_agent: Optional[str] = None) -> str:
    """Generate a random session token, store it, return the token."""
    token = secrets.token_urlsafe(32)  # 256 bits of entropy
    expires = datetime.now(EST) + timedelta(days=SESSION_DURATION_DAYS)
    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO sessions (token, user_id, expires_at, user_agent)
            VALUES (%s, %s, %s, %s)
        """, (token, user_id, expires, user_agent))
    return token


def get_user_from_session(token: Optional[str]):
    """
    Look up the user behind a session token. Returns a user dict or None.
    Also refreshes last_used_at as a side effect.
    """
    if not token:
        return None

    # Try the full query first (requires choir migration columns).
    # Fall back to a base query if any column is missing (migration not yet run).
    try:
        with db_cursor(commit=True) as cur:
            cur.execute("""
                SELECT u.id, u.username, u.fullname, u.email, u.role,
                       u.voice_type, u.specialty, s.expires_at, u.email_verified, u.theme,
                       u.org_id, u.instrument, u.teacher_type, u.teacher_instruments,
                       COALESCE(o.timezone, 'America/New_York') AS org_timezone,
                       COALESCE(o.org_type, 'opera') AS org_type,
                       u.section_id
                FROM sessions s
                JOIN users u ON u.id = s.user_id
                LEFT JOIN organizations o ON o.id = u.org_id
                WHERE s.token = %s
            """, (token,))
            row = cur.fetchone()
            if not row:
                return None

            expires_at = row[7]
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=pytz.utc)
            if expires_at < datetime.now(pytz.utc):
                cur.execute("DELETE FROM sessions WHERE token = %s", (token,))
                return None

            cur.execute("UPDATE sessions SET last_used_at = NOW() WHERE token = %s", (token,))

        return {
            "id": row[0], "username": row[1], "fullname": row[2],
            "email": row[3], "role": row[4],
            "voice_type": row[5], "specialty": row[6],
            "email_verified": row[8],
            "theme": row[9] or "queen-of-the-night",
            "org_id": row[10],
            "instrument": row[11],
            "teacher_type": row[12] or "vocal",
            "teacher_instruments": row[13] or "",
            "org_timezone": row[14] or "America/New_York",
            "org_type": row[15] or "opera",
            "section_id": row[16],
        }
    except Exception:
        # Fallback: query without choir-specific columns (pre-migration schema)
        pass

    try:
        with db_cursor(commit=True) as cur:
            cur.execute("""
                SELECT u.id, u.username, u.fullname, u.email, u.role,
                       u.voice_type, u.specialty, s.expires_at, u.email_verified, u.theme,
                       u.org_id, u.instrument, u.teacher_type, u.teacher_instruments,
                       COALESCE(o.timezone, 'America/New_York') AS org_timezone
                FROM sessions s
                JOIN users u ON u.id = s.user_id
                LEFT JOIN organizations o ON o.id = u.org_id
                WHERE s.token = %s
            """, (token,))
            row = cur.fetchone()
            if not row:
                return None

            expires_at = row[7]
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=pytz.utc)
            if expires_at < datetime.now(pytz.utc):
                cur.execute("DELETE FROM sessions WHERE token = %s", (token,))
                return None

            cur.execute("UPDATE sessions SET last_used_at = NOW() WHERE token = %s", (token,))

        return {
            "id": row[0], "username": row[1], "fullname": row[2],
            "email": row[3], "role": row[4],
            "voice_type": row[5], "specialty": row[6],
            "email_verified": row[8],
            "theme": row[9] or "queen-of-the-night",
            "org_id": row[10],
            "instrument": row[11],
            "teacher_type": row[12] or "vocal",
            "teacher_instruments": row[13] or "",
            "org_timezone": row[14] or "America/New_York",
            "org_type": "opera",
            "section_id": None,
        }
    except Exception:
        return None


def get_org_tz(user: dict):
    """Return the pytz timezone for a user's organization. Defaults to Eastern."""
    tz_name = (user or {}).get("org_timezone", "America/New_York") or "America/New_York"
    try:
        return pytz.timezone(tz_name)
    except pytz.exceptions.UnknownTimeZoneError:
        return pytz.timezone("America/New_York")


_LESSON_CONFIG_DEFAULTS = {
    "lessons_enabled": False,
    "duration_options": [30],
    "duration_min": 30,
    "max_per_day": 1,
    "booking_open_hour": 21,
    "booking_close_hour": 18,
    "cancellation_notice_min": 60,
    "has_lunch_break": True,
    "max_per_teacher": 5,
}

def _parse_durations(raw: str) -> list:
    """Parse a comma-separated duration string like '30,45,60' into a sorted int list."""
    options = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit() and int(part) > 0:
            options.append(int(part))
    return sorted(set(options)) or [30]

def get_org_lesson_config(org_id) -> dict:
    """Return lesson scheduling config for an org, falling back to safe defaults."""
    if not org_id:
        return dict(_LESSON_CONFIG_DEFAULTS)
    try:
        with db_cursor() as cur:
            cur.execute("""
                SELECT
                    COALESCE(lessons_enabled, FALSE),
                    COALESCE(lesson_durations, '30'),
                    COALESCE(lesson_max_per_day, 1),
                    COALESCE(lesson_booking_open_hour, 21),
                    COALESCE(lesson_booking_close_hour, 18),
                    COALESCE(lesson_cancellation_notice_min, 60),
                    COALESCE(lesson_has_lunch_break, TRUE),
                    COALESCE(lesson_max_per_teacher, 5)
                FROM organizations WHERE id = %s
            """, (org_id,))
            row = cur.fetchone()
        if not row:
            return dict(_LESSON_CONFIG_DEFAULTS)
        duration_options = _parse_durations(row[1])
        return {
            "lessons_enabled": bool(row[0]),
            "duration_options": duration_options,
            "duration_min": min(duration_options),
            "max_per_day": row[2],
            "booking_open_hour": row[3],
            "booking_close_hour": row[4],
            "cancellation_notice_min": row[5],
            "has_lunch_break": bool(row[6]),
            "max_per_teacher": row[7],
        }
    except Exception:
        return dict(_LESSON_CONFIG_DEFAULTS)


def delete_session(token: str):
    if not token:
        return
    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM sessions WHERE token = %s", (token,))


def delete_all_sessions_for_user(user_id: int, except_token: Optional[str] = None):
    """Kill all sessions for a user. Used when password changes."""
    with db_cursor(commit=True) as cur:
        if except_token:
            cur.execute(
                "DELETE FROM sessions WHERE user_id = %s AND token != %s",
                (user_id, except_token)
            )
        else:
            cur.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))


def current_user(request: Request):
    """
    Extract the session token from the cookie and return the user.
    Returns None if no valid session.
    """
    token = request.cookies.get("session")
    return get_user_from_session(token)


def require_user(request: Request, role: Optional[str] = None):
    """
    Returns the current user dict, or raises an auth error.
    If role="admin", accepts any admin-level role (admin, head_admin, system_admin).
    Otherwise enforces an exact role match.
    """
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    if role == "admin":
        if user["role"] not in ADMIN_ROLES:
            raise HTTPException(status_code=403, detail="Not authorized")
    elif role and user["role"] != role:
        raise HTTPException(status_code=403, detail="Not authorized")
    return user


def require_opera_admin(request: Request):
    """Admin-level check that explicitly excludes standalone orchestra orgs."""
    user = require_user(request, role="admin")
    if user.get("org_type") == "orchestra":
        raise HTTPException(status_code=403, detail="Use /orchestra/* endpoints for orchestra orgs")
    return user


def require_head_admin(request: Request):
    """Returns the current user only if they are head_admin or system_admin."""
    user = require_user(request)
    if user["role"] not in ("head_admin", "system_admin"):
        raise HTTPException(status_code=403, detail="Head admin access required")
    return user


def require_studio_teacher(request: Request):
    user = require_user(request)
    if user["role"] != "studio_teacher":
        raise HTTPException(status_code=403, detail="Studio teacher access required")
    return user


_org_id_cache: dict[str, int] = {}

def get_org_id(slug: str = DEFAULT_ORG_SLUG) -> Optional[int]:
    """Look up an org's id from its slug. Cached in memory — orgs don't change."""
    if slug in _org_id_cache:
        return _org_id_cache[slug]
    with db_cursor() as cur:
        cur.execute("SELECT id FROM organizations WHERE slug = %s", (slug,))
        row = cur.fetchone()
        if row:
            _org_id_cache[slug] = row[0]
            return row[0]
    return None


def find_user(username: str, role: Optional[str] = None):
    """Find a user by username (within the default org). Returns dict or None."""
    org_id = get_org_id()
    if org_id is None:
        return None
    with db_cursor() as cur:
        if role:
            cur.execute("""
                SELECT id, username, fullname, email, role, voice_type, specialty
                FROM users
                WHERE org_id = %s AND username = %s AND role = %s
            """, (org_id, username, role))
        else:
            cur.execute("""
                SELECT id, username, fullname, email, role, voice_type, specialty
                FROM users
                WHERE org_id = %s AND username = %s
            """, (org_id, username))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0], "username": row[1], "fullname": row[2],
            "email": row[3], "role": row[4],
            "voice_type": row[5], "specialty": row[6],
        }

def is_booking_window_open_for(target_date, tz=None, open_hour=21, close_hour=18) -> bool:
    """Window for target_date opens at open_hour the evening before and closes at close_hour on the day itself."""
    if tz is None:
        tz = EST
    now_local = datetime.now(tz)
    if now_local.date() == target_date:
        return now_local.hour < close_hour
    if now_local.date() == target_date - timedelta(days=1):
        return now_local.hour >= open_hour
    return False

def get_student_rehearsal_conflicts(student_id: int, date_obj, time_obj, tz=None) -> bool:
    """
    Check if a 30-min coaching slot overlaps with any rehearsal the student
    needs to attend.

    The student attends based on attendance_type + which casts are called
    (from rehearsal_casts, falling back to rehearsals.cast_id for old data):

      - 'principals' → student is principal in one of the called casts
      - 'chorus'     → student is in the opera AND has no principal role anywhere
                        (note: chorus is opera-wide; we ignore called casts here)
      - 'full'       → student is in the opera AND principal in a called cast,
                        OR student is chorus (anywhere) in the opera
      - 'coaching'   → student is principal in one of the called (role, cast) combos

    Touching at endpoints doesn't count as overlap. Returns True if conflict.
    """
    _tz = tz if tz is not None else EST
    slot_start = _tz.localize(datetime.combine(date_obj, time_obj))
    slot_end = slot_start + timedelta(minutes=30)

    with db_cursor() as cur:
        # Get all overlapping rehearsals + their attendance setup
        cur.execute("""
            SELECT
                r.id,
                r.opera_id,
                r.cast_id,          -- legacy; used as fallback
                r.attendance_type
            FROM rehearsals r
            WHERE r.start_time < %s
              AND r.end_time > %s
        """, (slot_end, slot_start))
        rehearsals = cur.fetchall()

        if not rehearsals:
            return False

        # Is the student assigned to any opera?
        cur.execute("""
            SELECT opera_id, cast_id
            FROM student_assignments
            WHERE student_id = %s
        """, (student_id,))
        student_assignments = cur.fetchall()
        student_opera_ids = {a[0] for a in student_assignments}

        # Principal-role assignments (opera_id, cast_id, role_name)
        cur.execute("""
            SELECT opera_id, cast_id, role_name
            FROM student_roles
            WHERE student_id = %s
              AND LOWER(role_name) <> 'chorus'
        """, (student_id,))
        principal_roles = cur.fetchall()
        # Lookup sets
        principal_cast_keys = {(p[0], p[1]) for p in principal_roles}      # (opera, cast)
        principal_role_keys = {(p[0], p[1], p[2]) for p in principal_roles}  # (opera, cast, role)
        principal_operas = {p[0] for p in principal_roles}

        # For each overlapping rehearsal, ask: does this student attend?
        for r_id, r_opera, r_cast_legacy, att_type in rehearsals:
            if r_opera not in student_opera_ids:
                continue  # student isn't in this opera at all

            # Fetch called casts from rehearsal_casts; fall back to legacy cast_id
            cur.execute(
                "SELECT cast_id FROM rehearsal_casts WHERE rehearsal_id = %s",
                (r_id,)
            )
            called_casts = {row[0] for row in cur.fetchall()}
            if not called_casts and r_cast_legacy is not None:
                called_casts = {r_cast_legacy}
            # Empty set = all casts (no filter)

            def cast_matches(cast_id):
                return not called_casts or cast_id in called_casts

            if att_type == "principals":
                # Student is principal of a called cast in this opera
                for (op, cst) in principal_cast_keys:
                    if op == r_opera and cast_matches(cst):
                        return True
                continue

            if att_type == "chorus":
                # Chorus rehearsal: student is in the opera AND has no principal
                # role anywhere. (Chorus is opera-wide, ignore called_casts.)
                if r_opera not in principal_operas:
                    return True
                continue

            if att_type == "full":
                # Full rehearsal: student is principal in a called cast,
                # OR student is chorus (= assigned to opera with no principal role
                # in any of the called casts).
                is_principal_here = any(
                    op == r_opera and cast_matches(cst)
                    for (op, cst) in principal_cast_keys
                )
                if is_principal_here:
                    return True
                # Is student chorus in one of the called casts?
                # Chorus means: no principal role in ANY of the called casts.
                # Equivalently: student is in opera and not-a-principal in at least one called cast.
                # If no called casts (all), student attends if they're in opera.
                if not called_casts:
                    return True  # full opera-wide rehearsal, student attends (either as principal or chorus)
                for target_cast in called_casts:
                    has_principal_role_in_this_cast = (r_opera, target_cast) in principal_cast_keys
                    if not has_principal_role_in_this_cast:
                        return True  # chorus in this cast
                continue

            if att_type == "coaching":
                # Coaching: student attends if (opera, cast, role) matches one of
                # the (called cast, called role) pairs.
                cur.execute(
                    "SELECT role_name FROM rehearsal_roles WHERE rehearsal_id = %s",
                    (r_id,)
                )
                called_roles = {row[0] for row in cur.fetchall()}
                if not called_roles:
                    continue  # coaching with no roles listed — nobody attends
                # Student's principal roles in this opera
                for (op, cst, rn) in principal_role_keys:
                    if op != r_opera:
                        continue
                    if rn not in called_roles:
                        continue
                    if not cast_matches(cst):
                        continue
                    return True
                continue

    return False
# Lunch is 1pm–2pm for everyone, for now
LUNCH_START = dtime(13, 0)
LUNCH_END = dtime(14, 0)

def get_student_conflict_context(student_id: int):
    """
    Fetch all data needed for conflict-checking for one student, in bulk.
    Returns a dict that can be passed to check_rehearsal_conflict_cached().

    This replaces the per-slot DB calls in get_student_rehearsal_conflicts —
    the data is the same, just fetched once instead of N times.
    """
    with db_cursor() as cur:
        # Student's opera assignments
        cur.execute("""
            SELECT opera_id, cast_id
            FROM student_assignments
            WHERE student_id = %s
        """, (student_id,))
        student_assignments = cur.fetchall()

        # Student's principal roles
        cur.execute("""
            SELECT opera_id, cast_id, role_name
            FROM student_roles
            WHERE student_id = %s
              AND LOWER(role_name) <> 'chorus'
        """, (student_id,))
        principal_roles = cur.fetchall()

        # All rehearsals with their called casts + roles, as a flat join
        # We'll filter by time in Python, so we fetch a window of recent + upcoming rehearsals.
        cur.execute("""
            SELECT
                r.id,
                r.opera_id,
                r.cast_id,
                r.attendance_type,
                r.start_time,
                r.end_time
            FROM rehearsals r
            WHERE r.end_time >= NOW() - INTERVAL '1 day'
              AND r.start_time <= NOW() + INTERVAL '2 days'
        """)
        rehearsals = cur.fetchall()

        # All rehearsal_casts rows for those rehearsals (in one query)
        rehearsal_ids = [r[0] for r in rehearsals]
        casts_by_rehearsal = {}
        if rehearsal_ids:
            cur.execute("""
                SELECT rehearsal_id, cast_id
                FROM rehearsal_casts
                WHERE rehearsal_id = ANY(%s)
            """, (rehearsal_ids,))
            for r_id, c_id in cur.fetchall():
                casts_by_rehearsal.setdefault(r_id, set()).add(c_id)

        # All rehearsal_roles rows for those rehearsals (in one query)
        roles_by_rehearsal = {}
        if rehearsal_ids:
            cur.execute("""
                SELECT rehearsal_id, role_name
                FROM rehearsal_roles
                WHERE rehearsal_id = ANY(%s)
            """, (rehearsal_ids,))
            for r_id, rn in cur.fetchall():
                roles_by_rehearsal.setdefault(r_id, set()).add(rn)

    return {
        "student_opera_ids": {a[0] for a in student_assignments},
        "principal_cast_keys": {(p[0], p[1]) for p in principal_roles},
        "principal_role_keys": {(p[0], p[1], p[2]) for p in principal_roles},
        "principal_operas": {p[0] for p in principal_roles},
        "rehearsals": rehearsals,  # list of tuples (id, opera_id, cast_id_legacy, attendance_type, start, end)
        "casts_by_rehearsal": casts_by_rehearsal,
        "roles_by_rehearsal": roles_by_rehearsal,
    }

def get_teacher_availability_context(target_date):
    """
    Fetch availability data for ALL teachers on a given date in one batch.
    Returns a dict that can be passed to get_available_slots().

    This replaces the per-teacher DB calls in /student/today —
    all the same data, fetched once for all teachers instead of N times.
    """
    weekday = target_date.weekday()

    with db_cursor() as cur:
        # All exceptions for this date across all teachers
        cur.execute("""
            SELECT teacher_id, start_time, end_time, active
            FROM availability_exceptions
            WHERE exception_date = %s
        """, (target_date,))
        exception_rows = cur.fetchall()

        # All weekly availability for this weekday across all teachers
        cur.execute("""
            SELECT teacher_id, start_time, end_time
            FROM weekly_availability
            WHERE weekday = %s AND active = TRUE
        """, (weekday,))
        weekly_rows = cur.fetchall()

        # All booked lessons for this date across all teachers
        cur.execute("""
            SELECT teacher_id, lesson_time, COALESCE(duration_min, 30)
            FROM lessons
            WHERE lesson_date = %s AND status = 'booked'
        """, (target_date,))
        booked_rows = cur.fetchall()

    # Organize by teacher
    # For exceptions: we need to know (a) whether the teacher has ANY exception,
    # and (b) the active ranges with start_time/end_time set.
    exceptions_by_teacher = {}
    has_any_exception_by_teacher = set()
    for teacher_id, start_t, end_t, active in exception_rows:
        has_any_exception_by_teacher.add(teacher_id)
        if active and start_t is not None and end_t is not None:
            exceptions_by_teacher.setdefault(teacher_id, []).append((start_t, end_t))

    weekly_by_teacher = {}
    for teacher_id, start_t, end_t in weekly_rows:
        weekly_by_teacher.setdefault(teacher_id, []).append((start_t, end_t))

    booked_by_teacher = {}
    for teacher_id, lesson_time, dur in booked_rows:
        if lesson_time is not None:
            booked_by_teacher.setdefault(teacher_id, []).append((lesson_time, dur))

    return {
        "target_date": target_date,
        "weekday": weekday,
        "exceptions_by_teacher": exceptions_by_teacher,
        "has_any_exception_by_teacher": has_any_exception_by_teacher,
        "weekly_by_teacher": weekly_by_teacher,
        "booked_by_teacher": booked_by_teacher,
    }


def check_rehearsal_conflict_cached(ctx: dict, date_obj, time_obj, tz=None) -> bool:
    """
    Same logic as get_student_rehearsal_conflicts, but uses a pre-fetched
    context dict instead of hitting the DB. For use when checking many
    slots for the same student.
    """
    _tz = tz if tz is not None else EST
    slot_start = _tz.localize(datetime.combine(date_obj, time_obj))
    slot_end = slot_start + timedelta(minutes=30)

    # Filter rehearsals to only those that overlap this slot
    overlapping = [
        r for r in ctx["rehearsals"]
        if r[4] < slot_end and r[5] > slot_start
    ]
    if not overlapping:
        return False

    principal_cast_keys = ctx["principal_cast_keys"]
    principal_role_keys = ctx["principal_role_keys"]
    principal_operas = ctx["principal_operas"]
    student_opera_ids = ctx["student_opera_ids"]
    casts_by_rehearsal = ctx["casts_by_rehearsal"]
    roles_by_rehearsal = ctx["roles_by_rehearsal"]

    for r_id, r_opera, r_cast_legacy, att_type, _, _ in overlapping:
        if r_opera not in student_opera_ids:
            continue

        called_casts = casts_by_rehearsal.get(r_id, set())
        if not called_casts and r_cast_legacy is not None:
            called_casts = {r_cast_legacy}

        def cast_matches(cast_id):
            return not called_casts or cast_id in called_casts

        if att_type == "principals":
            for (op, cst) in principal_cast_keys:
                if op == r_opera and cast_matches(cst):
                    return True
            continue

        if att_type == "chorus":
            if r_opera not in principal_operas:
                return True
            continue

        if att_type == "full":
            is_principal_here = any(
                op == r_opera and cast_matches(cst)
                for (op, cst) in principal_cast_keys
            )
            if is_principal_here:
                return True
            if not called_casts:
                return True
            for target_cast in called_casts:
                if (r_opera, target_cast) not in principal_cast_keys:
                    return True
            continue

        if att_type == "coaching":
            called_roles = roles_by_rehearsal.get(r_id, set())
            if not called_roles:
                continue
            for (op, cst, rn) in principal_role_keys:
                if op != r_opera:
                    continue
                if rn not in called_roles:
                    continue
                if not cast_matches(cst):
                    continue
                return True
            continue

    return False


def get_available_slots(teacher_id: int, target_date, student_id: Optional[int] = None, conflict_ctx: Optional[dict] = None, avail_ctx: Optional[dict] = None, tz=None, duration_min: int = 30, has_lunch_break: bool = True, min_dt=None):
    """
    Build the list of available slots for a given teacher on a given date.

    Slot length is `duration_min` (default 30). Filtering rules:
      - exception rows for this date (if any), which fully override the weekly template
      - otherwise, the teacher's weekly availability for that weekday
      - lunch hour (1pm–2pm) excluded when has_lunch_break=True
      - past times on today excluded
      - slots already booked by another student excluded
      - if student_id given, slots conflicting with that student's rehearsals excluded

    Returns a list of "HH:MM" strings.
    """
    _tz = tz if tz is not None else EST
    now_local = datetime.now(_tz)
    weekday = target_date.weekday()
    step = timedelta(minutes=duration_min)

    if avail_ctx is not None:
        # Fast path: use pre-fetched bulk data
        if teacher_id in avail_ctx["has_any_exception_by_teacher"]:
            ranges = avail_ctx["exceptions_by_teacher"].get(teacher_id, [])
        else:
            ranges = avail_ctx["weekly_by_teacher"].get(teacher_id, [])
        booked = avail_ctx["booked_by_teacher"].get(teacher_id, [])
    else:
        # Fallback path: original per-teacher queries (used by /student/book etc.)
        with db_cursor() as cur:
            cur.execute("""
                SELECT start_time, end_time
                FROM availability_exceptions
                WHERE teacher_id = %s
                  AND exception_date = %s
                  AND active = TRUE
                  AND start_time IS NOT NULL
                  AND end_time IS NOT NULL
            """, (teacher_id, target_date))
            exception_rows = cur.fetchall()

            cur.execute("""
                SELECT 1 FROM availability_exceptions
                WHERE teacher_id = %s AND exception_date = %s
                LIMIT 1
            """, (teacher_id, target_date))
            has_any_exception = cur.fetchone() is not None

            if has_any_exception:
                ranges = exception_rows
            else:
                cur.execute("""
                    SELECT start_time, end_time
                    FROM weekly_availability
                    WHERE teacher_id = %s AND weekday = %s AND active = TRUE
                """, (teacher_id, weekday))
                ranges = cur.fetchall()

            cur.execute("""
                SELECT lesson_time, COALESCE(duration_min, 30)
                FROM lessons
                WHERE teacher_id = %s AND lesson_date = %s AND status = 'booked'
            """, (teacher_id, target_date))
            booked = [(r[0], r[1]) for r in cur.fetchall() if r[0]]

    # Build occupied ranges (minutes since midnight) from booked lessons
    booked_ranges = [
        (t.hour * 60 + t.minute, t.hour * 60 + t.minute + d)
        for t, d in booked
    ]

    slots = []
    for start_t, end_t in ranges:
        cur_dt = _tz.localize(datetime.combine(target_date, start_t))
        end_dt = _tz.localize(datetime.combine(target_date, end_t))

        while cur_dt < end_dt:
            slot_time = cur_dt.time()

            # skip slots before the booking cutoff
            if min_dt is not None:
                if cur_dt <= min_dt:
                    cur_dt += step
                    continue
            elif target_date == now_local.date() and cur_dt <= now_local:
                cur_dt += step
                continue

            # skip slots that overlap the lunch hour (optional)
            if has_lunch_break:
                slot_end_t = (cur_dt + step).time()
                if slot_time < LUNCH_END and slot_end_t > LUNCH_START:
                    cur_dt += step
                    continue

            # skip slots whose time window overlaps any booked lesson's window
            t_min = slot_time.hour * 60 + slot_time.minute
            t_end = t_min + duration_min
            if any(t_end > s and t_min < e for s, e in booked_ranges):
                cur_dt += step
                continue

            # skip slots that conflict with the student's rehearsals
            if student_id is not None:
                if conflict_ctx is not None:
                    if check_rehearsal_conflict_cached(conflict_ctx, target_date, slot_time, tz=_tz):
                        cur_dt += step
                        continue
                elif get_student_rehearsal_conflicts(student_id, target_date, slot_time, tz=_tz):
                    cur_dt += step
                    continue

            slots.append(cur_dt.strftime("%H:%M"))
            cur_dt += step

    return slots

def classify_slot_time(slot_hhmm: str) -> str:
    """Return 'morning' for slots before 1pm, 'afternoon' for slots at/after 2pm."""
    h, m = slot_hhmm.split(":")
    t = dtime(int(h), int(m))
    if t < LUNCH_START:
        return "morning"
    return "afternoon"


# ========================================================
# MODELS
# ========================================================

class LoginData(BaseModel):
    username: str
    password: str
    org: Optional[str] = None   # accepted but ignored; we use DEFAULT_ORG_SLUG


class ScheduleEntry(BaseModel):
    weekday: int    # 0 = Monday ... 6 = Sunday
    start_time: str  # "HH:MM"
    end_time: str    # "HH:MM"


class AvailabilityRequestData(BaseModel):
    username: Optional[str] = None   # ignored; kept for backward compatibility
    scope: str
    effective_week_start: Optional[str] = None
    schedule: list[ScheduleEntry]
    note: Optional[str] = None


# ========================================================
# HTML ROUTES
# ========================================================

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    return templates.TemplateResponse(request, "login.html")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    return templates.TemplateResponse(request, "signup.html")


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return templates.TemplateResponse(request, "privacy.html")


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    return templates.TemplateResponse(request, "opera/admin.html")


@app.get("/orchestra-admin", response_class=HTMLResponse)
def orchestra_admin_page(request: Request):
    """Alias for standalone orchestra orgs — serves the same admin dashboard."""
    return templates.TemplateResponse(request, "opera/admin.html")


@app.get("/teacher", response_class=HTMLResponse)
def teacher_page(request: Request):
    return templates.TemplateResponse(request, "opera/teacher.html")


@app.get("/studio-teacher", response_class=HTMLResponse)
def studio_teacher_page(request: Request):
    return templates.TemplateResponse(request, "studio/teacher.html")


@app.get("/student", response_class=HTMLResponse)
def student_page(request: Request):
    return templates.TemplateResponse(request, "opera/student.html")


@app.get("/orchestra-member", response_class=HTMLResponse)
def orchestra_member_page(request: Request):
    return templates.TemplateResponse(request, "opera/orchestra_member.html")


# ========================================================
# AUTH
# ========================================================

@app.post("/login")
def login(data: LoginData, request: Request, response: Response):
    username = data.username.strip().lower()

    # Look up by username globally — usernames are unique across the system.
    # Try with org_type first (requires choir migration); fall back if column absent.
    user_id = role = fullname = stored_hash = pw_version = None
    org_type = "opera"
    try:
        with db_cursor() as cur:
            cur.execute("""
                SELECT u.id, u.role, u.fullname, u.password_hash, u.pw_version,
                       COALESCE(o.org_type, 'opera') AS org_type
                FROM users u
                LEFT JOIN organizations o ON o.id = u.org_id
                WHERE u.username = %s
                LIMIT 1
            """, (username,))
            row = cur.fetchone()
        if not row:
            return {"success": False, "message": "Invalid username or password"}
        user_id, role, fullname, stored_hash, pw_version, org_type = row
    except Exception:
        # org_type column not yet migrated — use base query
        with db_cursor() as cur:
            cur.execute("""
                SELECT u.id, u.role, u.fullname, u.password_hash, u.pw_version
                FROM users u
                WHERE u.username = %s
                LIMIT 1
            """, (username,))
            row = cur.fetchone()
        if not row:
            return {"success": False, "message": "Invalid username or password"}
        user_id, role, fullname, stored_hash, pw_version = row

    # Verify the password (works for both bcrypt and legacy sha256)
    if not verify_password(data.password, stored_hash, pw_version):
        return {"success": False, "message": "Invalid username or password"}

    # Transparent upgrade: if this was a SHA-256 hash, re-hash with bcrypt now
    if pw_version != "bcrypt":
        new_hash = hash_password_bcrypt(data.password)
        with db_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE users SET password_hash = %s, pw_version = 'bcrypt' WHERE id = %s",
                (new_hash, user_id)
            )

    # Create a session and set it as an HttpOnly cookie
    user_agent = request.headers.get("user-agent", "")[:500]
    token = create_session(user_id, user_agent=user_agent)
    response.set_cookie(
        key="session",
        value=token,
        max_age=SESSION_DURATION_DAYS * 24 * 3600,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
        domain=".countrpnt.com",
    )

    return {
        "success": True,
        "role": role,
        "org_type": org_type,
        "username": username,
        "fullname": fullname,
        "org": DEFAULT_ORG_SLUG,
    }


@app.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("session")
    delete_session(token) if token else None
    response.delete_cookie("session", path="/", domain=".countrpnt.com")
    return {"success": True}


@app.get("/me")
def me(request: Request):
    """Returns the current user's basic info. Used by frontend on page load."""
    user = current_user(request)
    if not user:
        return {"logged_in": False}
    org_name = None
    org_logo_url = None
    if user.get("org_id"):
        with db_cursor() as cur:
            cur.execute("SELECT name, logo_url FROM organizations WHERE id = %s", (user["org_id"],))
            row = cur.fetchone()
        if row:
            org_name = row[0]
            org_logo_url = row[1]
    return {
        "logged_in": True,
        "username": user["username"],
        "fullname": user["fullname"],
        "role": user["role"],
        "email_verified": user.get("email_verified", True),
        "theme": user.get("theme", "queen-of-the-night"),
        "org_name": org_name,
        "org_logo_url": org_logo_url,
        "org_type": user.get("org_type", "opera"),
        "section_id": user.get("section_id"),
    }


ALLOWED_VOICE_TYPES = {
    "soprano", "mezzo-soprano", "tenor",
    "baritone", "bass-baritone", "bass",
}


# ========================================================
# EMAIL + PASSWORD RESET
# ========================================================

import resend
import anthropic as _anthropic

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "noreply@countrpnt.com")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "Countrpnt")
APP_URL = os.environ.get("APP_URL", "https://countrpnt.com")



PASSWORD_RESET_TOKEN_HOURS = 1


def _sender_from_username(username: str) -> str:
    """Build a @countrpnt.com sender address from a username."""
    domain = EMAIL_FROM.split("@")[-1] if "@" in EMAIL_FROM else "countrpnt.com"
    safe = re.sub(r"[^a-zA-Z0-9._+-]", "", username)
    return f"{safe}@{domain}"


def send_email(to: str, subject: str, html_body: str, text_body: str,
               from_name: str = None, from_address: str = None,
               reply_to: str = None) -> bool:
    """
    Send an email via Resend. Returns True on success, False on failure.
    Pass from_name/from_address to send as a specific user; reply_to routes
    replies to their real inbox. Logs but does not raise.
    """
    if not RESEND_API_KEY:
        print("[email] RESEND_API_KEY not configured; skipping send.")
        return False

    sender_name = from_name or EMAIL_FROM_NAME
    sender_addr = from_address or EMAIL_FROM

    resend.api_key = RESEND_API_KEY
    payload = {
        "from": f"{sender_name} <{sender_addr}>",
        "to": [to],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
    if reply_to:
        payload["reply_to"] = reply_to

    try:
        resend.Emails.send(payload)
        print(f"[email] Sent to {to} from {sender_addr}: {subject}")
        return True
    except Exception as e:
        print(f"[email] Failed to send to {to}: {e}")
        return False


def render_password_reset_email(reset_url: str, fullname: str) -> tuple[str, str]:
    """Returns (html, plain_text) for the password reset email."""
    html = f"""\
<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 560px; margin: 40px auto; padding: 24px; color: #222;">
    <h2 style="color: #444;">Password Reset Request</h2>
    <p>Hi {fullname},</p>
    <p>Someone (hopefully you) requested a password reset for your account on CountrPnt.</p>
    <p style="margin: 32px 0;">
        <a href="{reset_url}" style="background: #6b5b3e; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: 500;">Reset your password</a>
    </p>
    <p style="color: #666; font-size: 14px;">Or copy and paste this link into your browser:</p>
    <p style="color: #666; font-size: 13px; word-break: break-all;">{reset_url}</p>
    <p style="color: #666; font-size: 14px; margin-top: 32px;">
        This link expires in {PASSWORD_RESET_TOKEN_HOURS} hour{"s" if PASSWORD_RESET_TOKEN_HOURS != 1 else ""}.
        If you didn't request a password reset, you can safely ignore this email.
    </p>
</body>
</html>
"""
    text = f"""\
Hi {fullname},

Someone (hopefully you) requested a password reset for your account on CountrPnt.

Click this link to reset your password:
{reset_url}

This link expires in {PASSWORD_RESET_TOKEN_HOURS} hour{"s" if PASSWORD_RESET_TOKEN_HOURS != 1 else ""}.

If you didn't request a password reset, you can safely ignore this email.
"""
    return html, text


@app.post("/auth/forgot-password")
def forgot_password(payload: dict):
    """
    Accept an email (and optional org_name) and send a password reset link.
    Always returns success so we don't leak which addresses are registered.
    """
    email = (payload.get("email") or "").strip().lower()
    org_name = (payload.get("org_name") or "").strip()
    if not email:
        return {"success": True}

    row = None
    with db_cursor() as cur:
        if org_name:
            # Prefer the user whose org name matches (case-insensitive partial match)
            cur.execute("""
                SELECT u.id, u.fullname FROM users u
                JOIN organizations o ON u.org_id = o.id
                WHERE u.email = %s AND o.name ILIKE %s
                LIMIT 1
            """, (email, f"%{org_name}%"))
            row = cur.fetchone()

        if not row:
            # Fall back to global lookup
            cur.execute("""
                SELECT id, fullname FROM users
                WHERE email = %s
                LIMIT 1
            """, (email,))
            row = cur.fetchone()

    if not row:
        return {"success": True}

    user_id, fullname = row

    # Generate token, store, send email
    token = secrets.token_urlsafe(32)
    expires = datetime.now(EST) + timedelta(hours=PASSWORD_RESET_TOKEN_HOURS)

    with db_cursor(commit=True) as cur:
        # Invalidate any existing unused tokens for this user (one active reset at a time)
        cur.execute("""
            UPDATE password_reset_tokens
            SET used_at = NOW()
            WHERE user_id = %s AND used_at IS NULL
        """, (user_id,))
        cur.execute("""
            INSERT INTO password_reset_tokens (token, user_id, expires_at)
            VALUES (%s, %s, %s)
        """, (token, user_id, expires))

    reset_url = f"{APP_URL}/reset-password?token={token}"
    html, text = render_password_reset_email(reset_url, fullname or "there")
    send_email(email, "Reset your password", html, text)

    return {"success": True}


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request):
    return templates.TemplateResponse(request, "reset.html")


@app.post("/auth/reset-password")
def reset_password(payload: dict):
    """
    Validate a reset token and set a new password.
    Invalidates all sessions for the user on success.
    """
    token = payload.get("token")
    new_password = payload.get("new_password")

    if not token or not new_password:
        return {"success": False, "message": "Missing token or password."}

    if len(new_password) < 8:
        return {"success": False, "message": "Password must be at least 8 characters."}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT user_id, expires_at, used_at
            FROM password_reset_tokens
            WHERE token = %s
        """, (token,))
        row = cur.fetchone()

        if not row:
            return {"success": False, "message": "Invalid or expired link."}

        user_id, expires_at, used_at = row

        if used_at is not None:
            return {"success": False, "message": "This link has already been used."}

        if expires_at < datetime.now(EST):
            return {"success": False, "message": "This link has expired. Request a new one."}

        # Update the password
        cur.execute("""
            UPDATE users
            SET password_hash = %s, pw_version = 'bcrypt'
            WHERE id = %s
        """, (hash_password_bcrypt(new_password), user_id))

        # Mark token used
        cur.execute("""
            UPDATE password_reset_tokens
            SET used_at = NOW()
            WHERE token = %s
        """, (token,))

    # Invalidate all sessions for this user — they should re-login with the new password
    delete_all_sessions_for_user(user_id)

    return {"success": True}


@app.post("/signup")
def signup(payload: dict):
    """
    Public signup is STUDENTS ONLY.
    Teachers and admins are invite-only — they get a link via /admin/invite.
    """
    role = payload.get("role")

    if role not in ("student", "orchestra_member"):
        return {"message": "Staff accounts are invite-only. Ask your admin for an invitation."}

    # Basic validation
    username = (payload.get("username") or "").strip().lower()
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    fullname = (payload.get("fullname") or "").strip()

    if not username or not email or not password or not fullname:
        return {"message": "All required fields must be filled in"}

    if len(password) < 8:
        return {"message": "Password must be at least 8 characters"}

    # Role-specific fields
    voice_type = None
    instrument = None

    if role == "student":
        vt = (payload.get("voice_type") or "").strip().lower()
        if vt not in ALLOWED_VOICE_TYPES:
            return {"message": "Please select a valid voice type"}
        voice_type = vt
    elif role == "orchestra_member":
        instrument = (payload.get("instrument") or "").strip().lower()
        if not instrument:
            return {"message": "Please enter your instrument"}

    # Theme — validate against the known list, fall back to default
    VALID_THEMES = {"queen-of-the-night", "mimi", "don-giovanni", "tosca", "carmen", "violetta"}
    theme = (payload.get("theme") or "queen-of-the-night").strip()
    if theme not in VALID_THEMES:
        theme = "queen-of-the-night"

    org_slug = (payload.get("org") or "").strip()
    if not org_slug:
        return {"message": "Please select an organization"}
    org_id = get_org_id(org_slug)
    if org_id is None:
        return {"message": "Invalid organization selected"}

    # Reject duplicate usernames and emails globally before attempting insert
    with db_cursor() as cur:
        cur.execute(
            "SELECT id FROM users WHERE username = %s OR email = %s LIMIT 1",
            (username, email)
        )
        if cur.fetchone():
            cur.execute("SELECT id FROM users WHERE username = %s LIMIT 1", (username,))
            if cur.fetchone():
                return {"message": "That username is already taken. Please choose another."}
            return {"message": "An account with that email already exists."}

    # Insert the user as unverified, then create + send a verification token
    try:
        with db_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO users (
                    org_id, username, email, password_hash,
                    fullname, role, voice_type, specialty, instrument, pw_version,
                    email_verified, theme
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, %s, 'bcrypt', FALSE, %s)
                RETURNING id
            """, (
                org_id, username, email, hash_password_bcrypt(password),
                fullname, role, voice_type, instrument, theme
            ))
            user_id = cur.fetchone()[0]
    except pg_errors.UniqueViolation:
        return {"message": "Username or email already in use"}
    except Exception as e:
        print("SIGNUP ERROR:", e)
        return {"message": "Signup failed. Please try again."}

    # Issue verification token + send email
    send_email_verification(user_id, email, fullname)

    return {"message": "Account created"}


# ========================================================
# EMAIL VERIFICATION (for students)
# ========================================================

VERIFY_TOKEN_DAYS = 7


def send_email_verification(user_id: int, email: str, fullname: str):
    """Generate a token, store it, send the verification email."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(EST) + timedelta(days=VERIFY_TOKEN_DAYS)

    with db_cursor(commit=True) as cur:
        # Invalidate any existing unused tokens for this user
        cur.execute("""
            UPDATE email_verifications
            SET used_at = NOW()
            WHERE user_id = %s AND used_at IS NULL
        """, (user_id,))
        cur.execute("""
            INSERT INTO email_verifications (token, user_id, expires_at)
            VALUES (%s, %s, %s)
        """, (token, user_id, expires))

    verify_url = f"{APP_URL}/verify-email?token={token}"
    html, text = render_verify_email(verify_url, fullname or "there")
    send_email(email, "Verify your email", html, text)


def render_verify_email(verify_url: str, fullname: str) -> tuple[str, str]:
    html = f"""\
<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 560px; margin: 40px auto; padding: 24px; color: #222;">
    <h2 style="color: #444;">Welcome to CountrPnt!</h2>
    <p>Hi {fullname},</p>
    <p>Thanks for signing up. Please verify your email so we can keep you updated on rehearsals, coachings, and any schedule changes.</p>
    <p style="margin: 32px 0;">
        <a href="{verify_url}" style="background: #6b5b3e; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: 500;">Verify your email</a>
    </p>
    <p style="color: #666; font-size: 14px;">Or copy and paste this link:</p>
    <p style="color: #666; font-size: 13px; word-break: break-all;">{verify_url}</p>
    <p style="color: #666; font-size: 14px; margin-top: 32px;">
        This link expires in {VERIFY_TOKEN_DAYS} days. If you didn't create this account, you can ignore this email.
    </p>
</body>
</html>
"""
    text = f"""\
Hi {fullname},

Thanks for signing up for CountrPnt. Please verify your email by clicking the link below:

{verify_url}

This link expires in {VERIFY_TOKEN_DAYS} days. If you didn't create this account, you can ignore this email.
"""
    return html, text


@app.get("/verify-email", response_class=HTMLResponse)
def verify_email_page(request: Request):
    return templates.TemplateResponse(request, "verify.html")


@app.post("/auth/verify-email")
def verify_email(payload: dict):
    token = payload.get("token")
    if not token:
        return {"success": False, "message": "Missing token."}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT user_id, expires_at, used_at
            FROM email_verifications
            WHERE token = %s
        """, (token,))
        row = cur.fetchone()
        if not row:
            return {"success": False, "message": "Invalid link."}

        user_id, expires_at, used_at = row
        if used_at is not None:
            return {"success": False, "message": "This link has already been used."}
        if expires_at < datetime.now(EST):
            return {"success": False, "message": "This link has expired."}

        cur.execute("UPDATE users SET email_verified = TRUE WHERE id = %s", (user_id,))
        cur.execute(
            "UPDATE email_verifications SET used_at = NOW() WHERE token = %s",
            (token,)
        )

    return {"success": True}


@app.post("/auth/resend-verification")
def resend_verification(request: Request):
    """A logged-in unverified user can request a fresh verification email."""
    user = require_user(request)
    if user.get("email_verified"):
        return {"success": True, "message": "Already verified."}

    with db_cursor() as cur:
        cur.execute("SELECT email, fullname FROM users WHERE id = %s", (user["id"],))
        row = cur.fetchone()

    if not row:
        return {"success": False, "message": "User not found."}

    send_email_verification(user["id"], row[0], row[1] or "there")
    return {"success": True}


# ========================================================
# STAFF INVITATIONS (admin → teacher / admin)
# ========================================================

INVITE_TOKEN_DAYS = 7


def render_invite_email(invite_url: str, role: str, fullname_hint: str, inviter_name: str, org_name: str = "") -> tuple[str, str]:
    role_labels = {
        "admin": "Admin",
        "head_admin": "Head Admin",
        "orchestra_admin": "Orchestra Admin",
        "teacher": "Teacher",
        "studio_teacher": "Studio Teacher",
        "studio_member": "Studio Member",
        "student": "Vocalist",
        "orchestra_member": "Instrumentalist",
        "choir_member": "Choir Member",
        "ensemble_member": "Ensemble Member",
    }
    role_label = role_labels.get(role, role.replace("_", " ").title())
    greeting = f"Hi {fullname_hint}," if fullname_hint else "Hi there,"
    inviter_line = f"{inviter_name} has invited you" if inviter_name else "You've been invited"
    org_suffix = f" of <strong>{org_name}</strong>" if org_name else ""
    org_suffix_text = f" of {org_name}" if org_name else ""

    html = f"""\
<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 560px; margin: 40px auto; padding: 24px; color: #222;">
    <h2 style="color: #444;">You're invited to join CountrPnt</h2>
    <p>{greeting}</p>
    <p>{inviter_line} to join CountrPnt as <strong>{role_label}</strong>{org_suffix}.</p>
    <p style="margin: 32px 0;">
        <a href="{invite_url}" style="background: #6b5b3e; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: 500;">Accept invitation</a>
    </p>
    <p style="color: #666; font-size: 14px;">Or copy and paste this link:</p>
    <p style="color: #666; font-size: 13px; word-break: break-all;">{invite_url}</p>
    <p style="color: #666; font-size: 14px; margin-top: 32px;">
        This invitation expires in {INVITE_TOKEN_DAYS} days. If you weren't expecting this email, you can ignore it.
    </p>
</body>
</html>
"""
    text = f"""\
{greeting}

{inviter_line} to join CountrPnt as {role_label}{org_suffix_text}.

Click here to accept and set up your account:
{invite_url}

This invitation expires in {INVITE_TOKEN_DAYS} days. If you weren't expecting this email, you can ignore it.
"""
    return html, text


_DEFAULT_CHOIR_SECTIONS = [
    ("Soprano", 0),
    ("Alto",    1),
    ("Tenor",   2),
    ("Bass",    3),
]

def _seed_default_choir_sections(org_id: int):
    """Insert SATB sections for a new choir org if none exist yet."""
    with db_cursor(commit=True) as cur:
        cur.execute("SELECT COUNT(*) FROM choir_sections WHERE org_id = %s", (org_id,))
        if cur.fetchone()[0] > 0:
            return
        for name, sort_order in _DEFAULT_CHOIR_SECTIONS:
            cur.execute(
                "INSERT INTO choir_sections (org_id, name, sort_order) VALUES (%s, %s, %s)",
                (org_id, name, sort_order),
            )


@app.post("/admin/invite")
def admin_invite(payload: dict, request: Request):
    """Admin sends an invite email. Role permissions follow hierarchy."""
    admin_user = require_user(request, role="admin")

    email = (payload.get("email") or "").strip().lower()
    role = payload.get("role")
    fullname_hint = (payload.get("fullname_hint") or "").strip() or None
    specialty_hint = (payload.get("specialty_hint") or "").strip() or None
    teacher_type = (payload.get("teacher_type") or "vocal").strip()
    teacher_instruments = (payload.get("teacher_instruments") or "").strip().lower()
    instrument = (payload.get("instrument") or "").strip() or None
    admin_role = (payload.get("admin_role") or "").strip() or None

    if teacher_type not in ("vocal", "instrumental"):
        teacher_type = "vocal"

    # Enforce invite hierarchy:
    #   system_admin   → can only invite head_admin (platform-level role)
    #   head_admin     → can invite admin, orchestra_admin, teacher
    #   admin          → can invite teacher only
    #   orchestra_admin → can invite teacher only
    org_type = admin_user.get("org_type", "opera")
    if org_type == "orchestra":
        allowed_by_role = {
            "system_admin":    {"head_admin", "orchestra_admin", "orchestra_member"},
            "head_admin":      {"orchestra_admin", "orchestra_member"},
            "orchestra_admin": {"orchestra_member"},
        }
    elif org_type == "choir":
        allowed_by_role = {
            "system_admin": {"head_admin", "admin", "student", "teacher", "studio_teacher"},
            "head_admin":   {"admin", "orchestra_admin", "teacher", "studio_teacher"},
            "admin":        {"teacher", "choir_member", "ensemble_member"},
        }
    else:
        allowed_by_role = {
            "system_admin":    {"head_admin", "admin", "student", "teacher", "studio_teacher"},
            "head_admin":      {"admin", "orchestra_admin", "teacher", "studio_teacher"},
            "admin":           {"teacher"},
            "orchestra_admin": {"teacher"},
        }
    allowed = allowed_by_role.get(admin_user["role"], set())
    if role not in allowed:
        return {"status": "fail", "message": f"Your role cannot invite '{role}'."}

    # Validate admin sub-role when inviting opera or orchestra admins
    if role == "admin":
        if admin_role not in OPERA_ADMIN_ROLES:
            return {"status": "fail", "message": "Please select a valid opera admin role."}
    elif role == "orchestra_admin":
        if admin_role not in ORCHESTRA_ADMIN_ROLES:
            return {"status": "fail", "message": "Please select a valid orchestra admin role."}
    else:
        admin_role = None

    if not email or not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        return {"status": "fail", "message": "Please enter a valid email."}

    # system_admin inviting a head_admin can specify (or create) an org for them
    org_name = (payload.get("org_name") or "").strip()
    org_slug = (payload.get("org_slug") or "").strip().lower()
    org_logo_url = (payload.get("org_logo_url") or "").strip() or None

    new_org_type = (payload.get("org_type") or "opera").strip()
    if new_org_type not in ("opera", "choir", "studio", "orchestra"):
        new_org_type = "opera"

    # Lesson configuration fields (only used when creating/updating an org)
    lessons_enabled = bool(payload.get("lessons_enabled", False))
    raw_durations = payload.get("lesson_durations") or "30"
    lesson_durations = ",".join(str(d) for d in _parse_durations(str(raw_durations)))
    lesson_max_per_day = int(payload.get("lesson_max_per_day") or 1)
    lesson_booking_open_hour = int(payload.get("lesson_booking_open_hour") or 21)
    lesson_booking_close_hour = int(payload.get("lesson_booking_close_hour") or 18)
    lesson_cancellation_notice_min = int(payload.get("lesson_cancellation_notice_min") or 60)
    lesson_has_lunch_break = bool(payload.get("lesson_has_lunch_break", True))
    lesson_max_per_teacher = int(payload.get("lesson_max_per_teacher") or 5)

    if admin_user["role"] == "system_admin" and org_slug:
        # Validate slug format
        if not re.match(r"^[a-z0-9-]+$", org_slug):
            return {"status": "fail", "message": "Organization ID may only contain lowercase letters, numbers, and hyphens."}
        existing = get_org_id(org_slug)
        if existing:
            org_id = existing
            # Update org_type, lesson config, and logo if supplied
            with db_cursor(commit=True) as cur:
                cur.execute("""
                    UPDATE organizations SET org_type=%s, lessons_enabled=%s,
                        lesson_durations=%s, lesson_max_per_day=%s,
                        lesson_booking_open_hour=%s, lesson_booking_close_hour=%s,
                        lesson_cancellation_notice_min=%s, lesson_has_lunch_break=%s,
                        lesson_max_per_teacher=%s, logo_url=COALESCE(%s, logo_url)
                    WHERE id=%s
                """, (new_org_type, lessons_enabled, lesson_durations, lesson_max_per_day,
                      lesson_booking_open_hour, lesson_booking_close_hour,
                      lesson_cancellation_notice_min, lesson_has_lunch_break,
                      lesson_max_per_teacher, org_logo_url, org_id))
            if new_org_type == "choir":
                _seed_default_choir_sections(org_id)
        elif org_name:
            # Create the org on the fly with the specified type and lesson config
            with db_cursor(commit=True) as cur:
                cur.execute("""
                    INSERT INTO organizations (name, slug, org_type, lessons_enabled,
                        lesson_durations, lesson_max_per_day, lesson_booking_open_hour,
                        lesson_booking_close_hour, lesson_cancellation_notice_min,
                        lesson_has_lunch_break, lesson_max_per_teacher, logo_url)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                """, (org_name, org_slug, new_org_type, lessons_enabled,
                      lesson_durations, lesson_max_per_day, lesson_booking_open_hour,
                      lesson_booking_close_hour, lesson_cancellation_notice_min,
                      lesson_has_lunch_break, lesson_max_per_teacher, org_logo_url))
                org_id = cur.fetchone()[0]
            _org_id_cache[org_slug] = org_id
            if new_org_type == "choir":
                _seed_default_choir_sections(org_id)
        else:
            return {"status": "fail", "message": "Please enter an organization name so we can create it."}
    else:
        org_id = admin_user["org_id"]

    if org_id is None:
        return {"status": "fail", "message": "Organization not configured."}

    # Don't invite an email that's already a user
    with db_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM users WHERE org_id = %s AND email = %s",
            (org_id, email)
        )
        if cur.fetchone():
            return {"status": "fail", "message": "A user with that email already exists."}

    # Generate token and store
    token = secrets.token_urlsafe(32)
    expires = datetime.now(EST) + timedelta(days=INVITE_TOKEN_DAYS)

    with db_cursor(commit=True) as cur:
        # Invalidate any existing pending invites for this email
        cur.execute("""
            UPDATE invitations SET accepted_at = NOW()
            WHERE email = %s AND org_id = %s AND accepted_at IS NULL
        """, (email, org_id))
        cur.execute("""
            INSERT INTO invitations (token, email, role, org_id, invited_by,
                                     fullname_hint, specialty_hint, expires_at,
                                     teacher_type, teacher_instruments, instrument, admin_role)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (token, email, role, org_id, admin_user["id"],
              fullname_hint, specialty_hint, expires,
              teacher_type, teacher_instruments, instrument, admin_role))

    invite_url = f"{APP_URL}/accept-invite?token={token}"

    # Look up org name to include in the email
    with db_cursor() as cur:
        cur.execute("SELECT name FROM organizations WHERE id = %s", (org_id,))
        org_row = cur.fetchone()
    resolved_org_name = org_row[0] if org_row else org_name or ""

    html, text = render_invite_email(invite_url, role, fullname_hint or "", admin_user.get("fullname", ""), resolved_org_name)
    sent = send_email(email, "You've been invited to CountrPnt", html, text)

    return {"status": "success", "email_sent": sent}


@app.get("/admin/orgs")
def admin_orgs(request: Request):
    """List all organizations — system_admin only."""
    user = require_user(request, role="admin")
    if user["role"] != "system_admin":
        return {"status": "fail", "message": "Forbidden"}
    with db_cursor() as cur:
        cur.execute("""
            SELECT o.id, o.name, o.slug, COALESCE(o.org_type, 'opera') AS org_type,
                   COUNT(u.id) AS member_count
            FROM organizations o
            LEFT JOIN users u ON u.org_id = o.id
            GROUP BY o.id, o.name, o.slug, o.org_type
            ORDER BY o.name
        """)
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "slug": r[2], "org_type": r[3], "member_count": r[4]} for r in rows]


@app.get("/admin/invitations")
def admin_invitations(request: Request):
    """List pending and recent invitations for the admin's org."""
    user = require_opera_admin(request)

    with db_cursor() as cur:
        if user["role"] == "system_admin":
            # system_admin invites span multiple orgs — filter by who sent them
            cur.execute("""
                SELECT i.token, i.email, i.role, i.fullname_hint, i.specialty_hint,
                       i.created_at, i.expires_at, i.accepted_at,
                       u.fullname AS invited_by_name
                FROM invitations i
                LEFT JOIN users u ON u.id = i.invited_by
                WHERE i.invited_by = %s
                ORDER BY i.created_at DESC
                LIMIT 100
            """, (user["id"],))
        else:
            cur.execute("""
                SELECT i.token, i.email, i.role, i.fullname_hint, i.specialty_hint,
                       i.created_at, i.expires_at, i.accepted_at,
                       u.fullname AS invited_by_name
                FROM invitations i
                LEFT JOIN users u ON u.id = i.invited_by
                WHERE i.org_id = %s AND i.email != %s
                ORDER BY i.created_at DESC
                LIMIT 100
            """, (user["org_id"], user["email"]))
        rows = cur.fetchall()

    now = datetime.now(EST)
    out = []
    for r in rows:
        token, email, role, fname, spec, created, expires, accepted, by_name = r
        if accepted:
            status = "accepted"
        elif expires < now:
            status = "expired"
        else:
            status = "pending"
        out.append({
            "email": email,
            "role": role,
            "fullname_hint": fname,
            "specialty_hint": spec,
            "created_at": created.isoformat() if created else None,
            "expires_at": expires.isoformat() if expires else None,
            "accepted_at": accepted.isoformat() if accepted else None,
            "invited_by": by_name,
            "status": status,
        })
    return out


@app.post("/admin/cancel-invitation")
def admin_cancel_invitation(payload: dict, request: Request):
    """Admin cancels (revokes) a pending invitation by email."""
    user = require_user(request, role="admin")
    email = (payload.get("email") or "").strip().lower()
    if not email:
        return {"status": "fail", "message": "Missing email."}

    with db_cursor(commit=True) as cur:
        if user["role"] == "system_admin":
            cur.execute("""
                UPDATE invitations SET accepted_at = NOW()
                WHERE invited_by = %s AND email = %s AND accepted_at IS NULL
            """, (user["id"], email))
        else:
            cur.execute("""
                UPDATE invitations SET accepted_at = NOW()
                WHERE org_id = %s AND email = %s AND accepted_at IS NULL
            """, (user["org_id"], email))
    return {"status": "success"}


@app.post("/admin/resend-invitation")
def admin_resend_invitation(payload: dict, request: Request):
    """Regenerate a fresh token and re-send the invite email for a pending or expired invitation."""
    user = require_user(request, role="admin")
    email = (payload.get("email") or "").strip().lower()
    if not email:
        return {"status": "fail", "message": "Missing email."}

    new_token = secrets.token_urlsafe(32)
    new_expires = datetime.now(EST) + timedelta(days=INVITE_TOKEN_DAYS)

    with db_cursor(commit=True) as cur:
        if user["role"] == "system_admin":
            cur.execute("""
                UPDATE invitations SET token = %s, expires_at = %s, accepted_at = NULL
                WHERE invited_by = %s AND email = %s
                RETURNING role, fullname_hint, org_id
            """, (new_token, new_expires, user["id"], email))
        else:
            cur.execute("""
                UPDATE invitations SET token = %s, expires_at = %s, accepted_at = NULL
                WHERE org_id = %s AND email = %s
                RETURNING role, fullname_hint, org_id
            """, (new_token, new_expires, user["org_id"], email))
        row = cur.fetchone()

    if not row:
        return {"status": "fail", "message": "Invitation not found."}

    role, fullname_hint, org_id = row
    with db_cursor() as cur:
        cur.execute("SELECT name FROM organizations WHERE id = %s", (org_id,))
        org_row = cur.fetchone()
    org_name = org_row[0] if org_row else ""

    invite_url = f"{APP_URL}/accept-invite?token={new_token}"
    html, text = render_invite_email(invite_url, role, fullname_hint or "", user.get("fullname", ""), org_name)
    sent = send_email(email, "You've been invited to CountrPnt", html, text)
    return {"status": "success", "email_sent": sent}


@app.get("/auth/invite-info")
def invite_info(token: str):
    """Public: given a token, return info needed to render the accept-invite page."""
    if not token:
        return {"valid": False, "message": "Missing token."}

    with db_cursor() as cur:
        cur.execute("""
            SELECT i.email, i.role, i.fullname_hint, i.specialty_hint,
                   i.expires_at, i.accepted_at, i.teacher_type, i.teacher_instruments,
                   o.name AS org_name, i.org_id,
                   COALESCE(o.org_type, 'opera') AS org_type, i.instrument, i.admin_role
            FROM invitations i
            LEFT JOIN organizations o ON o.id = i.org_id
            WHERE i.token = %s
        """, (token,))
        row = cur.fetchone()

    if not row:
        return {"valid": False, "message": "Invalid invitation link."}

    email, role, fname, spec, expires, accepted, t_type, t_instruments, org_name, org_id, org_type, inv_instrument, inv_admin_role = row
    if accepted:
        return {"valid": False, "message": "This invitation has already been used."}
    if expires < datetime.now(EST):
        return {"valid": False, "message": "This invitation has expired."}

    return {
        "valid": True,
        "email": email,
        "role": role,
        "fullname_hint": fname,
        "specialty_hint": spec,
        "teacher_type": t_type or "vocal",
        "teacher_instruments": t_instruments or "",
        "org_name": org_name or "",
        "org_id": org_id,
        "org_type": org_type,
        "instrument": inv_instrument or "",
        "admin_role": inv_admin_role or "",
    }


@app.get("/auth/org-sections/{org_id}")
def public_org_sections(org_id: int):
    """Public: return choir sections for an org (used on the accept-invite page)."""
    try:
        with db_cursor() as cur:
            cur.execute("""
                SELECT id, name FROM choir_sections
                WHERE org_id = %s ORDER BY sort_order, name
            """, (org_id,))
            rows = cur.fetchall()
        return [{"id": r[0], "name": r[1]} for r in rows]
    except Exception:
        return []


@app.get("/accept-invite", response_class=HTMLResponse)
def accept_invite_page(request: Request):
    return templates.TemplateResponse(request, "accept-invite.html")


@app.post("/auth/accept-invite")
def accept_invite(payload: dict):
    """Accept an invitation, creating the user account."""
    token = payload.get("token")
    username = (payload.get("username") or "").strip().lower()
    password = payload.get("password") or ""
    fullname = (payload.get("fullname") or "").strip()
    specialty = (payload.get("specialty") or "").strip() or None
    VALID_VOICE_TYPES = {"soprano", "alto", "tenor", "bass"}
    raw_vt = (payload.get("voice_type") or "").strip().lower()
    voice_type = raw_vt if raw_vt in VALID_VOICE_TYPES else None
    section_id = payload.get("section_id") or None
    if section_id is not None:
        try:
            section_id = int(section_id)
        except (ValueError, TypeError):
            section_id = None

    VALID_THEMES = {"queen-of-the-night", "mimi", "don-giovanni", "tosca", "carmen", "violetta"}
    theme = (payload.get("theme") or "queen-of-the-night").strip()
    if theme not in VALID_THEMES:
        theme = "queen-of-the-night"

    if not token or not username or not password or not fullname:
        return {"status": "fail", "message": "All fields are required."}

    if len(password) < 8:
        return {"status": "fail", "message": "Password must be at least 8 characters."}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT email, role, org_id, expires_at, accepted_at,
                   teacher_type, teacher_instruments, instrument, admin_role
            FROM invitations
            WHERE token = %s
        """, (token,))
        row = cur.fetchone()

        if not row:
            return {"status": "fail", "message": "Invalid invitation link."}

        email, role, org_id, expires, accepted, t_type, t_instruments, inv_instrument, inv_admin_role = row
        if accepted:
            return {"status": "fail", "message": "This invitation has already been used."}
        if expires < datetime.now(EST):
            return {"status": "fail", "message": "This invitation has expired."}

        t_type = t_type or "vocal"
        t_instruments = t_instruments or ""

        # Create the user. Email is pre-verified since it came from an invite.
        try:
            cur.execute("""
                INSERT INTO users (
                    org_id, username, email, password_hash,
                    fullname, role, voice_type, specialty, pw_version,
                    email_verified, theme, teacher_type, teacher_instruments,
                    section_id, instrument, admin_role
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'bcrypt', TRUE, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                org_id, username, email, hash_password_bcrypt(password),
                fullname, role, voice_type, specialty, theme, t_type, t_instruments,
                section_id, inv_instrument, inv_admin_role
            ))
            user_id = cur.fetchone()[0]
        except pg_errors.UniqueViolation:
            return {"status": "fail", "message": "Username already in use. Pick a different one."}

        cur.execute(
            "UPDATE invitations SET accepted_at = NOW() WHERE token = %s",
            (token,)
        )

        if role == "studio_teacher":
            import json as _json
            studio = payload.get("studio_settings") or {}
            zelle   = (studio.get("payment_zelle")   or "").strip() or None
            venmo   = (studio.get("payment_venmo")   or "").strip() or None
            cashapp = (studio.get("payment_cashapp") or "").strip() or None
            paypal  = (studio.get("payment_paypal")  or "").strip() or None
            raw_rates = studio.get("lesson_rates") or []
            rates = []
            for r in raw_rates:
                try:
                    dur = int(r.get("duration_min", 0))
                    rate_cents = int(round(float(r.get("rate", 0)) * 100))
                    if dur > 0:
                        rates.append({"duration_min": dur, "rate_cents": rate_cents})
                except (ValueError, TypeError):
                    pass
            cancel_hours = studio.get("cancel_hours")
            try:
                cancel_hours = int(cancel_hours) if cancel_hours is not None else None
            except (ValueError, TypeError):
                cancel_hours = None
            cancel_charge = bool(studio.get("cancel_charge", False))
            free_cancels = int(studio.get("free_cancels_per_student") or 0)
            cur.execute("""
                INSERT INTO studio_teacher_settings
                    (teacher_id, payment_zelle, payment_venmo, payment_cashapp, payment_paypal,
                     lesson_rates, cancel_hours, cancel_charge, free_cancels_per_student)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (teacher_id) DO UPDATE SET
                    payment_zelle             = EXCLUDED.payment_zelle,
                    payment_venmo             = EXCLUDED.payment_venmo,
                    payment_cashapp           = EXCLUDED.payment_cashapp,
                    payment_paypal            = EXCLUDED.payment_paypal,
                    lesson_rates              = EXCLUDED.lesson_rates,
                    cancel_hours              = EXCLUDED.cancel_hours,
                    cancel_charge             = EXCLUDED.cancel_charge,
                    free_cancels_per_student  = EXCLUDED.free_cancels_per_student
            """, (user_id, zelle, venmo, cashapp, paypal, _json.dumps(rates),
                  cancel_hours, cancel_charge, free_cancels))

    return {"status": "success", "role": role}


@app.post("/user/theme")
def set_theme(payload: dict, request: Request):
    """Save the logged-in user's theme preference."""
    user = require_user(request)

    VALID_THEMES = {"queen-of-the-night", "mimi", "don-giovanni", "tosca", "carmen", "violetta"}
    theme = (payload.get("theme") or "").strip()
    if theme not in VALID_THEMES:
        return {"status": "fail", "message": "Unknown theme."}

    with db_cursor(commit=True) as cur:
        cur.execute("UPDATE users SET theme = %s WHERE id = %s", (theme, user["id"]))

    return {"status": "success"}


# ========================================================
# PUBLIC (dropdowns etc.)
# ========================================================

@app.get("/orgs")
def get_orgs():
    """List opera festival organizations for the public signup dropdown."""
    try:
        with db_cursor() as cur:
            cur.execute("""
                SELECT id, slug, name FROM organizations
                WHERE COALESCE(org_type, 'opera') NOT IN ('choir', 'ensemble', 'studio')
                ORDER BY name
            """)
            rows = cur.fetchall()
        return [{"id": r[0], "slug": r[1], "name": r[2]} for r in rows]
    except Exception:
        with db_cursor() as cur:
            cur.execute("SELECT id, slug, name FROM organizations ORDER BY name")
            rows = cur.fetchall()
        return [{"id": r[0], "slug": r[1], "name": r[2]} for r in rows]


@app.get("/studio-orgs")
def get_studio_orgs():
    """List private studio organizations for the public signup dropdown."""
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, slug, name FROM organizations
            WHERE org_type = 'studio'
            ORDER BY name
        """)
        rows = cur.fetchall()
    return [{"id": r[0], "slug": r[1], "name": r[2]} for r in rows]


@app.post("/contact")
def submit_contact(payload: dict):
    """Public contact form — no auth required. Sends to the system_admin email."""
    name    = (payload.get("name")    or "").strip()
    email   = (payload.get("email")   or "").strip()
    message = (payload.get("message") or "").strip()
    topic   = (payload.get("topic")   or "general").strip()

    if not name or not email or not message:
        return {"status": "error", "message": "All fields are required."}
    if not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return {"status": "error", "message": "Please enter a valid email address."}

    with db_cursor() as cur:
        cur.execute("SELECT email FROM users WHERE role = 'system_admin' LIMIT 1")
        row = cur.fetchone()
    admin_email = row[0] if row else None
    if not admin_email:
        return {"status": "error", "message": "Could not deliver message. Please try again later."}

    subj = f"CountrPnt Contact ({topic}): {name}"
    n = html_mod.escape(name)
    e = html_mod.escape(email)
    m = html_mod.escape(message).replace("\n", "<br>")
    html_body = f"""
        <p><strong>Name:</strong> {n}</p>
        <p><strong>Email:</strong> {e}</p>
        <p><strong>Topic:</strong> {html_mod.escape(topic)}</p>
        <hr>
        <p>{m}</p>
    """
    text_body = f"Name: {name}\nEmail: {email}\nTopic: {topic}\n\n{message}"
    send_email(
        to=admin_email,
        subject=subj,
        html_body=html_body,
        text_body=text_body,
        reply_to=email,
    )
    return {"status": "success"}


def _resolve_org_id(request: Request) -> Optional[int]:
    """Return the org_id for the current request.

    If a valid session is present, use the user's org. Otherwise fall back
    to the default org so unauthenticated calls still work during development.
    """
    user = current_user(request)
    if user and user.get("org_id"):
        return user["org_id"]
    return get_org_id()


@app.get("/teachers")
def get_teachers(request: Request):
    """List of teachers for dropdowns, scoped to the caller's org."""
    org_id = _resolve_org_id(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, fullname
            FROM users
            WHERE org_id = %s AND role = 'teacher'
            ORDER BY fullname
        """, (org_id,))
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1]} for r in rows]


@app.get("/operas")
def get_operas(request: Request):
    """List of operas/productions scoped to the caller's org."""
    org_id = _resolve_org_id(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, opera_name
            FROM operas
            WHERE org_id = %s
            ORDER BY opera_name
        """, (org_id,))
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1]} for r in rows]


# ========================================================
# ADMIN
# ========================================================

@app.get("/admin/teachers")
def admin_teachers(request: Request):
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, fullname, email, specialty, teacher_type, teacher_instruments
            FROM users
            WHERE org_id = %s AND role = 'teacher'
            ORDER BY fullname
        """, (org_id,))
        rows = cur.fetchall()
    return [
        {
            "id": r[0], "name": r[1], "email": r[2], "specialty": r[3],
            "teacher_type": r[4] or "vocal",
            "teacher_instruments": r[5] or "",
        }
        for r in rows
    ]


@app.post("/admin/teachers/{teacher_id}")
def admin_update_teacher(teacher_id: int, payload: dict, request: Request):
    admin = require_user(request, role="admin")
    teacher_type = (payload.get("teacher_type") or "vocal").strip()
    if teacher_type not in ("vocal", "instrumental"):
        teacher_type = "vocal"
    teacher_instruments = (payload.get("teacher_instruments") or "").strip().lower()

    with db_cursor(commit=True) as cur:
        cur.execute(
            "SELECT 1 FROM users WHERE id=%s AND org_id=%s AND role='teacher'",
            (teacher_id, admin["org_id"])
        )
        if not cur.fetchone():
            return {"status": "fail", "message": "Teacher not found"}
        cur.execute(
            "UPDATE users SET teacher_type=%s, teacher_instruments=%s WHERE id=%s",
            (teacher_type, teacher_instruments, teacher_id)
        )
    return {"status": "success"}


@app.post("/admin/assign-group")
def assign_group(payload: dict):
    opera_id = payload.get("opera_id")
    cast_id = payload.get("cast_id")
    student_ids = payload.get("student_ids") or []

    if not opera_id or not student_ids:
        return {"status": "fail", "message": "Missing opera or students"}

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT 1 FROM operas WHERE id=%s", (opera_id,))
        if not cur.fetchone():
            return {"status": "fail", "message": "Opera not found"}

        if cast_id is not None:
            cur.execute("SELECT 1 FROM casts WHERE id=%s AND opera_id=%s", (cast_id, opera_id))
            if not cur.fetchone():
                return {"status": "fail", "message": "Invalid cast for opera"}

        for student_id in student_ids:
            cur.execute("""
                INSERT INTO student_assignments (student_id, opera_id, cast_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (student_id, opera_id, cast_id) DO NOTHING
            """, (student_id, opera_id, cast_id))

    return {"status": "success"}


@app.post("/admin/assign-role")
def assign_role(payload: dict):
    student_id = payload.get("student_id")
    opera_id = payload.get("opera_id")
    cast_id = payload.get("cast_id")
    remove = payload.get("remove", False)
    role_name = payload.get("role_name")

    if not student_id or not opera_id:
        return {"status": "fail", "message": "Missing identifiers"}

    with db_cursor(commit=True) as cur:
        if remove and cast_id:
            cur.execute("""
                DELETE FROM student_roles
                WHERE student_id=%s AND opera_id=%s AND cast_id=%s
            """, (student_id, opera_id, cast_id))
            cur.execute("""
                DELETE FROM student_assignments
                WHERE student_id=%s AND opera_id=%s AND cast_id=%s
            """, (student_id, opera_id, cast_id))
            return {"status": "success"}

        if role_name and role_name.strip():
            cur.execute("""
                INSERT INTO student_roles (student_id, opera_id, cast_id, role_name)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (student_id, opera_id, cast_id)
                DO UPDATE SET role_name = EXCLUDED.role_name
            """, (student_id, opera_id, cast_id, role_name.strip()))
        else:
            cur.execute("""
                DELETE FROM student_roles
                WHERE student_id=%s AND opera_id=%s AND cast_id=%s
            """, (student_id, opera_id, cast_id))

    return {"status": "success"}


@app.get("/admin/student-pool")
def student_pool(request: Request):
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    with db_cursor() as cur:
        cur.execute("""
            SELECT
                u.id, u.fullname, u.voice_type,
                o.id, o.opera_name,
                ca.id, ca.name,
                sr.role_name
            FROM users u
            LEFT JOIN student_assignments sa ON sa.student_id = u.id
            LEFT JOIN operas o ON o.id = sa.opera_id
            LEFT JOIN casts ca ON ca.id = sa.cast_id
            LEFT JOIN student_roles sr
                ON sr.student_id = u.id
                AND sr.opera_id = sa.opera_id
                AND sr.cast_id = sa.cast_id
            WHERE u.org_id = %s AND u.role = 'student'
            ORDER BY o.opera_name NULLS LAST, u.fullname, ca.name
        """, (org_id,))
        rows = cur.fetchall()

    operas = {}
    unassigned = []
    unassigned_seen = set()

    for sid, name, voice_type, opera_id, opera_name, cast_id, cast_name, role in rows:
        if opera_id is None:
            if sid not in unassigned_seen:
                unassigned_seen.add(sid)
                unassigned.append({
                    "id": sid, "name": name, "voice_type": voice_type
                })
            continue

        if opera_id not in operas:
            operas[opera_id] = {
                "opera_id": opera_id,
                "opera": opera_name,
                "students": {}
            }
        students_map = operas[opera_id]["students"]

        if sid not in students_map:
            students_map[sid] = {
                "id": sid, "name": name,
                "voice_type": voice_type, "casts": []
            }

        students_map[sid]["casts"].append({
            "cast_id": cast_id,
            "cast": cast_name,
            "role": role
        })

    return {
        "operas": [
            {
                "opera_id": o["opera_id"],
                "opera": o["opera"],
                "students": list(o["students"].values())
            }
            for o in operas.values()
        ],
        "unassigned": unassigned
    }


@app.get("/admin/casts")
def admin_casts(opera_id: int, request: Request):
    user = require_user(request, role="admin")
    with db_cursor() as cur:
        cur.execute("""
            SELECT c.id, c.name FROM casts c
            JOIN operas op ON op.id = c.opera_id
            WHERE c.opera_id = %s AND op.org_id = %s
            ORDER BY c.name
        """, (opera_id, user["org_id"]))
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1]} for r in rows]




@app.post("/admin/create-rehearsal")
def create_rehearsal(payload: dict, request: Request):
    """
    Payload shape:
      opera_id: int (required)
      attendance_type: 'full' | 'principals' | 'chorus' | 'coaching'
      cast_ids: [int, ...]      (optional, empty = all casts)
      role_names: [str, ...]    (required if coaching, ignored otherwise)
      leader_ids: [int, ...]    (optional)
      start_time: ISO string
      end_time: ISO string
      notes: str (optional)
    """
    admin = require_user(request, role="admin")
    org_tz = get_org_tz(admin)

    opera_id = payload.get("opera_id")
    attendance_type = payload.get("attendance_type", "full")
    rehearsal_type = payload.get("rehearsal_type", "vocal")
    if rehearsal_type not in ("vocal", "orchestra"):
        rehearsal_type = "vocal"
    cast_ids = payload.get("cast_ids") or []
    role_names = payload.get("role_names") or []
    leader_ids = payload.get("leader_ids") or []
    start_time = payload.get("start_time")
    end_time = payload.get("end_time")
    notes = payload.get("notes", "") or ""
    location = (payload.get("location") or "").strip()

    if not opera_id or not start_time or not end_time:
        return {"status": "fail", "message": "Missing required fields"}

    if attendance_type not in ("full", "principals", "chorus", "coaching"):
        return {"status": "fail", "message": "Invalid attendance type"}

    # Coaching requires roles
    if attendance_type == "coaching" and not role_names:
        return {"status": "fail", "message": "Coaching rehearsals need at least one role"}

    # Principals require at least one cast (can't have 'principals' across all casts meaningfully)
    if attendance_type == "principals" and not cast_ids:
        return {"status": "fail", "message": "Principal rehearsals need at least one cast"}

    # Chorus is opera-wide — cast selection is ignored
    if attendance_type == "chorus":
        cast_ids = []

    try:
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        if end_dt <= start_dt:
            return {"status": "fail", "message": "End time must be after start time"}
    except Exception:
        return {"status": "fail", "message": "Invalid time format"}

    with db_cursor(commit=True) as cur:
        # Validate opera
        cur.execute("SELECT 1 FROM operas WHERE id=%s", (opera_id,))
        if not cur.fetchone():
            return {"status": "fail", "message": "Opera not found"}

        # Validate casts
        for cid in cast_ids:
            cur.execute(
                "SELECT 1 FROM casts WHERE id=%s AND opera_id=%s",
                (cid, opera_id)
            )
            if not cur.fetchone():
                return {"status": "fail", "message": f"Cast {cid} not in this opera"}

        # Validate leaders (must be teachers)
        for lid in leader_ids:
            cur.execute(
                "SELECT 1 FROM users WHERE id=%s AND role='teacher'",
                (lid,)
            )
            if not cur.fetchone():
                return {"status": "fail", "message": f"Leader {lid} is not a teacher"}

        # Insert the rehearsal row.
        # We leave the legacy cast_id column NULL — new logic uses rehearsal_casts.
        cur.execute("""
            INSERT INTO rehearsals
                (opera_id, cast_id, start_time, end_time, notes, attendance_type, location, rehearsal_type)
            VALUES (%s, NULL, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (opera_id, start_time, end_time, notes, attendance_type, location or None, rehearsal_type))
        rehearsal_id = cur.fetchone()[0]

        # Insert called casts
        for cid in cast_ids:
            cur.execute("""
                INSERT INTO rehearsal_casts (rehearsal_id, cast_id)
                VALUES (%s, %s)
            """, (rehearsal_id, cid))

        # Insert roles (coaching only)
        if attendance_type == "coaching":
            for rn in role_names:
                cur.execute("""
                    INSERT INTO rehearsal_roles (rehearsal_id, role_name)
                    VALUES (%s, %s)
                """, (rehearsal_id, rn.strip()))

        # Insert leaders
        for lid in leader_ids:
            cur.execute("""
                INSERT INTO rehearsal_leaders (rehearsal_id, teacher_id)
                VALUES (%s, %s)
            """, (rehearsal_id, lid))

    # ── Cancel conflicting student lessons ────────────────────────────────────
    lessons_cancelled = 0
    if rehearsal_type == "vocal":
        try:
            with db_cursor(commit=True) as cur:
                # Resolve which students are called based on attendance type
                if attendance_type == "full":
                    cur.execute("""
                        SELECT DISTINCT u.id, u.fullname, u.email
                        FROM users u
                        JOIN student_assignments sa ON sa.student_id = u.id
                        WHERE sa.opera_id = %s AND u.role = 'student'
                    """, (opera_id,))
                elif attendance_type == "principals":
                    cur.execute("""
                        SELECT DISTINCT u.id, u.fullname, u.email
                        FROM users u
                        JOIN student_assignments sa ON sa.student_id = u.id
                        WHERE sa.opera_id = %s AND sa.cast_id = ANY(%s) AND u.role = 'student'
                    """, (opera_id, cast_ids))
                elif attendance_type == "chorus":
                    cur.execute("""
                        SELECT DISTINCT u.id, u.fullname, u.email
                        FROM users u
                        JOIN student_assignments sa ON sa.student_id = u.id
                        WHERE sa.opera_id = %s AND sa.cast_id IS NULL AND u.role = 'student'
                    """, (opera_id,))
                else:  # coaching
                    cur.execute("""
                        SELECT DISTINCT u.id, u.fullname, u.email
                        FROM users u
                        JOIN student_roles sr ON sr.student_id = u.id
                        WHERE sr.opera_id = %s AND sr.role_name = ANY(%s) AND u.role = 'student'
                    """, (opera_id, role_names))

                called_students = {r[0]: {"fullname": r[1], "email": r[2]} for r in cur.fetchall()}

                if called_students:
                    called_ids = list(called_students.keys())
                    # Convert UTC rehearsal times to org's local timezone to match stored lesson times
                    local_start = start_dt.astimezone(org_tz)
                    local_end = end_dt.astimezone(org_tz)
                    rehearsal_date = local_start.date()
                    start_time_str = local_start.strftime("%H:%M:%S")
                    end_time_str = local_end.strftime("%H:%M:%S")

                    cur.execute("""
                        SELECT l.id, l.student_id, l.lesson_time, u_t.fullname
                        FROM lessons l
                        JOIN users u_t ON u_t.id = l.teacher_id
                        WHERE l.lesson_date = %s
                          AND l.lesson_time < %s::time
                          AND l.lesson_time + interval '30 minutes' > %s::time
                          AND l.student_id = ANY(%s)
                          AND l.status = 'booked'
                    """, (rehearsal_date, end_time_str, start_time_str, called_ids))
                    conflicting = cur.fetchall()

                    if conflicting:
                        lesson_ids = [r[0] for r in conflicting]
                        cur.execute("""
                            UPDATE lessons SET status = 'cancelled', cancelled_at = NOW()
                            WHERE id = ANY(%s)
                        """, (lesson_ids,))
                        lessons_cancelled = len(conflicting)

                        cur.execute("SELECT opera_name FROM operas WHERE id = %s", (opera_id,))
                        opera_row = cur.fetchone()
                        opera_name_for_email = opera_row[0] if opera_row else "an opera"

                        for _, student_id, lesson_time_val, teacher_name in conflicting:
                            student = called_students.get(student_id)
                            if not student or not student["email"]:
                                continue
                            html_body, text_body = render_lesson_cancelled_email(
                                student_name=student["fullname"],
                                teacher_name=teacher_name,
                                lesson_date=rehearsal_date,
                                lesson_time=lesson_time_val,
                                opera_name=opera_name_for_email,
                                rehearsal_start=start_dt,
                            )
                            send_email(
                                to=student["email"],
                                subject="Your coaching lesson has been cancelled",
                                html_body=html_body,
                                text_body=text_body,
                            )
        except Exception as e:
            print(f"[create_rehearsal] lesson cancellation error: {e}")

    return {"status": "success", "rehearsal_id": rehearsal_id, "lessons_cancelled": lessons_cancelled}


@app.post("/admin/rehearsals/bulk")
def admin_create_rehearsals_bulk(payload: dict, request: Request):
    """
    Payload:
      opera_id: int
      attendance_type: 'full' | 'principals' | 'chorus' | 'coaching'
      rehearsal_type: 'vocal' | 'orchestra'
      cast_ids: [int, ...]
      role_names: [str, ...]
      leader_ids: [int, ...]
      start_date: 'YYYY-MM-DD'
      end_date: 'YYYY-MM-DD'
      days: ['monday', 'tuesday', ...]
      start_time: 'HH:MM'
      end_time: 'HH:MM'
      location: str
      notes: str
    """
    from datetime import date as date_type, timedelta
    admin = require_user(request, role="admin")

    opera_id = payload.get("opera_id")
    attendance_type = payload.get("attendance_type", "full")
    rehearsal_type = payload.get("rehearsal_type", "vocal")
    if rehearsal_type not in ("vocal", "orchestra"):
        rehearsal_type = "vocal"
    cast_ids = payload.get("cast_ids") or []
    role_names = payload.get("role_names") or []
    leader_ids = payload.get("leader_ids") or []
    start_date = payload.get("start_date")
    end_date = payload.get("end_date")
    days = payload.get("days") or []
    start_time_str = payload.get("start_time")
    end_time_str = payload.get("end_time") or None
    location = (payload.get("location") or "").strip() or None
    notes = (payload.get("notes") or "").strip() or None

    if not opera_id or not start_date or not end_date or not days or not start_time_str:
        return {"status": "fail", "message": "Missing required fields"}

    if attendance_type not in ("full", "principals", "chorus", "coaching"):
        return {"status": "fail", "message": "Invalid attendance type"}

    if attendance_type == "coaching" and not role_names:
        return {"status": "fail", "message": "Coaching rehearsals need at least one role"}

    if attendance_type == "chorus":
        cast_ids = []

    DAY_MAP = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
               "friday": 4, "saturday": 5, "sunday": 6}
    day_nums = [DAY_MAP[d.lower()] for d in days if d.lower() in DAY_MAP]
    if not day_nums:
        return {"status": "fail", "message": "No valid days selected"}

    try:
        sd = date_type.fromisoformat(start_date)
        ed = date_type.fromisoformat(end_date)
    except Exception:
        return {"status": "fail", "message": "Invalid date format"}

    if ed < sd:
        return {"status": "fail", "message": "End date must be after start date"}
    if (ed - sd).days > 365:
        return {"status": "fail", "message": "Date range cannot exceed one year"}

    rehearsal_dates = []
    current = sd
    while current <= ed:
        if current.weekday() in day_nums:
            rehearsal_dates.append(current)
        current += timedelta(days=1)

    if not rehearsal_dates:
        return {"status": "fail", "message": "No rehearsals fall in that date range"}
    if len(rehearsal_dates) > 100:
        return {"status": "fail", "message": f"Too many rehearsals ({len(rehearsal_dates)}). Narrow your date range."}

    with db_cursor() as cur:
        cur.execute("SELECT 1 FROM operas WHERE id=%s", (opera_id,))
        if not cur.fetchone():
            return {"status": "fail", "message": "Opera not found"}

        for cid in cast_ids:
            cur.execute("SELECT 1 FROM casts WHERE id=%s AND opera_id=%s", (cid, opera_id))
            if not cur.fetchone():
                return {"status": "fail", "message": f"Cast {cid} not in this opera"}

        for lid in leader_ids:
            cur.execute("SELECT 1 FROM users WHERE id=%s AND role='teacher'", (lid,))
            if not cur.fetchone():
                return {"status": "fail", "message": f"Leader {lid} is not a teacher"}

    created = 0
    with db_cursor(commit=True) as cur:
        for rdate in rehearsal_dates:
            cur.execute("SAVEPOINT bulk_reh")
            try:
                start_dt = datetime.fromisoformat(f"{rdate}T{start_time_str}")
                end_dt = datetime.fromisoformat(f"{rdate}T{end_time_str}") if end_time_str else None
                cur.execute("""
                    INSERT INTO rehearsals
                        (opera_id, cast_id, start_time, end_time, notes, attendance_type, location, rehearsal_type)
                    VALUES (%s, NULL, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (opera_id, start_dt, end_dt, notes, attendance_type, location, rehearsal_type))
                rid = cur.fetchone()[0]

                for cid in cast_ids:
                    cur.execute(
                        "INSERT INTO rehearsal_casts (rehearsal_id, cast_id) VALUES (%s, %s)",
                        (rid, cid)
                    )

                if attendance_type == "coaching":
                    for rn in role_names:
                        cur.execute(
                            "INSERT INTO rehearsal_roles (rehearsal_id, role_name) VALUES (%s, %s)",
                            (rid, rn.strip())
                        )

                for lid in leader_ids:
                    cur.execute(
                        "INSERT INTO rehearsal_leaders (rehearsal_id, teacher_id) VALUES (%s, %s)",
                        (rid, lid)
                    )

                cur.execute("RELEASE SAVEPOINT bulk_reh")
                created += 1
            except Exception as e:
                cur.execute("ROLLBACK TO SAVEPOINT bulk_reh")
                print(f"[admin_bulk] skipped {rdate}: {e}")

    return {"status": "success", "created": created}


def render_rehearsal_notes_email(opera_name: str, date_str: str, time_str: str, notes: str):
    html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:600px;margin:auto;padding:24px;">
<h2 style="color:#333;margin-bottom:4px;">Rehearsal Notes: {html_mod.escape(opera_name)}</h2>
<p style="color:#888;margin-top:0;">{html_mod.escape(date_str)} &middot; {html_mod.escape(time_str)}</p>
<hr style="border:none;border-top:1px solid #eee;margin:16px 0;">
<div style="white-space:pre-wrap;font-size:15px;line-height:1.6;color:#222;">{html_mod.escape(notes)}</div>
</body></html>"""
    text = f"Rehearsal Notes: {opera_name}\n{date_str} \xb7 {time_str}\n\n{notes}"
    return html, text


def render_choir_notes_email(date_str: str, time_str: str, notes: str):
    html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:600px;margin:auto;padding:24px;">
<h2 style="color:#333;margin-bottom:4px;">Rehearsal Notes</h2>
<p style="color:#888;margin-top:0;">{html_mod.escape(date_str)} &middot; {html_mod.escape(time_str)}</p>
<hr style="border:none;border-top:1px solid #eee;margin:16px 0;">
<div style="white-space:pre-wrap;font-size:15px;line-height:1.6;color:#222;">{html_mod.escape(notes)}</div>
</body></html>"""
    text = f"Rehearsal Notes\n{date_str} \xb7 {time_str}\n\n{notes}"
    return html, text


@app.post("/admin/rehearsals/{rehearsal_id}/notes")
def admin_set_rehearsal_notes(rehearsal_id: int, payload: dict, request: Request):
    admin = require_user(request, role="admin")
    notes = (payload.get("notes") or "").strip()

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT r.opera_id, r.attendance_type, r.rehearsal_type, r.start_time, r.end_time,
                   o.opera_name, o.org_id
            FROM rehearsals r JOIN operas o ON o.id = r.opera_id
            WHERE r.id = %s
        """, (rehearsal_id,))
        row = cur.fetchone()
        if not row:
            return {"status": "fail", "message": "Rehearsal not found"}
        opera_id, attendance_type, rehearsal_type, start_dt, end_dt, opera_name, org_id = row

        if org_id != admin["org_id"]:
            return {"status": "fail", "message": "Not authorized"}

        cur.execute("UPDATE rehearsals SET notes = %s WHERE id = %s", (notes or None, rehearsal_id))

        # Gather called casts and roles
        cur.execute("SELECT cast_id FROM rehearsal_casts WHERE rehearsal_id = %s", (rehearsal_id,))
        cast_ids = [r[0] for r in cur.fetchall()]

        cur.execute("SELECT role_name FROM rehearsal_roles WHERE rehearsal_id = %s", (rehearsal_id,))
        role_names = [r[0] for r in cur.fetchall()]

        # Org admins (including conductors / assistant conductors)
        cur.execute("""
            SELECT fullname, email FROM users
            WHERE org_id = %s AND role IN ('admin', 'head_admin', 'system_admin', 'orchestra_admin')
              AND email IS NOT NULL
        """, (org_id,))
        recipients = {email: name for name, email in cur.fetchall() if email}

        # Production staff for this specific production/concert (includes no-account contacts)
        cur.execute("""
            SELECT u.fullname, u.email, os.external_name, os.external_email
            FROM opera_staff os
            LEFT JOIN users u ON u.id = os.teacher_id
            WHERE os.opera_id = %s
        """, (opera_id,))
        for full_name, u_email, ext_name, ext_email in cur.fetchall():
            name, email = (full_name, u_email) if u_email else (ext_name, ext_email)
            if email and email not in recipients:
                recipients[email] = name

        # Called students / orchestra members
        if rehearsal_type == "orchestra":
            cur.execute("""
                SELECT fullname, email FROM users
                WHERE org_id = %s AND role = 'orchestra_member' AND email IS NOT NULL
            """, (org_id,))
            for name, email in cur.fetchall():
                if email and email not in recipients:
                    recipients[email] = name

            # Seat holders for this specific concert (includes no-account contacts)
            cur.execute("""
                SELECT u.fullname, u.email, ose.external_name, ose.external_email
                FROM orchestra_seats ose
                LEFT JOIN users u ON u.id = ose.member_id
                WHERE ose.opera_id = %s
            """, (opera_id,))
            for full_name, u_email, ext_name, ext_email in cur.fetchall():
                name, email = (full_name, u_email) if u_email else (ext_name, ext_email)
                if email and email not in recipients:
                    recipients[email] = name
        elif attendance_type == "full":
            cur.execute("""
                SELECT DISTINCT u.fullname, u.email FROM users u
                JOIN student_assignments sa ON sa.student_id = u.id
                WHERE sa.opera_id = %s AND u.role = 'student' AND u.email IS NOT NULL
            """, (opera_id,))
            for name, email in cur.fetchall():
                if email and email not in recipients:
                    recipients[email] = name
        elif attendance_type in ("principals", "chorus") and cast_ids:
            cur.execute("""
                SELECT DISTINCT u.fullname, u.email FROM users u
                JOIN student_assignments sa ON sa.student_id = u.id
                WHERE sa.opera_id = %s AND sa.cast_id = ANY(%s)
                  AND u.role = 'student' AND u.email IS NOT NULL
            """, (opera_id, cast_ids))
            for name, email in cur.fetchall():
                if email and email not in recipients:
                    recipients[email] = name
        elif attendance_type == "coaching" and role_names:
            cur.execute("""
                SELECT DISTINCT u.fullname, u.email, sr.external_name, sr.external_email
                FROM student_roles sr
                LEFT JOIN users u ON u.id = sr.student_id
                WHERE sr.opera_id = %s AND sr.role_name = ANY(%s)
            """, (opera_id, role_names))
            for full_name, u_email, ext_name, ext_email in cur.fetchall():
                name, email = (full_name, u_email) if u_email else (ext_name, ext_email)
                if email and email not in recipients:
                    recipients[email] = name
        else:
            cur.execute("""
                SELECT DISTINCT u.fullname, u.email FROM users u
                JOIN student_assignments sa ON sa.student_id = u.id
                WHERE sa.opera_id = %s AND u.role = 'student' AND u.email IS NOT NULL
            """, (opera_id,))
            for name, email in cur.fetchall():
                if email and email not in recipients:
                    recipients[email] = name

    org_tz = get_org_tz(admin)
    local_start = start_dt.astimezone(org_tz) if start_dt.tzinfo else start_dt
    local_end = end_dt.astimezone(org_tz) if end_dt and end_dt.tzinfo else end_dt
    date_str = local_start.strftime("%A, %B %-d, %Y")
    start_str = local_start.strftime("%-I:%M %p")
    end_str = local_end.strftime("%-I:%M %p") if local_end else ""
    time_str = f"{start_str}–{end_str}" if end_str else start_str

    html_body, text_body = render_rehearsal_notes_email(opera_name, date_str, time_str, notes)
    subject = f"Rehearsal Notes – {opera_name}"

    sent = 0
    for email in recipients:
        if send_email(to=email, subject=subject, html_body=html_body, text_body=text_body):
            sent += 1

    return {"status": "success", "emailed": sent}


@app.put("/admin/rehearsals/{rehearsal_id}")
def admin_edit_rehearsal(rehearsal_id: int, payload: dict, request: Request):
    admin = require_user(request, role="admin")

    start_time = payload.get("start_time")
    end_time = payload.get("end_time") or None
    location = (payload.get("location") or "").strip() or None
    notes = (payload.get("notes") or "").strip() or None
    attendance_type = payload.get("attendance_type") or None

    if not start_time:
        return {"status": "fail", "message": "start_time required"}

    valid_attendance = {"full", "principals", "chorus", "coaching"}
    if attendance_type and attendance_type not in valid_attendance:
        return {"status": "fail", "message": "Invalid attendance_type"}

    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            SELECT r.id FROM rehearsals r
            JOIN operas o ON o.id = r.opera_id
            WHERE r.id=%s AND o.org_id=%s
            """,
            (rehearsal_id, admin["org_id"]),
        )
        if not cur.fetchone():
            return {"status": "fail", "message": "Rehearsal not found"}

        fields = "start_time=%s, end_time=%s, location=%s, notes=%s"
        params = [start_time, end_time, location, notes]
        if attendance_type:
            fields += ", attendance_type=%s"
            params.append(attendance_type)
        params.append(rehearsal_id)
        cur.execute(f"UPDATE rehearsals SET {fields} WHERE id=%s", params)

    return {"status": "success"}


@app.get("/admin/rehearsals")
def admin_rehearsals(request: Request):
    user = require_user(request, role="admin")
    # Filter by rehearsal type based on the caller's role
    if user["role"] == "orchestra_admin":
        type_filter = "AND r.rehearsal_type = 'orchestra'"
    elif user["role"] == "admin":
        type_filter = "AND r.rehearsal_type = 'vocal'"
    else:
        type_filter = ""  # head_admin / system_admin see everything

    with db_cursor() as cur:
        cur.execute(f"""
            SELECT
                r.id, r.start_time, r.end_time, r.notes,
                o.opera_name, r.attendance_type, r.location,
                r.rehearsal_type, r.opera_id
            FROM rehearsals r
            JOIN operas o ON o.id = r.opera_id
            WHERE o.org_id = %s {type_filter}
            ORDER BY r.start_time
        """, (user["org_id"],))
        base_rows = cur.fetchall()

        # Fetch all called casts, roles, and leaders in bulk (avoid N+1)
        cur.execute("""
            SELECT rc.rehearsal_id, c.name
            FROM rehearsal_casts rc
            JOIN casts c ON c.id = rc.cast_id
            ORDER BY c.name
        """)
        casts_by_rehearsal = {}
        for r_id, c_name in cur.fetchall():
            casts_by_rehearsal.setdefault(r_id, []).append(c_name)

        cur.execute("""
            SELECT rehearsal_id, role_name
            FROM rehearsal_roles
            ORDER BY role_name
        """)
        roles_by_rehearsal = {}
        for r_id, rn in cur.fetchall():
            roles_by_rehearsal.setdefault(r_id, []).append(rn)

        cur.execute("""
            SELECT rl.rehearsal_id, u.fullname
            FROM rehearsal_leaders rl
            JOIN users u ON u.id = rl.teacher_id
            ORDER BY u.fullname
        """)
        leaders_by_rehearsal = {}
        for r_id, name in cur.fetchall():
            leaders_by_rehearsal.setdefault(r_id, []).append(name)

        rehearsal_ids = [r[0] for r in base_rows]
        absence_count_by_rehearsal = {}
        if rehearsal_ids:
            cur.execute(
                "SELECT rehearsal_id, COUNT(*) FROM absence_requests WHERE rehearsal_id = ANY(%s) GROUP BY rehearsal_id",
                (rehearsal_ids,),
            )
            for r_id, cnt in cur.fetchall():
                absence_count_by_rehearsal[r_id] = cnt

    return [
        {
            "id": r[0],
            "start_time": r[1].isoformat(),
            "end_time": r[2].isoformat(),
            "notes": r[3],
            "opera": r[4],
            "attendance_type": r[5],
            "location": r[6] or "",
            "casts": casts_by_rehearsal.get(r[0], []),
            "roles": roles_by_rehearsal.get(r[0], []),
            "leaders": leaders_by_rehearsal.get(r[0], []),
            "rehearsal_type": r[7] or "vocal",
            "opera_id": r[8],
            "absence_count": absence_count_by_rehearsal.get(r[0], 0),
        }
        for r in base_rows
    ]


@app.get("/admin/rehearsals/{rehearsal_id}/absences")
def admin_rehearsal_absences(rehearsal_id: int, request: Request):
    user = require_user(request, role="admin")
    with db_cursor() as cur:
        cur.execute("""
            SELECT u.fullname, ar.reason, ar.note
            FROM absence_requests ar
            JOIN users u ON u.id = ar.singer_id
            JOIN rehearsals r ON r.id = ar.rehearsal_id
            WHERE ar.rehearsal_id = %s AND r.org_id = %s
            ORDER BY u.fullname
        """, (rehearsal_id, user["org_id"]))
        rows = cur.fetchall()
    return [{"name": r[0], "reason": r[1] or "", "note": r[2] or ""} for r in rows]

# ── Production Staff ──────────────────────────────────────────────────────────

@app.get("/admin/opera/{opera_id}/staff")
def get_opera_staff(opera_id: int, request: Request):
    require_user(request, role="admin")
    with db_cursor() as cur:
        cur.execute("""
            SELECT u.id, u.fullname, s.staff_role
            FROM opera_staff s
            JOIN users u ON u.id = s.teacher_id
            WHERE s.opera_id = %s
            ORDER BY u.fullname
        """, (opera_id,))
        return [{"id": r[0], "name": r[1], "role": r[2] or ""} for r in cur.fetchall()]


@app.post("/admin/opera/{opera_id}/staff")
def add_opera_staff(opera_id: int, payload: dict, request: Request):
    require_user(request, role="admin")
    teacher_id = payload.get("teacher_id")
    role_label = (payload.get("role_label") or "").strip()
    if not teacher_id:
        return {"status": "fail", "message": "Missing teacher_id"}
    with db_cursor(commit=True) as cur:
        # Delete existing entry for this teacher on this opera, then re-insert
        cur.execute("DELETE FROM opera_staff WHERE opera_id = %s AND teacher_id = %s",
                    (opera_id, teacher_id))
        cur.execute("""
            INSERT INTO opera_staff (opera_id, teacher_id, staff_role)
            VALUES (%s, %s, %s)
        """, (opera_id, teacher_id, role_label))
    return {"status": "success"}


@app.delete("/admin/opera/{opera_id}/staff/{teacher_id}")
def remove_opera_staff(opera_id: int, teacher_id: int, request: Request):
    require_user(request, role="admin")
    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM opera_staff WHERE opera_id = %s AND teacher_id = %s",
                    (opera_id, teacher_id))
    return {"status": "success"}


@app.delete("/admin/rehearsal/{rehearsal_id}")
def delete_rehearsal(rehearsal_id: int, request: Request):
    """Cancel (delete) a rehearsal and all its associated records."""
    require_user(request, role="admin")
    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM rehearsal_leaders WHERE rehearsal_id = %s", (rehearsal_id,))
        cur.execute("DELETE FROM rehearsal_roles    WHERE rehearsal_id = %s", (rehearsal_id,))
        cur.execute("DELETE FROM rehearsal_casts    WHERE rehearsal_id = %s", (rehearsal_id,))
        cur.execute("DELETE FROM rehearsals         WHERE id = %s", (rehearsal_id,))
    return {"status": "success"}


@app.get("/admin/all-schedules")
def admin_all_schedules(request: Request):
    """Returns each teacher with their current weekly availability."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    with db_cursor() as cur:
        cur.execute("""
            SELECT u.id, u.fullname
            FROM users u
            WHERE u.org_id = %s AND u.role = 'teacher'
            ORDER BY u.fullname
        """, (org_id,))
        teachers = cur.fetchall()

        results = []
        for teacher_id, fullname in teachers:
            cur.execute("""
                SELECT weekday, start_time, end_time
                FROM weekly_availability
                WHERE teacher_id = %s AND active = TRUE
                ORDER BY weekday, start_time
            """, (teacher_id,))
            rows = cur.fetchall()
            schedule = [
                {
                    "weekday": r[0],
                    "start": r[1].strftime("%H:%M") if r[1] else None,
                    "end": r[2].strftime("%H:%M") if r[2] else None,
                }
                for r in rows
            ]
            results.append({
                "id": teacher_id,
                "name": fullname,
                "schedule": schedule,
            })

    return results

@app.get("/admin/opera-casting/{opera_id}")
def admin_opera_casting(opera_id: int, request: Request):
    """
    Returns all data the casting UI needs for one opera:
      - The opera's principal roles with their voice types
      - Current cast assignments (role + cast + student) for this opera
      - Students assigned to the opera (with their voice types)
      - Available students NOT yet in this opera (pool for adding to chorus)
    """
    user = require_user(request, role="admin")
    org_id = user["org_id"]

    with db_cursor() as cur:
        # Opera info
        cur.execute("""
            SELECT id, opera_name FROM operas
            WHERE id = %s AND org_id = %s
        """, (opera_id, org_id))
        opera_row = cur.fetchone()
        if not opera_row:
            return {"error": "Opera not found"}

        # Principal roles (exclude Chorus)
        cur.execute("""
            SELECT id, role_name, voice_type
            FROM opera_roles
            WHERE opera_id = %s
              AND (is_principal = TRUE OR is_principal IS NULL)
              AND LOWER(role_name) <> 'chorus'
            ORDER BY role_name
        """, (opera_id,))
        role_rows = cur.fetchall()

        # Casts for this opera
        cur.execute("""
            SELECT id, name FROM casts
            WHERE opera_id = %s
            ORDER BY name
        """, (opera_id,))
        cast_rows = cur.fetchall()

        # Current principal assignments for this opera
        cur.execute("""
            SELECT sr.student_id, sr.cast_id, sr.role_name,
                   u.fullname, u.voice_type, sr.external_name, sr.external_email
            FROM student_roles sr
            LEFT JOIN users u ON u.id = sr.student_id
            WHERE sr.opera_id = %s
              AND LOWER(sr.role_name) <> 'chorus'
        """, (opera_id,))
        assignment_rows = cur.fetchall()

        # All students assigned to this opera (regardless of cast/role)
        cur.execute("""
            SELECT DISTINCT u.id, u.fullname, u.voice_type
            FROM users u
            JOIN student_assignments sa ON sa.student_id = u.id
            WHERE sa.opera_id = %s
              AND u.role = 'student'
            ORDER BY u.voice_type, u.fullname
        """, (opera_id,))
        assigned_students = cur.fetchall()

        # All students in org (for the "add to opera" flow)
        cur.execute("""
            SELECT id, fullname, voice_type
            FROM users
            WHERE role = 'student'
              AND org_id = %s
            ORDER BY voice_type, fullname
        """, (org_id,))
        all_students = cur.fetchall()

        # Covers for this opera
        cur.execute("""
            SELECT rc.id, rc.cast_id, rc.role_name, rc.student_id,
                   u.fullname, u.voice_type
            FROM role_covers rc
            JOIN users u ON u.id = rc.student_id
            WHERE rc.opera_id = %s
            ORDER BY rc.role_name, u.fullname
        """, (opera_id,))
        cover_rows = cur.fetchall()

    # Build assignments lookup: {(cast_id, role_name): student_info}
    assignments = {}
    for s_id, c_id, r_name, name, voice, ext_name, ext_email in assignment_rows:
        if c_id is None:
            continue  # skip any bad data
        assignments[(c_id, r_name)] = {
            "student_id": s_id,
            "name": name if s_id else ext_name,
            "voice_type": voice,
            "external_email": None if s_id else ext_email,
        }

    # Chorus count: assigned students who have no principal role
    principal_student_ids = {a[0] for a in assignment_rows if a[0]}
    chorus_count = sum(
        1 for s_id, _, _ in assigned_students
        if s_id not in principal_student_ids
    )

    return {
        "opera": {"id": opera_row[0], "name": opera_row[1]},
        "casts": [{"id": c[0], "name": c[1]} for c in cast_rows],
        "roles": [
            {"id": r[0], "name": r[1], "voice_type": r[2]}
            for r in role_rows
        ],
        "assignments": [
            {
                "cast_id": c_id,
                "role_name": r_name,
                "student_id": info["student_id"],
                "student_name": info["name"],
                "student_voice": info["voice_type"],
                "external_email": info["external_email"],
            }
            for (c_id, r_name), info in assignments.items()
        ],
        "assigned_students": [
            {"id": s[0], "name": s[1], "voice_type": s[2]}
            for s in assigned_students
        ],
        "all_students": [
            {"id": s[0], "name": s[1], "voice_type": s[2]}
            for s in all_students
        ],
        "chorus_count": chorus_count,
        "covers": [
            {
                "id": r[0], "cast_id": r[1], "role_name": r[2],
                "student_id": r[3], "student_name": r[4], "student_voice": r[5],
            }
            for r in cover_rows
        ],
    }
@app.post("/admin/assign-principal")
def admin_assign_principal(payload: dict):
    """
    Assign or clear a principal role.

    Payload shape for ASSIGN (account holder):
      { opera_id, cast_id, role_name, student_id }

    Payload shape for ASSIGN (no account yet — still gets rehearsal notices):
      { opera_id, cast_id, role_name, external_name, external_email }

    Payload shape for CLEAR (pass student_id=null and no external fields):
      { opera_id, cast_id, role_name, student_id: null }
    """
    opera_id = payload.get("opera_id")
    cast_id = payload.get("cast_id")
    role_name = payload.get("role_name")
    student_id = payload.get("student_id")  # may be null to clear
    external_name = (payload.get("external_name") or "").strip() or None
    external_email = (payload.get("external_email") or "").strip() or None

    if not (opera_id and cast_id and role_name):
        return {"status": "fail", "message": "Missing opera_id, cast_id, or role_name"}

    with db_cursor(commit=True) as cur:
        # Validate opera, cast, role exist
        cur.execute("SELECT 1 FROM operas WHERE id=%s", (opera_id,))
        if not cur.fetchone():
            return {"status": "fail", "message": "Opera not found"}

        cur.execute(
            "SELECT 1 FROM casts WHERE id=%s AND opera_id=%s",
            (cast_id, opera_id)
        )
        if not cur.fetchone():
            return {"status": "fail", "message": "Cast not in this opera"}

        # First: clear any existing assignment for (opera, cast, role)
        # (If another student was previously in this role/cast, they're now chorus.)
        cur.execute("""
            DELETE FROM student_roles
            WHERE opera_id=%s AND cast_id=%s AND role_name=%s
        """, (opera_id, cast_id, role_name))

        # No-account contact: store name/email directly, no student_assignments row
        if not student_id and external_name:
            cur.execute("""
                INSERT INTO student_roles (student_id, opera_id, cast_id, role_name, external_name, external_email)
                VALUES (NULL, %s, %s, %s, %s, %s)
            """, (opera_id, cast_id, role_name, external_name, external_email))
            return {"status": "success"}

        # If student_id is null, we're just clearing — done
        if not student_id:
            return {"status": "success", "message": "Role cleared"}

        # Validate student
        cur.execute("""
            SELECT 1 FROM users WHERE id=%s AND role='student'
        """, (student_id,))
        if not cur.fetchone():
            return {"status": "fail", "message": "Student not found"}

        # Ensure student is assigned to the opera (with this cast_id)
        cur.execute("""
            INSERT INTO student_assignments (student_id, opera_id, cast_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (student_id, opera_id, cast_id) DO NOTHING
        """, (student_id, opera_id, cast_id))

        # Insert the principal role assignment
        cur.execute("""
            INSERT INTO student_roles (student_id, opera_id, cast_id, role_name)
            VALUES (%s, %s, %s, %s)
        """, (student_id, opera_id, cast_id, role_name))

    return {"status": "success"}


@app.post("/admin/covers")
def admin_add_cover(payload: dict, request: Request):
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    opera_id = payload.get("opera_id")
    cast_id = payload.get("cast_id")
    role_name = payload.get("role_name")
    student_id = payload.get("student_id")

    if not (opera_id and cast_id and role_name and student_id):
        return {"status": "fail", "message": "Missing required fields"}

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT 1 FROM operas WHERE id=%s AND org_id=%s", (opera_id, org_id))
        if not cur.fetchone():
            return {"status": "fail", "message": "Opera not found"}

        cur.execute("SELECT 1 FROM casts WHERE id=%s AND opera_id=%s", (cast_id, opera_id))
        if not cur.fetchone():
            return {"status": "fail", "message": "Cast not in this opera"}

        cur.execute("SELECT 1 FROM users WHERE id=%s AND role='student' AND org_id=%s", (student_id, org_id))
        if not cur.fetchone():
            return {"status": "fail", "message": "Student not found"}

        try:
            cur.execute("""
                INSERT INTO role_covers (opera_id, cast_id, role_name, student_id)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (opera_id, cast_id, role_name, student_id))
            new_id = cur.fetchone()[0]
            cur.execute("SELECT fullname, voice_type FROM users WHERE id=%s", (student_id,))
            name_row = cur.fetchone()
        except pg_errors.UniqueViolation:
            return {"status": "fail", "message": "This singer is already a cover for this role"}

    return {
        "status": "success",
        "cover": {
            "id": new_id,
            "cast_id": cast_id,
            "role_name": role_name,
            "student_id": student_id,
            "student_name": name_row[0],
            "student_voice": name_row[1],
        },
    }


@app.delete("/admin/covers/{cover_id}")
def admin_remove_cover(cover_id: int, request: Request):
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    with db_cursor(commit=True) as cur:
        cur.execute("""
            DELETE FROM role_covers rc
            USING operas o
            WHERE rc.id = %s AND rc.opera_id = o.id AND o.org_id = %s
        """, (cover_id, org_id))
        if cur.rowcount == 0:
            return {"status": "fail", "message": "Cover not found"}
    return {"status": "success"}


@app.post("/admin/add-to-opera")
def admin_add_to_opera(payload: dict):
    """
    Add one or more students to an opera (as chorus by default — no cast_id).
    Payload: { opera_id, student_ids: [...] }
    """
    opera_id = payload.get("opera_id")
    student_ids = payload.get("student_ids") or []

    if not opera_id or not student_ids:
        return {"status": "fail", "message": "Missing opera_id or student_ids"}

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT 1 FROM operas WHERE id=%s", (opera_id,))
        if not cur.fetchone():
            return {"status": "fail", "message": "Opera not found"}

        for sid in student_ids:
            # Add with cast_id=NULL (chorus by default)
            cur.execute("""
                INSERT INTO student_assignments (student_id, opera_id, cast_id)
                VALUES (%s, %s, NULL)
                ON CONFLICT (student_id, opera_id, cast_id) DO NOTHING
            """, (sid, opera_id))

    return {"status": "success"}


@app.post("/admin/remove-from-opera")
def admin_remove_from_opera(payload: dict):
    """
    Remove a student entirely from an opera (removes all their cast assignments and roles).
    Payload: { opera_id, student_id }
    """
    opera_id = payload.get("opera_id")
    student_id = payload.get("student_id")

    if not opera_id or not student_id:
        return {"status": "fail", "message": "Missing opera_id or student_id"}

    with db_cursor(commit=True) as cur:
        # Remove any roles
        cur.execute("""
            DELETE FROM student_roles
            WHERE student_id=%s AND opera_id=%s
        """, (student_id, opera_id))

        # Remove from opera
        cur.execute("""
            DELETE FROM student_assignments
            WHERE student_id=%s AND opera_id=%s
        """, (student_id, opera_id))

    return {"status": "success"}
@app.get("/admin/opera-staff/{opera_id}")
def admin_opera_staff(opera_id: int, request: Request):
    """Returns staff for this opera + all admins available to add."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    with db_cursor() as cur:
        # Current staff on this opera
        cur.execute("""
            SELECT os.id, os.teacher_id, u.fullname, os.staff_role, os.external_name, os.external_email
            FROM opera_staff os
            LEFT JOIN users u ON u.id = os.teacher_id
            WHERE os.opera_id = %s
            ORDER BY os.staff_role, COALESCE(u.fullname, os.external_name)
        """, (opera_id,))
        staff = [
            {
                "id": r[0],
                "teacher_id": r[1],
                "teacher_name": r[2] if r[1] else r[4],
                "staff_role": r[3],
                "external_email": None if r[1] else r[5],
            }
            for r in cur.fetchall()
        ]

        # All opera/orchestra admins in org with a defined sub-role
        cur.execute("""
            SELECT id, fullname, admin_role
            FROM users
            WHERE org_id = %s AND role IN ('admin', 'orchestra_admin') AND admin_role IS NOT NULL
            ORDER BY fullname
        """, (org_id,))
        teachers = [{"id": r[0], "name": r[1], "admin_role": r[2]} for r in cur.fetchall()]

    return {"staff": staff, "teachers": teachers}


@app.post("/admin/assign-staff")
def admin_assign_staff(payload: dict, request: Request):
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    opera_id = payload.get("opera_id")
    teacher_id = payload.get("teacher_id")
    external_name = (payload.get("external_name") or "").strip() or None
    external_email = (payload.get("external_email") or "").strip() or None
    external_role = (payload.get("external_role") or "").strip() or None

    if not opera_id or not (teacher_id or external_name):
        return {"status": "fail", "message": "Missing fields"}

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT 1 FROM operas WHERE id=%s AND org_id=%s", (opera_id, org_id))
        if not cur.fetchone():
            return {"status": "fail", "message": "Opera not found"}

        if teacher_id:
            cur.execute(
                "SELECT admin_role FROM users WHERE id=%s AND org_id=%s AND role IN ('admin', 'orchestra_admin')",
                (teacher_id, org_id)
            )
            row = cur.fetchone()
            if not row:
                return {"status": "fail", "message": "Admin not found"}
            staff_role = row[0]
            if not staff_role:
                return {"status": "fail", "message": "This admin does not have a production role assigned"}

            try:
                cur.execute("""
                    INSERT INTO opera_staff (opera_id, teacher_id, staff_role)
                    VALUES (%s, %s, %s)
                """, (opera_id, teacher_id, staff_role))
            except pg_errors.UniqueViolation:
                return {"status": "fail", "message": "This person is already assigned to this production"}
        else:
            if not external_role:
                return {"status": "fail", "message": "Please pick a role"}
            cur.execute("""
                INSERT INTO opera_staff (opera_id, teacher_id, staff_role, external_name, external_email)
                VALUES (%s, NULL, %s, %s, %s)
            """, (opera_id, external_role, external_name, external_email))

    return {"status": "success"}


@app.post("/admin/remove-staff")
def admin_remove_staff(payload: dict, request: Request):
    require_user(request, role="admin")
    staff_id = payload.get("staff_id")
    if not staff_id:
        return {"status": "fail", "message": "Missing staff_id"}

    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM opera_staff WHERE id = %s", (staff_id,))

    return {"status": "success"}


@app.get("/admin/opera-leaders/{opera_id}")
def admin_opera_leaders(opera_id: int):
    """
    Returns the production staff for this opera, formatted for a 'led by'
    multi-select when creating rehearsals.
    """
    with db_cursor() as cur:
        cur.execute("""
            SELECT os.teacher_id, u.fullname, os.staff_role
            FROM opera_staff os
            JOIN users u ON u.id = os.teacher_id
            WHERE os.opera_id = %s
            ORDER BY u.fullname
        """, (opera_id,))
        rows = cur.fetchall()

    # Dedupe: a teacher might hold multiple roles; show them once with all roles listed.
    by_teacher = {}
    for teacher_id, name, staff_role in rows:
        if teacher_id not in by_teacher:
            by_teacher[teacher_id] = {
                "teacher_id": teacher_id,
                "name": name,
                "roles": [],
            }
        by_teacher[teacher_id]["roles"].append(staff_role)

    return list(by_teacher.values())


# ========================================================
# PRODUCTIONS (head_admin / system_admin only)
# ========================================================

@app.get("/admin/productions")
def admin_list_productions(request: Request):
    """List all productions (extended opera records) for the head admin's org."""
    user = require_head_admin(request)
    org_id = user["org_id"]

    with db_cursor() as cur:
        cur.execute("""
            SELECT id, opera_name, start_date, end_date, num_casts
            FROM operas
            WHERE org_id = %s
            ORDER BY COALESCE(start_date, '9999-01-01'), opera_name
        """, (org_id,))
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "title": r[1],
            "start_date": r[2].isoformat() if r[2] else None,
            "end_date": r[3].isoformat() if r[3] else None,
            "num_casts": r[4] or 1,
        }
        for r in rows
    ]


@app.post("/admin/productions")
def admin_create_production(payload: dict, request: Request):
    """Create a new production. Auto-creates N cast records."""
    user = require_head_admin(request)
    org_id = user["org_id"]

    title = (payload.get("title") or "").strip()
    start_date = payload.get("start_date") or None
    end_date = payload.get("end_date") or None
    num_casts = int(payload.get("num_casts") or 1)
    roles = payload.get("roles") or []  # list of {role_name, voice_type}

    if not title:
        return {"status": "fail", "message": "Production title is required."}
    if num_casts < 1 or num_casts > 10:
        return {"status": "fail", "message": "Number of casts must be 1–10."}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO operas (org_id, opera_name, start_date, end_date, num_casts)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (org_id, title, start_date, end_date, num_casts))
        opera_id = cur.fetchone()[0]

        # Auto-create cast records (Cast A, Cast B, …)
        cast_letters = "ABCDEFGHIJ"
        for i in range(num_casts):
            cur.execute("""
                INSERT INTO casts (opera_id, name) VALUES (%s, %s)
            """, (opera_id, f"Cast {cast_letters[i]}"))

        # Insert named roles
        for r in roles:
            rname = (r.get("role_name") or "").strip()
            vtype = (r.get("voice_type") or "Any").strip()
            if rname:
                cur.execute("""
                    INSERT INTO opera_roles (opera_id, role_name, voice_type, is_principal)
                    VALUES (%s, %s, %s, TRUE)
                """, (opera_id, rname, vtype))

    return {"status": "success", "opera_id": opera_id}


@app.put("/admin/productions/{opera_id}")
def admin_update_production(opera_id: int, payload: dict, request: Request):
    """Update production metadata (title, dates)."""
    user = require_head_admin(request)
    org_id = user["org_id"]

    title = (payload.get("title") or "").strip()
    start_date = payload.get("start_date") or None
    end_date = payload.get("end_date") or None

    if not title:
        return {"status": "fail", "message": "Production title is required."}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE operas
            SET opera_name = %s, start_date = %s, end_date = %s
            WHERE id = %s AND org_id = %s
        """, (title, start_date, end_date, opera_id, org_id))

    return {"status": "success"}


@app.post("/admin/productions/{opera_id}/casts")
def admin_add_cast(opera_id: int, request: Request):
    """Add a new cast to an existing production."""
    user = require_head_admin(request)
    org_id = user["org_id"]

    with db_cursor(commit=True) as cur:
        cur.execute(
            "SELECT num_casts FROM operas WHERE id=%s AND org_id=%s",
            (opera_id, org_id)
        )
        row = cur.fetchone()
        if not row:
            return {"status": "fail", "message": "Production not found."}

        current_count = row[0] or 0
        if current_count >= 10:
            return {"status": "fail", "message": "Maximum of 10 casts allowed."}

        cast_letters = "ABCDEFGHIJ"
        new_name = f"Cast {cast_letters[current_count]}"

        cur.execute(
            "INSERT INTO casts (opera_id, name) VALUES (%s, %s) RETURNING id",
            (opera_id, new_name)
        )
        new_cast_id = cur.fetchone()[0]

        cur.execute(
            "UPDATE operas SET num_casts = num_casts + 1 WHERE id=%s",
            (opera_id,)
        )

    return {"status": "success", "cast": {"id": new_cast_id, "name": new_name}}


@app.patch("/admin/casts/{cast_id}")
def admin_rename_cast(cast_id: int, payload: dict, request: Request):
    """Rename a cast."""
    user = require_head_admin(request)
    org_id = user["org_id"]
    new_name = (payload.get("name") or "").strip()
    if not new_name:
        return {"status": "fail", "message": "Name is required."}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE casts SET name=%s
            WHERE id=%s
              AND opera_id IN (SELECT id FROM operas WHERE org_id=%s)
        """, (new_name, cast_id, org_id))

    return {"status": "success"}


@app.delete("/admin/casts/{cast_id}")
def admin_delete_cast(cast_id: int, request: Request):
    """Remove a cast and all its assignments."""
    user = require_head_admin(request)
    org_id = user["org_id"]

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT opera_id FROM casts
            WHERE id=%s AND opera_id IN (SELECT id FROM operas WHERE org_id=%s)
        """, (cast_id, org_id))
        row = cur.fetchone()
        if not row:
            return {"status": "fail", "message": "Cast not found."}
        opera_id = row[0]

        cur.execute("DELETE FROM student_assignments WHERE cast_id=%s", (cast_id,))
        cur.execute("DELETE FROM student_roles WHERE cast_id=%s", (cast_id,))
        cur.execute("DELETE FROM rehearsal_casts WHERE cast_id=%s", (cast_id,))
        cur.execute("DELETE FROM casts WHERE id=%s", (cast_id,))
        cur.execute("""
            UPDATE operas
            SET num_casts = (SELECT COUNT(*) FROM casts WHERE opera_id=%s)
            WHERE id=%s
        """, (opera_id, opera_id))

    return {"status": "success"}


# ========================================================
# ORG TRANSFER REQUESTS (student submits; head_admin reviews)
# ========================================================

@app.post("/student/org-transfer-request")
def submit_org_transfer(payload: dict, request: Request):
    """Student requests a transfer to a different org."""
    user = require_user(request, role="student")
    to_org_slug = (payload.get("to_org") or "").strip()

    to_org_id = get_org_id(to_org_slug)
    if to_org_id is None:
        return {"status": "fail", "message": "Invalid destination organization."}

    from_org_id = user["org_id"]
    if to_org_id == from_org_id:
        return {"status": "fail", "message": "You are already in that organization."}

    with db_cursor(commit=True) as cur:
        # Only one pending request per student at a time
        cur.execute("""
            SELECT 1 FROM org_transfer_requests
            WHERE student_id = %s AND status = 'pending'
        """, (user["id"],))
        if cur.fetchone():
            return {"status": "fail", "message": "You already have a pending transfer request."}

        cur.execute("""
            INSERT INTO org_transfer_requests
                (student_id, from_org_id, to_org_id, message)
            VALUES (%s, %s, %s, %s)
        """, (user["id"], from_org_id, to_org_id, payload.get("message") or None))

    return {"status": "success"}


@app.get("/student/org-transfer-request")
def get_student_transfer_request(request: Request):
    """Return the student's current (most recent) transfer request, if any."""
    user = require_user(request, role="student")

    with db_cursor() as cur:
        cur.execute("""
            SELECT r.id, o.name AS to_org, r.status, r.created_at, r.reviewed_at
            FROM org_transfer_requests r
            JOIN organizations o ON o.id = r.to_org_id
            WHERE r.student_id = %s
            ORDER BY r.created_at DESC
            LIMIT 1
        """, (user["id"],))
        row = cur.fetchone()

    if not row:
        return {"request": None}

    return {
        "request": {
            "id": row[0],
            "to_org": row[1],
            "status": row[2],
            "created_at": row[3].isoformat() if row[3] else None,
            "reviewed_at": row[4].isoformat() if row[4] else None,
        }
    }


@app.get("/admin/org-transfer-requests")
def admin_list_transfers(request: Request):
    """Head admin sees pending transfer requests directed at their org."""
    user = require_head_admin(request)
    org_id = user["org_id"]

    with db_cursor() as cur:
        cur.execute("""
            SELECT r.id, u.fullname, u.email, o_from.name AS from_org,
                   r.status, r.message, r.created_at
            FROM org_transfer_requests r
            JOIN users u ON u.id = r.student_id
            JOIN organizations o_from ON o_from.id = r.from_org_id
            WHERE r.to_org_id = %s AND r.status = 'pending'
            ORDER BY r.created_at
        """, (org_id,))
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "student_name": r[1],
            "student_email": r[2],
            "from_org": r[3],
            "status": r[4],
            "message": r[5],
            "created_at": r[6].isoformat() if r[6] else None,
        }
        for r in rows
    ]


@app.post("/admin/org-transfer-requests/{request_id}/review")
def admin_review_transfer(request_id: int, payload: dict, request: Request):
    """Head admin approves or denies a transfer request."""
    user = require_head_admin(request)
    org_id = user["org_id"]
    decision = payload.get("decision")  # "approved" or "denied"

    if decision not in ("approved", "denied"):
        return {"status": "fail", "message": "Decision must be 'approved' or 'denied'."}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT student_id, from_org_id, to_org_id
            FROM org_transfer_requests
            WHERE id = %s AND to_org_id = %s AND status = 'pending'
        """, (request_id, org_id))
        row = cur.fetchone()
        if not row:
            return {"status": "fail", "message": "Request not found."}

        student_id, to_org_id = row[0], row[2]

        cur.execute("""
            UPDATE org_transfer_requests
            SET status = %s, reviewed_at = NOW(), reviewed_by = %s
            WHERE id = %s
        """, (decision, user["id"], request_id))

        if decision == "approved":
            cur.execute(
                "UPDATE users SET org_id = %s WHERE id = %s",
                (to_org_id, student_id)
            )

    return {"status": "success"}


# ========================================================
# TEACHER
# ========================================================
def get_teacher_viewing_date(tz=None):
    """
    Returns the date whose lessons the teacher should see.
    Rolls over at 6 PM to show the NEXT day's lessons.
    """
    if tz is None:
        tz = EST
    now_local = datetime.now(tz)
    if now_local.hour >= 18:
        return now_local.date() + timedelta(days=1)
    return now_local.date()

def get_bookable_date(tz=None, close_hour=18, open_hour=21):
    """
    The date currently eligible for booking: today until close_hour, then
    tomorrow once open_hour has passed (previous-evening booking window).
    """
    if tz is None:
        tz = EST
    now_local = datetime.now(tz)
    if now_local.hour < close_hour:
        return now_local.date()
    return now_local.date() + timedelta(days=1)

@app.get("/teacher/today")
def teacher_today(request: Request):
    """Teacher's lessons for the currently-relevant day, split by active/cancelled."""
    teacher = require_user(request, role="teacher")
    target_date = get_teacher_viewing_date(get_org_tz(teacher))
    cfg = get_org_lesson_config(teacher["org_id"])
    default_duration = cfg["duration_min"]

    with db_cursor() as cur:
        cur.execute("""
            SELECT
                lessons.id,
                COALESCE(u.fullname, lessons.external_name, 'External Student') AS student_name,
                lessons.lesson_time,
                lessons.status,
                lessons.cancelled_at,
                lessons.duration_min
            FROM lessons
            LEFT JOIN users u ON u.id = lessons.student_id
            WHERE lessons.teacher_id = %s
              AND lessons.lesson_date = %s
            ORDER BY lessons.lesson_time
        """, (teacher["id"], target_date))
        rows = cur.fetchall()

    active = []
    cancelled = []
    for r in rows:
        lesson = {
            "id": r[0],
            "student": r[1],
            "time": r[2].strftime("%H:%M") if r[2] else None,
            "duration_min": r[5] if r[5] is not None else default_duration,
        }
        if r[3] == "cancelled":
            lesson["cancelled_at"] = r[4].isoformat() if r[4] else None
            cancelled.append(lesson)
        else:
            active.append(lesson)

    return {
        "date": target_date.isoformat(),
        "lessons": active,
        "cancelled": cancelled,
    }


@app.get("/teacher/weekly")
def teacher_weekly(request: Request):
    teacher = require_user(request)
    if teacher["role"] not in TEACHER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")
    with db_cursor() as cur:
        cur.execute("""
            SELECT weekday, start_time, end_time
            FROM weekly_availability
            WHERE teacher_id = %s AND active = TRUE
            ORDER BY weekday, start_time
        """, (teacher["id"],))
        rows = cur.fetchall()
    return [
        {
            "weekday": r[0],
            "start": r[1].strftime("%H:%M") if r[1] else None,
            "end": r[2].strftime("%H:%M") if r[2] else None,
        }
        for r in rows
    ]

@app.post("/teacher/update-availability")
def teacher_update_availability(data: AvailabilityRequestData, request: Request):
    """
    Teacher updates their own availability directly (no approval needed).

    - scope='permanent': replaces the weekly template
    - scope='one_time': adds exceptions for the specified week
    """
    teacher = require_user(request)
    if teacher["role"] not in TEACHER_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized")

    if data.scope not in ("permanent", "one_time"):
        return {"status": "fail", "message": "Invalid scope"}

    # Validate and parse the effective week if one_time
    effective_week = None
    if data.scope == "one_time":
        if not data.effective_week_start:
            return {"status": "fail", "message": "One-time changes need a week start date"}
        try:
            effective_week = datetime.strptime(data.effective_week_start, "%Y-%m-%d").date()
        except ValueError:
            return {"status": "fail", "message": "Invalid week start date"}
        # Normalize to the Monday of that week
        effective_week = effective_week - timedelta(days=effective_week.weekday())

    # Validate each schedule entry
    clean_schedule = []
    for entry in data.schedule:
        if not (0 <= entry.weekday <= 6):
            return {"status": "fail", "message": "Invalid weekday"}
        try:
            start_t = datetime.strptime(entry.start_time, "%H:%M").time()
            end_t = datetime.strptime(entry.end_time, "%H:%M").time()
        except ValueError:
            return {"status": "fail", "message": "Invalid time format"}
        if end_t <= start_t:
            return {"status": "fail", "message": "End time must be after start time"}

        clean_schedule.append({
            "weekday": entry.weekday,
            "start_time": entry.start_time,
            "end_time": entry.end_time,
        })

    with db_cursor(commit=True) as cur:
        if data.scope == "permanent":
            # Wipe and replace the weekly_availability
            cur.execute(
                "DELETE FROM weekly_availability WHERE teacher_id = %s",
                (teacher["id"],)
            )
            for entry in clean_schedule:
                cur.execute("""
                    INSERT INTO weekly_availability
                        (teacher_id, weekday, start_time, end_time, active)
                    VALUES (%s, %s, %s::time, %s::time, TRUE)
                """, (
                    teacher["id"],
                    entry["weekday"],
                    entry["start_time"],
                    entry["end_time"],
                ))
        else:
            # one_time: set exceptions for the specified week
            week_dates = [effective_week + timedelta(days=i) for i in range(7)]
            cur.execute("""
                DELETE FROM availability_exceptions
                WHERE teacher_id = %s AND exception_date = ANY(%s)
            """, (teacher["id"], week_dates))

            schedule_by_weekday = {e["weekday"]: e for e in clean_schedule}
            for i in range(7):
                d = effective_week + timedelta(days=i)
                entry = schedule_by_weekday.get(i)
                if entry:
                    cur.execute("""
                        INSERT INTO availability_exceptions
                            (teacher_id, exception_date, start_time, end_time, active)
                        VALUES (%s, %s, %s::time, %s::time, TRUE)
                    """, (
                        teacher["id"], d,
                        entry["start_time"], entry["end_time"]
                    ))
                else:
                    # "off" marker — suppresses weekly template for this date
                    cur.execute("""
                        INSERT INTO availability_exceptions
                            (teacher_id, exception_date, active)
                        VALUES (%s, %s, FALSE)
                    """, (teacher["id"], d))

    return {"status": "success", "message": "Schedule updated"}

# -------- Lesson notes --------

NOTES_EDIT_WINDOW_DAYS = 7


def _is_notes_editable(lesson_date) -> bool:
    """Notes can only be edited within 7 days of the lesson date."""
    today = datetime.now(EST).date()
    return (today - lesson_date).days <= NOTES_EDIT_WINDOW_DAYS


@app.get("/teacher/lesson-notes")
def get_lesson_notes(lesson_id: int, request: Request):
    """Fetch notes for a single lesson (and whether it's editable)."""
    teacher = require_user(request, role="teacher")
    with db_cursor() as cur:
        # Verify the lesson belongs to this teacher
        cur.execute("""
            SELECT l.lesson_date, l.lesson_time, u.fullname
            FROM lessons l
            JOIN users u ON u.id = l.student_id
            WHERE l.id = %s AND l.teacher_id = %s
        """, (lesson_id, teacher["id"]))
        lesson_row = cur.fetchone()
        if not lesson_row:
            return {"status": "fail", "message": "Lesson not found"}

        lesson_date, lesson_time, student_name = lesson_row

        # Fetch notes (may not exist yet)
        cur.execute("""
            SELECT piece, technique, other, updated_at, shared_with_student, shared_at
            FROM lesson_notes
            WHERE lesson_id = %s
        """, (lesson_id,))
        notes_row = cur.fetchone()

    if notes_row:
        piece, technique, other, updated_at, shared, shared_at = notes_row
        updated_iso = updated_at.isoformat() if updated_at else None
        shared_at_iso = shared_at.isoformat() if shared_at else None
    else:
        piece = technique = other = ""
        updated_iso = None
        shared = False
        shared_at_iso = None

    return {
        "status": "success",
        "lesson_id": lesson_id,
        "student_name": student_name,
        "lesson_date": lesson_date.isoformat(),
        "lesson_time": lesson_time.strftime("%H:%M") if lesson_time else None,
        "piece": piece or "",
        "technique": technique or "",
        "other": other or "",
        "updated_at": updated_iso,
        "editable": _is_notes_editable(lesson_date),
        "shared_with_student": shared,
        "shared_at": shared_at_iso,
    }


@app.post("/teacher/lesson-notes")
def save_lesson_notes(payload: dict, request: Request):
    """Create or update notes for a lesson. Rejects if lesson > 7 days old."""
    teacher = require_user(request, role="teacher")
    lesson_id = payload.get("lesson_id")

    if not lesson_id:
        return {"status": "fail", "message": "Missing lesson_id"}

    piece = (payload.get("piece") or "").strip()
    technique = (payload.get("technique") or "").strip()
    other = (payload.get("other") or "").strip()

    with db_cursor(commit=True) as cur:
        # Verify teacher owns this lesson + check editable
        cur.execute("""
            SELECT lesson_date FROM lessons
            WHERE id = %s AND teacher_id = %s
        """, (lesson_id, teacher["id"]))
        row = cur.fetchone()
        if not row:
            return {"status": "fail", "message": "Lesson not found"}

        lesson_date = row[0]
        if not _is_notes_editable(lesson_date):
            return {"status": "fail", "message": "Notes are locked (>7 days old)"}

        # Upsert
        cur.execute("""
            INSERT INTO lesson_notes (lesson_id, piece, technique, other, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (lesson_id) DO UPDATE
            SET piece = EXCLUDED.piece,
                technique = EXCLUDED.technique,
                other = EXCLUDED.other,
                updated_at = NOW()
        """, (lesson_id, piece, technique, other))

    return {"status": "success"}
@app.post("/teacher/share-notes")
def share_notes(payload: dict, request: Request):
    """Mark a lesson's notes as shared with the student. Cannot be undone."""
    teacher = require_user(request, role="teacher")
    lesson_id = payload.get("lesson_id")

    if not lesson_id:
        return {"status": "fail", "message": "Missing lesson_id"}

    with db_cursor(commit=True) as cur:
        # Verify teacher owns the lesson
        cur.execute("""
            SELECT 1 FROM lessons
            WHERE id = %s AND teacher_id = %s
        """, (lesson_id, teacher["id"]))
        if not cur.fetchone():
            return {"status": "fail", "message": "Lesson not found"}

        # Ensure notes exist (create empty row if somehow not)
        cur.execute("""
            INSERT INTO lesson_notes (lesson_id, piece, technique, other)
            VALUES (%s, '', '', '')
            ON CONFLICT (lesson_id) DO NOTHING
        """, (lesson_id,))

        # Mark as shared
        cur.execute("""
            UPDATE lesson_notes
            SET shared_with_student = TRUE,
                shared_at = COALESCE(shared_at, NOW())
            WHERE lesson_id = %s
        """, (lesson_id,))

    return {"status": "success"}


@app.get("/teacher/students")
def teacher_students(request: Request):
    """
    List students this teacher has had lessons with.
    Includes lesson count and date of most recent lesson.
    Ordered by most recent lesson first.
    """
    teacher = require_user(request, role="teacher")

    with db_cursor() as cur:
        cur.execute("""
            SELECT
                u.id,
                u.fullname,
                u.voice_type,
                COUNT(l.id) AS lesson_count,
                MAX(l.lesson_date) AS most_recent
            FROM lessons l
            JOIN users u ON u.id = l.student_id
            WHERE l.teacher_id = %s
            GROUP BY u.id, u.fullname, u.voice_type
            ORDER BY most_recent DESC, u.fullname
        """, (teacher["id"],))
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "name": r[1],
            "voice_type": r[2],
            "lesson_count": r[3],
            "most_recent": r[4].isoformat() if r[4] else None,
        }
        for r in rows
    ]


@app.get("/teacher/student-history")
def teacher_student_history(request: Request, student_id: int):
    """
    Full lesson history with a specific student, most recent first.
    Each lesson includes any notes (or empty strings if no notes).
    """
    teacher = require_user(request, role="teacher")
    with db_cursor() as cur:
        # Verify student exists
        cur.execute(
            "SELECT id, fullname, voice_type FROM users WHERE id = %s AND role = 'student'",
            (student_id,)
        )
        student_row = cur.fetchone()
        if not student_row:
            return {"student": None, "lessons": []}

        # All lessons between this teacher and student, with notes
        cur.execute("""
            SELECT
                l.id, l.lesson_date, l.lesson_time, l.status,
                n.piece, n.technique, n.other, n.updated_at,
                n.shared_with_student
            FROM lessons l
            LEFT JOIN lesson_notes n ON n.lesson_id = l.id
            WHERE l.teacher_id = %s AND l.student_id = %s
            ORDER BY l.lesson_date DESC, l.lesson_time DESC
        """, (teacher["id"], student_id))
        lesson_rows = cur.fetchall()

    lessons = []
    for r in lesson_rows:
        (lesson_id, lesson_date, lesson_time, status,
         piece, technique, other, updated_at, shared) = r
        has_notes = bool(piece or technique or other)
        lessons.append({
            "id": lesson_id,
            "date": lesson_date.isoformat() if lesson_date else None,
            "time": lesson_time.strftime("%H:%M") if lesson_time else None,
            "status": status,
            "has_notes": has_notes,
            "piece": piece or "",
            "technique": technique or "",
            "other": other or "",
            "updated_at": updated_at.isoformat() if updated_at else None,
            "editable": _is_notes_editable(lesson_date) and status == "booked",
            "shared_with_student": bool(shared),
        })

    return {
        "student": {
            "id": student_row[0],
            "name": student_row[1],
            "voice_type": student_row[2],
            "lesson_count": len(lessons),
        },
        "lessons": lessons,
    }


@app.get("/teacher/weekly-schedule")
def teacher_weekly_schedule(request: Request, duration: int = Query(30)):
    """Teacher's weekly availability grid with recurring student assignments overlaid."""
    teacher = require_user(request, role="teacher")
    cfg = get_org_lesson_config(teacher["org_id"])
    # Clamp duration to a sensible range
    duration = max(15, min(duration, 180))

    with db_cursor() as cur:
        cur.execute("""
            SELECT weekday, start_time, end_time
            FROM weekly_availability
            WHERE teacher_id = %s AND active = TRUE
            ORDER BY weekday, start_time
        """, (teacher["id"],))
        avail_rows = cur.fetchall()

        cur.execute("""
            SELECT rl.id, rl.weekday, rl.lesson_time,
                   COALESCE(u.fullname, rl.external_name) AS student_name,
                   rl.student_id, rl.duration_min
            FROM recurring_lessons rl
            LEFT JOIN users u ON u.id = rl.student_id
            WHERE rl.teacher_id = %s AND rl.active = TRUE
        """, (teacher["id"],))
        recurring_rows = cur.fetchall()

    recurring_map = {}
    occupied_ranges = {}
    for rid, weekday, lesson_time, student_name, student_id, dur in recurring_rows:
        t_str = lesson_time.strftime("%H:%M")
        assign_dur = dur or 30
        recurring_map.setdefault(weekday, {})[t_str] = {
            "id": rid,
            "student_name": student_name,
            "duration_min": assign_dur,
        }
        rng_start = lesson_time.hour * 60 + lesson_time.minute
        occupied_ranges.setdefault(weekday, []).append((rng_start, rng_start + assign_dur))

    step = timedelta(minutes=duration)
    days = {}
    for weekday, start_t, end_t in avail_rows:
        slots = days.setdefault(weekday, [])
        today = datetime.today().date()
        cur_dt = datetime.combine(today, start_t)
        end_dt = datetime.combine(today, end_t)
        day_occupied = occupied_ranges.get(weekday, [])
        day_assigned = recurring_map.get(weekday, {})
        while cur_dt < end_dt:
            t_str = cur_dt.strftime("%H:%M")
            t_min = cur_dt.hour * 60 + cur_dt.minute
            # Always show slots that already have an assignment so the teacher
            # can see and remove them even if they overlap another assignment.
            # Only hide empty slots whose time window conflicts with an existing one.
            has_assignment = t_str in day_assigned
            if not has_assignment:
                blocked = any(
                    t_min + duration > rng_start and t_min < rng_end
                    for rng_start, rng_end in day_occupied
                )
                if blocked:
                    cur_dt += step
                    continue
            if cfg["has_lunch_break"]:
                slot_end = (cur_dt + step).time()
                if cur_dt.time() < LUNCH_END and slot_end > LUNCH_START:
                    cur_dt += step
                    continue
            slots.append({
                "time": t_str,
                "assignment": recurring_map.get(weekday, {}).get(t_str),
            })
            cur_dt += step

    # Inject any assigned slots that don't fall on the current step boundary
    # (e.g. a 45-min lesson at 11:30 won't appear in a 60-min step grid)
    for wd, slots in days.items():
        existing_times = {s["time"] for s in slots}
        for t_str, asgn in recurring_map.get(wd, {}).items():
            if t_str not in existing_times:
                slots.append({"time": t_str, "assignment": asgn})
        slots.sort(key=lambda s: s["time"])

    return [{"weekday": wd, "slots": s} for wd, s in sorted(days.items())]


@app.get("/teacher/org-students")
def teacher_org_students(request: Request):
    """All bookable users in the teacher's org (for teacher self-scheduling)."""
    teacher = require_user(request, role="teacher")
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, fullname, role, voice_type
            FROM users
            WHERE org_id = %s
              AND role IN ('student', 'choir_member', 'orchestra_member', 'ensemble_member')
            ORDER BY fullname
        """, (teacher["org_id"],))
        rows = cur.fetchall()
    return [
        {"id": r[0], "name": r[1], "role": r[2], "voice_type": r[3]}
        for r in rows
    ]


@app.post("/teacher/recurring-lesson")
def teacher_assign_recurring(payload: dict, request: Request):
    """Assign a student to a recurring weekday+time slot and generate the next 12 lessons."""
    teacher = require_user(request, role="teacher")
    org_tz = get_org_tz(teacher)

    weekday = payload.get("weekday")
    time_str = payload.get("time")
    student_id = payload.get("student_id")
    external_name = (payload.get("external_name") or "").strip()
    external_email = (payload.get("external_email") or "").strip()
    duration_min = int(payload.get("duration_min") or 30)

    if weekday is None or not time_str:
        return {"status": "fail", "message": "Weekday and time are required"}
    if not student_id and not external_name:
        return {"status": "fail", "message": "Select a student or enter a name"}

    try:
        lesson_time = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        return {"status": "fail", "message": "Invalid time"}

    student_name = None
    student_email = None
    if student_id:
        with db_cursor() as cur:
            cur.execute("SELECT fullname, email, org_id FROM users WHERE id = %s", (student_id,))
            srow = cur.fetchone()
        if not srow or srow[2] != teacher["org_id"]:
            return {"status": "fail", "message": "Student not found in your org"}
        student_name = srow[0]
        student_email = srow[1]

    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO recurring_lessons
                (teacher_id, student_id, external_name, external_email, weekday, lesson_time, duration_min)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (teacher_id, weekday, lesson_time) DO UPDATE
                SET student_id = EXCLUDED.student_id,
                    external_name = EXCLUDED.external_name,
                    external_email = EXCLUDED.external_email,
                    duration_min = EXCLUDED.duration_min,
                    active = TRUE
            RETURNING id
        """, (
            teacher["id"],
            student_id if student_id else None,
            external_name if not student_id else None,
            external_email if not student_id else None,
            weekday, lesson_time, duration_min
        ))
        recurring_id = cur.fetchone()[0]

        # Delete all future lessons for this slot so re-assignment creates a clean slate.
        # Simply cancelling them leaves cancelled rows that block the upcoming INSERTs
        # via the UNIQUE(teacher_id, lesson_date, lesson_time) constraint.
        cur.execute("""
            DELETE FROM lessons
            WHERE recurring_lesson_id = %s
              AND lesson_date >= CURRENT_DATE
        """, (recurring_id,))

        # Generate next 12 occurrences starting from the next matching weekday.
        # If today is the right weekday and the lesson time hasn't passed, include today.
        today = datetime.now(org_tz).date()
        now_local = datetime.now(org_tz).time()
        days_ahead = (weekday - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7  # recurring assignments always start from next occurrence
        first_date = today + timedelta(days=days_ahead)

        lessons_created = 0
        for i in range(12):
            lesson_date = first_date + timedelta(weeks=i)
            cur.execute("SAVEPOINT sp")
            try:
                cur.execute("""
                    INSERT INTO lessons
                        (teacher_id, student_id, lesson_date, lesson_time,
                         external_name, external_email, recurring_lesson_id, duration_min, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'booked')
                """, (
                    teacher["id"],
                    student_id if student_id else None,
                    lesson_date, lesson_time,
                    external_name if not student_id else None,
                    external_email if not student_id else None,
                    recurring_id,
                    duration_min,
                ))
                cur.execute("RELEASE SAVEPOINT sp")
                lessons_created += 1
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT sp")

    # Email notification
    recipient_email = student_email if student_id else (external_email or None)
    recipient_name = student_name if student_id else external_name
    day_name = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][weekday]
    h, m = lesson_time.hour, lesson_time.minute
    time_display = f"{((h - 1) % 12) + 1}:{m:02d} {'PM' if h >= 12 else 'AM'}"

    if recipient_email:
        teacher_name = teacher["fullname"]
        subject = f"Recurring Lesson Scheduled - {teacher_name} - {day_name}s at {time_display}"
        html_body = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:600px;margin:auto;padding:24px;">
<h2 style="color:#333;">Recurring Lesson Scheduled</h2>
<p>Hi {recipient_name},</p>
<p><strong>{teacher_name}</strong> has scheduled a weekly lesson for you:</p>
<p><strong>Every {day_name} at {time_display}</strong></p>
<p>{lessons_created} upcoming lessons have been added to your schedule.</p>
{'<p>Log in to CountrPnt to view your upcoming lessons.</p>' if student_id else ''}
</body></html>"""
        text_body = (
            f"Recurring Lesson Scheduled\n\nHi {recipient_name},\n"
            f"{teacher_name} has scheduled a weekly lesson: every {day_name} at {time_display}.\n"
            f"{lessons_created} upcoming lessons added.\n"
        )
        send_email(to=recipient_email, subject=subject, html_body=html_body, text_body=text_body)

    return {"status": "success", "recurring_id": recurring_id, "lessons_created": lessons_created}


@app.delete("/teacher/recurring-lesson/{recurring_id}")
def teacher_delete_recurring(recurring_id: int, request: Request):
    """Deactivate a recurring lesson assignment and cancel all future booked lessons tied to it."""
    teacher = require_user(request, role="teacher")
    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE recurring_lessons SET active = FALSE
            WHERE id = %s AND teacher_id = %s
        """, (recurring_id, teacher["id"]))
        cur.execute("""
            UPDATE lessons SET status = 'cancelled', cancelled_at = NOW()
            WHERE recurring_lesson_id = %s
              AND lesson_date >= CURRENT_DATE
              AND status = 'booked'
        """, (recurring_id,))
    return {"status": "success"}


# ========================================================
# STUDENT
# ========================================================

@app.get("/student/lessons")
def student_lessons(request: Request):
    student = require_user(request, role="student")
    cfg = get_org_lesson_config(student["org_id"])
    default_duration = cfg["duration_min"]
    with db_cursor() as cur:
        cur.execute("""
            SELECT l.id, l.lesson_date, l.lesson_time, u.fullname, l.status, l.duration_min
            FROM lessons l
            JOIN users u ON u.id = l.teacher_id
            WHERE l.student_id = %s
              AND (
                l.status = 'booked'
                OR (l.status = 'cancelled' AND l.lesson_date = CURRENT_DATE)
              )
            ORDER BY l.lesson_date, l.lesson_time
        """, (student["id"],))
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "date": r[1].isoformat() if r[1] else None,
            "time": r[2].strftime("%H:%M") if r[2] else None,
            "teacher": r[3],
            "status": r[4],
            "duration_min": r[5] if r[5] is not None else default_duration,
        }
        for r in rows
    ]

@app.get("/student/shared-notes")
def student_shared_notes(request: Request):
    """Returns all lesson notes a teacher has shared with this student."""
    student = require_user(request, role="student")

    with db_cursor() as cur:
        cur.execute("""
            SELECT
                l.id, l.lesson_date, l.lesson_time,
                u.fullname AS teacher_name,
                n.piece, n.technique, n.other, n.shared_at
            FROM lessons l
            JOIN lesson_notes n ON n.lesson_id = l.id
            JOIN users u ON u.id = l.teacher_id
            WHERE l.student_id = %s
              AND n.shared_with_student = TRUE
            ORDER BY l.lesson_date DESC, l.lesson_time DESC
        """, (student["id"],))
        rows = cur.fetchall()

    return [
        {
            "lesson_id": r[0],
            "date": r[1].isoformat() if r[1] else None,
            "time": r[2].strftime("%H:%M") if r[2] else None,
            "teacher": r[3],
            "piece": r[4] or "",
            "technique": r[5] or "",
            "other": r[6] or "",
            "shared_at": r[7].isoformat() if r[7] else None,
        }
        for r in rows
    ]

@app.get("/student/today")
def student_today(request: Request):
    """
    Returns everything the student dashboard needs for today's booking view:
      - Today's date
      - Whether the booking window is currently open
      - The student's rehearsals for today
      - A list of teachers with their morning/afternoon slot counts
    """
    student = require_user(request, role="student")
    org_tz = get_org_tz(student)
    cfg = get_org_lesson_config(student["org_id"])
    now_local = datetime.now(org_tz)
    target_date = get_bookable_date(org_tz, close_hour=cfg["booking_close_hour"], open_hour=cfg["booking_open_hour"])
    booking_open = is_booking_window_open_for(target_date, org_tz, open_hour=cfg["booking_open_hour"], close_hour=cfg["booking_close_hour"])
    booking_pending = not booking_open  # target date is set but window hasn't opened yet tonight

    # Today's rehearsals for this student
    with db_cursor() as cur:
        cur.execute("""
            SELECT DISTINCT
                r.id,
                r.start_time,
                r.end_time,
                r.notes,
                o.opera_name,
                c.name AS cast_name
            FROM rehearsals r
            JOIN operas o ON o.id = r.opera_id
            LEFT JOIN casts c ON c.id = r.cast_id
            JOIN student_assignments sa
                ON sa.student_id = %s
                AND sa.opera_id = r.opera_id
                AND (r.cast_id IS NULL OR r.cast_id = sa.cast_id)
            WHERE (r.start_time AT TIME ZONE %s)::date = %s
            ORDER BY r.start_time
        """, (student["id"], org_tz.zone, target_date))
        rows = cur.fetchall()

    rehearsals = [
        {
            "id": r[0],
            "start": r[1].isoformat(),
            "end": r[2].isoformat(),
            "notes": r[3],
            "opera": r[4],
            "cast": r[5] if r[5] else "All casts",
        }
        for r in rows
    ]

    # Teachers + slot counts. Return all teachers with a status flag so the
    # frontend can show each one appropriately.
    #   status='available'    → has bookable slots
    #   status='all_booked'   → is scheduled today but nothing's open
    #   status='not_working'  → off today (no weekly availability or exception off)
    teachers = []
    if booking_open:
        org_id = student["org_id"]
        weekday = target_date.weekday()

        with db_cursor() as cur:
            cur.execute("""
                SELECT id, fullname, teacher_type
                FROM users
                WHERE org_id = %s AND role = 'teacher'
                ORDER BY fullname
            """, (org_id,))
            teacher_rows = cur.fetchall()

        # Precompute context ONCE for this dashboard load.
        # These two helpers do all the DB work upfront in ~5 queries total,
        # so the per-teacher loop below is pure Python (no more DB calls per teacher).
        conflict_ctx = get_student_conflict_context(student["id"])
        avail_ctx = get_teacher_availability_context(target_date)

        for t_id, t_name, t_type in teacher_rows:
            t_label = "Coaching" if (t_type or "vocal") == "instrumental" else "Voice"

            if t_id in avail_ctx["has_any_exception_by_teacher"]:
                is_working = bool(avail_ctx["exceptions_by_teacher"].get(t_id))
            else:
                is_working = bool(avail_ctx["weekly_by_teacher"].get(t_id))

            if not is_working:
                teachers.append({
                    "id": t_id, "name": t_name, "label": t_label,
                    "morning": 0, "afternoon": 0, "status": "not_working",
                })
                continue

            slots = get_available_slots(
                t_id, target_date,
                student_id=student["id"],
                conflict_ctx=conflict_ctx,
                avail_ctx=avail_ctx,
                tz=org_tz,
                duration_min=cfg["duration_min"],
                has_lunch_break=cfg["has_lunch_break"],
            )
            morning = sum(1 for s in slots if classify_slot_time(s) == "morning")
            afternoon = sum(1 for s in slots if classify_slot_time(s) == "afternoon")

            if morning + afternoon == 0:
                teachers.append({
                    "id": t_id, "name": t_name, "label": t_label,
                    "morning": 0, "afternoon": 0, "status": "all_booked",
                })
            else:
                teachers.append({
                    "id": t_id, "name": t_name, "label": t_label,
                    "morning": morning, "afternoon": afternoon, "status": "available",
                })

    return {
        "date": target_date.isoformat(),
        "booking_open": booking_open,
        "booking_pending": booking_pending,
        "rehearsals": rehearsals,
        "teachers": teachers,
        "duration_options": cfg["duration_options"],
        "cancellation_notice_min": cfg["cancellation_notice_min"],
    }
@app.get("/student/week-slots")
def student_week_slots(request: Request, duration: int = 0):
    """Returns 7 days of teacher availability for studio orgs, with 24-hour booking constraint."""
    student = require_user(request, role="student")
    if student.get("org_type") != "studio":
        return []

    org_tz = get_org_tz(student)
    cfg = get_org_lesson_config(student["org_id"])
    slot_duration = duration if duration in cfg["duration_options"] else cfg["duration_min"]

    now_local = datetime.now(org_tz)
    min_dt = now_local + timedelta(hours=24)

    with db_cursor() as cur:
        cur.execute("""
            SELECT id, fullname, teacher_type
            FROM users
            WHERE org_id = %s AND role = 'teacher'
            ORDER BY fullname
        """, (student["org_id"],))
        teacher_rows = cur.fetchall()

    if not teacher_rows:
        return []

    conflict_ctx = get_student_conflict_context(student["id"])
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    result = []
    for day_offset in range(1, 8):  # tomorrow through 7 days out — today is always blocked by 24h rule
        target_date = now_local.date() + timedelta(days=day_offset)
        avail_ctx = get_teacher_availability_context(target_date)

        day_teachers = []
        for t_id, t_name, t_type in teacher_rows:
            t_label = "Coaching" if (t_type or "vocal") == "instrumental" else "Voice"

            if t_id in avail_ctx["has_any_exception_by_teacher"]:
                is_working = bool(avail_ctx["exceptions_by_teacher"].get(t_id))
            else:
                is_working = bool(avail_ctx["weekly_by_teacher"].get(t_id))

            if not is_working:
                continue

            slots = get_available_slots(
                t_id, target_date,
                student_id=student["id"],
                conflict_ctx=conflict_ctx,
                avail_ctx=avail_ctx,
                tz=org_tz,
                duration_min=slot_duration,
                has_lunch_break=cfg["has_lunch_break"],
                min_dt=min_dt,
            )
            morning = sum(1 for s in slots if classify_slot_time(s) == "morning")
            afternoon = sum(1 for s in slots if classify_slot_time(s) == "afternoon")

            if morning + afternoon > 0:
                day_teachers.append({
                    "id": t_id, "name": t_name, "label": t_label,
                    "morning": morning, "afternoon": afternoon,
                })

        if day_teachers:
            result.append({
                "date": target_date.isoformat(),
                "day_name": day_names[target_date.weekday()],
                "teachers": day_teachers,
            })

    return result


@app.get("/student/teacher-slots")
def student_teacher_slots(request: Request, teacher: int, period: str, duration: int = 0, date: Optional[str] = None):
    """Returns bookable slots for a teacher, filtered by morning/afternoon and duration.

    For studio orgs, accepts an optional `date` param (YYYY-MM-DD) for week-view booking.
    Otherwise uses the standard single-day booking window.
    """
    if period not in ("morning", "afternoon"):
        return []

    student = require_user(request, role="student")
    org_tz = get_org_tz(student)
    cfg = get_org_lesson_config(student["org_id"])

    slot_duration = duration if duration in cfg["duration_options"] else cfg["duration_min"]

    min_dt = None
    if date and student.get("org_type") == "studio":
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return []
        min_dt = datetime.now(org_tz) + timedelta(hours=24)
    else:
        target_date = get_bookable_date(org_tz, close_hour=cfg["booking_close_hour"], open_hour=cfg["booking_open_hour"])
        if not is_booking_window_open_for(target_date, org_tz, open_hour=cfg["booking_open_hour"], close_hour=cfg["booking_close_hour"]):
            return []

    conflict_ctx = get_student_conflict_context(student["id"])
    all_slots = get_available_slots(
        teacher, target_date,
        student_id=student["id"],
        conflict_ctx=conflict_ctx,
        tz=org_tz,
        duration_min=slot_duration,
        has_lunch_break=cfg["has_lunch_break"],
        min_dt=min_dt,
    )
    return [s for s in all_slots if classify_slot_time(s) == period]

@app.get("/student/rehearsals")
def student_rehearsals(request: Request):
    """Rehearsals the given student is expected to attend.

    Includes rehearsals for operas they're cast in, where either:
      - the rehearsal is for their specific cast, OR
      - the rehearsal has no cast set (applies to all casts of that opera)
    """
    student = require_user(request, role="student")
    

    with db_cursor() as cur:
        cur.execute("""
            SELECT DISTINCT
                r.id,
                r.start_time,
                r.end_time,
                r.notes,
                o.opera_name,
                c.name AS cast_name
            FROM rehearsals r
            JOIN operas o ON o.id = r.opera_id
            LEFT JOIN casts c ON c.id = r.cast_id
            JOIN student_assignments sa
                ON sa.student_id = %s
                AND sa.opera_id = r.opera_id
                AND (r.cast_id IS NULL OR r.cast_id = sa.cast_id)
            ORDER BY r.start_time
        """, (student["id"],))
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "start": r[1].isoformat(),
            "end": r[2].isoformat(),
            "notes": r[3],
            "opera": r[4],
            "cast": r[5] if r[5] else "All casts",
        }
        for r in rows
    ]
@app.get("/student/absences")
def student_absences(request: Request):
    student = require_user(request, role="student")
    with db_cursor() as cur:
        cur.execute(
            "SELECT rehearsal_id FROM absence_requests WHERE singer_id=%s",
            (student["id"],),
        )
        return [r[0] for r in cur.fetchall()]


@app.post("/student/absence")
def student_mark_absence(payload: dict, request: Request):
    student = require_user(request, role="student")
    rehearsal_id = payload.get("rehearsal_id")
    reason = (payload.get("reason") or "").strip()
    note = (payload.get("note") or "").strip()
    if not rehearsal_id:
        return {"status": "fail", "message": "rehearsal_id required"}
    if not reason:
        return {"status": "fail", "message": "reason required"}

    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO absence_requests (rehearsal_id, singer_id, reason, note)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (rehearsal_id, singer_id) DO UPDATE SET reason=EXCLUDED.reason, note=EXCLUDED.note
            """,
            (rehearsal_id, student["id"], reason, note or None),
        )
        cur.execute(
            """
            SELECT r.start_time, o.opera_name FROM rehearsals r
            JOIN operas o ON o.id = r.opera_id WHERE r.id=%s
            """,
            (rehearsal_id,),
        )
        row = cur.fetchone()

        cur.execute(
            """
            SELECT fullname, email FROM users
            WHERE org_id=%s AND role IN ('admin','head_admin') AND email IS NOT NULL
            """,
            (student["org_id"],),
        )
        admins = cur.fetchall()

    if row and admins:
        start_dt, opera_name = row
        org_tz = get_org_tz(student)
        local_dt = start_dt.astimezone(org_tz) if start_dt.tzinfo else start_dt
        date_str = local_dt.strftime("%A, %B %-d, %Y")
        note_html = f"<p><strong>Notes:</strong> {html_mod.escape(note)}</p>" if note else ""
        note_text = f"\nNotes: {note}" if note else ""
        subject = f"Absence Notice – {student['fullname']} – {opera_name}"
        html_body = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:600px;margin:auto;padding:24px;">
<h2 style="color:#333;">Absence Notice</h2>
<p><strong>{html_mod.escape(student['fullname'])}</strong> has marked themselves absent for the
<strong>{html_mod.escape(opera_name)}</strong> rehearsal on <strong>{date_str}</strong>.</p>
<p><strong>Reason:</strong> {html_mod.escape(reason)}</p>
{note_html}
</body></html>"""
        text_body = f"Absence Notice\n{student['fullname']} has marked themselves absent for the {opera_name} rehearsal on {date_str}.\nReason: {reason}{note_text}"
        for _, email in admins:
            send_email(to=email, subject=subject, html_body=html_body, text_body=text_body)

    return {"status": "success"}


@app.delete("/student/absence/{rehearsal_id}")
def student_cancel_absence(rehearsal_id: int, request: Request):
    student = require_user(request, role="student")
    with db_cursor(commit=True) as cur:
        cur.execute(
            "DELETE FROM absence_requests WHERE rehearsal_id=%s AND singer_id=%s",
            (rehearsal_id, student["id"]),
        )
    return {"status": "success"}


@app.post("/student/book")
def student_book(payload: dict, request: Request):
    student = require_user(request, role="student")
    student_id = student["id"]

    date_str = payload.get("date")
    teacher_id = payload.get("teacher_id")
    time_str = payload.get("time")
    duration_req = payload.get("duration", 0)

    if not (date_str and teacher_id and time_str):
        return {"status": "fail", "message": "Missing required fields"}

    try:
        teacher_id = int(teacher_id)
    except (TypeError, ValueError):
        return {"status": "fail", "message": "Invalid teacher"}

    # Parse date and time safely
    try:
        lesson_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        lesson_time = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        return {"status": "fail", "message": "Invalid date or time format"}

    org_tz = get_org_tz(student)
    cfg = get_org_lesson_config(student["org_id"])

    slot_duration = int(duration_req) if int(duration_req or 0) in cfg["duration_options"] else cfg["duration_min"]

    slot_dt = org_tz.localize(datetime.combine(lesson_date, lesson_time))

    if student.get("org_type") == "studio":
        # Studio: 24-hour advance constraint instead of a fixed booking window
        if slot_dt < datetime.now(org_tz) + timedelta(hours=24):
            return {"status": "fail", "message": "Lessons must be booked at least 24 hours in advance"}
    else:
        # Can only book for the currently bookable date
        if lesson_date != get_bookable_date(org_tz, close_hour=cfg["booking_close_hour"], open_hour=cfg["booking_open_hour"]):
            return {"status": "fail", "message": "Lessons can only be booked for the current bookable day"}

        # Booking window check
        if not is_booking_window_open_for(lesson_date, org_tz, open_hour=cfg["booking_open_hour"], close_hour=cfg["booking_close_hour"]):
            return {
                "status": "fail",
                "message": f"Booking is closed. Booking opens at {cfg['booking_open_hour']}:00 the evening before and closes at {cfg['booking_close_hour']}:00 on the day of your lesson."
            }

        # Block past times today
        if slot_dt <= datetime.now(org_tz):
            return {"status": "fail", "message": "Cannot book past times"}

    # Block slots during the lunch break if enabled
    if cfg["has_lunch_break"]:
        slot_end_t = (slot_dt + timedelta(minutes=slot_duration)).time()
        if lesson_time < LUNCH_END and slot_end_t > LUNCH_START:
            return {"status": "fail", "message": "This slot overlaps the lunch break"}

    # Rehearsal conflict check — stops students from booking over their rehearsals
    if get_student_rehearsal_conflicts(student_id, lesson_date, lesson_time, tz=org_tz):
        return {
            "status": "fail",
            "message": "This time conflicts with one of your rehearsals."
        }

    with db_cursor(commit=True) as cur:
        # Daily limit
        cur.execute("""
            SELECT COUNT(*) FROM lessons
            WHERE student_id=%s AND lesson_date=%s AND status='booked'
        """, (student_id, lesson_date))
        if cur.fetchone()[0] >= cfg["max_per_day"]:
            return {"status": "fail", "message": "You have reached the maximum lessons for that day"}

        # Teacher limit
        cur.execute("""
            SELECT COUNT(*) FROM lessons
            WHERE student_id=%s AND teacher_id=%s AND status='booked'
        """, (student_id, teacher_id))
        if cur.fetchone()[0] >= cfg["max_per_teacher"]:
            return {"status": "fail", "message": "Maximum lessons with this teacher reached"}

        # Try to insert — UNIQUE(teacher_id, lesson_date, lesson_time) prevents
        # double-booking atomically, no race condition.
        try:
            cur.execute("""
                INSERT INTO lessons (teacher_id, student_id, lesson_date, lesson_time, duration_min)
                VALUES (%s, %s, %s, %s, %s)
            """, (teacher_id, student_id, lesson_date, lesson_time, slot_duration))
        except pg_errors.UniqueViolation:
            return {"status": "fail", "message": "That slot was just taken. Please refresh and try another."}
        except Exception as e:
            print("BOOK ERROR:", e)
            return {"status": "fail", "message": "Booking failed. Please try again."}

    return {"status": "success", "message": "Lesson booked!"}


@app.post("/student/cancel-lesson")
def cancel_lesson(payload: dict, request: Request):
    """
    Cancel a lesson.
      - Students: soft-cancel only, must be at least 1 hour before lesson time.
      - Teachers: can cancel any of their lessons at any time.
      - Admins: can cancel anything.
    """
    lesson_id = payload.get("lesson_id")

    if not lesson_id:
        return {"status": "fail", "message": "Missing required fields"}

    user = require_user(request)
    if not user:
        return {"status": "fail", "message": "User not found"}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT student_id, teacher_id, lesson_date, lesson_time, status
            FROM lessons
            WHERE id = %s
        """, (lesson_id,))
        row = cur.fetchone()
        if not row:
            return {"status": "fail", "message": "Lesson not found"}

        student_id, teacher_id, lesson_date, lesson_time, current_status = row

        if current_status == "cancelled":
            return {"status": "fail", "message": "Lesson is already cancelled"}

        # Permissions
        if user["role"] == "student":
            if user["id"] != student_id:
                return {"status": "fail", "message": "Not your lesson"}

            user_tz = get_org_tz(user)
            cfg = get_org_lesson_config(user["org_id"])
            notice_min = cfg["cancellation_notice_min"]
            lesson_dt = user_tz.localize(datetime.combine(lesson_date, lesson_time))
            cutoff = lesson_dt - timedelta(minutes=notice_min)
            if datetime.now(user_tz) >= cutoff:
                hours = notice_min // 60
                mins = notice_min % 60
                notice_str = f"{hours}h {mins}m" if mins else f"{hours}h"
                return {
                    "status": "fail",
                    "message": f"Can't cancel within {notice_str} of the lesson."
                }
        elif user["role"] == "teacher":
            if user["id"] != teacher_id:
                return {"status": "fail", "message": "Not your lesson"}
        # Admins can cancel anything — no further check

        cur.execute("""
            UPDATE lessons
            SET status = 'cancelled',
                cancelled_at = NOW()
            WHERE id = %s
        """, (lesson_id,))

    return {"status": "success"}


@app.get("/student/teacher-availability")
def student_teacher_availability(teacher: int):
    """Distinct weekdays a teacher is available on — used for preview on student dashboard."""
    with db_cursor() as cur:
        cur.execute("""
            SELECT DISTINCT weekday
            FROM weekly_availability
            WHERE teacher_id = %s AND active = TRUE
            ORDER BY weekday
        """, (teacher,))
        rows = cur.fetchall()
    return [{"weekday": int(r[0])} for r in rows]


# ========================================================
# USER (shared)
# ========================================================

@app.get("/user/fullname")
def get_fullname(request: Request):
    user = require_user(request)
    return {"fullname": user["fullname"] if user else None}


@app.post("/user/update-account")
def update_account(payload: dict, request: Request):
    user = require_user(request)
    user_id = user["id"]

    new_username = payload.get("new_username")
    new_password = payload.get("new_password")
    current_password = payload.get("current_password")
    logout_other_devices = payload.get("logout_other_devices", True)

    if not current_password:
        return {"status": "fail", "message": "Current password required"}

    with db_cursor(commit=True) as cur:
        # Verify current password
        cur.execute(
            "SELECT password_hash, pw_version FROM users WHERE id = %s",
            (user_id,)
        )
        row = cur.fetchone()
        if not row or not verify_password(current_password, row[0], row[1]):
            return {"status": "fail", "message": "Current password incorrect"}

        # Username change — usernames are globally unique
        if new_username:
            new_username = new_username.strip().lower()
            cur.execute("""
                SELECT 1 FROM users
                WHERE username = %s AND id != %s
            """, (new_username, user_id))
            if cur.fetchone():
                return {"status": "fail", "message": "Username already taken"}

            cur.execute(
                "UPDATE users SET username=%s WHERE id=%s",
                (new_username, user_id)
            )

        # Password change
        if new_password:
            cur.execute(
                "UPDATE users SET password_hash=%s, pw_version='bcrypt' WHERE id=%s",
                (hash_password_bcrypt(new_password), user_id)
            )

            # Optionally kill other sessions
            if logout_other_devices:
                current_token = request.cookies.get("session")
                delete_all_sessions_for_user(user_id, except_token=current_token)

    return {"status": "success"}


# ========================================================
# ORCHESTRA MEMBER ENDPOINTS
# ========================================================

@app.get("/orchestra-member/today")
def orchestra_member_today(request: Request):
    """
    Everything the orchestra member dashboard needs for today's view:
      - Today's date / booking window
      - Today's orchestra rehearsals
      - Instrumental teachers who match the member's instrument
    """
    member = require_user(request, role="orchestra_member")
    org_tz = get_org_tz(member)
    cfg = get_org_lesson_config(member["org_id"])
    now_local = datetime.now(org_tz)
    target_date = get_bookable_date(org_tz, close_hour=cfg["booking_close_hour"], open_hour=cfg["booking_open_hour"])
    booking_open = is_booking_window_open_for(target_date, org_tz, open_hour=cfg["booking_open_hour"], close_hour=cfg["booking_close_hour"])
    booking_pending = not booking_open  # target date is set but window hasn't opened yet tonight

    org_id = member["org_id"]
    member_instrument = (member.get("instrument") or "").strip().lower()

    # Orchestra rehearsals today (all orchestra rehearsals — all members attend)
    with db_cursor() as cur:
        cur.execute("""
            SELECT r.id, r.start_time, r.end_time, r.notes, o.opera_name, r.location
            FROM rehearsals r
            JOIN operas o ON o.id = r.opera_id
            WHERE r.rehearsal_type = 'orchestra'
              AND o.org_id = %s
              AND (r.start_time AT TIME ZONE %s)::date = %s
            ORDER BY r.start_time
        """, (org_id, org_tz.zone, target_date))
        rows = cur.fetchall()

    rehearsals = [
        {
            "id": r[0],
            "start": r[1].isoformat(),
            "end": r[2].isoformat(),
            "notes": r[3],
            "opera": r[4],
            "location": r[5] or "",
        }
        for r in rows
    ]

    # Seat assignments today's member holds (per opera)
    with db_cursor() as cur:
        cur.execute("""
            SELECT o.opera_name, os2.name AS section_name, ose.chair_number
            FROM orchestra_seats ose
            JOIN orchestra_sections os2 ON os2.id = ose.section_id
            JOIN operas o ON o.id = ose.opera_id
            WHERE ose.member_id = %s
            ORDER BY o.opera_name, os2.sort_order, ose.chair_number
        """, (member["id"],))
        seat_rows = cur.fetchall()

    seats = [
        {"opera": r[0], "section": r[1], "chair": r[2]}
        for r in seat_rows
    ]

    # Instrumental teachers matching the member's instrument
    teachers = []
    if booking_open and member_instrument:
        with db_cursor() as cur:
            cur.execute("""
                SELECT id, fullname, teacher_instruments
                FROM users
                WHERE org_id = %s AND role = 'teacher' AND teacher_type = 'instrumental'
                ORDER BY fullname
            """, (org_id,))
            teacher_rows = cur.fetchall()

        # Filter to teachers who teach this member's instrument
        matched = [
            (r[0], r[1])
            for r in teacher_rows
            if member_instrument in [i.strip().lower() for i in (r[2] or "").split(",") if i.strip()]
        ]

        avail_ctx = get_teacher_availability_context(target_date)

        for t_id, t_name in matched:
            if t_id in avail_ctx["has_any_exception_by_teacher"]:
                is_working = bool(avail_ctx["exceptions_by_teacher"].get(t_id))
            else:
                is_working = bool(avail_ctx["weekly_by_teacher"].get(t_id))

            if not is_working:
                teachers.append({"id": t_id, "name": t_name, "morning": 0, "afternoon": 0, "status": "not_working"})
                continue

            slots = get_available_slots(t_id, target_date, avail_ctx=avail_ctx, tz=org_tz,
                                        duration_min=cfg["duration_min"], has_lunch_break=cfg["has_lunch_break"])
            morning = sum(1 for s in slots if classify_slot_time(s) == "morning")
            afternoon = sum(1 for s in slots if classify_slot_time(s) == "afternoon")

            if morning + afternoon == 0:
                teachers.append({"id": t_id, "name": t_name, "morning": 0, "afternoon": 0, "status": "all_booked"})
            else:
                teachers.append({"id": t_id, "name": t_name, "morning": morning, "afternoon": afternoon, "status": "available"})

    return {
        "date": target_date.isoformat(),
        "booking_open": booking_open,
        "booking_pending": booking_pending,
        "rehearsals": rehearsals,
        "teachers": teachers,
        "seats": seats,
        "duration_options": cfg["duration_options"],
        "cancellation_notice_min": cfg["cancellation_notice_min"],
    }


@app.get("/orchestra-member/rehearsals")
def orchestra_member_rehearsals(request: Request):
    member = require_user(request, role="orchestra_member")
    org_id = member["org_id"]
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT r.id, r.start_time, r.end_time, r.notes, o.opera_name, r.location
            FROM rehearsals r
            JOIN operas o ON o.id = r.opera_id
            WHERE r.rehearsal_type = 'orchestra'
              AND o.org_id = %s
            ORDER BY r.start_time
            """,
            (org_id,),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "start": r[1].isoformat(),
            "end": r[2].isoformat(),
            "notes": r[3],
            "opera": r[4],
            "location": r[5] or "",
        }
        for r in rows
    ]


@app.get("/orchestra-member/absences")
def orchestra_member_absences(request: Request):
    member = require_user(request, role="orchestra_member")
    with db_cursor() as cur:
        cur.execute(
            "SELECT rehearsal_id FROM absence_requests WHERE singer_id=%s",
            (member["id"],),
        )
        return [r[0] for r in cur.fetchall()]


@app.post("/orchestra-member/absence")
def orchestra_member_mark_absence(payload: dict, request: Request):
    member = require_user(request, role="orchestra_member")
    rehearsal_id = payload.get("rehearsal_id")
    reason = (payload.get("reason") or "").strip()
    note = (payload.get("note") or "").strip()
    if not rehearsal_id:
        return {"status": "fail", "message": "rehearsal_id required"}
    if not reason:
        return {"status": "fail", "message": "reason required"}

    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO absence_requests (rehearsal_id, singer_id, reason, note)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (rehearsal_id, singer_id) DO UPDATE SET reason=EXCLUDED.reason, note=EXCLUDED.note
            """,
            (rehearsal_id, member["id"], reason, note or None),
        )
        cur.execute(
            """
            SELECT r.start_time, o.opera_name FROM rehearsals r
            LEFT JOIN operas o ON o.id = r.opera_id WHERE r.id=%s
            """,
            (rehearsal_id,),
        )
        row = cur.fetchone()

        cur.execute(
            """
            SELECT email FROM users
            WHERE org_id=%s AND role IN ('admin','head_admin','orchestra_admin') AND email IS NOT NULL
            """,
            (member["org_id"],),
        )
        admin_emails = [r[0] for r in cur.fetchall()]

    if row and admin_emails:
        start_dt, opera_name = row
        rehearsal_label = f"{opera_name} orchestra" if opera_name else "orchestra"
        org_tz = get_org_tz(member)
        local_dt = start_dt.astimezone(org_tz) if start_dt.tzinfo else start_dt
        date_str = local_dt.strftime("%A, %B %-d, %Y")
        note_html = f"<p><strong>Notes:</strong> {html_mod.escape(note)}</p>" if note else ""
        note_text = f"\nNotes: {note}" if note else ""
        subject = f"Absence Notice - {member['fullname']} - {opera_name or 'Orchestra Rehearsal'}"
        html_body = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:600px;margin:auto;padding:24px;">
<h2 style="color:#333;">Absence Notice</h2>
<p><strong>{html_mod.escape(member['fullname'])}</strong> has marked themselves absent for the
<strong>{html_mod.escape(rehearsal_label)}</strong> rehearsal on <strong>{date_str}</strong>.</p>
<p><strong>Reason:</strong> {html_mod.escape(reason)}</p>{note_html}
</body></html>"""
        text_body = f"Absence Notice\n{member['fullname']} has marked themselves absent for the {rehearsal_label} rehearsal on {date_str}.\nReason: {reason}{note_text}"
        for email in admin_emails:
            send_email(to=email, subject=subject, html_body=html_body, text_body=text_body)

    return {"status": "success"}


@app.delete("/orchestra-member/absence/{rehearsal_id}")
def orchestra_member_cancel_absence(rehearsal_id: int, request: Request):
    member = require_user(request, role="orchestra_member")
    with db_cursor(commit=True) as cur:
        cur.execute(
            "DELETE FROM absence_requests WHERE rehearsal_id=%s AND singer_id=%s",
            (rehearsal_id, member["id"]),
        )
    return {"status": "success"}


@app.get("/orchestra-member/lessons")
def orchestra_member_lessons(request: Request):
    """Return all booked/upcoming lessons for this member."""
    member = require_user(request, role="orchestra_member")
    cfg = get_org_lesson_config(member["org_id"])
    default_duration = cfg["duration_min"]
    with db_cursor() as cur:
        cur.execute("""
            SELECT l.id, l.lesson_date, l.lesson_time, u.fullname, l.status, l.duration_min
            FROM lessons l
            JOIN users u ON u.id = l.teacher_id
            WHERE l.student_id = %s AND l.status = 'booked'
            ORDER BY l.lesson_date, l.lesson_time
        """, (member["id"],))
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "date": r[1].isoformat() if r[1] else None,
            "time": r[2].strftime("%H:%M") if r[2] else None,
            "teacher": r[3],
            "status": r[4],
            "duration_min": r[5] if r[5] is not None else default_duration,
        }
        for r in rows
    ]


@app.get("/orchestra-member/teacher-slots")
def orchestra_member_teacher_slots(request: Request, teacher: int, period: str, duration: int = 0):
    """Available slots for a given instrumental teacher today."""
    if period not in ("morning", "afternoon"):
        return []

    member = require_user(request, role="orchestra_member")
    org_tz = get_org_tz(member)
    cfg = get_org_lesson_config(member["org_id"])

    slot_duration = duration if duration in cfg["duration_options"] else cfg["duration_min"]

    target_date = get_bookable_date(org_tz, close_hour=cfg["booking_close_hour"], open_hour=cfg["booking_open_hour"])
    if not is_booking_window_open_for(target_date, org_tz, open_hour=cfg["booking_open_hour"], close_hour=cfg["booking_close_hour"]):
        return []

    all_slots = get_available_slots(teacher, target_date, tz=org_tz,
                                    duration_min=slot_duration, has_lunch_break=cfg["has_lunch_break"])
    return [s for s in all_slots if classify_slot_time(s) == period]


@app.post("/orchestra-member/book")
def orchestra_member_book(payload: dict, request: Request):
    """Book a coaching with an instrumental teacher."""
    member = require_user(request, role="orchestra_member")
    member_id = member["id"]

    date_str = payload.get("date")
    teacher_id = payload.get("teacher_id")
    time_str = payload.get("time")
    duration_req = payload.get("duration", 0)

    if not (date_str and teacher_id and time_str):
        return {"status": "fail", "message": "Missing required fields"}

    try:
        teacher_id = int(teacher_id)
    except (TypeError, ValueError):
        return {"status": "fail", "message": "Invalid teacher"}

    try:
        lesson_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        lesson_time = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        return {"status": "fail", "message": "Invalid date or time format"}

    org_tz = get_org_tz(member)
    cfg = get_org_lesson_config(member["org_id"])

    slot_duration = int(duration_req) if int(duration_req or 0) in cfg["duration_options"] else cfg["duration_min"]

    if lesson_date != get_bookable_date(org_tz, close_hour=cfg["booking_close_hour"], open_hour=cfg["booking_open_hour"]):
        return {"status": "fail", "message": "Lessons can only be booked for the current bookable day"}

    if not is_booking_window_open_for(lesson_date, org_tz, open_hour=cfg["booking_open_hour"], close_hour=cfg["booking_close_hour"]):
        return {"status": "fail", "message": "Booking is closed."}

    slot_dt = org_tz.localize(datetime.combine(lesson_date, lesson_time))
    if slot_dt <= datetime.now(org_tz):
        return {"status": "fail", "message": "Cannot book past times"}

    if cfg["has_lunch_break"]:
        slot_end_t = (slot_dt + timedelta(minutes=slot_duration)).time()
        if lesson_time < LUNCH_END and slot_end_t > LUNCH_START:
            return {"status": "fail", "message": "This slot overlaps the lunch break"}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT COUNT(*) FROM lessons
            WHERE student_id=%s AND lesson_date=%s AND status='booked'
        """, (member_id, lesson_date))
        if cur.fetchone()[0] >= cfg["max_per_day"]:
            return {"status": "fail", "message": "You have reached the maximum lessons for that day"}

        cur.execute("""
            SELECT COUNT(*) FROM lessons
            WHERE student_id=%s AND teacher_id=%s AND status='booked'
        """, (member_id, teacher_id))
        if cur.fetchone()[0] >= cfg["max_per_teacher"]:
            return {"status": "fail", "message": "Maximum lessons with this teacher reached"}

        try:
            cur.execute("""
                INSERT INTO lessons (teacher_id, student_id, lesson_date, lesson_time, duration_min)
                VALUES (%s, %s, %s, %s, %s)
            """, (teacher_id, member_id, lesson_date, lesson_time, slot_duration))
        except pg_errors.UniqueViolation:
            return {"status": "fail", "message": "That slot was just taken. Please refresh and try another."}
        except Exception as e:
            print("ORCHESTRA BOOK ERROR:", e)
            return {"status": "fail", "message": "Booking failed. Please try again."}

    return {"status": "success", "message": "Lesson booked!"}


@app.post("/orchestra-member/cancel-lesson")
def orchestra_member_cancel(payload: dict, request: Request):
    """Cancel a coaching (same rules as student cancel)."""
    member = require_user(request, role="orchestra_member")
    lesson_id = payload.get("lesson_id")
    if not lesson_id:
        return {"status": "fail", "message": "Missing lesson_id"}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT student_id, lesson_date, lesson_time, status
            FROM lessons WHERE id = %s
        """, (lesson_id,))
        row = cur.fetchone()
        if not row:
            return {"status": "fail", "message": "Lesson not found"}

        if row[0] != member["id"]:
            return {"status": "fail", "message": "Not your lesson"}

        if row[3] == "cancelled":
            return {"status": "fail", "message": "Already cancelled"}

        lesson_date, lesson_time = row[1], row[2]
        member_tz = get_org_tz(member)
        cfg = get_org_lesson_config(member["org_id"])
        notice_min = cfg["cancellation_notice_min"]
        lesson_dt = member_tz.localize(datetime.combine(lesson_date, lesson_time))
        cutoff = lesson_dt - timedelta(minutes=notice_min)
        if datetime.now(member_tz) >= cutoff:
            hours = notice_min // 60
            mins = notice_min % 60
            notice_str = f"{hours}h {mins}m" if mins else f"{hours}h"
            return {"status": "fail", "message": f"Too close to lesson time to cancel (need {notice_str} notice)"}

        cur.execute("""
            UPDATE lessons SET status='cancelled', cancelled_at=NOW()
            WHERE id = %s
        """, (lesson_id,))

    return {"status": "success"}


@app.get("/orchestra-member/shared-notes")
def orchestra_member_notes(request: Request):
    """Notes shared by teachers for this member's lessons."""
    member = require_user(request, role="orchestra_member")
    with db_cursor() as cur:
        cur.execute("""
            SELECT ln.piece, ln.technique, ln.other,
                   l.lesson_date, l.lesson_time, u.fullname
            FROM lesson_notes ln
            JOIN lessons l ON l.id = ln.lesson_id
            JOIN users u ON u.id = l.teacher_id
            WHERE l.student_id = %s
              AND ln.shared = TRUE
            ORDER BY l.lesson_date DESC, l.lesson_time DESC
        """, (member["id"],))
        rows = cur.fetchall()
    return [
        {
            "piece": r[0], "technique": r[1], "other": r[2],
            "date": r[3].isoformat() if r[3] else None,
            "time": r[4].strftime("%H:%M") if r[4] else None,
            "teacher": r[5],
        }
        for r in rows
    ]


@app.get("/orchestra-member/my-seats")
def orchestra_member_seats(request: Request):
    """Seat assignments for this member across all operas."""
    member = require_user(request, role="orchestra_member")
    with db_cursor() as cur:
        cur.execute("""
            SELECT o.opera_name, os2.name, os2.instrument, ose.chair_number
            FROM orchestra_seats ose
            JOIN orchestra_sections os2 ON os2.id = ose.section_id
            JOIN operas o ON o.id = ose.opera_id
            WHERE ose.member_id = %s
            ORDER BY o.opera_name, os2.sort_order, ose.chair_number
        """, (member["id"],))
        rows = cur.fetchall()
    return [
        {"opera": r[0], "section": r[1], "instrument": r[2], "chair": r[3]}
        for r in rows
    ]


# ========================================================
# ORCHESTRA ADMIN ENDPOINTS
# ========================================================

@app.get("/admin/orchestra-sections")
def get_orchestra_sections(request: Request):
    """List orchestra sections for this org."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, name, instrument, sort_order, chair_count
            FROM orchestra_sections
            WHERE org_id = %s
            ORDER BY sort_order, name
        """, (org_id,))
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "instrument": r[2], "sort_order": r[3], "chair_count": r[4]} for r in rows]


@app.post("/admin/orchestra-sections")
def create_orchestra_section(payload: dict, request: Request):
    """Add a new orchestra section for this org."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    name = (payload.get("name") or "").strip()
    instrument = (payload.get("instrument") or "").strip().lower()
    sort_order = int(payload.get("sort_order") or 0)
    chair_count = max(1, int(payload.get("chair_count") or 5))

    if not name:
        return {"status": "fail", "message": "Section name is required."}
    if not instrument:
        return {"status": "fail", "message": "Instrument is required."}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (org_id, name, instrument, sort_order, chair_count))
        section_id = cur.fetchone()[0]

    return {"status": "success", "id": section_id}


@app.post("/admin/orchestra-sections/init-defaults")
def init_default_sections(payload: dict, request: Request):
    """Auto-create standard sections if none exist yet for this org."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    sections = payload.get("sections") or []

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT COUNT(*) FROM orchestra_sections WHERE org_id = %s", (org_id,))
        if cur.fetchone()[0] > 0:
            return {"status": "skipped"}
        for i, s in enumerate(sections):
            name = (s.get("name") or "").strip()
            instrument = (s.get("instrument") or "").strip().lower()
            if not name or not instrument:
                continue
            chair_count = max(1, int(s.get("chair_count") or 5))
            cur.execute("""
                INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
                VALUES (%s, %s, %s, %s, %s)
            """, (org_id, name, instrument, i, chair_count))

    return {"status": "success"}


@app.patch("/admin/orchestra-sections/{section_id}/chair-count")
def adjust_chair_count(section_id: int, payload: dict, request: Request):
    """Increment or decrement the chair count for a section (min 1)."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    delta = int(payload.get("delta") or 0)
    if delta not in (1, -1):
        return {"status": "fail", "message": "Delta must be 1 or -1."}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE orchestra_sections
            SET chair_count = GREATEST(1, chair_count + %s)
            WHERE id = %s AND org_id = %s
            RETURNING chair_count
        """, (delta, section_id, org_id))
        row = cur.fetchone()
        if not row:
            return {"status": "fail", "message": "Section not found."}

    return {"status": "success", "chair_count": row[0]}


@app.delete("/admin/orchestra-sections/{section_id}")
def delete_orchestra_section(section_id: int, request: Request):
    """Delete a section (and its seat assignments)."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    with db_cursor(commit=True) as cur:
        cur.execute("""
            DELETE FROM orchestra_sections
            WHERE id = %s AND org_id = %s
        """, (section_id, org_id))
    return {"status": "success"}


@app.get("/admin/orchestra-members")
def get_orchestra_members(request: Request):
    """List orchestra members in this org."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, fullname, instrument, doublings
            FROM users
            WHERE org_id = %s AND role = 'orchestra_member'
            ORDER BY instrument, fullname
        """, (org_id,))
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "instrument": r[2] or "", "doublings": r[3] or ""} for r in rows]


@app.patch("/admin/orchestra-members/{member_id}/doublings")
def update_orchestra_member_doublings(member_id: int, payload: dict, request: Request):
    """Update an opera-side orchestra member's recorded doublings."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    doublings = (payload.get("doublings") or "").strip()
    with db_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE users SET doublings=%s WHERE id=%s AND org_id=%s AND role='orchestra_member'",
            (doublings or None, member_id, org_id)
        )
    return {"status": "success"}


@app.get("/admin/orchestra-seats/{opera_id}")
def get_orchestra_seats(opera_id: int, request: Request):
    """Seat assignments for a specific opera."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    with db_cursor() as cur:
        # Verify opera belongs to this org
        cur.execute("SELECT 1 FROM operas WHERE id=%s AND org_id=%s", (opera_id, org_id))
        if not cur.fetchone():
            return []

        cur.execute("""
            SELECT ose.id, ose.section_id, ose.chair_number, ose.member_id, u.fullname,
                   ose.external_name, ose.external_email
            FROM orchestra_seats ose
            LEFT JOIN users u ON u.id = ose.member_id
            WHERE ose.opera_id = %s
            ORDER BY ose.section_id, ose.chair_number
        """, (opera_id,))
        rows = cur.fetchall()

    return [
        {
            "id": r[0], "section_id": r[1], "chair_number": r[2],
            "member_id": r[3], "member_name": r[4] if r[3] else r[5],
            "external_name": None if r[3] else r[5],
            "external_email": None if r[3] else r[6],
        }
        for r in rows
    ]


@app.post("/admin/orchestra-seats")
def assign_orchestra_seat(payload: dict, request: Request):
    """
    Assign or clear an orchestra seat.
    Payload: { opera_id, section_id, chair_number, member_id (null to clear) }
    """
    user = require_user(request, role="admin")
    org_id = user["org_id"]

    opera_id = payload.get("opera_id")
    section_id = payload.get("section_id")
    chair_number = payload.get("chair_number")
    member_id = payload.get("member_id")  # None = clear
    external_name = (payload.get("external_name") or "").strip() or None
    external_email = (payload.get("external_email") or "").strip() or None

    if not (opera_id and section_id and chair_number):
        return {"status": "fail", "message": "Missing required fields"}

    with db_cursor(commit=True) as cur:
        # Validate opera belongs to org
        cur.execute("SELECT 1 FROM operas WHERE id=%s AND org_id=%s", (opera_id, org_id))
        if not cur.fetchone():
            return {"status": "fail", "message": "Opera not found"}

        # Validate section belongs to org
        cur.execute("SELECT 1 FROM orchestra_sections WHERE id=%s AND org_id=%s", (section_id, org_id))
        if not cur.fetchone():
            return {"status": "fail", "message": "Section not found"}

        if member_id:
            # Validate member
            cur.execute("SELECT 1 FROM users WHERE id=%s AND role='orchestra_member'", (member_id,))
            if not cur.fetchone():
                return {"status": "fail", "message": "Orchestra member not found"}

            cur.execute("""
                INSERT INTO orchestra_seats (opera_id, section_id, chair_number, member_id, external_name, external_email)
                VALUES (%s, %s, %s, %s, NULL, NULL)
                ON CONFLICT (opera_id, section_id, chair_number)
                DO UPDATE SET member_id = EXCLUDED.member_id, external_name = NULL, external_email = NULL
            """, (opera_id, section_id, chair_number, member_id))
        elif external_name:
            cur.execute("""
                INSERT INTO orchestra_seats (opera_id, section_id, chair_number, member_id, external_name, external_email)
                VALUES (%s, %s, %s, NULL, %s, %s)
                ON CONFLICT (opera_id, section_id, chair_number)
                DO UPDATE SET member_id = NULL, external_name = EXCLUDED.external_name, external_email = EXCLUDED.external_email
            """, (opera_id, section_id, chair_number, external_name, external_email))
        else:
            cur.execute("""
                INSERT INTO orchestra_seats (opera_id, section_id, chair_number, member_id, external_name, external_email)
                VALUES (%s, %s, %s, NULL, NULL, NULL)
                ON CONFLICT (opera_id, section_id, chair_number)
                DO UPDATE SET member_id = NULL, external_name = NULL, external_email = NULL
            """, (opera_id, section_id, chair_number))

    return {"status": "success"}


@app.post("/admin/orchestra-seats/copy")
def copy_orchestra_seats(payload: dict, request: Request):
    """Copy all seat assignments from one opera to another, replacing existing assignments."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    from_opera_id = payload.get("from_opera_id")
    to_opera_id = payload.get("to_opera_id")

    if not from_opera_id or not to_opera_id:
        return {"status": "fail", "message": "Both from_opera_id and to_opera_id are required."}
    if from_opera_id == to_opera_id:
        return {"status": "fail", "message": "Cannot copy a production to itself."}

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT 1 FROM operas WHERE id=%s AND org_id=%s", (from_opera_id, org_id))
        if not cur.fetchone():
            return {"status": "fail", "message": "Source production not found."}
        cur.execute("SELECT 1 FROM operas WHERE id=%s AND org_id=%s", (to_opera_id, org_id))
        if not cur.fetchone():
            return {"status": "fail", "message": "Target production not found."}

        cur.execute("DELETE FROM orchestra_seats WHERE opera_id = %s", (to_opera_id,))
        cur.execute("""
            INSERT INTO orchestra_seats (opera_id, section_id, chair_number, member_id)
            SELECT %s, section_id, chair_number, member_id
            FROM orchestra_seats
            WHERE opera_id = %s
        """, (to_opera_id, from_opera_id))

    return {"status": "success"}


# ── Lesson cancellation email ─────────────────────────────────────────────────

def render_lesson_cancelled_email(
    student_name: str, teacher_name: str,
    lesson_date, lesson_time, opera_name: str, rehearsal_start: datetime
) -> tuple[str, str]:
    date_str = lesson_date.strftime("%A, %B %d, %Y") if hasattr(lesson_date, "strftime") else str(lesson_date)
    h, m = lesson_time.hour, lesson_time.minute
    suffix = "PM" if h >= 12 else "AM"
    h12 = ((h + 11) % 12) + 1
    time_str = f"{h12}:{m:02d} {suffix}"

    html = f"""\
<!DOCTYPE html>
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:560px;margin:40px auto;padding:24px;color:#222;">
    <h2 style="color:#c0392b;">Coaching Lesson Cancelled</h2>
    <p>Hi {student_name},</p>
    <p>Your <strong>{time_str}</strong> coaching with <strong>{teacher_name}</strong> on <strong>{date_str}</strong>
       has been cancelled because a rehearsal for <strong>{opera_name}</strong> has been scheduled during that time.</p>
    <p>Please contact your administrator to find another time.</p>
    <p style="color:#888;font-size:13px;">This cancellation was made automatically when the rehearsal was created.</p>
</body>
</html>"""

    text = f"""Coaching Lesson Cancelled

Hi {student_name},

Your {time_str} coaching with {teacher_name} on {date_str} has been cancelled because a rehearsal for {opera_name} has been scheduled during that time.

Please contact your administrator to find another time.
"""
    return html, text


# ── Call Singers ──────────────────────────────────────────────────────────────

def render_call_singers_email(
    fullname: str, opera_name: str, rehearsal_time: datetime,
    location: str, scope_label: str, is_optional: bool = False
) -> tuple[str, str]:
    date_str = rehearsal_time.strftime("%A, %B %d, %Y")
    time_str = rehearsal_time.strftime("%I:%M %p").lstrip("0")
    loc_line_html = f"<p><strong>Location:</strong> {location}</p>" if location else ""
    loc_line_text = f"\nLocation: {location}" if location else ""

    scope_display = {
        "tutti": "all singers (tutti)",
    }.get(scope_label, scope_label.replace("cast:", "Cast ").replace("role:", "Role: "))

    if is_optional:
        headline = "Optional Orchestra Rehearsal"
        call_line = f"You're welcome to join an orchestra rehearsal of <strong>{opera_name}</strong> if your schedule allows — attendance is <strong>optional</strong>."
        call_line_text = f"You're welcome to join an orchestra rehearsal of {opera_name} if your schedule allows — attendance is optional."
        closing = "No need to confirm — just come if you can."
    else:
        headline = "Orchestra Rehearsal Call"
        call_line = f"You are called for an orchestra rehearsal of <strong>{opera_name}</strong>."
        call_line_text = f"You are called for an orchestra rehearsal of {opera_name}."
        closing = "Please confirm with your conductor or administrator if you have any questions."

    html = f"""\
<!DOCTYPE html>
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:560px;margin:40px auto;padding:24px;color:#222;">
    <h2 style="color:#444;">{headline}</h2>
    <p>Hi {fullname},</p>
    <p>{call_line}</p>
    <p><strong>Date:</strong> {date_str}<br>
       <strong>Time:</strong> {time_str}</p>
    {loc_line_html}
    <p style="color:#666;font-size:14px;">Called as: {scope_display}</p>
    <p>{closing}</p>
</body>
</html>"""

    text = f"""{headline}

Hi {fullname},

{call_line_text}

Date: {date_str}
Time: {time_str}{loc_line_text}
Called as: {scope_display}

{closing}
"""
    return html, text


@app.get("/admin/rehearsal/{rehearsal_id}/call-singers-data")
def get_call_singers_data(rehearsal_id: int, request: Request):
    """Return casts and roles for the opera attached to a rehearsal."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    with db_cursor() as cur:
        cur.execute("""
            SELECT r.opera_id, o.opera_name
            FROM rehearsals r
            JOIN operas o ON o.id = r.opera_id
            WHERE r.id = %s AND o.org_id = %s
        """, (rehearsal_id, org_id))
        row = cur.fetchone()
        if not row:
            return {"error": "Rehearsal not found"}
        opera_id, opera_name = row

        cur.execute("SELECT id, name FROM casts WHERE opera_id = %s ORDER BY name", (opera_id,))
        casts = [{"id": r[0], "name": r[1]} for r in cur.fetchall()]

        cur.execute("""
            SELECT DISTINCT role_name FROM opera_roles
            WHERE opera_id = %s ORDER BY role_name
        """, (opera_id,))
        roles = [r[0] for r in cur.fetchall()]

    return {"opera_name": opera_name, "opera_id": opera_id, "casts": casts, "roles": roles}


@app.post("/admin/rehearsal/{rehearsal_id}/call-singers")
def call_singers(rehearsal_id: int, payload: dict, request: Request):
    """Resolve singers by scope and email them a rehearsal call."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]

    scope = payload.get("scope")
    cast_id = payload.get("cast_id")
    role_name = payload.get("role_name", "").strip()

    if scope not in ("tutti", "cast", "role"):
        return {"status": "fail", "message": "Invalid scope"}
    if scope == "cast" and not cast_id:
        return {"status": "fail", "message": "cast_id required for cast scope"}
    if scope == "role" and not role_name:
        return {"status": "fail", "message": "role_name required for role scope"}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT r.opera_id, o.opera_name, r.start_time, r.location
            FROM rehearsals r
            JOIN operas o ON o.id = r.opera_id
            WHERE r.id = %s AND o.org_id = %s
        """, (rehearsal_id, org_id))
        row = cur.fetchone()
        if not row:
            return {"status": "fail", "message": "Rehearsal not found"}
        opera_id, opera_name, start_time, location = row

        if scope == "tutti":
            cur.execute("""
                SELECT DISTINCT u.id, u.fullname, u.email, NULL, NULL
                FROM users u
                JOIN student_assignments sa ON sa.student_id = u.id
                WHERE sa.opera_id = %s AND u.role = 'student'
            """, (opera_id,))
        elif scope == "cast":
            cur.execute("""
                SELECT DISTINCT u.id, u.fullname, u.email, NULL, NULL
                FROM users u
                JOIN student_assignments sa ON sa.student_id = u.id
                WHERE sa.opera_id = %s AND sa.cast_id = %s AND u.role = 'student'
            """, (opera_id, cast_id))
        else:
            cur.execute("""
                SELECT DISTINCT u.id, u.fullname, u.email, sr.external_name, sr.external_email
                FROM student_roles sr
                LEFT JOIN users u ON u.id = sr.student_id
                WHERE sr.opera_id = %s AND sr.role_name = %s
            """, (opera_id, role_name))

        singers = [
            (sid, full_name if u_email else ext_name, u_email if u_email else ext_email)
            for sid, full_name, u_email, ext_name, ext_email in cur.fetchall()
            if u_email or ext_email
        ]

        # Build scope label for the log
        if scope == "tutti":
            scope_label = "tutti"
        elif scope == "cast":
            cur.execute("SELECT name FROM casts WHERE id = %s", (cast_id,))
            cast_row = cur.fetchone()
            scope_label = f"cast:{cast_row[0] if cast_row else cast_id}"
        else:
            scope_label = f"role:{role_name}"

        cur.execute("""
            INSERT INTO rehearsal_calls (rehearsal_id, opera_id, call_scope, called_by, is_optional)
            VALUES (%s, %s, %s, %s, %s)
        """, (rehearsal_id, opera_id, scope_label, user["id"], bool(payload.get("is_optional", False))))

    is_optional = bool(payload.get("is_optional", False))
    subject = f"{'Optional orchestra call' if is_optional else 'Orchestra rehearsal call'} — {opera_name}"

    count = 0
    for _, fullname, email in singers:
        html, text = render_call_singers_email(fullname, opera_name, start_time, location or "", scope_label, is_optional)
        if send_email(email, subject, html, text):
            count += 1

    return {"status": "success", "count": count, "total": len(singers)}


# ========================================================
# CHOIR APP
# ========================================================

def require_choir_admin(request: Request):
    user = require_user(request, role="admin")
    if user.get("org_type") != "choir":
        raise HTTPException(status_code=403, detail="Choir org required")
    return user

def require_choir_member(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    if user.get("org_type") != "choir":
        raise HTTPException(status_code=403, detail="Choir org required")
    return user


def resolve_member_section_id(user: dict) -> Optional[int]:
    """
    Resolve a choir/ensemble member's section_id, falling back to a
    voice_type (choir members) or instrument (ensemble members) name
    match against choir_sections. Creates the section if none matches
    yet, and persists the result onto the user row so subsequent
    lookups (subs, calendar filtering, rehearsal calls) don't need to
    re-resolve it.
    """
    section_id = user.get("section_id")
    if section_id:
        return section_id
    name = (user.get("voice_type") or user.get("instrument") or "").strip()
    if not name:
        return None
    org_id = user["org_id"]
    with db_cursor() as cur:
        cur.execute(
            "SELECT id FROM choir_sections WHERE org_id=%s AND LOWER(name)=%s LIMIT 1",
            (org_id, name.lower())
        )
        row = cur.fetchone()
    if row:
        section_id = row[0]
    else:
        with db_cursor(commit=True) as cur:
            cur.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM choir_sections WHERE org_id=%s",
                (org_id,)
            )
            next_sort = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO choir_sections (org_id, name, sort_order) VALUES (%s, %s, %s) RETURNING id",
                (org_id, name.title(), next_sort)
            )
            section_id = cur.fetchone()[0]
    with db_cursor(commit=True) as cur:
        cur.execute("UPDATE users SET section_id=%s WHERE id=%s", (section_id, user["id"]))
    return section_id


# -- Page routes --------------------------------------------------------------

@app.get("/choir/admin", response_class=HTMLResponse)
def choir_admin_page(request: Request):
    return templates.TemplateResponse(request, "choir/choir_admin.html")

@app.get("/choir/member", response_class=HTMLResponse)
def choir_member_page(request: Request):
    return templates.TemplateResponse(request, "choir/choir_member.html")

@app.get("/choir/sub-response/{token}", response_class=HTMLResponse)
def choir_sub_response_page(token: str, r: Optional[str] = None, request: Request = None):
    """Public page â€” sub clicks Accept or Decline from their email link."""
    if not r or r not in ("accepted", "declined"):
        return templates.TemplateResponse(request, "choir/choir_sub_response.html",
            {"message": "Invalid response link.", "success": False})

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT sc.id, sc.sub_request_id, sc.response, sc.sub_id,
                   sr.status, sr.rehearsal_id, sr.section_id,
                   s.fullname, s.email, sc.tier
            FROM sub_contacts sc
            JOIN sub_requests sr ON sr.id = sc.sub_request_id
            JOIN subs s ON s.id = sc.sub_id
            WHERE sc.token = %s
        """, (token,))
        row = cur.fetchone()

        if not row:
            return templates.TemplateResponse(request, "choir/choir_sub_response.html",
                {"message": "This link is invalid or has expired.", "success": False})

        sc_id, req_id, existing_response, sub_id, req_status, rehearsal_id, section_id, sub_name, sub_email, sc_tier = row

        if existing_response != "pending":
            if req_status == "filled" and existing_response == "declined":
                return templates.TemplateResponse(request, "choir/choir_sub_response.html",
                    {"message": "This position has already been filled. Thank you for your willingness!", "success": True})
            return templates.TemplateResponse(request, "choir/choir_sub_response.html",
                {"message": "You have already responded - thank you!", "success": True})

        if req_status == "filled":
            cur.execute("UPDATE sub_contacts SET response='declined', responded_at=NOW() WHERE id=%s", (sc_id,))
            return templates.TemplateResponse(request, "choir/choir_sub_response.html",
                {"message": "This position has already been filled. Thank you for your willingness!", "success": True})

        cur.execute("UPDATE sub_contacts SET response=%s, responded_at=NOW() WHERE id=%s", (r, sc_id))

        if r == "accepted":
            cur.execute("UPDATE sub_requests SET status='filled', filled_by_sub_id=%s WHERE id=%s",
                        (sub_id, req_id))
            cur.execute("""
                UPDATE sub_contacts SET response='declined', responded_at=NOW()
                WHERE sub_request_id=%s AND id != %s AND response='pending'
            """, (req_id, sc_id))

            cur.execute("""
                SELECT r.start_time, cs.name, o.name
                FROM rehearsals r
                JOIN choir_sections cs ON cs.id = %s
                JOIN organizations o ON o.id = r.org_id
                WHERE r.id = %s
            """, (section_id, rehearsal_id))
            reh = cur.fetchone()

            cur.execute("""
                SELECT u.email FROM users u
                WHERE u.org_id = (SELECT org_id FROM choir_sections WHERE id=%s)
                  AND u.role='admin' LIMIT 1
            """, (section_id,))
            admin_row = cur.fetchone()

            cur.execute("""
                SELECT u.fullname, u.email FROM users u
                JOIN sub_requests sr ON sr.created_by = u.id
                WHERE sr.id = %s
            """, (req_id,))
            member_row = cur.fetchone()

            if reh:
                rdate = reh[0].strftime("%A, %B %-d") if hasattr(reh[0], "strftime") else str(reh[0])
                html_body = (f"<p><strong>{sub_name}</strong> accepted the sub for "
                             f"<strong>{reh[1]}</strong> on {rdate}.</p>")
                text_body = f"{sub_name} accepted the sub for {reh[1]} on {rdate}."
                if admin_row:
                    send_email(admin_row[0], f"Sub confirmed - {reh[1]}", html_body, text_body)
                if member_row:
                    mbr_html = (f"<p>Hi {member_row[0]},</p>"
                                f"<p><strong>{sub_name}</strong> has accepted the sub for "
                                f"<strong>{reh[1]}</strong> on {rdate}. You're all set!</p>")
                    mbr_text = (f"Hi {member_row[0]},\n\n"
                                f"{sub_name} has accepted the sub for {reh[1]} on {rdate}. You're all set!")
                    send_email(member_row[1], f"Sub confirmed - {reh[1]}", mbr_html, mbr_text)

            return templates.TemplateResponse(request, "choir/choir_sub_response.html",
                {"message": f"You are confirmed! Thank you, {sub_name}. See you at rehearsal.", "success": True, "sub_token": token})

        # r == "declined": notify the choir member and admin
        cur.execute("""
            SELECT r.start_time, cs.name, u.fullname, u.email
            FROM sub_requests sr
            JOIN rehearsals r ON r.id = sr.rehearsal_id
            JOIN choir_sections cs ON cs.id = sr.section_id
            JOIN users u ON u.id = sr.created_by
            WHERE sr.id = %s
        """, (req_id,))
        dec_info = cur.fetchone()
        if dec_info:
            rdate_d = dec_info[0].strftime("%A, %B %-d") if hasattr(dec_info[0], "strftime") else str(dec_info[0])
            sec_nm, mbr_name, mbr_email = dec_info[1], dec_info[2], dec_info[3]
            dec_html = (
                f"<p>Hi {mbr_name},</p>"
                f"<p><strong>{sub_name}</strong> has declined your sub request for "
                f"<strong>{sec_nm}</strong> on {rdate_d}. "
                f"You may want to reach out to another sub.</p>"
            )
            dec_text = (
                f"Hi {mbr_name},\n\n"
                f"{sub_name} has declined your sub request for {sec_nm} on {rdate_d}. "
                f"You may want to reach out to another sub."
            )
            send_email(mbr_email, f"Sub declined - {sec_nm} on {rdate_d}", dec_html, dec_text)

            cur.execute("""
                SELECT u.email FROM users u
                WHERE u.org_id = (SELECT org_id FROM choir_sections WHERE id=%s)
                  AND u.role='admin' LIMIT 1
            """, (section_id,))
            dec_admin_row = cur.fetchone()
            if dec_admin_row:
                adm_html = (f"<p><strong>{sub_name}</strong> has declined the sub request for "
                            f"<strong>{sec_nm}</strong> on {rdate_d} "
                            f"(requested by {mbr_name}).</p>")
                adm_text = (f"{sub_name} has declined the sub request for {sec_nm} on {rdate_d} "
                            f"(requested by {mbr_name}).")
                send_email(dec_admin_row[0], f"Sub declined - {sec_nm} on {rdate_d}", adm_html, adm_text)

    # If a preferred sub declined, immediately contact the next ranked preferred sub
    if sc_tier == "preferred":
        _advance_preferred_sub(req_id, rehearsal_id, section_id)

    return templates.TemplateResponse(request, "choir/choir_sub_response.html",
        {"message": "Your response has been recorded. Thank you!", "success": True})


# -- Sections -----------------------------------------------------------------

@app.get("/choir/sections")
def choir_get_sections(request: Request):
    user = require_choir_member(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, name, sort_order FROM choir_sections
            WHERE org_id = %s ORDER BY sort_order, name
        """, (user["org_id"],))
        return [{"id": r[0], "name": r[1], "sort_order": r[2]} for r in cur.fetchall()]

@app.post("/choir/sections")
def choir_add_section(payload: dict, request: Request):
    user = require_choir_admin(request)
    name = (payload.get("name") or "").strip()
    if not name:
        return {"status": "fail", "message": "Section name required"}
    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO choir_sections (org_id, name, sort_order)
            VALUES (%s, %s,
                COALESCE((SELECT MAX(sort_order)+1 FROM choir_sections WHERE org_id=%s), 0))
            RETURNING id
        """, (user["org_id"], name, user["org_id"]))
        new_id = cur.fetchone()[0]
    return {"status": "success", "id": new_id}

@app.delete("/choir/section/{section_id}")
def choir_delete_section(section_id: int, request: Request):
    user = require_choir_admin(request)
    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM choir_sections WHERE id=%s AND org_id=%s",
                    (section_id, user["org_id"]))
    return {"status": "success"}


VOICE_ORDER = ["soprano", "alto", "tenor", "bass"]

@app.get("/choir/sections/roster")
def choir_sections_roster(request: Request):
    """Sections tab: singers grouped by voice type with attendance for next rehearsal."""
    user = require_choir_admin(request)
    org_id = user["org_id"]
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, start_time FROM rehearsals
            WHERE org_id = %s AND start_time::date >= CURRENT_DATE
            ORDER BY start_time
            LIMIT 1
        """, (org_id,))
        reh_row = cur.fetchone()
        reh_id = reh_row[0] if reh_row else None
        reh_date = reh_row[1].date().isoformat() if reh_row else None

        cur.execute("""
            SELECT id, fullname, voice_type FROM users
            WHERE org_id = %s AND role IN ('student', 'choir_member')
            ORDER BY fullname
        """, (org_id,))
        singers = cur.fetchall()

        absent_ids = set()
        if reh_id:
            cur.execute("""
                SELECT singer_id FROM absence_requests WHERE rehearsal_id = %s
            """, (reh_id,))
            absent_ids = {r[0] for r in cur.fetchall()}

    groups = {v: [] for v in VOICE_ORDER}
    groups["other"] = []
    for sid, name, vt in singers:
        key = (vt or "").lower()
        if key not in groups:
            key = "other"
        status = "absent" if sid in absent_ids else "attending"
        groups[key].append({"id": sid, "name": name, "status": status})

    return {
        "rehearsal_date": reh_date,
        "groups": [
            {"voice_type": v, "singers": groups[v]}
            for v in [*VOICE_ORDER, "other"]
            if groups[v]
        ],
    }


# -- Rehearsals ---------------------------------------------------------------

@app.get("/choir/rehearsals")
def choir_get_rehearsals(request: Request):
    user = require_choir_member(request)
    org_id = user["org_id"]
    role = user["role"]
    section_id = user.get("section_id") if role == "admin" else resolve_member_section_id(user)

    with db_cursor() as cur:
        cur.execute("""
            SELECT r.id, r.start_time, r.end_time, r.location, r.notes,
                   COALESCE(r.choir_type, 'choir') AS choir_type, r.materials_url
            FROM rehearsals r
            WHERE r.org_id = %s AND r.start_time::date >= CURRENT_DATE
            ORDER BY r.start_time
        """, (org_id,))
        rows = cur.fetchall()

        result = []
        for r in rows:
            rid, rstart, rend, location, notes, ctype, mat_url = r
            cur.execute("SELECT section_id FROM rehearsal_sections WHERE rehearsal_id=%s", (rid,))
            called = [row[0] for row in cur.fetchall()]
            if role != "admin" and section_id and called and section_id not in called:
                continue
            cur.execute("""
                SELECT u.id, u.fullname, u.instrument
                FROM rehearsal_members rm
                JOIN users u ON u.id = rm.user_id
                WHERE rm.rehearsal_id = %s
            """, (rid,))
            indiv = [{"id": rw[0], "fullname": rw[1], "instrument": rw[2] or ""} for rw in cur.fetchall()]
            result.append({
                "id": rid,
                "date": rstart.date().isoformat(),
                "start_time": rstart.strftime("%H:%M"),
                "end_time": rend.strftime("%H:%M") if rend else None,
                "location": location or "",
                "notes": notes or "",
                "called_sections": called,
                "choir_type": ctype,
                "individual_members": indiv,
                "materials_url": mat_url or "",
                "absence_count": 0,
            })

        if result:
            reh_ids = [r["id"] for r in result]
            cur.execute(
                "SELECT rehearsal_id, COUNT(*) FROM absence_requests WHERE rehearsal_id = ANY(%s) GROUP BY rehearsal_id",
                (reh_ids,),
            )
            absence_counts = {row[0]: row[1] for row in cur.fetchall()}
            cur.execute(
                "SELECT rehearsal_id, COUNT(*) FROM absence_requests WHERE rehearsal_id = ANY(%s) AND status='pending' GROUP BY rehearsal_id",
                (reh_ids,),
            )
            pending_counts = {row[0]: row[1] for row in cur.fetchall()}
            for r in result:
                r["absence_count"] = absence_counts.get(r["id"], 0)
                r["pending_count"] = pending_counts.get(r["id"], 0)

        return result

@app.post("/choir/rehearsals")
def choir_create_rehearsal(payload: dict, request: Request):
    user = require_choir_admin(request)
    date = payload.get("date")
    start = payload.get("start_time")
    end = payload.get("end_time") or None
    location = (payload.get("location") or "").strip()
    notes = (payload.get("notes") or "").strip()
    sections = payload.get("sections", [])
    choir_type = payload.get("choir_type", "choir")
    if choir_type not in ("choir", "ensemble"):
        choir_type = "choir"
    members = payload.get("members", [])
    materials_url = (payload.get("materials_url") or "").strip() or None

    if not date or not start:
        return {"status": "fail", "message": "Date and start time required"}

    try:
        start_dt = datetime.fromisoformat(f"{date}T{start}")
        end_dt = datetime.fromisoformat(f"{date}T{end}") if end else None
        if end_dt and end_dt <= start_dt:
            return {"status": "fail", "message": "End time must be after start time"}
    except Exception:
        return {"status": "fail", "message": "Invalid date or time format"}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO rehearsals (org_id, start_time, end_time, location, notes, rehearsal_type, attendance_type, choir_type, materials_url)
            VALUES (%s, %s, %s, %s, %s, 'vocal', 'full', %s, %s) RETURNING id
        """, (user["org_id"], start_dt, end_dt, location, notes, choir_type, materials_url))
        rid = cur.fetchone()[0]
        for sid in sections:
            cur.execute("""
                INSERT INTO rehearsal_sections (rehearsal_id, section_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
            """, (rid, sid))
        for uid in members:
            cur.execute("""
                INSERT INTO rehearsal_members (rehearsal_id, user_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
            """, (rid, uid))

    return {"status": "success", "rehearsal_id": rid}

@app.delete("/choir/rehearsal/{rehearsal_id}")
def choir_delete_rehearsal(rehearsal_id: int, request: Request):
    user = require_choir_admin(request)
    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM rehearsals WHERE id=%s AND org_id=%s",
                    (rehearsal_id, user["org_id"]))
    return {"status": "success"}


@app.post("/choir/rehearsals/{rehearsal_id}/members")
def choir_add_rehearsal_member(rehearsal_id: int, payload: dict, request: Request):
    user = require_choir_admin(request)
    user_id = payload.get("user_id")
    if not user_id:
        return {"status": "fail", "message": "user_id required"}
    with db_cursor(commit=True) as cur:
        cur.execute("SELECT id FROM rehearsals WHERE id=%s AND org_id=%s", (rehearsal_id, user["org_id"]))
        if not cur.fetchone():
            return {"status": "fail", "message": "Rehearsal not found"}
        cur.execute("""
            INSERT INTO rehearsal_members (rehearsal_id, user_id)
            VALUES (%s, %s) ON CONFLICT DO NOTHING
        """, (rehearsal_id, user_id))
        cur.execute("""
            SELECT u.id, u.fullname, u.instrument
            FROM rehearsal_members rm
            JOIN users u ON u.id = rm.user_id
            WHERE rm.rehearsal_id = %s
        """, (rehearsal_id,))
        members = [{"id": r[0], "fullname": r[1], "instrument": r[2] or ""} for r in cur.fetchall()]
    return {"status": "success", "individual_members": members}


@app.delete("/choir/rehearsals/{rehearsal_id}/members/{user_id}")
def choir_remove_rehearsal_member(rehearsal_id: int, user_id: int, request: Request):
    admin = require_choir_admin(request)
    with db_cursor(commit=True) as cur:
        cur.execute("SELECT id FROM rehearsals WHERE id=%s AND org_id=%s", (rehearsal_id, admin["org_id"]))
        if not cur.fetchone():
            return {"status": "fail", "message": "Rehearsal not found"}
        cur.execute(
            "DELETE FROM rehearsal_members WHERE rehearsal_id=%s AND user_id=%s",
            (rehearsal_id, user_id)
        )
        cur.execute("""
            SELECT u.id, u.fullname, u.instrument
            FROM rehearsal_members rm
            JOIN users u ON u.id = rm.user_id
            WHERE rm.rehearsal_id = %s
        """, (rehearsal_id,))
        members = [{"id": r[0], "fullname": r[1], "instrument": r[2] or ""} for r in cur.fetchall()]
    return {"status": "success", "individual_members": members}


@app.post("/choir/rehearsals/{rehearsal_id}/notes")
def choir_set_rehearsal_notes(rehearsal_id: int, payload: dict, request: Request):
    user = require_choir_admin(request)
    org_id = user["org_id"]
    notes = (payload.get("notes") or "").strip()

    with db_cursor(commit=True) as cur:
        cur.execute(
            "SELECT id, start_time, end_time FROM rehearsals WHERE id=%s AND org_id=%s",
            (rehearsal_id, org_id),
        )
        row = cur.fetchone()
        if not row:
            return {"status": "fail", "message": "Rehearsal not found"}
        _, start_dt, end_dt = row

        cur.execute("UPDATE rehearsals SET notes=%s WHERE id=%s", (notes or None, rehearsal_id))

        cur.execute(
            "SELECT section_id FROM rehearsal_sections WHERE rehearsal_id=%s",
            (rehearsal_id,),
        )
        section_ids = [r[0] for r in cur.fetchall()]

        # Collect recipients: choir members in called sections + choir admins
        if section_ids:
            cur.execute(
                """
                SELECT DISTINCT u.fullname, u.email FROM users u
                WHERE u.org_id=%s AND u.role='choir_member'
                  AND u.section_id = ANY(%s) AND u.email IS NOT NULL
                """,
                (org_id, section_ids),
            )
        else:
            cur.execute(
                "SELECT fullname, email FROM users WHERE org_id=%s AND role='choir_member' AND email IS NOT NULL",
                (org_id,),
            )
        recipients = {email: name for name, email in cur.fetchall() if email}

        cur.execute(
            "SELECT fullname, email FROM users WHERE org_id=%s AND role='choir_admin' AND email IS NOT NULL",
            (org_id,),
        )
        for name, email in cur.fetchall():
            if email and email not in recipients:
                recipients[email] = name

    org_tz = get_org_tz(user)
    local_start = start_dt.astimezone(org_tz) if start_dt.tzinfo else start_dt
    local_end = end_dt.astimezone(org_tz) if end_dt and end_dt.tzinfo else end_dt
    date_str = local_start.strftime("%A, %B %-d, %Y")
    start_str = local_start.strftime("%-I:%M %p")
    end_str = local_end.strftime("%-I:%M %p") if local_end else ""
    time_str = f"{start_str}–{end_str}" if end_str else start_str

    html_body, text_body = render_choir_notes_email(date_str, time_str, notes)
    subject = f"Rehearsal Notes – {date_str}"

    sent = 0
    for email in recipients:
        if send_email(to=email, subject=subject, html_body=html_body, text_body=text_body):
            sent += 1

    return {"status": "success", "emailed": sent}


@app.delete("/choir/rehearsals/{rehearsal_id}/notes")
def choir_delete_rehearsal_notes(rehearsal_id: int, request: Request):
    user = require_choir_admin(request)
    with db_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE rehearsals SET notes=NULL WHERE id=%s AND org_id=%s",
            (rehearsal_id, user["org_id"]),
        )
    return {"status": "success"}


@app.put("/choir/rehearsals/{rehearsal_id}")
def choir_edit_rehearsal(rehearsal_id: int, payload: dict, request: Request):
    user = require_choir_admin(request)
    org_id = user["org_id"]

    start_time = payload.get("start_time")
    end_time = payload.get("end_time") or None
    location = (payload.get("location") or "").strip() or None
    notes = (payload.get("notes") or "").strip() or None
    sections = payload.get("sections")
    members = payload.get("members")
    choir_type = payload.get("choir_type")
    if choir_type and choir_type not in ("choir", "ensemble"):
        choir_type = None
    materials_url = (payload.get("materials_url") or "").strip() or None

    if not start_time:
        return {"status": "fail", "message": "start_time required"}

    with db_cursor(commit=True) as cur:
        cur.execute(
            "SELECT start_time FROM rehearsals WHERE id=%s AND org_id=%s",
            (rehearsal_id, org_id),
        )
        row = cur.fetchone()
        if not row:
            return {"status": "fail", "message": "Rehearsal not found"}

        existing_date = row[0].date()
        new_start = datetime.fromisoformat(f"{existing_date}T{start_time}")
        new_end = datetime.fromisoformat(f"{existing_date}T{end_time}") if end_time else None

        if choir_type:
            cur.execute(
                "UPDATE rehearsals SET start_time=%s, end_time=%s, location=%s, notes=%s, choir_type=%s, materials_url=%s WHERE id=%s",
                (new_start, new_end, location, notes, choir_type, materials_url, rehearsal_id),
            )
        else:
            cur.execute(
                "UPDATE rehearsals SET start_time=%s, end_time=%s, location=%s, notes=%s, materials_url=%s WHERE id=%s",
                (new_start, new_end, location, notes, materials_url, rehearsal_id),
            )

        if sections is not None:
            cur.execute("DELETE FROM rehearsal_sections WHERE rehearsal_id=%s", (rehearsal_id,))
            for sid in sections:
                cur.execute(
                    "INSERT INTO rehearsal_sections (rehearsal_id, section_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (rehearsal_id, sid),
                )
        if members is not None:
            cur.execute("DELETE FROM rehearsal_members WHERE rehearsal_id=%s", (rehearsal_id,))
            for uid in members:
                cur.execute(
                    "INSERT INTO rehearsal_members (rehearsal_id, user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (rehearsal_id, uid),
                )

    return {"status": "success"}


@app.post("/choir/rehearsals/bulk")
def choir_create_rehearsals_bulk(payload: dict, request: Request):
    from datetime import date as date_type, timedelta
    user = require_choir_admin(request)
    start_date = payload.get("start_date")
    end_date = payload.get("end_date")
    days = payload.get("days", [])
    start = payload.get("start_time")
    end = payload.get("end_time") or None
    location = (payload.get("location") or "").strip() or None
    notes = (payload.get("notes") or "").strip() or None
    sections = payload.get("sections", [])
    choir_type = payload.get("choir_type", "choir")
    if choir_type not in ("choir", "ensemble"):
        choir_type = "choir"
    materials_url = (payload.get("materials_url") or "").strip() or None

    if not start_date or not end_date or not days or not start:
        return {"status": "fail", "message": "Start date, end date, days, and start time are required"}

    DAY_MAP = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
               "friday": 4, "saturday": 5, "sunday": 6}
    day_nums = [DAY_MAP[d.lower()] for d in days if d.lower() in DAY_MAP]
    if not day_nums:
        return {"status": "fail", "message": "No valid days selected"}

    try:
        sd = date_type.fromisoformat(start_date)
        ed = date_type.fromisoformat(end_date)
    except Exception:
        return {"status": "fail", "message": "Invalid date format"}

    if ed < sd:
        return {"status": "fail", "message": "End date must be after start date"}
    if (ed - sd).days > 365:
        return {"status": "fail", "message": "Date range cannot exceed one year"}

    rehearsal_dates = []
    current = sd
    while current <= ed:
        if current.weekday() in day_nums:
            rehearsal_dates.append(current)
        current += timedelta(days=1)

    if not rehearsal_dates:
        return {"status": "fail", "message": "No rehearsals fall in that date range"}
    if len(rehearsal_dates) > 100:
        return {"status": "fail", "message": f"Too many rehearsals ({len(rehearsal_dates)}). Narrow your date range."}

    created = 0
    with db_cursor(commit=True) as cur:
        for rdate in rehearsal_dates:
            cur.execute("SAVEPOINT bulk_reh")
            try:
                start_dt = datetime.fromisoformat(f"{rdate}T{start}")
                end_dt = datetime.fromisoformat(f"{rdate}T{end}") if end else None
                cur.execute("""
                    INSERT INTO rehearsals (org_id, start_time, end_time, location, notes, rehearsal_type, attendance_type, choir_type, materials_url)
                    VALUES (%s, %s, %s, %s, %s, 'vocal', 'full', %s, %s) RETURNING id
                """, (user["org_id"], start_dt, end_dt, location, notes, choir_type, materials_url))
                rid = cur.fetchone()[0]
                for sid in sections:
                    cur.execute("""
                        INSERT INTO rehearsal_sections (rehearsal_id, section_id)
                        VALUES (%s, %s) ON CONFLICT DO NOTHING
                    """, (rid, sid))
                cur.execute("RELEASE SAVEPOINT bulk_reh")
                created += 1
            except Exception as e:
                cur.execute("ROLLBACK TO SAVEPOINT bulk_reh")
                print(f"[choir_bulk] skipped {rdate}: {e}")

    return {"status": "success", "created": created}


# -- Sub roster ---------------------------------------------------------------

@app.get("/choir/subs")
def choir_get_subs(request: Request, section_id: Optional[int] = None):
    user = require_choir_member(request)
    org_id = user["org_id"]
    if user["role"] != "admin":
        section_id = resolve_member_section_id(user)
        if not section_id:
            return []

    with db_cursor() as cur:
        if section_id:
            cur.execute("""
                SELECT s.id, s.fullname, s.email, s.phone, s.is_preferred, s.notes,
                       cs.name, s.section_id, s.preferred_rank,
                       COUNT(CASE WHEN sc.response = 'accepted' THEN 1 END) AS accepted_count,
                       COUNT(CASE WHEN sc.response = 'declined' THEN 1 END) AS declined_count
                FROM subs s
                JOIN choir_sections cs ON cs.id = s.section_id
                LEFT JOIN sub_contacts sc ON sc.sub_id = s.id
                WHERE s.org_id=%s AND s.section_id=%s AND s.active=true
                GROUP BY s.id, cs.id
                ORDER BY s.is_preferred DESC, s.preferred_rank NULLS LAST, s.fullname
            """, (org_id, section_id))
        else:
            cur.execute("""
                SELECT s.id, s.fullname, s.email, s.phone, s.is_preferred, s.notes,
                       cs.name, s.section_id, s.preferred_rank,
                       COUNT(CASE WHEN sc.response = 'accepted' THEN 1 END) AS accepted_count,
                       COUNT(CASE WHEN sc.response = 'declined' THEN 1 END) AS declined_count
                FROM subs s
                JOIN choir_sections cs ON cs.id = s.section_id
                LEFT JOIN sub_contacts sc ON sc.sub_id = s.id
                WHERE s.org_id=%s AND s.active=true
                GROUP BY s.id, cs.id
                ORDER BY cs.sort_order, s.is_preferred DESC, s.preferred_rank NULLS LAST, s.fullname
            """, (org_id,))
        return [{"id": r[0], "fullname": r[1], "email": r[2], "phone": r[3] or "",
                 "is_preferred": r[4], "notes": r[5] or "",
                 "section_name": r[6], "section_id": r[7],
                 "preferred_rank": r[8],
                 "accepted_count": r[9], "declined_count": r[10]} for r in cur.fetchall()]

@app.post("/choir/subs")
def choir_add_sub(payload: dict, request: Request):
    user = require_choir_admin(request)
    fullname = (payload.get("fullname") or "").strip()
    email = (payload.get("email") or "").strip().lower()
    section_id = payload.get("section_id")
    phone = (payload.get("phone") or "").strip() or None
    is_preferred = bool(payload.get("is_preferred", False))
    notes = (payload.get("notes") or "").strip() or None
    if not fullname or not email or not section_id:
        return {"status": "fail", "message": "Name, email, and section required"}
    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO subs (org_id, section_id, fullname, email, phone, is_preferred, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (user["org_id"], section_id, fullname, email, phone, is_preferred, notes))
        new_id = cur.fetchone()[0]
    return {"status": "success", "id": new_id}

@app.patch("/choir/sub/{sub_id}")
def choir_update_sub(sub_id: int, payload: dict, request: Request):
    user = require_choir_admin(request)
    fields, vals = [], []
    for col in ("fullname", "email", "phone", "notes"):
        if col in payload:
            fields.append(f"{col}=%s")
            vals.append((payload[col] or "").strip() or None)
    if "is_preferred" in payload:
        fields.append("is_preferred=%s")
        vals.append(bool(payload["is_preferred"]))
    if "preferred_rank" in payload:
        fields.append("preferred_rank=%s")
        raw_rank = payload["preferred_rank"]
        vals.append(int(raw_rank) if raw_rank is not None else None)
    if "active" in payload:
        fields.append("active=%s")
        vals.append(bool(payload["active"]))
    if not fields:
        return {"status": "ok"}
    vals += [sub_id, user["org_id"]]
    with db_cursor(commit=True) as cur:
        cur.execute(f"UPDATE subs SET {', '.join(fields)} WHERE id=%s AND org_id=%s", vals)
    return {"status": "success"}

@app.delete("/choir/sub/{sub_id}")
def choir_delete_sub(sub_id: int, request: Request):
    user = require_choir_admin(request)
    with db_cursor(commit=True) as cur:
        cur.execute("UPDATE subs SET active=false WHERE id=%s AND org_id=%s",
                    (sub_id, user["org_id"]))
    return {"status": "success"}


# -- Singers ------------------------------------------------------------------

@app.get("/choir/singers")
def choir_get_singers(request: Request):
    user = require_choir_admin(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT u.id, u.fullname, u.email, u.username, u.section_id, cs.name
            FROM users u
            LEFT JOIN choir_sections cs ON cs.id = u.section_id
            WHERE u.org_id=%s AND u.role IN ('student', 'choir_member')
            ORDER BY cs.sort_order NULLS LAST, u.fullname
        """, (user["org_id"],))
        return [{"id": r[0], "fullname": r[1], "email": r[2], "username": r[3],
                 "section_id": r[4], "section_name": r[5] or "Unassigned"}
                for r in cur.fetchall()]

@app.patch("/choir/singer/{singer_id}/section")
def choir_set_singer_section(singer_id: int, payload: dict, request: Request):
    user = require_choir_admin(request)
    section_id = payload.get("section_id")
    with db_cursor(commit=True) as cur:
        cur.execute("UPDATE users SET section_id=%s WHERE id=%s AND org_id=%s",
                    (section_id, singer_id, user["org_id"]))
    return {"status": "success"}


# -- Absence requests ---------------------------------------------------------

@app.post("/choir/absence-request")
def choir_mark_absent(payload: dict, request: Request):
    user = require_choir_member(request)
    rehearsal_id = payload.get("rehearsal_id")
    reason = (payload.get("reason") or "").strip() or None
    note = (payload.get("note") or "").strip() or None
    if not rehearsal_id:
        return {"status": "fail", "message": "rehearsal_id required"}
    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO absence_requests (rehearsal_id, singer_id, reason, note, status)
            VALUES (%s, %s, %s, %s, 'pending')
            ON CONFLICT (rehearsal_id, singer_id) DO UPDATE
              SET reason=EXCLUDED.reason, note=EXCLUDED.note, status='pending', contact_preferred_on_approval=FALSE
        """, (rehearsal_id, user["id"], reason, note))
    return {"status": "success"}

@app.delete("/choir/absence-request/{rehearsal_id}")
def choir_cancel_absence(rehearsal_id: int, request: Request):
    user = require_choir_member(request)
    org_id = user["org_id"]

    section_id = resolve_member_section_id(user)

    if section_id:
        with db_cursor() as cur:
            cur.execute("""
                SELECT sr.id, s.fullname, s.email, r.start_time, cs.name
                FROM sub_requests sr
                JOIN subs s ON s.id = sr.filled_by_sub_id
                JOIN rehearsals r ON r.id = sr.rehearsal_id
                JOIN choir_sections cs ON cs.id = sr.section_id
                WHERE sr.rehearsal_id = %s AND sr.section_id = %s AND sr.status = 'filled'
            """, (rehearsal_id, section_id))
            sub_row = cur.fetchone()

        if sub_row:
            sr_id, sub_name, sub_email, start_time, section_name = sub_row
            rdate = start_time.strftime("%A, %B %-d") if hasattr(start_time, "strftime") else str(start_time)
            with db_cursor(commit=True) as cur:
                cur.execute("UPDATE sub_requests SET status='cancelled' WHERE id=%s", (sr_id,))
            html_body = (
                f"<p>Hi {sub_name},</p>"
                f"<p>Good news - you are no longer needed as a substitute for "
                f"<strong>{section_name}</strong> on {rdate}. The singer is now available.</p>"
                f"<p>Thank you for your willingness to help!</p>"
            )
            text_body = (
                f"Hi {sub_name},\n\n"
                f"Good news - you are no longer needed as a substitute for "
                f"{section_name} on {rdate}. The singer is now available.\n\n"
                f"Thank you for your willingness to help!"
            )
            send_email(sub_email, f"Sub no longer needed - {section_name} on {rdate}", html_body, text_body)

    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM absence_requests WHERE rehearsal_id=%s AND singer_id=%s",
                    (rehearsal_id, user["id"]))
    return {"status": "success"}


@app.post("/choir/absence-request/{absence_id}/approve")
def choir_approve_absence(absence_id: int, request: Request):
    user = require_choir_admin(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT ar.id, ar.singer_id, ar.rehearsal_id, ar.contact_preferred_on_approval,
                   u.fullname, u.email,
                   COALESCE(u.section_id,
                       (SELECT cs.id FROM choir_sections cs
                        WHERE cs.org_id = u.org_id AND LOWER(cs.name) = LOWER(u.voice_type) LIMIT 1)
                   ) AS section_id,
                   r.start_time
            FROM absence_requests ar
            JOIN users u ON u.id = ar.singer_id
            JOIN rehearsals r ON r.id = ar.rehearsal_id
            WHERE ar.id = %s
        """, (absence_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Absence request not found")
    ar_id, singer_id, rehearsal_id, contact_flag, singer_name, singer_email, section_id, start_time = row

    with db_cursor(commit=True) as cur:
        cur.execute("UPDATE absence_requests SET status='approved' WHERE id=%s", (ar_id,))

    if contact_flag and section_id:
        with db_cursor(commit=True) as cur:
            cur.execute("""
                SELECT id FROM sub_requests
                WHERE rehearsal_id=%s AND section_id=%s AND status NOT IN ('filled','cancelled')
            """, (rehearsal_id, section_id))
            sr_row = cur.fetchone()
            if sr_row:
                sub_request_id = sr_row[0]
            else:
                cur.execute("""
                    INSERT INTO sub_requests (rehearsal_id, section_id, created_by)
                    VALUES (%s, %s, %s) RETURNING id
                """, (rehearsal_id, section_id, singer_id))
                sub_request_id = cur.fetchone()[0]
        _advance_preferred_sub(sub_request_id, rehearsal_id, section_id)

    if singer_email:
        rdate = start_time.strftime("%A, %B %-d") if hasattr(start_time, "strftime") else str(start_time)
        html_body = (
            f"<p>Hi {singer_name},</p>"
            f"<p>Your absence request for the rehearsal on <strong>{rdate}</strong> has been approved.</p>"
        )
        text_body = f"Hi {singer_name},\n\nYour absence request for the rehearsal on {rdate} has been approved."
        send_email(singer_email, f"Absence request approved — {rdate}", html_body, text_body)

    return {"status": "success"}


@app.post("/choir/absence-request/{absence_id}/deny")
def choir_deny_absence(absence_id: int, request: Request):
    user = require_choir_admin(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT ar.singer_id, ar.rehearsal_id, u.fullname, u.email, r.start_time
            FROM absence_requests ar
            JOIN users u ON u.id = ar.singer_id
            JOIN rehearsals r ON r.id = ar.rehearsal_id
            WHERE ar.id = %s
        """, (absence_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Absence request not found")
    singer_id, rehearsal_id, singer_name, singer_email, start_time = row

    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM absence_requests WHERE id=%s", (absence_id,))

    if singer_email:
        rdate = start_time.strftime("%A, %B %-d") if hasattr(start_time, "strftime") else str(start_time)
        html_body = (
            f"<p>Hi {singer_name},</p>"
            f"<p>Your absence request for the rehearsal on <strong>{rdate}</strong> was not approved. "
            f"Please contact your admin if you have any questions.</p>"
        )
        text_body = (
            f"Hi {singer_name},\n\nYour absence request for the rehearsal on {rdate} was not approved. "
            f"Please contact your admin if you have any questions."
        )
        send_email(singer_email, f"Absence request not approved — {rdate}", html_body, text_body)

    return {"status": "success"}


@app.get("/choir/absences/{rehearsal_id}")
def choir_get_absences(rehearsal_id: int, request: Request):
    user = require_choir_admin(request)
    org_id = user["org_id"]
    with db_cursor() as cur:
        cur.execute("""
            SELECT ar.singer_id, u.fullname, u.section_id, u.voice_type, ar.reason, ar.note,
                   ar.id, ar.status, ar.contact_preferred_on_approval
            FROM absence_requests ar
            JOIN users u ON u.id = ar.singer_id
            WHERE ar.rehearsal_id = %s
            ORDER BY ar.status DESC, u.fullname
        """, (rehearsal_id,))
        rows = cur.fetchall()

        result = []
        for singer_id, fullname, section_id, voice_type, reason, note, ar_id, ar_status, contact_flag in rows:
            resolved_id = section_id
            section_name = None
            if resolved_id:
                cur.execute("SELECT name FROM choir_sections WHERE id = %s", (resolved_id,))
                sec_row = cur.fetchone()
                section_name = sec_row[0] if sec_row else "?"
            elif voice_type:
                cur.execute("""
                    SELECT id, name FROM choir_sections
                    WHERE org_id = %s AND LOWER(name) = LOWER(%s) LIMIT 1
                """, (org_id, voice_type))
                sec_row = cur.fetchone()
                if sec_row:
                    resolved_id = sec_row[0]
                    section_name = sec_row[1]
                else:
                    section_name = voice_type.capitalize()
            result.append({
                "id": ar_id,
                "singer_id": singer_id,
                "singer": fullname,
                "section_id": resolved_id,
                "section": section_name or "?",
                "reason": reason or "",
                "note": note or "",
                "status": ar_status or "approved",
                "contact_preferred_on_approval": bool(contact_flag),
            })
        return result

@app.get("/choir/my-absences")
def choir_my_absences(request: Request):
    user = require_choir_member(request)
    with db_cursor() as cur:
        cur.execute(
            "SELECT rehearsal_id, status FROM absence_requests WHERE singer_id=%s",
            (user["id"],)
        )
        return [{"rehearsal_id": r[0], "status": r[1]} for r in cur.fetchall()]


@app.get("/choir/my-sub-status")
def choir_my_sub_status(request: Request):
    """For each rehearsal the member is absent from, return the sub_request status for their section."""
    user = require_choir_member(request)
    org_id = user["org_id"]

    section_id = resolve_member_section_id(user)

    if not section_id:
        return []

    with db_cursor() as cur:
        cur.execute("""
            SELECT sr.rehearsal_id, sr.status, s.fullname,
                (sr.status != 'filled'
                 AND EXISTS (
                     SELECT 1 FROM sub_contacts sc
                     WHERE sc.sub_request_id = sr.id AND sc.response = 'declined'
                 )
                 AND NOT EXISTS (
                     SELECT 1 FROM sub_contacts sc
                     WHERE sc.sub_request_id = sr.id AND sc.response = 'pending'
                 )
                ) AS all_declined
            FROM absence_requests ar
            JOIN sub_requests sr ON sr.rehearsal_id = ar.rehearsal_id
                                AND sr.section_id = %s
            LEFT JOIN subs s ON s.id = sr.filled_by_sub_id
            WHERE ar.singer_id = %s
        """, (section_id, user["id"]))
        return [{"rehearsal_id": r[0], "status": r[1], "filled_by_name": r[2], "all_declined": bool(r[3])}
                for r in cur.fetchall()]


# -- Sub requests + email workflow -------------------------------------------

def _render_sub_email(sub_name: str, section_name: str, org_name: str,
                      rdate: str, rstart: str, location: str, notes: str,
                      token: str, admin_name: str = None, admin_email: str = None,
                      custom_message: str = None) -> tuple:
    accept_url = f"{APP_URL}/choir/sub-response/{token}?r=accepted"
    decline_url = f"{APP_URL}/choir/sub-response/{token}?r=declined"
    loc_line = f"<br><em>{location}</em>" if location else ""
    note_line = f"<br><em>{notes}</em>" if notes else ""
    if admin_name and admin_email:
        footer_html = f"Please contact {admin_name} at <a href=\"mailto:{admin_email}\">{admin_email}</a> with any questions."
        footer_text = f"Please contact {admin_name} at {admin_email} with any questions."
    elif admin_name:
        footer_html = f"Please contact {admin_name} with any questions."
        footer_text = footer_html
    else:
        footer_html = f"Sent on behalf of {org_name}."
        footer_text = footer_html

    if custom_message:
        import html as html_lib
        safe_msg = html_lib.escape(custom_message).replace("\n", "<br>")
        html = f"""
<div style="font-family:Georgia,serif;max-width:520px;margin:0 auto;color:#1a1a1a;">
  <h2 style="color:#8b6914;margin-bottom:4px;">{org_name}</h2>
  <p style="color:#666;margin-top:0;font-style:italic;">Sub needed &mdash; {section_name}</p>
  <p>Hi {sub_name},</p>
  <div style="padding:12px 16px;background:#faf7f0;border-left:3px solid #c9a227;
              border-radius:4px;margin:12px 0;">
    <strong>{rdate}</strong> at <strong>{rstart}</strong>{loc_line}
  </div>
  <p>{safe_msg}</p>
  <p>Can you make it?</p>
  <table cellpadding="0" cellspacing="0"><tr>
    <td style="padding-right:12px;">
      <a href="{accept_url}" style="display:inline-block;padding:11px 22px;
         background:#2f8f6a;color:#fff;text-decoration:none;border-radius:4px;
         font-weight:600;font-family:sans-serif;">Yes, I can make it</a>
    </td>
    <td>
      <a href="{decline_url}" style="display:inline-block;padding:11px 22px;
         background:#b23a3a;color:#fff;text-decoration:none;border-radius:4px;
         font-weight:600;font-family:sans-serif;">No, I cannot make it</a>
    </td>
  </tr></table>
  <p style="font-size:0.82rem;color:#888;margin-top:28px;border-top:1px solid #e8e3d8;
            padding-top:12px;">
    {footer_html}
  </p>
</div>"""
        text = (f"{org_name} - Sub needed ({section_name})\n\n"
                f"Hi {sub_name},\n\n"
                f"{rdate} at {rstart}{(' at ' + location) if location else ''}\n\n"
                f"{custom_message}\n\n"
                f"Accept: {accept_url}\nDecline: {decline_url}\n\n"
                f"{footer_text}")
        return html, text

    html = f"""
<div style="font-family:Georgia,serif;max-width:520px;margin:0 auto;color:#1a1a1a;">
  <h2 style="color:#8b6914;margin-bottom:4px;">{org_name}</h2>
  <p style="color:#666;margin-top:0;font-style:italic;">Sub needed &mdash; {section_name}</p>
  <p>Hi {sub_name},</p>
  <p>We need a substitute for the <strong>{section_name}</strong> part at an upcoming rehearsal:</p>
  <div style="padding:12px 16px;background:#faf7f0;border-left:3px solid #c9a227;
              border-radius:4px;margin:12px 0;">
    <strong>{rdate}</strong> at <strong>{rstart}</strong>{loc_line}{note_line}
  </div>
  <p>Can you make it?</p>
  <table cellpadding="0" cellspacing="0"><tr>
    <td style="padding-right:12px;">
      <a href="{accept_url}" style="display:inline-block;padding:11px 22px;
         background:#2f8f6a;color:#fff;text-decoration:none;border-radius:4px;
         font-weight:600;font-family:sans-serif;">Yes, I can make it</a>
    </td>
    <td>
      <a href="{decline_url}" style="display:inline-block;padding:11px 22px;
         background:#b23a3a;color:#fff;text-decoration:none;border-radius:4px;
         font-weight:600;font-family:sans-serif;">No, I cannot make it</a>
    </td>
  </tr></table>
  <p style="font-size:0.82rem;color:#888;margin-top:28px;border-top:1px solid #e8e3d8;
            padding-top:12px;">
    {footer_html}
  </p>
</div>"""
    text = (f"{org_name} - Sub needed ({section_name})\n\n"
            f"Hi {sub_name},\n\n"
            f"We need a sub for {section_name} on {rdate} at {rstart}"
            f"{(' at ' + location) if location else ''}.\n\n"
            f"Accept: {accept_url}\nDecline: {decline_url}\n\n"
            f"{footer_text}")
    return html, text


def _preferred_window_hours(rank) -> int:
    """Hours a preferred sub at the given rank has to respond before the next is contacted."""
    if rank == 1:
        return 8
    if rank == 2:
        return 6
    return 4


def _advance_preferred_sub(req_id: int, rehearsal_id: int, section_id: int) -> bool:
    """Contact the next ranked preferred sub for this sub_request.
    If the preferred list is exhausted, immediately bulk-contacts all remaining subs.
    Returns True if a preferred sub was contacted, False if we fell through to bulk."""
    with db_cursor() as cur:
        cur.execute("""
            SELECT s.id, s.fullname, s.email
            FROM subs s
            WHERE s.section_id = %s AND s.is_preferred = true AND s.active = true
              AND s.id NOT IN (SELECT sub_id FROM sub_contacts WHERE sub_request_id = %s)
            ORDER BY s.preferred_rank NULLS LAST, s.fullname
            LIMIT 1
        """, (section_id, req_id))
        row = cur.fetchone()

    if row:
        _send_sub_emails(
            [{"id": row[0], "fullname": row[1], "email": row[2]}],
            req_id, rehearsal_id, section_id, "preferred",
        )
        with db_cursor(commit=True) as cur:
            cur.execute("""
                UPDATE sub_requests
                SET status = 'preferred_sent',
                    preferred_sent_at = COALESCE(preferred_sent_at, NOW())
                WHERE id = %s AND status NOT IN ('filled', 'cancelled')
            """, (req_id,))
        return True

    # Preferred list exhausted — immediately bulk-contact all remaining subs
    with db_cursor() as cur:
        cur.execute("""
            SELECT s.id, s.fullname, s.email FROM subs s
            WHERE s.section_id = %s AND s.active = true
              AND s.id NOT IN (SELECT sub_id FROM sub_contacts WHERE sub_request_id = %s)
        """, (section_id, req_id))
        remaining = [{"id": r[0], "fullname": r[1], "email": r[2]} for r in cur.fetchall()]

    if remaining:
        _send_sub_emails(remaining, req_id, rehearsal_id, section_id, "regular")

    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE sub_requests SET status = 'all_sent', all_sent_at = NOW()
            WHERE id = %s AND status NOT IN ('filled', 'cancelled')
        """, (req_id,))
    return False


def _send_sub_emails(sub_list: list, sub_request_id: int, rehearsal_id: int,
                     section_id: int, tier: str, custom_message: str = None,
                     sender_name: str = None, sender_username: str = None,
                     sender_email: str = None) -> int:
    with db_cursor() as cur:
        cur.execute("""
            SELECT r.start_time, r.location, r.notes,
                   cs.name, o.name, o.id
            FROM rehearsals r
            JOIN choir_sections cs ON cs.id = %s
            JOIN organizations o ON o.id = r.org_id
            WHERE r.id = %s
        """, (section_id, rehearsal_id))
        reh = cur.fetchone()
    if not reh:
        return 0

    start_dt = reh[0]
    rdate = start_dt.strftime("%A, %B %-d") if hasattr(start_dt, "strftime") else str(start_dt)
    rstart = start_dt.strftime("%H:%M") if hasattr(start_dt, "strftime") else ""
    section_name, org_name, org_id = reh[3], reh[4], reh[5]

    admin_name = admin_email = None
    with db_cursor() as cur:
        cur.execute("""
            SELECT fullname, email FROM users
            WHERE org_id = %s AND role IN ('admin', 'head_admin')
            ORDER BY CASE role WHEN 'head_admin' THEN 0 ELSE 1 END LIMIT 1
        """, (org_id,))
        adm = cur.fetchone()
        if adm:
            admin_name, admin_email = adm[0], adm[1]

    sent = 0
    for sub in sub_list:
        token = secrets.token_urlsafe(32)
        with db_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO sub_contacts (sub_request_id, sub_id, tier, token)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (sub_request_id, sub_id) DO NOTHING
            """, (sub_request_id, sub["id"], tier, token))
            if cur.rowcount == 0:
                continue
        html, text = _render_sub_email(sub["fullname"], section_name, org_name,
                                       rdate, rstart, reh[1] or "", reh[2] or "", token,
                                       admin_name, admin_email, custom_message)
        from_addr = _sender_from_username(sender_username) if sender_username else None
        if send_email(sub["email"],
                      f"Sub needed - {section_name} | {org_name}", html, text,
                      from_name=sender_name if sender_username else None,
                      from_address=from_addr,
                      reply_to=sender_email if sender_username else None):
            sent += 1
    return sent


@app.post("/choir/sub-request")
def choir_create_sub_request(payload: dict, request: Request):
    user = require_choir_member(request)
    rehearsal_id = payload.get("rehearsal_id")
    org_id = user["org_id"]

    # Resolve the user's own section_id (from profile, voice_type, or instrument fallback)
    user_section_id = resolve_member_section_id(user)

    # Admins may specify any section; members are locked to their own
    if user["role"] == "admin":
        section_id = payload.get("section_id") or user_section_id
    else:
        section_id = user_section_id

    if not rehearsal_id or not section_id:
        return {"status": "fail", "message": "rehearsal_id and section_id required"}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT id FROM sub_requests
            WHERE rehearsal_id=%s AND section_id=%s AND status NOT IN ('filled','cancelled')
        """, (rehearsal_id, section_id))
        existing = cur.fetchone()
        if existing:
            return {"status": "ok", "sub_request_id": existing[0], "existing": True}
        cur.execute("""
            INSERT INTO sub_requests (rehearsal_id, section_id, created_by)
            VALUES (%s, %s, %s) RETURNING id
        """, (rehearsal_id, section_id, user["id"]))
        req_id = cur.fetchone()[0]

    return {"status": "success", "sub_request_id": req_id}


@app.get("/choir/sub-request/{sub_request_id}")
def choir_get_sub_request(sub_request_id: int, request: Request):
    require_choir_member(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT sr.id, sr.rehearsal_id, sr.section_id, sr.status,
                   sr.preferred_sent_at, sr.all_sent_at,
                   sr.filled_by_sub_id, s.fullname
            FROM sub_requests sr
            LEFT JOIN subs s ON s.id = sr.filled_by_sub_id
            WHERE sr.id=%s
        """, (sub_request_id,))
        req = cur.fetchone()
        if not req:
            raise HTTPException(status_code=404)

        cur.execute("""
            SELECT sc.id, sc.sub_id, s.fullname, s.is_preferred,
                   sc.tier, sc.contacted_at, sc.response, sc.responded_at
            FROM sub_contacts sc
            JOIN subs s ON s.id = sc.sub_id
            WHERE sc.sub_request_id=%s
            ORDER BY s.is_preferred DESC, s.fullname
        """, (sub_request_id,))
        contacts = [{"id": r[0], "sub_id": r[1], "name": r[2], "is_preferred": r[3],
                     "tier": r[4], "contacted_at": str(r[5]), "response": r[6],
                     "responded_at": str(r[7]) if r[7] else None}
                    for r in cur.fetchall()]

    return {"id": req[0], "rehearsal_id": req[1], "section_id": req[2],
            "status": req[3],
            "preferred_sent_at": str(req[4]) if req[4] else None,
            "all_sent_at": str(req[5]) if req[5] else None,
            "filled_by_name": req[7], "contacts": contacts}


@app.get("/choir/sub-requests/{rehearsal_id}")
def choir_get_sub_requests(rehearsal_id: int, request: Request):
    require_choir_member(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT sr.id, sr.section_id, cs.name, sr.status,
                   sr.preferred_sent_at, sr.all_sent_at, s.fullname
            FROM sub_requests sr
            JOIN choir_sections cs ON cs.id = sr.section_id
            LEFT JOIN subs s ON s.id = sr.filled_by_sub_id
            WHERE sr.rehearsal_id=%s
            ORDER BY cs.sort_order
        """, (rehearsal_id,))
        return [{"id": r[0], "section_id": r[1], "section_name": r[2], "status": r[3],
                 "preferred_sent_at": str(r[4]) if r[4] else None,
                 "all_sent_at": str(r[5]) if r[5] else None,
                 "filled_by_name": r[6]}
                for r in cur.fetchall()]


@app.post("/choir/sub-request/{sub_request_id}/contact-preferred")
def choir_contact_preferred(sub_request_id: int, request: Request):
    require_choir_member(request)
    with db_cursor() as cur:
        cur.execute("SELECT rehearsal_id, section_id, status FROM sub_requests WHERE id=%s",
                    (sub_request_id,))
        req = cur.fetchone()
    if not req:
        raise HTTPException(status_code=404)
    rehearsal_id, section_id, status = req
    if status == "filled":
        return {"status": "fail", "message": "Already filled"}

    _advance_preferred_sub(sub_request_id, rehearsal_id, section_id)
    return {"status": "success"}


@app.post("/choir/sub-request/{sub_request_id}/contact-all")
def choir_contact_all(sub_request_id: int, request: Request):
    require_choir_member(request)
    with db_cursor() as cur:
        cur.execute("SELECT rehearsal_id, section_id, status FROM sub_requests WHERE id=%s",
                    (sub_request_id,))
        req = cur.fetchone()
    if not req:
        raise HTTPException(status_code=404)
    rehearsal_id, section_id, status = req
    if status == "filled":
        return {"status": "fail", "message": "Already filled"}

    with db_cursor() as cur:
        cur.execute("""
            SELECT s.id, s.fullname, s.email FROM subs s
            WHERE s.section_id=%s AND s.active=true
              AND s.id NOT IN (
                SELECT sub_id FROM sub_contacts WHERE sub_request_id=%s
              )
        """, (section_id, sub_request_id))
        remaining = [{"id": r[0], "fullname": r[1], "email": r[2]} for r in cur.fetchall()]

    sent = _send_sub_emails(remaining, sub_request_id, rehearsal_id, section_id, "regular")

    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE sub_requests SET status='all_sent', all_sent_at=NOW() WHERE id=%s
        """, (sub_request_id,))

    return {"status": "success", "sent": sent, "total_remaining": len(remaining)}


def _lesson_rows_to_events(rows) -> list:
    """Convert (id, lesson_date, lesson_time, label) rows to ICS event dicts."""
    events = []
    for lesson_id, lesson_date, lesson_time, label in rows:
        start = datetime.combine(lesson_date, lesson_time)
        events.append({
            "uid": f"lesson-{lesson_id}@countrpnt.com",
            "start": start,
            "end": start + timedelta(minutes=30),
            "summary": f"Coaching – {label}",
            "location": "",
            "description": "",
        })
    return events


def _make_ics(events: list, cal_name: str = "Choir Rehearsals") -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Countrpnt//Choir Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{cal_name}",
        "X-WR-TIMEZONE:America/New_York",
    ]
    for e in events:
        start = e["start"].strftime("%Y%m%dT%H%M%S")
        end_dt = e.get("end")
        end = end_dt.strftime("%Y%m%dT%H%M%S") if end_dt else (e["start"] + timedelta(hours=2)).strftime("%Y%m%dT%H%M%S")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}",
            f"DTSTART;TZID=America/New_York:{start}",
            f"DTEND;TZID=America/New_York:{end}",
            f"SUMMARY:{e.get('summary', 'Choir Rehearsal')}",
            f"LOCATION:{e.get('location', '')}",
            f"DESCRIPTION:{e.get('description', '')}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def _get_or_create_calendar_token(user_id: int) -> str:
    import secrets as _sec
    with db_cursor(commit=True) as cur:
        cur.execute("SELECT calendar_token FROM users WHERE id=%s", (user_id,))
        row = cur.fetchone()
        token = row[0] if row and row[0] else None
        if not token:
            token = _sec.token_urlsafe(32)
            cur.execute("UPDATE users SET calendar_token=%s WHERE id=%s", (token, user_id))
    return token


@app.get("/teacher/my-calendar-token")
def teacher_my_calendar_token(request: Request):
    user = require_user(request, role="teacher")
    return {"token": _get_or_create_calendar_token(user["id"])}


@app.get("/teacher/calendar/{token}.ics")
def teacher_calendar_ics(token: str):
    with db_cursor() as cur:
        cur.execute("SELECT id, org_id FROM users WHERE calendar_token=%s AND role='teacher'", (token,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Calendar not found")
    teacher_id, org_id = row

    with db_cursor() as cur:
        cur.execute("""
            SELECT l.id, l.lesson_date, l.lesson_time, u.fullname
            FROM lessons l
            JOIN users u ON u.id = l.student_id
            WHERE l.teacher_id=%s AND l.status='booked' AND l.lesson_date >= CURRENT_DATE
            ORDER BY l.lesson_date, l.lesson_time
        """, (teacher_id,))
        rows = cur.fetchall()

    events = _lesson_rows_to_events(rows)
    return Response(content=_make_ics(events, "My Coaching Schedule"), media_type="text/calendar")


@app.get("/student/my-calendar-token")
def student_my_calendar_token(request: Request):
    user = require_user(request, role="student")
    return {"token": _get_or_create_calendar_token(user["id"])}


@app.get("/student/calendar/{token}.ics")
def student_calendar_ics(token: str):
    with db_cursor() as cur:
        cur.execute("SELECT id, org_id FROM users WHERE calendar_token=%s AND role='student'", (token,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Calendar not found")
    student_id, org_id = row

    with db_cursor() as cur:
        # Booked lessons
        cur.execute("""
            SELECT l.id, l.lesson_date, l.lesson_time, u.fullname
            FROM lessons l
            JOIN users u ON u.id = l.teacher_id
            WHERE l.student_id=%s AND l.status='booked' AND l.lesson_date >= CURRENT_DATE
            ORDER BY l.lesson_date, l.lesson_time
        """, (student_id,))
        lesson_rows = cur.fetchall()

        # Upcoming rehearsals via student_assignments
        cur.execute("""
            SELECT DISTINCT r.id, r.start_time, r.end_time, o.opera_name, r.location, r.notes
            FROM rehearsals r
            JOIN operas o ON o.id = r.opera_id
            JOIN student_assignments sa ON sa.student_id=%s
                AND sa.opera_id = r.opera_id
                AND (r.cast_id IS NULL OR r.cast_id = sa.cast_id)
            WHERE r.end_time >= NOW()
            ORDER BY r.start_time
        """, (student_id,))
        rehearsal_rows = cur.fetchall()

    events = _lesson_rows_to_events(lesson_rows)
    for rid, rstart, rend, opera_name, location, notes in rehearsal_rows:
        events.append({
            "uid": f"rehearsal-{rid}@countrpnt.com",
            "start": rstart,
            "end": rend or (rstart + timedelta(hours=2)),
            "summary": f"Rehearsal – {opera_name}",
            "location": location or "",
            "description": notes or "",
        })
    events.sort(key=lambda e: e["start"])
    return Response(content=_make_ics(events, "My Schedule"), media_type="text/calendar")


@app.get("/orchestra-member/my-calendar-token")
def orchestra_member_my_calendar_token(request: Request):
    user = require_user(request, role="orchestra_member")
    return {"token": _get_or_create_calendar_token(user["id"])}


@app.get("/orchestra-member/calendar/{token}.ics")
def orchestra_member_calendar_ics(token: str):
    with db_cursor() as cur:
        cur.execute("SELECT id, org_id FROM users WHERE calendar_token=%s AND role='orchestra_member'", (token,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Calendar not found")
    member_id, org_id = row

    with db_cursor() as cur:
        # Booked lessons
        cur.execute("""
            SELECT l.id, l.lesson_date, l.lesson_time, u.fullname
            FROM lessons l
            JOIN users u ON u.id = l.teacher_id
            WHERE l.student_id=%s AND l.status='booked' AND l.lesson_date >= CURRENT_DATE
            ORDER BY l.lesson_date, l.lesson_time
        """, (member_id,))
        lesson_rows = cur.fetchall()

        # Upcoming orchestra rehearsals (all members attend)
        cur.execute("""
            SELECT r.id, r.start_time, r.end_time, o.opera_name, r.location, r.notes
            FROM rehearsals r
            JOIN operas o ON o.id = r.opera_id
            WHERE r.rehearsal_type = 'orchestra'
              AND o.org_id = %s
              AND r.end_time >= NOW()
            ORDER BY r.start_time
        """, (org_id,))
        rehearsal_rows = cur.fetchall()

    events = _lesson_rows_to_events(lesson_rows)
    for rid, rstart, rend, opera_name, location, notes in rehearsal_rows:
        events.append({
            "uid": f"rehearsal-{rid}@countrpnt.com",
            "start": rstart,
            "end": rend or (rstart + timedelta(hours=2)),
            "summary": f"Orchestra Rehearsal – {opera_name}",
            "location": location or "",
            "description": notes or "",
        })
    events.sort(key=lambda e: e["start"])
    return Response(content=_make_ics(events, "My Schedule"), media_type="text/calendar")


@app.get("/choir/my-calendar-token")
def choir_my_calendar_token(request: Request):
    import secrets as _secrets
    user = require_choir_member(request)
    with db_cursor(commit=True) as cur:
        cur.execute("SELECT calendar_token FROM users WHERE id=%s", (user["id"],))
        row = cur.fetchone()
        token = row[0] if row and row[0] else None
        if not token:
            token = _secrets.token_urlsafe(32)
            cur.execute("UPDATE users SET calendar_token=%s WHERE id=%s", (token, user["id"]))
    return {"token": token}


@app.get("/choir/calendar/{token}.ics")
def choir_calendar_ics(token: str):
    with db_cursor() as cur:
        cur.execute("""
            SELECT u.id, u.org_id, u.section_id, u.voice_type, u.instrument
            FROM users u WHERE u.calendar_token=%s
        """, (token,))
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Calendar not found")

    user_id, org_id, section_id, voice_type, instrument = row
    section_id = resolve_member_section_id({
        "id": user_id, "org_id": org_id, "section_id": section_id,
        "voice_type": voice_type, "instrument": instrument,
    })

    with db_cursor() as cur:
        cur.execute("""
            SELECT r.id, r.start_time, r.end_time, r.location, r.notes
            FROM rehearsals r
            WHERE r.org_id=%s AND r.start_time::date >= CURRENT_DATE
            ORDER BY r.start_time
        """, (org_id,))
        rows = cur.fetchall()

        cur.execute("""
            SELECT rehearsal_id FROM absence_requests WHERE singer_id=%s
        """, (user_id,))
        absent_ids = {r[0] for r in cur.fetchall()}

    events = []
    for rid, rstart, rend, location, notes in rows:
        if rid in absent_ids:
            continue
        if section_id:
            with db_cursor() as cur:
                cur.execute("SELECT section_id FROM rehearsal_sections WHERE rehearsal_id=%s", (rid,))
                called = [r[0] for r in cur.fetchall()]
            if called and section_id not in called:
                continue
        events.append({
            "uid": f"rehearsal-{rid}@countrpnt.com",
            "start": rstart,
            "end": rend,
            "summary": "Choir Rehearsal",
            "location": location or "",
            "description": notes or "",
        })

    ics = _make_ics(events)
    return Response(content=ics, media_type="text/calendar")


@app.get("/choir/sub-ics/{token}")
def choir_sub_ics(token: str):
    with db_cursor() as cur:
        cur.execute("""
            SELECT sr.rehearsal_id, sr.section_id
            FROM sub_contacts sc
            JOIN sub_requests sr ON sr.id = sc.sub_request_id
            WHERE sc.token=%s
        """, (token,))
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Not found")

    rehearsal_id, section_id = row

    with db_cursor() as cur:
        cur.execute("""
            SELECT r.start_time, r.end_time, r.location, r.notes, cs.name
            FROM rehearsals r
            JOIN choir_sections cs ON cs.id=%s
            WHERE r.id=%s
        """, (section_id, rehearsal_id))
        reh = cur.fetchone()

    if not reh:
        raise HTTPException(status_code=404, detail="Rehearsal not found")

    rstart, rend, location, notes, section_name = reh
    events = [{
        "uid": f"rehearsal-{rehearsal_id}@countrpnt.com",
        "start": rstart,
        "end": rend,
        "summary": f"Choir Rehearsal – {section_name}",
        "location": location or "",
        "description": notes or "",
    }]
    ics = _make_ics(events, cal_name=f"{section_name} Rehearsal")
    return Response(
        content=ics,
        media_type="text/calendar",
        headers={"Content-Disposition": "attachment; filename=rehearsal.ics"},
    )


@app.post("/choir/cron/escalate-subs")
def choir_escalate_subs(request: Request):
    """Hourly cron with two passes:
      1. Advance preferred-sub cascade when a rank's response window has expired.
      2. 24-hour bulk fallback: email all remaining subs for any still-open request.
    Protect with CRON_SECRET env var; call from an external scheduler (recommend hourly)."""
    import os as _os
    cron_secret = _os.environ.get("CRON_SECRET", "")
    if not cron_secret or request.headers.get("x-cron-secret") != cron_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        return _run_escalate_subs()
    except Exception as e:
        import traceback
        return JSONResponse(status_code=500, content={"error": str(e), "trace": traceback.format_exc()})


def _run_escalate_subs():
    # ── Pass 1: advance stale preferred windows ──────────────────────────────
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, rehearsal_id, section_id FROM sub_requests
            WHERE status = 'preferred_sent'
        """)
        preferred_active = cur.fetchall()

    cascaded = 0
    for req_id, rehearsal_id, section_id in preferred_active:
        with db_cursor() as cur:
            cur.execute("""
                SELECT sc.contacted_at, s.preferred_rank
                FROM sub_contacts sc
                JOIN subs s ON s.id = sc.sub_id
                WHERE sc.sub_request_id = %s AND sc.tier = 'preferred' AND sc.response = 'pending'
                ORDER BY sc.contacted_at DESC
                LIMIT 1
            """, (req_id,))
            latest = cur.fetchone()

        if not latest:
            continue

        contacted_at, rank = latest
        window = _preferred_window_hours(rank or 99)
        if contacted_at.tzinfo is None:
            contacted_at = contacted_at.replace(tzinfo=pytz.utc)
        if datetime.now(pytz.utc) >= contacted_at + timedelta(hours=window):
            _advance_preferred_sub(req_id, rehearsal_id, section_id)
            cascaded += 1

    # ── Pass 2: 24-hour bulk fallback ─────────────────────────────────────────
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, rehearsal_id, section_id FROM sub_requests
            WHERE status NOT IN ('filled', 'cancelled', 'all_sent')
              AND created_at < NOW() - INTERVAL '24 hours'
        """)
        stale = cur.fetchall()

    escalated = 0
    for req_id, rehearsal_id, section_id in stale:
        with db_cursor() as cur:
            cur.execute("""
                SELECT s.id, s.fullname, s.email FROM subs s
                WHERE s.section_id = %s AND s.active = true
                  AND s.id NOT IN (
                    SELECT sub_id FROM sub_contacts WHERE sub_request_id = %s
                  )
            """, (section_id, req_id))
            remaining = [{"id": r[0], "fullname": r[1], "email": r[2]} for r in cur.fetchall()]

        if remaining:
            sent = _send_sub_emails(remaining, req_id, rehearsal_id, section_id, "regular")
            if sent > 0:
                escalated += 1

        with db_cursor(commit=True) as cur:
            cur.execute("""
                UPDATE sub_requests SET status = 'all_sent', all_sent_at = NOW()
                WHERE id = %s AND status NOT IN ('filled', 'cancelled', 'all_sent')
            """, (req_id,))

    return {"status": "ok", "cascaded": cascaded, "escalated": escalated, "checked": len(stale)}



@app.post("/choir/contact-sub")
def choir_contact_one_sub(payload: dict, request: Request):
    """Email a single sub for a rehearsal section. Creates the sub_request if needed."""
    user = require_choir_member(request)
    rehearsal_id = payload.get("rehearsal_id")
    section_id = payload.get("section_id")
    sub_id = payload.get("sub_id")
    custom_message = (payload.get("custom_message") or "").strip() or None
    org_id = user["org_id"]
    if not all([rehearsal_id, section_id, sub_id]):
        return {"status": "fail", "message": "Missing required fields"}

    # Non-admin members may only contact subs for their own section
    if user["role"] != "admin":
        user_section_id = resolve_member_section_id(user)
        if user_section_id and int(section_id) != user_section_id:
            raise HTTPException(status_code=403, detail="Members can only contact subs for their own section")

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT id FROM sub_requests WHERE rehearsal_id=%s AND section_id=%s
        """, (rehearsal_id, section_id))
        row = cur.fetchone()
        if row:
            sub_request_id = row[0]
        else:
            cur.execute("""
                INSERT INTO sub_requests (rehearsal_id, section_id, created_by)
                VALUES (%s, %s, %s) RETURNING id
            """, (rehearsal_id, section_id, user["id"]))
            sub_request_id = cur.fetchone()[0]

    with db_cursor() as cur:
        cur.execute("SELECT id, fullname, email, is_preferred FROM subs WHERE id=%s", (sub_id,))
        row = cur.fetchone()
    if not row:
        return {"status": "fail", "message": "Sub not found"}

    sub = {"id": row[0], "fullname": row[1], "email": row[2]}
    tier = "preferred" if row[3] else "regular"
    sent = _send_sub_emails(
        [sub], sub_request_id, rehearsal_id, section_id, tier, custom_message,
        sender_name=user.get("fullname") or user.get("username"),
        sender_username=user.get("username"),
        sender_email=user.get("email"),
    )
    if sent == 0:
        with db_cursor() as cur:
            cur.execute(
                "SELECT id FROM sub_contacts WHERE sub_request_id=%s AND sub_id=%s",
                (sub_request_id, sub_id),
            )
            if cur.fetchone():
                return {"status": "fail", "message": "Already contacted"}
        return {"status": "fail", "message": "Email failed to send"}
    return {"status": "success"}


@app.post("/choir/contact-preferred-subs")
def choir_contact_preferred_subs(payload: dict, request: Request):
    """Contact the next ranked preferred sub for a section. Creates sub_request if needed.
    Admins may pass section_id directly; members use their own section."""
    user = require_choir_member(request)
    rehearsal_id = payload.get("rehearsal_id")
    org_id = user["org_id"]
    if not rehearsal_id:
        return {"status": "fail", "message": "rehearsal_id required"}

    if user["role"] == "admin" and payload.get("section_id"):
        section_id = payload["section_id"]
    else:
        section_id = resolve_member_section_id(user)
        if not section_id:
            return {"status": "fail", "message": "Could not resolve your section"}

    # If member's absence is still pending approval, store intent — don't email yet
    if user["role"] != "admin":
        with db_cursor() as cur:
            cur.execute("""
                SELECT id FROM absence_requests
                WHERE rehearsal_id = %s AND singer_id = %s AND status = 'pending'
            """, (rehearsal_id, user["id"]))
            pending_row = cur.fetchone()
        if pending_row:
            with db_cursor(commit=True) as cur:
                cur.execute("""
                    UPDATE absence_requests SET contact_preferred_on_approval = TRUE
                    WHERE id = %s
                """, (pending_row[0],))
            return {"status": "success", "pending_approval": True}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT id FROM sub_requests
            WHERE rehearsal_id = %s AND section_id = %s
              AND status NOT IN ('filled', 'cancelled')
        """, (rehearsal_id, section_id))
        row = cur.fetchone()
        if row:
            sub_request_id = row[0]
        else:
            cur.execute("""
                INSERT INTO sub_requests (rehearsal_id, section_id, created_by)
                VALUES (%s, %s, %s) RETURNING id
            """, (rehearsal_id, section_id, user["id"]))
            sub_request_id = cur.fetchone()[0]

    _advance_preferred_sub(sub_request_id, rehearsal_id, section_id)
    return {"status": "success"}


# ========================================================
# CHOIR MEMBER LESSON BOOKING
# ========================================================

@app.get("/choir-member/org-config")
def choir_member_org_config(request: Request):
    """Returns lesson booking config for the choir member's org."""
    user = require_choir_member(request)
    cfg = get_org_lesson_config(user["org_id"])
    return {
        "lessons_enabled": cfg.get("lessons_enabled", False),
        "duration_min": cfg["duration_min"],
        "max_per_day": cfg["max_per_day"],
        "booking_open_hour": cfg["booking_open_hour"],
        "booking_close_hour": cfg["booking_close_hour"],
        "cancellation_notice_min": cfg["cancellation_notice_min"],
    }


@app.get("/choir-member/today")
def choir_member_today_booking(request: Request):
    """Booking dashboard view for choir members with lessons_enabled."""
    user = require_choir_member(request)
    org_tz = get_org_tz(user)
    cfg = get_org_lesson_config(user["org_id"])

    if not cfg.get("lessons_enabled"):
        return {"lessons_enabled": False}

    now_local = datetime.now(org_tz)
    target_date = get_bookable_date(org_tz, close_hour=cfg["booking_close_hour"], open_hour=cfg["booking_open_hour"])
    booking_open = is_booking_window_open_for(target_date, org_tz, open_hour=cfg["booking_open_hour"], close_hour=cfg["booking_close_hour"])
    booking_pending = not booking_open  # target date is set but window hasn't opened yet tonight

    org_id = user["org_id"]
    teachers = []
    if booking_open:
        with db_cursor() as cur:
            cur.execute("""
                SELECT id, fullname, teacher_type
                FROM users
                WHERE org_id = %s AND role = 'teacher'
                ORDER BY fullname
            """, (org_id,))
            teacher_rows = cur.fetchall()

        avail_ctx = get_teacher_availability_context(target_date)

        for t_id, t_name, t_type in teacher_rows:
            t_label = "Coaching" if (t_type or "vocal") == "instrumental" else "Voice"

            if t_id in avail_ctx["has_any_exception_by_teacher"]:
                is_working = bool(avail_ctx["exceptions_by_teacher"].get(t_id))
            else:
                is_working = bool(avail_ctx["weekly_by_teacher"].get(t_id))

            if not is_working:
                teachers.append({"id": t_id, "name": t_name, "label": t_label, "morning": 0, "afternoon": 0, "status": "not_working"})
                continue

            slots = get_available_slots(t_id, target_date, avail_ctx=avail_ctx, tz=org_tz,
                                        duration_min=cfg["duration_min"], has_lunch_break=cfg["has_lunch_break"])
            morning = sum(1 for s in slots if classify_slot_time(s) == "morning")
            afternoon = sum(1 for s in slots if classify_slot_time(s) == "afternoon")

            if morning + afternoon == 0:
                teachers.append({"id": t_id, "name": t_name, "label": t_label, "morning": 0, "afternoon": 0, "status": "all_booked"})
            else:
                teachers.append({"id": t_id, "name": t_name, "label": t_label, "morning": morning, "afternoon": afternoon, "status": "available"})

    return {
        "lessons_enabled": True,
        "date": target_date.isoformat(),
        "booking_open": booking_open,
        "booking_pending": booking_pending,
        "teachers": teachers,
        "duration_options": cfg["duration_options"],
    }


@app.get("/choir-member/teacher-slots")
def choir_member_teacher_slots(request: Request, teacher: int, period: str, duration: int = 0):
    """Available lesson slots for a teacher — choir member view."""
    if period not in ("morning", "afternoon"):
        return []

    user = require_choir_member(request)
    org_tz = get_org_tz(user)
    cfg = get_org_lesson_config(user["org_id"])

    if not cfg.get("lessons_enabled"):
        return []

    slot_duration = duration if duration in cfg["duration_options"] else cfg["duration_min"]

    target_date = get_bookable_date(org_tz, close_hour=cfg["booking_close_hour"], open_hour=cfg["booking_open_hour"])
    if not is_booking_window_open_for(target_date, org_tz, open_hour=cfg["booking_open_hour"], close_hour=cfg["booking_close_hour"]):
        return []

    all_slots = get_available_slots(teacher, target_date, tz=org_tz,
                                    duration_min=slot_duration, has_lunch_break=cfg["has_lunch_break"])
    return [s for s in all_slots if classify_slot_time(s) == period]


@app.post("/choir-member/book")
def choir_member_book(payload: dict, request: Request):
    """Book a lesson with a teacher — choir member."""
    user = require_choir_member(request)
    member_id = user["id"]

    date_str = payload.get("date")
    teacher_id = payload.get("teacher_id")
    time_str = payload.get("time")
    duration_req = payload.get("duration", 0)

    if not (date_str and teacher_id and time_str):
        return {"status": "fail", "message": "Missing required fields"}

    try:
        teacher_id = int(teacher_id)
    except (TypeError, ValueError):
        return {"status": "fail", "message": "Invalid teacher"}

    try:
        lesson_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        lesson_time = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        return {"status": "fail", "message": "Invalid date or time format"}

    org_tz = get_org_tz(user)
    cfg = get_org_lesson_config(user["org_id"])

    slot_duration = int(duration_req) if int(duration_req or 0) in cfg["duration_options"] else cfg["duration_min"]

    if not cfg.get("lessons_enabled"):
        return {"status": "fail", "message": "Lesson booking is not enabled for your organization."}

    if lesson_date != get_bookable_date(org_tz, close_hour=cfg["booking_close_hour"], open_hour=cfg["booking_open_hour"]):
        return {"status": "fail", "message": "Lessons can only be booked for the current bookable day"}

    if not is_booking_window_open_for(lesson_date, org_tz, open_hour=cfg["booking_open_hour"], close_hour=cfg["booking_close_hour"]):
        return {"status": "fail", "message": f"Booking is closed. Booking opens at {cfg['booking_open_hour']}:00 the evening before and closes at {cfg['booking_close_hour']}:00 on the day of your lesson."}

    slot_dt = org_tz.localize(datetime.combine(lesson_date, lesson_time))
    if slot_dt <= datetime.now(org_tz):
        return {"status": "fail", "message": "Cannot book past times"}

    if cfg["has_lunch_break"]:
        slot_end_t = (slot_dt + timedelta(minutes=slot_duration)).time()
        if lesson_time < LUNCH_END and slot_end_t > LUNCH_START:
            return {"status": "fail", "message": "This slot overlaps the lunch break"}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT COUNT(*) FROM lessons
            WHERE student_id=%s AND lesson_date=%s AND status='booked'
        """, (member_id, lesson_date))
        if cur.fetchone()[0] >= cfg["max_per_day"]:
            return {"status": "fail", "message": "You have reached the maximum lessons for that day"}

        cur.execute("""
            SELECT COUNT(*) FROM lessons
            WHERE student_id=%s AND teacher_id=%s AND status='booked'
        """, (member_id, teacher_id))
        if cur.fetchone()[0] >= cfg["max_per_teacher"]:
            return {"status": "fail", "message": "Maximum lessons with this teacher reached"}

        try:
            cur.execute("""
                INSERT INTO lessons (teacher_id, student_id, lesson_date, lesson_time, duration_min)
                VALUES (%s, %s, %s, %s, %s)
            """, (teacher_id, member_id, lesson_date, lesson_time, slot_duration))
        except pg_errors.UniqueViolation:
            return {"status": "fail", "message": "That slot was just taken. Please refresh and try another."}
        except Exception as e:
            print("CHOIR BOOK ERROR:", e)
            return {"status": "fail", "message": "Booking failed. Please try again."}

    return {"status": "success", "message": "Lesson booked!"}


@app.get("/choir-member/lessons")
def choir_member_lessons(request: Request):
    """Upcoming and past booked lessons for a choir member."""
    user = require_choir_member(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT l.id, l.lesson_date, l.lesson_time, l.status,
                   u.fullname AS teacher_name, l.teacher_id
            FROM lessons l
            JOIN users u ON u.id = l.teacher_id
            WHERE l.student_id = %s
            ORDER BY l.lesson_date DESC, l.lesson_time DESC
        """, (user["id"],))
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "date": r[1].isoformat() if r[1] else None,
            "time": r[2].strftime("%H:%M") if r[2] else None,
            "status": r[3],
            "teacher": r[4],
            "teacher_id": r[5],
        }
        for r in rows
    ]


@app.post("/choir-member/cancel-lesson")
def choir_member_cancel_lesson(payload: dict, request: Request):
    """Cancel a lesson booking — choir member."""
    user = require_choir_member(request)
    lesson_id = payload.get("lesson_id")
    if not lesson_id:
        return {"status": "fail", "message": "Missing lesson_id"}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT student_id, lesson_date, lesson_time, status
            FROM lessons WHERE id = %s
        """, (lesson_id,))
        row = cur.fetchone()
        if not row:
            return {"status": "fail", "message": "Lesson not found"}

        if row[0] != user["id"]:
            return {"status": "fail", "message": "Not your lesson"}

        if row[3] == "cancelled":
            return {"status": "fail", "message": "Already cancelled"}

        lesson_date, lesson_time = row[1], row[2]
        user_tz = get_org_tz(user)
        cfg = get_org_lesson_config(user["org_id"])
        notice_min = cfg["cancellation_notice_min"]
        lesson_dt = user_tz.localize(datetime.combine(lesson_date, lesson_time))
        cutoff = lesson_dt - timedelta(minutes=notice_min)
        if datetime.now(user_tz) >= cutoff:
            hours = notice_min // 60
            mins = notice_min % 60
            notice_str = f"{hours}h {mins}m" if mins else f"{hours}h"
            return {"status": "fail", "message": f"Too close to lesson time to cancel (need {notice_str} notice)"}

        cur.execute("""
            UPDATE lessons SET status='cancelled', cancelled_at=NOW()
            WHERE id = %s
        """, (lesson_id,))

    return {"status": "success"}


# ========================================================
# ENSEMBLE MEMBER APP
# ========================================================

def require_ensemble_member(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    if user.get("org_type") != "choir":
        raise HTTPException(status_code=403, detail="Choir org required")
    if user.get("role") != "ensemble_member":
        raise HTTPException(status_code=403, detail="Ensemble member required")
    return user


@app.get("/ensemble/member", response_class=HTMLResponse)
def ensemble_member_page(request: Request):
    return templates.TemplateResponse(request, "choir/ensemble_member.html")


@app.get("/ensemble/me")
def ensemble_me(request: Request):
    user = require_ensemble_member(request)
    return {
        "id": user["id"],
        "fullname": user.get("fullname", ""),
        "instrument": user.get("instrument", ""),
        "username": user.get("username", ""),
        "theme": user.get("theme", "queen-of-the-night"),
    }


@app.get("/ensemble/rehearsals")
def ensemble_rehearsals(request: Request):
    user = require_ensemble_member(request)
    org_id = user["org_id"]
    user_id = user["id"]
    today = datetime.now(EST).date()
    with db_cursor() as cur:
        cur.execute("""
            SELECT DISTINCT r.id, r.start_time, r.end_time, r.location, r.notes,
                   r.choir_type, r.materials_url
            FROM rehearsals r
            LEFT JOIN rehearsal_members rm ON rm.rehearsal_id = r.id AND rm.user_id = %s
            WHERE r.org_id = %s
              AND r.choir_type = 'ensemble'
              AND r.start_time >= %s
              AND (
                  (SELECT COUNT(*) FROM rehearsal_members rm2 WHERE rm2.rehearsal_id = r.id) = 0
                  OR rm.user_id IS NOT NULL
              )
            ORDER BY r.start_time
        """, (user_id, org_id, today))
        rows = cur.fetchall()
    result = []
    for row in rows:
        rid, start, end, location, notes, choir_type, mat_url = row
        result.append({
            "id": rid,
            "date": start.strftime("%Y-%m-%d"),
            "start_time": start.strftime("%H:%M"),
            "end_time": end.strftime("%H:%M") if end else None,
            "location": location or "",
            "notes": notes or "",
            "materials_url": mat_url or "",
        })
    return result


@app.get("/ensemble/absences")
def ensemble_absences(request: Request):
    user = require_ensemble_member(request)
    with db_cursor() as cur:
        cur.execute(
            "SELECT rehearsal_id, status FROM absence_requests WHERE singer_id = %s",
            (user["id"],)
        )
        rows = cur.fetchall()
    return [{"rehearsal_id": r[0], "status": r[1]} for r in rows]


@app.post("/ensemble/absence")
def ensemble_mark_absent(payload: dict, request: Request):
    user = require_ensemble_member(request)
    rehearsal_id = payload.get("rehearsal_id")
    reason = (payload.get("reason") or "").strip()
    note = (payload.get("note") or "").strip() or None
    if not rehearsal_id:
        return {"status": "fail", "message": "rehearsal_id required"}
    if not reason:
        return {"status": "fail", "message": "reason required"}
    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO absence_requests (rehearsal_id, singer_id, reason, note, status)
            VALUES (%s, %s, %s, %s, 'pending')
            ON CONFLICT (rehearsal_id, singer_id) DO UPDATE
              SET reason = EXCLUDED.reason, note = EXCLUDED.note, status = 'pending', contact_preferred_on_approval = FALSE
        """, (rehearsal_id, user["id"], reason, note))
    return {"status": "success"}


@app.delete("/ensemble/absence/{rehearsal_id}")
def ensemble_undo_absent(rehearsal_id: int, request: Request):
    user = require_ensemble_member(request)
    with db_cursor(commit=True) as cur:
        cur.execute(
            "DELETE FROM absence_requests WHERE rehearsal_id = %s AND singer_id = %s",
            (rehearsal_id, user["id"])
        )
    return {"status": "success"}


@app.get("/ensemble/members")
def list_ensemble_members(request: Request):
    """Choir admin: list all ensemble members in the org."""
    user = require_choir_admin(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, fullname, instrument
            FROM users
            WHERE org_id = %s AND role = 'ensemble_member'
            ORDER BY fullname
        """, (user["org_id"],))
        rows = cur.fetchall()
    return [{"id": r[0], "fullname": r[1], "instrument": r[2] or ""} for r in rows]


@app.get("/choir/members/all")
def list_all_choir_members(request: Request):
    """Choir admin: list choir members + ensemble members for individual calling."""
    user = require_choir_admin(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT u.id, u.fullname, u.role, u.voice_type, u.instrument, cs.name AS section_name, cs.id AS section_id
            FROM users u
            LEFT JOIN choir_sections cs ON cs.id = u.section_id
            WHERE u.org_id = %s AND u.role IN ('student', 'choir_member', 'ensemble_member')
            ORDER BY cs.sort_order NULLS LAST, u.fullname
        """, (user["org_id"],))
        rows = cur.fetchall()
    result = []
    for uid, fullname, role, voice_type, instrument, section_name, section_id in rows:
        # Normalize legacy 'student' role to 'choir_member' for the frontend
        normalized_role = "choir_member" if role in ("student", "choir_member") else role
        # Choir members may store voice type as section name when no section_id set
        display_section = section_name or (voice_type.capitalize() if voice_type else "")
        result.append({
            "id": uid,
            "fullname": fullname,
            "role": normalized_role,
            "section_name": display_section,
            "section_id": section_id,
            "instrument": instrument or "",
        })
    return result


# ========================================================
# STAFF MESSAGING
# ========================================================

def render_staff_message_email(sender_name, body, scope_label, reply_url, recipient_name=""):
    greeting = f"Hi {recipient_name}," if recipient_name else "Hi there,"
    html = f"""<!DOCTYPE html>
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 560px; margin: 40px auto; padding: 24px; color: #222;">
    <h2 style="color: #444;">New message from {sender_name}</h2>
    <p>{greeting}</p>
    <p style="color: #666; font-size: 13px; margin-bottom: 4px;">{scope_label}</p>
    <blockquote style="border-left: 3px solid #6b5b3e; margin: 16px 0; padding: 12px 16px; background: #faf8f5; color: #333; font-size: 15px; white-space: pre-wrap;">{body}</blockquote>
    <p style="margin: 32px 0;">
        <a href="{reply_url}" style="background: #6b5b3e; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: 500;">Reply in App</a>
    </p>
    <p style="color: #999; font-size: 13px;">You received this because you were addressed directly in CountrPnt.</p>
</body>
</html>"""
    text = f"{greeting}\n\n{sender_name} sent you a message ({scope_label}):\n\n{body}\n\nReply in the app: {reply_url}\n"
    return html, text


@app.get("/admin/messages/staff")
def get_message_staff(request: Request):
    """Return all admins in the org for the recipient picker."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, fullname, admin_role, role
            FROM users
            WHERE org_id = %s AND role IN ('admin', 'orchestra_admin', 'head_admin')
            AND id != %s
            ORDER BY fullname
        """, (org_id, user["id"]))
        rows = cur.fetchall()
    return [{"id": r[0], "fullname": r[1], "admin_role": r[2], "role": r[3]} for r in rows]


@app.get("/admin/messages")
def get_admin_messages(request: Request, scope: str = "org"):
    """Return messages for a board scope ('org' or 'opera_123')."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    uid = user["id"]

    opera_id = None
    if scope.startswith("opera_"):
        try:
            opera_id = int(scope[6:])
        except ValueError:
            return {"status": "fail", "message": "Invalid scope"}

    with db_cursor(commit=True) as cur:
        if opera_id is not None:
            cur.execute("""
                SELECT m.id, m.sender_id, u.fullname, m.body, m.created_at, m.opera_id, o.title
                FROM staff_messages m
                JOIN users u ON u.id = m.sender_id
                LEFT JOIN operas o ON o.id = m.opera_id
                WHERE m.org_id = %s AND m.opera_id = %s
                ORDER BY m.created_at DESC LIMIT 100
            """, (org_id, opera_id))
        else:
            cur.execute("""
                SELECT m.id, m.sender_id, u.fullname, m.body, m.created_at, m.opera_id, NULL
                FROM staff_messages m
                JOIN users u ON u.id = m.sender_id
                WHERE m.org_id = %s AND m.opera_id IS NULL
                ORDER BY m.created_at DESC LIMIT 100
            """, (org_id,))
        rows = cur.fetchall()

        msg_ids = [r[0] for r in rows]
        recipients_by_msg = {}
        if msg_ids:
            cur.execute("""
                SELECT smr.message_id, u.fullname
                FROM staff_message_recipients smr
                JOIN users u ON u.id = smr.user_id
                WHERE smr.message_id = ANY(%s)
            """, (msg_ids,))
            for mid, name in cur.fetchall():
                recipients_by_msg.setdefault(mid, []).append(name)

        cur.execute("""
            INSERT INTO staff_board_views (user_id, scope, last_viewed_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (user_id, scope) DO UPDATE SET last_viewed_at = NOW()
        """, (uid, scope))

    return [
        {
            "id": r[0], "sender_id": r[1], "sender_name": r[2],
            "body": r[3], "created_at": r[4].isoformat() if r[4] else None,
            "opera_id": r[5], "opera_title": r[6],
            "recipients": recipients_by_msg.get(r[0], []),
        }
        for r in rows
    ]


@app.post("/admin/messages")
def send_staff_message(payload: dict, request: Request):
    """Post a board message or send a directed message to specific admins."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    uid = user["id"]

    body = (payload.get("body") or "").strip()
    if not body:
        return {"status": "fail", "message": "Message cannot be empty"}

    scope = payload.get("scope", "org")
    opera_id = None
    if scope.startswith("opera_"):
        try:
            opera_id = int(scope[6:])
        except ValueError:
            return {"status": "fail", "message": "Invalid scope"}

    recipient_ids = [int(x) for x in (payload.get("recipient_ids") or [])]

    with db_cursor(commit=True) as cur:
        scope_label = "Org-wide board"
        if opera_id is not None:
            cur.execute("SELECT title FROM operas WHERE id=%s AND org_id=%s", (opera_id, org_id))
            row = cur.fetchone()
            if not row:
                return {"status": "fail", "message": "Production not found"}
            scope_label = f"Production: {row[0]}"

        recipients = []
        if recipient_ids:
            cur.execute("""
                SELECT id, fullname, email FROM users
                WHERE id = ANY(%s) AND org_id = %s
                AND role IN ('admin', 'orchestra_admin', 'head_admin')
            """, (recipient_ids, org_id))
            recipients = cur.fetchall()

        cur.execute("""
            INSERT INTO staff_messages (org_id, opera_id, sender_id, body)
            VALUES (%s, %s, %s, %s) RETURNING id
        """, (org_id, opera_id, uid, body))
        message_id = cur.fetchone()[0]

        for rid, _, _ in recipients:
            cur.execute("""
                INSERT INTO staff_message_recipients (message_id, user_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
            """, (message_id, rid))

    if recipients:
        reply_url = f"{APP_URL}/admin#messages"
        sender_name = user.get("fullname") or "A staff member"
        for _, rec_name, rec_email in recipients:
            if not rec_email:
                continue
            html, text = render_staff_message_email(sender_name, body, scope_label, reply_url, rec_name)
            send_email(
                rec_email,
                f"New message from {sender_name} — CountrPnt",
                html, text,
                from_name=sender_name,
                from_address=_sender_from_username(user["username"]),
                reply_to=user.get("email"),
            )

    return {"status": "success", "message_id": message_id}


@app.get("/admin/messages/unread")
def get_message_unread(request: Request):
    """Return unread message counts per scope."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    uid = user["id"]

    with db_cursor() as cur:
        cur.execute("SELECT scope, last_viewed_at FROM staff_board_views WHERE user_id = %s", (uid,))
        views = {row[0]: row[1] for row in cur.fetchall()}

        org_last = views.get("org")
        if org_last:
            cur.execute("""
                SELECT COUNT(*) FROM staff_messages
                WHERE org_id=%s AND opera_id IS NULL AND created_at > %s
            """, (org_id, org_last))
        else:
            cur.execute("SELECT COUNT(*) FROM staff_messages WHERE org_id=%s AND opera_id IS NULL", (org_id,))
        org_unread = cur.fetchone()[0]

        cur.execute("SELECT id, title FROM operas WHERE org_id=%s ORDER BY title", (org_id,))
        productions = cur.fetchall()

        prod_unread = {}
        for pid, _ in productions:
            scope_key = f"opera_{pid}"
            prod_last = views.get(scope_key)
            if prod_last:
                cur.execute("""
                    SELECT COUNT(*) FROM staff_messages
                    WHERE opera_id=%s AND created_at > %s
                """, (pid, prod_last))
            else:
                cur.execute("SELECT COUNT(*) FROM staff_messages WHERE opera_id=%s", (pid,))
            prod_unread[str(pid)] = cur.fetchone()[0]

    total = org_unread + sum(prod_unread.values())
    return {"org": org_unread, "productions": prod_unread, "total": total}


# ========================================================
# DIRECT MESSAGES (DM) — all roles
# ========================================================

def _render_dm_email(sender_name: str, body: str, recipient_name: str, app_url: str):
    safe_body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    html = f"""<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px;">
<p style="margin:0 0 16px;font-size:15px;color:#222;">Hi {recipient_name},</p>
<p style="margin:0 0 16px;font-size:15px;color:#222;">
  <strong>{sender_name}</strong> sent you a message via CountrPnt:
</p>
<div style="background:#f5f5f0;border-left:4px solid #b8860b;padding:16px;margin:0 0 16px;border-radius:4px;">
  <p style="margin:0;font-size:15px;color:#222;">{safe_body}</p>
</div>
<p style="margin:0;font-size:14px;color:#666;">
  Reply to this email to respond, or
  <a href="{app_url}" style="color:#b8860b;">log in to CountrPnt</a>.
</p>
</div>"""
    text = f"Hi {recipient_name},\n\n{sender_name} sent you a message via CountrPnt:\n\n{body}\n\nReply to this email to respond.\n"
    return html, text


@app.get("/dm")
def get_dm(request: Request):
    """Return inbox and sent messages for the current user."""
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    uid = user["id"]
    org_id = user["org_id"]

    with db_cursor() as cur:
        cur.execute("""
            SELECT m.id, m.sender_id, u.fullname AS sender_name, m.body,
                   m.scope, m.created_at, mr.read_at
            FROM messages m
            JOIN message_recipients mr ON mr.message_id = m.id AND mr.user_id = %s
            JOIN users u ON u.id = m.sender_id
            WHERE m.org_id = %s AND m.sender_id != %s
            ORDER BY m.created_at DESC
            LIMIT 100
        """, (uid, org_id, uid))
        inbox = [
            {
                "id": r[0], "sender_id": r[1], "sender_name": r[2],
                "body": r[3], "scope": r[4],
                "created_at": r[5].isoformat() if r[5] else None,
                "read_at": r[6].isoformat() if r[6] else None,
            }
            for r in cur.fetchall()
        ]

        cur.execute("""
            SELECT m.id, m.body, m.scope, m.created_at,
                   ARRAY_AGG(u.fullname ORDER BY u.fullname) FILTER (WHERE u.fullname IS NOT NULL) AS recipient_names,
                   m.external_recipient_names
            FROM messages m
            LEFT JOIN message_recipients mr ON mr.message_id = m.id
            LEFT JOIN users u ON u.id = mr.user_id
            WHERE m.sender_id = %s
            GROUP BY m.id
            ORDER BY m.created_at DESC
            LIMIT 50
        """, (uid,))
        sent = []
        for r in cur.fetchall():
            names = list(r[4] or [])
            if r[5]:
                names += [n.strip() for n in r[5].split(",")]
            sent.append({
                "id": r[0], "body": r[1], "scope": r[2],
                "created_at": r[3].isoformat() if r[3] else None,
                "recipients": names,
            })

    return {"inbox": inbox, "sent": sent}


@app.post("/dm/{message_id}/read")
def mark_dm_read(message_id: int, request: Request):
    """Mark a received message as read."""
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE message_recipients SET read_at = NOW()
            WHERE message_id = %s AND user_id = %s AND read_at IS NULL
        """, (message_id, user["id"]))
    return {"status": "ok"}


@app.get("/dm/unread")
def get_dm_unread(request: Request):
    """Return count of unread DMs for current user."""
    user = current_user(request)
    if not user:
        return {"count": 0}
    with db_cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM message_recipients mr
            JOIN messages m ON m.id = mr.message_id
            WHERE mr.user_id = %s AND mr.read_at IS NULL AND m.sender_id != %s
        """, (user["id"], user["id"]))
        count = cur.fetchone()[0]
    return {"count": count}


@app.get("/dm/contacts")
def get_dm_contacts(request: Request):
    """Return available message recipients for the current user's role."""
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    role = user["role"]
    org_id = user["org_id"]
    uid = user["id"]
    org_type = user.get("org_type", "opera")

    contacts = []
    with db_cursor() as cur:
        if role in ("head_admin", "system_admin", "admin", "orchestra_admin"):
            if org_type == "choir":
                cur.execute("""
                    SELECT id, fullname, role FROM users
                    WHERE org_id = %s AND id != %s
                      AND role IN ('choir_member', 'ensemble_member', 'admin')
                    ORDER BY role, fullname
                """, (org_id, uid))
            else:
                cur.execute("""
                    SELECT id, fullname, role FROM users
                    WHERE org_id = %s AND id != %s AND role NOT IN ('system_admin')
                    ORDER BY role, fullname
                """, (org_id, uid))
            contacts = [{"id": r[0], "fullname": r[1], "role": r[2], "group": "Members"} for r in cur.fetchall()]

            if org_type == "orchestra":
                # Standalone orchestra orgs keep their roster in orchestra_members,
                # which has no linked users row — surface them as roster contacts
                # (messages to them are sent as direct email, not an inbox DM).
                cur.execute("""
                    SELECT id, fullname FROM orchestra_members
                    WHERE org_id = %s AND active = true AND email IS NOT NULL
                    ORDER BY fullname
                """, (org_id,))
                contacts += [
                    {"id": f"om:{r[0]}", "fullname": r[1], "role": "orchestra_member", "group": "Roster"}
                    for r in cur.fetchall()
                ]

        elif role in ("teacher", "studio_teacher"):
            cur.execute("""
                SELECT DISTINCT u.id, u.fullname, u.role FROM users u
                JOIN lessons l ON l.student_id = u.id
                WHERE l.teacher_id = %s AND l.status = 'booked'
                ORDER BY u.fullname
            """, (uid,))
            contacts = [{"id": r[0], "fullname": r[1], "role": r[2], "group": "Students"} for r in cur.fetchall()]

        elif role in ("student", "orchestra_member"):
            cur.execute("""
                SELECT DISTINCT u.id, u.fullname, u.role FROM users u
                JOIN lessons l ON l.teacher_id = u.id
                WHERE l.student_id = %s AND l.status = 'booked'
                ORDER BY u.fullname
            """, (uid,))
            teachers = [{"id": r[0], "fullname": r[1], "role": r[2], "group": "Teachers"} for r in cur.fetchall()]
            cur.execute("""
                SELECT id, fullname, role FROM users
                WHERE org_id = %s AND role IN ('admin', 'orchestra_admin') AND id != %s
                ORDER BY fullname
            """, (org_id, uid))
            admins = [{"id": r[0], "fullname": r[1], "role": r[2], "group": "Production Staff"} for r in cur.fetchall()]
            contacts = teachers + admins

        elif role in ("choir_member", "ensemble_member"):
            cur.execute("""
                SELECT id, fullname, role FROM users
                WHERE org_id = %s AND role = 'admin' AND id != %s
                ORDER BY fullname
            """, (org_id, uid))
            contacts = [{"id": r[0], "fullname": r[1], "role": r[2], "group": "Choir Admin"} for r in cur.fetchall()]

    return contacts


@app.post("/dm")
def send_dm(payload: dict, request: Request):
    """Send a message to one or more users."""
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")

    body = (payload.get("body") or "").strip()
    if not body:
        return {"status": "fail", "message": "Message body is required"}

    scope = (payload.get("scope") or "direct").strip()
    recipient_ids_raw = payload.get("recipient_ids") or []
    user_recipient_ids, roster_recipient_ids = [], []
    for x in recipient_ids_raw:
        if isinstance(x, str) and x.startswith("om:"):
            roster_recipient_ids.append(int(x[3:]))
        else:
            user_recipient_ids.append(int(x))
    org_id = user["org_id"]
    uid = user["id"]
    role = user["role"]
    org_type = user.get("org_type", "opera")

    resolved = []
    roster_resolved = []
    with db_cursor() as cur:
        if scope == "direct":
            if not user_recipient_ids and not roster_recipient_ids:
                return {"status": "fail", "message": "No recipients selected"}
            if user_recipient_ids:
                cur.execute(
                    "SELECT id FROM users WHERE id = ANY(%s) AND org_id = %s AND id != %s",
                    (user_recipient_ids, org_id, uid)
                )
                resolved = [r[0] for r in cur.fetchall()]
            if roster_recipient_ids:
                cur.execute(
                    "SELECT id FROM orchestra_members WHERE id = ANY(%s) AND org_id = %s AND active = true",
                    (roster_recipient_ids, org_id)
                )
                roster_resolved = [r[0] for r in cur.fetchall()]

        elif scope == "org":
            if role not in ("head_admin", "system_admin", "orchestra_admin") and not (org_type == "choir" and role == "admin"):
                return {"status": "fail", "message": "Not authorized for org-wide messages"}
            cur.execute(
                "SELECT id FROM users WHERE org_id = %s AND id != %s AND role NOT IN ('system_admin')",
                (org_id, uid)
            )
            resolved = [r[0] for r in cur.fetchall()]
            if org_type == "orchestra":
                cur.execute(
                    "SELECT id FROM orchestra_members WHERE org_id = %s AND active = true AND email IS NOT NULL",
                    (org_id,)
                )
                roster_resolved = [r[0] for r in cur.fetchall()]

        elif scope == "choir":
            if not (org_type == "choir" and role == "admin"):
                return {"status": "fail", "message": "Not authorized"}
            cur.execute("SELECT id FROM users WHERE org_id = %s AND role = 'choir_member'", (org_id,))
            resolved = [r[0] for r in cur.fetchall()]

        elif scope == "ensemble":
            if not (org_type == "choir" and role == "admin"):
                return {"status": "fail", "message": "Not authorized"}
            cur.execute("SELECT id FROM users WHERE org_id = %s AND role = 'ensemble_member'", (org_id,))
            resolved = [r[0] for r in cur.fetchall()]

        elif scope == "studio_today":
            if role not in ("teacher", "studio_teacher"):
                return {"status": "fail", "message": "Not authorized"}
            from datetime import date as _date
            today = _date.today()
            cur.execute(
                "SELECT DISTINCT student_id FROM lessons WHERE teacher_id=%s AND lesson_date=%s AND status='booked'",
                (uid, today)
            )
            resolved = [r[0] for r in cur.fetchall()]

        elif scope == "studio_week":
            if role not in ("teacher", "studio_teacher"):
                return {"status": "fail", "message": "Not authorized"}
            from datetime import date as _date, timedelta
            today = _date.today()
            week_start = today - timedelta(days=today.weekday())
            week_end = week_start + timedelta(days=6)
            cur.execute("""
                SELECT DISTINCT student_id FROM lessons
                WHERE teacher_id=%s AND lesson_date BETWEEN %s AND %s AND status='booked'
            """, (uid, week_start, week_end))
            resolved = [r[0] for r in cur.fetchall()]

        elif scope == "studio_all":
            if role not in ("teacher", "studio_teacher"):
                return {"status": "fail", "message": "Not authorized"}
            cur.execute(
                "SELECT DISTINCT student_id FROM lessons WHERE teacher_id=%s AND status='booked'",
                (uid,)
            )
            resolved = [r[0] for r in cur.fetchall()]

        else:
            return {"status": "fail", "message": "Invalid scope"}

    if not resolved and not roster_resolved:
        return {"status": "fail", "message": "No recipients found for this scope"}

    with db_cursor() as cur:
        roster_data = []
        if roster_resolved:
            cur.execute("SELECT id, fullname, email FROM orchestra_members WHERE id = ANY(%s)", (roster_resolved,))
            roster_data = cur.fetchall()

    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO messages (org_id, sender_id, body, scope, external_recipient_names)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
        """, (org_id, uid, body, scope, ", ".join(r[1] for r in roster_data) or None))
        message_id = cur.fetchone()[0]
        for rid in resolved:
            cur.execute("""
                INSERT INTO message_recipients (message_id, user_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
            """, (message_id, rid))

    with db_cursor() as cur:
        cur.execute("SELECT id, fullname, email FROM users WHERE id = ANY(%s)", (resolved,))
        recipients_data = cur.fetchall()

    sender_name = user.get("fullname") or user.get("username") or "CountrPnt"
    sender_addr = _sender_from_username(user["username"])
    reply_email = user.get("email")

    for _, rname, remail in recipients_data + roster_data:
        if not remail:
            continue
        html, text = _render_dm_email(sender_name, body, rname, APP_URL)
        send_email(
            remail,
            f"Message from {sender_name} — CountrPnt",
            html, text,
            from_name=sender_name,
            from_address=sender_addr,
            reply_to=reply_email,
        )

    return {"status": "success", "sent_to": len(resolved) + len(roster_resolved)}


# ========================================================
# STUDIO TEACHER
# ========================================================

@app.get("/studio-teacher/settings")
def studio_teacher_settings_get(request: Request):
    teacher = require_studio_teacher(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT payment_zelle, payment_venmo, payment_cashapp, payment_paypal,
                   lesson_rates, cancel_hours, cancel_charge, free_cancels_per_student,
                   packages_enabled, package_size
            FROM studio_teacher_settings WHERE teacher_id = %s
        """, (teacher["id"],))
        row = cur.fetchone()
    if not row:
        return {"payment_zelle": None, "payment_venmo": None,
                "payment_cashapp": None, "payment_paypal": None,
                "lesson_rates": [], "cancel_hours": None, "cancel_charge": False,
                "free_cancels_per_student": 0, "packages_enabled": False, "package_size": 4}
    return {
        "payment_zelle":              row[0],
        "payment_venmo":              row[1],
        "payment_cashapp":            row[2],
        "payment_paypal":             row[3],
        "lesson_rates":               row[4] or [],
        "cancel_hours":               row[5],
        "cancel_charge":              row[6],
        "free_cancels_per_student":   row[7] or 0,
        "packages_enabled":           row[8] or False,
        "package_size":               row[9] or 4,
    }


@app.patch("/studio-teacher/settings")
def studio_teacher_settings_update(payload: dict, request: Request):
    teacher = require_studio_teacher(request)
    import json as _json
    zelle   = (payload.get("payment_zelle")   or "").strip() or None
    venmo   = (payload.get("payment_venmo")   or "").strip() or None
    cashapp = (payload.get("payment_cashapp") or "").strip() or None
    paypal  = (payload.get("payment_paypal")  or "").strip() or None
    raw_rates = payload.get("lesson_rates") or []
    rates = []
    for r in raw_rates:
        try:
            dur = int(r.get("duration_min", 0))
            rate_cents = int(round(float(r.get("rate", 0)) * 100))
            pkg_rate_cents = None
            if r.get("package_rate") not in (None, "", 0):
                pkg_rate_cents = int(round(float(r.get("package_rate", 0)) * 100))
            if dur > 0:
                rates.append({"duration_min": dur, "rate_cents": rate_cents,
                               "package_rate_cents": pkg_rate_cents})
        except (ValueError, TypeError):
            pass
    cancel_hours = payload.get("cancel_hours")
    try:
        cancel_hours = int(cancel_hours) if cancel_hours is not None else None
    except (ValueError, TypeError):
        cancel_hours = None
    cancel_charge = bool(payload.get("cancel_charge", False))
    free_cancels = int(payload.get("free_cancels_per_student") or 0)
    packages_enabled = bool(payload.get("packages_enabled", False))
    try:
        package_size = max(1, int(payload.get("package_size") or 4))
    except (ValueError, TypeError):
        package_size = 4
    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO studio_teacher_settings
                (teacher_id, payment_zelle, payment_venmo, payment_cashapp, payment_paypal,
                 lesson_rates, cancel_hours, cancel_charge, free_cancels_per_student,
                 packages_enabled, package_size)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (teacher_id) DO UPDATE SET
                payment_zelle             = EXCLUDED.payment_zelle,
                payment_venmo             = EXCLUDED.payment_venmo,
                payment_cashapp           = EXCLUDED.payment_cashapp,
                payment_paypal            = EXCLUDED.payment_paypal,
                lesson_rates              = EXCLUDED.lesson_rates,
                cancel_hours              = EXCLUDED.cancel_hours,
                cancel_charge             = EXCLUDED.cancel_charge,
                free_cancels_per_student  = EXCLUDED.free_cancels_per_student,
                packages_enabled          = EXCLUDED.packages_enabled,
                package_size              = EXCLUDED.package_size
        """, (teacher["id"], zelle, venmo, cashapp, paypal, _json.dumps(rates),
              cancel_hours, cancel_charge, free_cancels, packages_enabled, package_size))
    return {"status": "success"}


def _resolve_studio_student(cur, teacher_id: int, name: str, email: str | None) -> int:
    """Find or create a studio_students record. Match email first, then name. Returns student id."""
    if email:
        cur.execute(
            "SELECT id FROM studio_students WHERE teacher_id = %s AND LOWER(email) = LOWER(%s)",
            (teacher_id, email)
        )
        row = cur.fetchone()
        if row:
            # Fill in name if it was blank
            cur.execute(
                "UPDATE studio_students SET name = %s WHERE id = %s AND (name IS NULL OR name = '')",
                (name, row[0])
            )
            return row[0]
    if name:
        cur.execute(
            "SELECT id FROM studio_students WHERE teacher_id = %s AND LOWER(name) = LOWER(%s)",
            (teacher_id, name)
        )
        row = cur.fetchone()
        if row:
            # Fill in email if the student didn't have one
            if email:
                cur.execute(
                    "UPDATE studio_students SET email = LOWER(%s) WHERE id = %s AND email IS NULL",
                    (email, row[0])
                )
            return row[0]
    # No match — create new student
    cur.execute(
        "INSERT INTO studio_students (teacher_id, name, email) VALUES (%s, %s, %s) RETURNING id",
        (teacher_id, name, email.lower() if email else None)
    )
    return cur.fetchone()[0]


def _studio_payment_balance(cur, teacher_id: int, student_id: int, duration_min: int) -> dict:
    """Returns {lessons_paid, scheduled, remaining} for a student's billing group.
    lessons_paid is the SUM of all payment transactions for this billing unit + duration.
    scheduled counts upcoming booked lessons only (>= today).
    """
    cur.execute(
        "SELECT family_id FROM studio_students WHERE id = %s AND teacher_id = %s",
        (student_id, teacher_id)
    )
    row = cur.fetchone()
    if not row:
        return {"lessons_paid": 0, "scheduled": 0, "remaining": 0}
    family_id = row[0]

    if family_id:
        cur.execute("""
            SELECT COALESCE(SUM(lessons_count), 0)
            FROM studio_payment_transactions
            WHERE teacher_id = %s AND family_id = %s AND duration_min = %s
        """, (teacher_id, family_id, duration_min))
        lessons_paid = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM lessons
            WHERE studio_student_id IN (
                SELECT id FROM studio_students WHERE family_id = %s AND teacher_id = %s
            ) AND duration_min = %s AND status = 'booked' AND lesson_date >= CURRENT_DATE
        """, (family_id, teacher_id, duration_min))
    else:
        cur.execute("""
            SELECT COALESCE(SUM(lessons_count), 0)
            FROM studio_payment_transactions
            WHERE teacher_id = %s AND student_id = %s AND duration_min = %s
        """, (teacher_id, student_id, duration_min))
        lessons_paid = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM lessons
            WHERE studio_student_id = %s AND duration_min = %s AND status = 'booked'
              AND lesson_date >= CURRENT_DATE
        """, (student_id, duration_min))

    scheduled = cur.fetchone()[0]
    return {
        "lessons_paid": int(lessons_paid),
        "scheduled": int(scheduled),
        "remaining": int(lessons_paid) - int(scheduled),
    }


def _send_payment_reminder_email(teacher_name: str, student_name: str, student_email: str,
                                  parent_name: str, parent_email: str,
                                  line_items: list, payment_handles: dict = None):
    """Send a payment reminder with line-item breakdown and exact dollar amounts.

    line_items: [{duration_min, owed_count, rate_cents, owed_dollars}]
    """
    recipient_email = parent_email or student_email
    recipient_name = parent_name or student_name
    if not recipient_email:
        return False

    total = sum(item["owed_dollars"] for item in line_items)
    subject = f"Payment reminder — {student_name}"

    lines_html = ""
    lines_text = ""
    for item in line_items:
        dur = item["duration_min"]
        owed_count = item["owed_count"]
        rate_dollars = item["rate_cents"] / 100 if item["rate_cents"] else None
        owed = item["owed_dollars"]
        if rate_dollars:
            lines_html += (
                f"<li>{owed_count} × {dur}-min lesson{'s' if owed_count != 1 else ''}"
                f" @ ${rate_dollars:.2f} = <strong>${owed:.2f}</strong></li>"
            )
            lines_text += f"  • {owed_count} × {dur}-min lessons @ ${rate_dollars:.2f} = ${owed:.2f}\n"
        else:
            lines_html += f"<li>{owed_count} × {dur}-min lesson{'s' if owed_count != 1 else ''} outstanding</li>"
            lines_text += f"  • {owed_count} × {dur}-min lessons outstanding\n"

    handles_html = ""
    handles_text = ""
    if payment_handles:
        parts = [f"{m.title()}: <strong>{h}</strong>" for m, h in payment_handles.items() if h]
        if parts:
            handles_html = "<p>You can submit payment via: " + " · ".join(parts) + "</p>"
            handles_text = "You can submit payment via: " + ", ".join(
                p.replace("<strong>", "").replace("</strong>", "") for p in parts
            ) + "\n\n"

    total_line_html = f"<p><strong>Total owed: ${total:.2f}</strong></p>" if total else ""
    total_line_text = f"Total owed: ${total:.2f}\n\n" if total else ""

    html = (
        f"<p>Hi {recipient_name},</p>"
        f"<p>This is a friendly payment reminder for your upcoming lessons:</p>"
        f"<ul>{lines_html}</ul>"
        f"{total_line_html}"
        f"{handles_html}"
        f"<p>Please submit payment at your earliest convenience. Reply to this email with any questions.</p>"
        f"<p>— {teacher_name}</p>"
    )
    text = (
        f"Hi {recipient_name},\n\n"
        f"This is a friendly payment reminder for your upcoming lessons:\n\n"
        f"{lines_text}\n"
        f"{total_line_text}"
        f"{handles_text}"
        f"Please submit payment at your earliest convenience.\n\n— {teacher_name}"
    )
    return send_email(recipient_email, subject, html, text)


@app.get("/studio-teacher/lessons")
def studio_teacher_lessons(request: Request):
    teacher = require_studio_teacher(request)
    today = datetime.now(EST).date()
    week_ago = today - timedelta(days=7)
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, lesson_date, lesson_time, duration_min,
                   external_name, external_email, studio_student_id,
                   zoom_link, attendance, status
            FROM lessons
            WHERE teacher_id = %s AND status = 'booked'
              AND lesson_date >= %s
            ORDER BY lesson_date, lesson_time
        """, (teacher["id"], week_ago))
        rows = cur.fetchall()

    lessons = []
    for r in rows:
        lid, ldate, ltime, dur, ext_name, ext_email, ss_id, zoom, att, status = r
        lessons.append({
            "id": lid,
            "date": ldate.isoformat(),
            "time": ltime.strftime("%H:%M") if ltime else None,
            "duration_min": dur,
            "student_name": ext_name,
            "student_email": ext_email,
            "studio_student_id": ss_id,
            "zoom_link": zoom,
            "attendance": att,
            "is_today": ldate == today,
            "is_past": ldate < today,
        })
    return lessons


@app.get("/studio-teacher/lessons-for-date")
def studio_teacher_lessons_for_date(request: Request, date: str):
    teacher = require_studio_teacher(request)
    from datetime import date as _date
    try:
        target = _date.fromisoformat(date)
    except ValueError:
        return []
    with db_cursor() as cur:
        cur.execute("""
            SELECT l.id, l.lesson_time, l.duration_min,
                   l.external_name, l.external_email,
                   l.studio_student_id, l.zoom_link, l.attendance,
                   ss.name AS reg_name, ss.email AS reg_email
            FROM lessons l
            LEFT JOIN studio_students ss ON ss.id = l.studio_student_id
            WHERE l.teacher_id = %s AND l.lesson_date = %s AND l.status = 'booked'
            ORDER BY l.lesson_time
        """, (teacher["id"], target))
        rows = cur.fetchall()
    result = []
    for r in rows:
        lid, ltime, dur, ext_name, ext_email, ss_id, zoom, att, reg_name, reg_email = r
        result.append({
            "id": lid,
            "time": ltime.strftime("%H:%M") if ltime else None,
            "duration_min": dur,
            "student_name": reg_name or ext_name or "Unknown",
            "student_email": reg_email or ext_email,
            "studio_student_id": ss_id,
            "zoom_link": zoom,
            "attendance": att,
        })
    return result


@app.get("/studio-teacher/calendar")
def studio_teacher_calendar(request: Request, year: int = None, month: int = None):
    teacher = require_studio_teacher(request)
    today = datetime.now(EST).date()
    if year is None:
        year = today.year
    if month is None:
        month = today.month

    import calendar as _cal
    num_days = _cal.monthrange(year, month)[1]

    with db_cursor() as cur:
        cur.execute("""
            SELECT weekday, start_time FROM weekly_availability
            WHERE teacher_id = %s AND active = TRUE
        """, (teacher["id"],))
        avail_rows = cur.fetchall()

        available_weekdays = {r[0] for r in avail_rows}

        cur.execute("""
            SELECT EXTRACT(DAY FROM lesson_date)::int,
                   COUNT(*),
                   BOOL_OR(COALESCE(payment_overrun, FALSE))
            FROM lessons
            WHERE teacher_id = %s AND status = 'booked'
              AND EXTRACT(YEAR FROM lesson_date) = %s
              AND EXTRACT(MONTH FROM lesson_date) = %s
            GROUP BY EXTRACT(DAY FROM lesson_date)
        """, (teacher["id"], year, month))
        lesson_rows = cur.fetchall()

    lessons_map = {r[0]: {"count": r[1], "has_yellow": r[2]} for r in lesson_rows}

    from datetime import date as _date
    available_days = []
    for day in range(1, num_days + 1):
        d = _date(year, month, day)
        if d.weekday() in available_weekdays:
            available_days.append(day)

    lessons = [
        {"day": day, "count": info["count"], "has_yellow": info["has_yellow"]}
        for day, info in lessons_map.items()
    ]

    return {"available_days": available_days, "lessons": lessons, "year": year, "month": month}


@app.get("/studio-teacher/available-slots")
def studio_teacher_available_slots(request: Request, date: str):
    teacher = require_studio_teacher(request)
    try:
        from datetime import date as _date
        target = _date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date")
    slots = get_available_slots(teacher["id"], target, has_lunch_break=False)
    return {"slots": slots}


@app.post("/studio-teacher/lesson")
def studio_teacher_add_lesson(payload: dict, request: Request):
    teacher = require_studio_teacher(request)
    date_str = (payload.get("date") or "").strip()
    time_str = (payload.get("time") or "").strip()
    duration_min = int(payload.get("duration_min") or 30)
    ext_name = (payload.get("external_name") or "").strip() or None
    ext_email = (payload.get("external_email") or "").strip().lower() or None
    studio_student_id = payload.get("studio_student_id") or None
    zoom_link = (payload.get("zoom_link") or "").strip() or None

    if not date_str or not time_str:
        return {"status": "fail", "message": "Date and time are required"}

    try:
        from datetime import date as _date, time as _time
        lesson_date = _date.fromisoformat(date_str)
        lesson_time = datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        return {"status": "fail", "message": "Invalid date or time format"}

    payment_overrun = False
    reminder_needed = False

    with db_cursor(commit=True) as cur:
        if studio_student_id:
            studio_student_id = int(studio_student_id)
            cur.execute(
                "SELECT name, email, parent_email FROM studio_students WHERE id = %s AND teacher_id = %s",
                (studio_student_id, teacher["id"])
            )
            ss_row = cur.fetchone()
            if ss_row:
                if not ext_name: ext_name = ss_row[0]
                if not ext_email: ext_email = ss_row[1]
        elif ext_name:
            # Auto-create or match student so they appear in the Students tab
            studio_student_id = _resolve_studio_student(cur, teacher["id"], ext_name, ext_email)

        if studio_student_id:
            balance = _studio_payment_balance(cur, teacher["id"], studio_student_id, duration_min)
            if balance["remaining"] <= 0:
                payment_overrun = True
                reminder_needed = True

        cur.execute("""
            INSERT INTO lessons
                (teacher_id, lesson_date, lesson_time, duration_min,
                 external_name, external_email, studio_student_id,
                 zoom_link, payment_overrun, status)
            VALUES (%s, %s, %s::time, %s, %s, %s, %s, %s, %s, 'booked')
            RETURNING id
        """, (
            teacher["id"], lesson_date, time_str, duration_min,
            ext_name, ext_email, studio_student_id,
            zoom_link, payment_overrun
        ))
        lesson_id = cur.fetchone()[0]

    if reminder_needed and studio_student_id:
        with db_cursor() as cur:
            cur.execute(
                "SELECT name, email, parent_name, parent_email FROM studio_students WHERE id = %s",
                (studio_student_id,)
            )
            sr = cur.fetchone()
            if sr:
                bal = _studio_payment_balance(cur, teacher["id"], studio_student_id, duration_min)
                owed_count = max(0, bal["scheduled"] - bal["lessons_paid"])
                cur.execute(
                    "SELECT lesson_rates, payment_venmo, payment_zelle, payment_cashapp, payment_paypal FROM studio_teacher_settings WHERE teacher_id = %s",
                    (teacher["id"],)
                )
                s_row = cur.fetchone()
                rate_map = {r["duration_min"]: r["rate_cents"] for r in (s_row[0] or [])} if s_row else {}
                payment_handles = {}
                if s_row:
                    for method, val in zip(["venmo", "zelle", "cashapp", "paypal"], s_row[1:]):
                        if val:
                            payment_handles[method] = val
                rate_cents = rate_map.get(duration_min, 0)
                line_items = [{
                    "duration_min": duration_min,
                    "owed_count": owed_count,
                    "rate_cents": rate_cents,
                    "owed_dollars": owed_count * rate_cents / 100,
                }]
                _send_payment_reminder_email(
                    teacher.get("fullname", "Your teacher"),
                    sr[0], sr[1], sr[2], sr[3],
                    line_items, payment_handles
                )

    if ext_email:
        from datetime import date as _date
        date_label = lesson_date.strftime("%A, %B %-d")
        html = (
            f"<p>Hi {ext_name or 'there'},</p>"
            f"<p>Your lesson has been scheduled for <strong>{date_label} at {time_str}</strong> "
            f"({duration_min} minutes).</p>"
        )
        if zoom_link:
            html += f"<p>Zoom link: <a href='{zoom_link}'>{zoom_link}</a></p>"
        html += f"<p>— {teacher.get('fullname', 'Your teacher')}</p>"
        text = f"Hi {ext_name or 'there'},\n\nYour lesson is scheduled for {date_label} at {time_str} ({duration_min} min)."
        if zoom_link:
            text += f"\nZoom: {zoom_link}"
        send_email(ext_email, "Lesson scheduled — CountrPnt", html, text)

    return {"status": "success", "lesson_id": lesson_id, "payment_overrun": payment_overrun}


@app.post("/studio-teacher/lessons-bulk")
def studio_teacher_add_lessons_bulk(payload: dict, request: Request):
    """Insert multiple lessons and send ONE summary email per student (not one per lesson)."""
    teacher = require_studio_teacher(request)
    teacher_name = teacher.get("fullname") or "Your teacher"
    lessons_in = payload.get("lessons") or []

    added = []
    with db_cursor(commit=True) as cur:
        for item in lessons_in:
            date_str = (item.get("date") or "").strip()
            time_str = (item.get("time") or "").strip()
            duration_min = int(item.get("duration_min") or 30)
            ext_name = (item.get("external_name") or "").strip() or None
            ext_email = (item.get("external_email") or "").strip().lower() or None
            studio_student_id = item.get("studio_student_id") or None
            zoom_link = (item.get("zoom_link") or "").strip() or None

            if not date_str or not time_str:
                continue
            try:
                from datetime import date as _date
                lesson_date = _date.fromisoformat(date_str)
                datetime.strptime(time_str, "%H:%M")  # validate
            except ValueError:
                continue

            payment_overrun = False
            if studio_student_id:
                studio_student_id = int(studio_student_id)
                cur.execute(
                    "SELECT name, email FROM studio_students WHERE id = %s AND teacher_id = %s",
                    (studio_student_id, teacher["id"])
                )
                ss = cur.fetchone()
                if ss:
                    if not ext_name: ext_name = ss[0]
                    if not ext_email: ext_email = ss[1]
            elif ext_name:
                studio_student_id = _resolve_studio_student(cur, teacher["id"], ext_name, ext_email)

            if studio_student_id:
                bal = _studio_payment_balance(cur, teacher["id"], studio_student_id, duration_min)
                if bal["remaining"] <= 0:
                    payment_overrun = True

            cur.execute("""
                INSERT INTO lessons
                    (teacher_id, lesson_date, lesson_time, duration_min,
                     external_name, external_email, studio_student_id,
                     zoom_link, payment_overrun, status)
                VALUES (%s, %s, %s::time, %s, %s, %s, %s, %s, %s, 'booked')
                RETURNING id
            """, (
                teacher["id"], lesson_date, time_str, duration_min,
                ext_name, ext_email, studio_student_id, zoom_link, payment_overrun
            ))
            added.append({
                "id": cur.fetchone()[0],
                "date": date_str,
                "time": time_str,
                "duration_min": duration_min,
                "student_name": ext_name or "Student",
                "email": ext_email,
                "zoom_link": zoom_link,
            })

    # One email per unique student email address
    from collections import defaultdict as _dd
    by_email = _dd(list)
    for item in added:
        if item["email"]:
            by_email[item["email"]].append(item)

    for email, items in by_email.items():
        student_name = items[0]["student_name"]
        items.sort(key=lambda x: (x["date"], x["time"]))

        rows_html = ""
        rows_text = ""
        for item in items:
            d_obj = datetime.strptime(item["date"], "%Y-%m-%d")
            date_label = d_obj.strftime("%A, %B %-d")
            time_label = datetime.strptime(item["time"], "%H:%M").strftime("%-I:%M %p")
            start_dt = datetime.combine(d_obj.date(), datetime.strptime(item["time"], "%H:%M").time())
            end_dt = start_dt + timedelta(minutes=item["duration_min"])
            gcal_title = f"Lesson+with+{teacher_name.replace(' ', '+')}"
            gcal_url = (
                f"https://calendar.google.com/calendar/render?action=TEMPLATE"
                f"&text={gcal_title}"
                f"&dates={start_dt.strftime('%Y%m%dT%H%M%S')}/{end_dt.strftime('%Y%m%dT%H%M%S')}"
            )
            if item["zoom_link"]:
                import urllib.parse as _up
                gcal_url += f"&location={_up.quote(item['zoom_link'], safe='')}"
            rows_html += (
                f"<tr><td style='padding:4px 14px 4px 0'>{date_label}</td>"
                f"<td style='padding:4px 14px 4px 0'>{time_label}</td>"
                f"<td style='padding:4px 14px 4px 0'>{item['duration_min']} min</td>"
                f"<td style='padding:4px 0'><a href='{gcal_url}' style='color:#7c5cbf'>Add to Calendar</a></td></tr>"
            )
            rows_text += f"  • {date_label} at {time_label} ({item['duration_min']} min)\n"
            if item["zoom_link"]:
                rows_text += f"    Zoom: {item['zoom_link']}\n"

        html = (
            f"<p>Hi {student_name},</p>"
            f"<p>The following lessons have been scheduled with {teacher_name}:</p>"
            f"<table style='border-collapse:collapse;font-family:sans-serif;font-size:14px'>"
            f"<tr style='color:#888;font-size:12px'><th style='text-align:left;padding:4px 14px 4px 0'>Date</th>"
            f"<th style='text-align:left;padding:4px 14px 4px 0'>Time</th>"
            f"<th style='text-align:left;padding:4px 14px 4px 0'>Duration</th><th></th></tr>"
            f"{rows_html}</table>"
            f"<p style='margin-top:16px'>— {teacher_name}</p>"
        )
        text = (
            f"Hi {student_name},\n\n"
            f"The following lessons have been scheduled with {teacher_name}:\n\n"
            f"{rows_text}\n— {teacher_name}"
        )
        send_email(email, f"Lessons scheduled with {teacher_name}", html, text)

    return {"status": "success", "added": len(added)}


@app.delete("/studio-teacher/lesson/{lesson_id}")
def studio_teacher_cancel_lesson(lesson_id: int, request: Request):
    teacher = require_studio_teacher(request)
    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE lessons SET status = 'cancelled', cancelled_at = NOW()
            WHERE id = %s AND teacher_id = %s AND status = 'booked'
            RETURNING external_name, external_email, lesson_date, lesson_time, duration_min, studio_student_id
        """, (lesson_id, teacher["id"]))
        row = cur.fetchone()
        if not row:
            return {"status": "fail", "message": "Lesson not found"}

        ext_name, ext_email, ldate, ltime, dur, ss_id = row
        free_cancel_used = False

        if ss_id:
            # Check teacher's free cancel allowance
            cur.execute(
                "SELECT free_cancels_per_student FROM studio_teacher_settings WHERE teacher_id = %s",
                (teacher["id"],)
            )
            settings_row = cur.fetchone()
            allowed = settings_row[0] if settings_row else 0
            if allowed > 0:
                cur.execute(
                    "SELECT free_cancels_used FROM studio_students WHERE id = %s",
                    (ss_id,)
                )
                used_row = cur.fetchone()
                used = used_row[0] if used_row else 0
                if used < allowed:
                    cur.execute(
                        "UPDATE studio_students SET free_cancels_used = free_cancels_used + 1 WHERE id = %s",
                        (ss_id,)
                    )
                    free_cancel_used = True

    if ext_email:
        date_label = ldate.strftime("%A, %B %-d")
        time_label = ltime.strftime("%-I:%M %p") if ltime else ""
        cancel_note = " (free cancellation applied)" if free_cancel_used else ""
        html = (
            f"<p>Hi {ext_name or 'there'},</p>"
            f"<p>Your {dur}-minute lesson on <strong>{date_label} at {time_label}</strong> has been cancelled{cancel_note}.</p>"
            f"<p>— {teacher.get('fullname', 'Your teacher')}</p>"
        )
        text = f"Hi {ext_name or 'there'},\nYour lesson on {date_label} at {time_label} has been cancelled{cancel_note}."
        send_email(ext_email, "Lesson cancelled — CountrPnt", html, text)
    return {"status": "success", "free_cancel_used": free_cancel_used}


@app.patch("/studio-teacher/lesson/{lesson_id}/attendance")
def studio_teacher_mark_attendance(lesson_id: int, payload: dict, request: Request):
    teacher = require_studio_teacher(request)
    att = (payload.get("attendance") or "").strip()
    if att not in ("present", "absent"):
        return {"status": "fail", "message": "attendance must be 'present' or 'absent'"}
    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE lessons SET attendance = %s
            WHERE id = %s AND teacher_id = %s
        """, (att, lesson_id, teacher["id"]))
    return {"status": "success"}


def _snap_to_duration(gap: int):
    """Return the standard lesson duration that this gap clearly represents,
    or None if the gap is ambiguous (e.g. 75 min could be 60+buffer or 90-short).
    Each standard has its own tolerance so genuinely ambiguous gaps don't get forced."""
    if gap > 90:
        return None  # break between sessions
    for dur, tol in [(30, 6), (45, 6), (60, 7), (90, 5)]:
        if abs(gap - dur) <= tol:
            return dur
    return None  # gap sits between standards — don't guess


def _infer_durations_from_gaps(lessons: list) -> list:
    """Infer duration_min from time gaps between consecutive lessons on the same day.
    Each gap sets the duration for the preceding lesson independently.
    Gaps over 90 min are treated as breaks and skipped entirely.
    The last lesson of each day inherits the most common inferred duration for that day."""
    from datetime import datetime as _dtp
    from collections import Counter as _Counter

    by_date: dict = {}
    for i, lesson in enumerate(lessons):
        by_date.setdefault(lesson.get("date", ""), []).append((i, lesson))

    for entries in by_date.values():
        if len(entries) < 2:
            continue
        entries.sort(key=lambda x: x[1].get("time", "00:00"))

        day_inferred: list = []
        for j in range(len(entries) - 1):
            idx, curr = entries[j]
            _, nxt = entries[j + 1]
            try:
                t1 = _dtp.strptime(curr["time"], "%H:%M")
                t2 = _dtp.strptime(nxt["time"], "%H:%M")
                gap = int((t2 - t1).total_seconds() / 60)
                snapped = _snap_to_duration(gap)
                if snapped is not None:
                    lessons[idx]["duration_min"] = snapped
                    day_inferred.append(snapped)
            except (ValueError, KeyError):
                pass

        # Last lesson: use the most common duration inferred that day
        if day_inferred:
            most_common = _Counter(day_inferred).most_common(1)[0][0]
            last_idx = entries[-1][0]
            lessons[last_idx]["duration_min"] = most_common

    return lessons


def _apply_historical_durations(lessons: list, teacher_id: int) -> list:
    """For lessons whose duration is still at the default (60), look up the student's
    most common past lesson duration for this teacher and apply it.
    Matches by first name (case-insensitive) — skips if the name is ambiguous."""
    from collections import Counter as _Counter

    DEFAULT = 60
    # Collect names that still need a duration
    needs_lookup = [l for l in lessons if l.get("duration_min") == DEFAULT]
    if not needs_lookup:
        return lessons

    first_names = list({l["student_name"].strip().split()[0].lower()
                        for l in needs_lookup if l.get("student_name")})
    if not first_names:
        return lessons

    with db_cursor() as cur:
        # Get all students for this teacher whose first name matches any in our list
        cur.execute("""
            SELECT ss.id, lower(split_part(ss.name, ' ', 1)) AS fname
            FROM studio_students ss
            WHERE ss.teacher_id = %s
              AND lower(split_part(ss.name, ' ', 1)) = ANY(%s)
        """, (teacher_id, first_names))
        name_to_ids: dict = {}
        for sid, fname in cur.fetchall():
            name_to_ids.setdefault(fname, []).append(sid)

        # Remove ambiguous first names (two different students with same first name)
        unambiguous = {fname: ids[0] for fname, ids in name_to_ids.items() if len(ids) == 1}
        if not unambiguous:
            return lessons

        student_ids = list(unambiguous.values())
        cur.execute("""
            SELECT studio_student_id, duration_min
            FROM lessons
            WHERE teacher_id = %s
              AND studio_student_id = ANY(%s)
              AND duration_min IS NOT NULL
              AND status = 'booked'
        """, (teacher_id, student_ids))
        history: dict = {}
        for sid, dur in cur.fetchall():
            history.setdefault(sid, []).append(dur)

    # Build first_name → most_common_duration map
    id_to_fname = {v: k for k, v in unambiguous.items()}
    fname_dur: dict = {}
    for sid, durs in history.items():
        if durs:
            fname_dur[id_to_fname[sid]] = _Counter(durs).most_common(1)[0][0]

    for lesson in lessons:
        if lesson.get("duration_min") != DEFAULT:
            continue
        fname = (lesson.get("student_name") or "").strip().split()[0].lower()
        if fname in fname_dur:
            lesson["duration_min"] = fname_dur[fname]

    return lessons


@app.post("/studio-teacher/lessons-parse")
def studio_teacher_lessons_parse(payload: dict, request: Request):
    teacher = require_studio_teacher(request)
    raw_text = (payload.get("text") or "").strip()
    if not raw_text:
        return {"status": "fail", "message": "No text provided"}
    if not ANTHROPIC_API_KEY:
        return {"status": "fail", "message": "Parsing not configured"}

    current_year = datetime.now(EST).year
    system_prompt = (
        f"You are a lesson schedule parser. Today's year is {current_year}. "
        "Extract every individual lesson entry from the schedule text. "
        "Return a JSON array only — no explanation, no markdown, no code fences. Each entry: "
        "{\"date\": \"YYYY-MM-DD\", \"time\": \"HH:MM\", \"student_name\": \"First Last\", "
        "\"email\": null, \"duration_min\": 60}. "
        "Rules: "
        "1. Date headers like 'WEDNESDAY 6/3' or 'TUES 6/9' apply to all lessons listed beneath them until the next header. Infer the year from context. "
        "2. Times may lack colons — '645PM' means 6:45 PM, '11AM' means 11:00 AM. Convert to 24-hour HH:MM. "
        "3. Student names are ALL CAPS on their own line followed by a time — capitalize them normally (e.g. 'MEL' → 'Mel'). "
        "4. Ignore separator lines (___), notes in parentheses like (NO KATHERINE), and blank lines. "
        "5. Infer duration_min from the time gap between consecutive lessons on the same day. "
        "   Snap to the nearest standard length: 30, 45, 60, or 90 min. "
        "   For the last lesson of the day (no following lesson), use the most common duration seen that day. "
        "   If the schedule is only one lesson with no gaps to measure, default to 60. "
        "6. Return only the raw JSON array."
    )
    client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": raw_text}]
        )
        import json as _json, re as _re
        raw = msg.content[0].text.strip()
        raw = _re.sub(r'^```[a-z]*\n?', '', raw).rstrip('`').strip()
        m = _re.search(r'\[.*\]', raw, _re.DOTALL)
        raw = m.group(0) if m else raw
        parsed = _json.loads(raw)
        if not isinstance(parsed, list):
            parsed = []
    except Exception as e:
        return {"status": "fail", "message": f"Parse error: {e}"}

    # Post-process: deterministically infer durations from time gaps
    parsed = _infer_durations_from_gaps(parsed)

    # Final pass: for any lesson still at the default, check the student's history
    parsed = _apply_historical_durations(parsed, teacher["id"])

    return {"status": "success", "lessons": parsed}


@app.get("/studio-teacher/students")
def studio_teacher_students(request: Request):
    teacher = require_studio_teacher(request)
    today = datetime.now(EST).date()
    with db_cursor() as cur:
        cur.execute("""
            SELECT ss.id, ss.name, ss.email, ss.parent_name, ss.parent_email, ss.family_id,
                   sf.family_name, sf.parent_name AS fam_parent_name, sf.parent_email AS fam_parent_email,
                   ss.free_cancels_used
            FROM studio_students ss
            LEFT JOIN studio_families sf ON sf.id = ss.family_id
            WHERE ss.teacher_id = %s
            ORDER BY sf.family_name NULLS LAST, ss.name
        """, (teacher["id"],))
        student_rows = cur.fetchall()

        cur.execute("""
            SELECT studio_student_id,
                   SUM(CASE WHEN attendance = 'present' THEN 1 ELSE 0 END) AS present_count,
                   SUM(CASE WHEN attendance = 'absent' THEN 1 ELSE 0 END) AS absent_count
            FROM lessons
            WHERE teacher_id = %s AND studio_student_id IS NOT NULL
            GROUP BY studio_student_id
        """, (teacher["id"],))
        att_rows = {r[0]: {"present": r[1], "absent": r[2]} for r in cur.fetchall()}

        # lessons_paid = SUM of all transactions per billing unit + duration
        cur.execute("""
            SELECT student_id, family_id, duration_min, COALESCE(SUM(lessons_count), 0)
            FROM studio_payment_transactions
            WHERE teacher_id = %s
            GROUP BY student_id, family_id, duration_min
        """, (teacher["id"],))
        pool_rows = cur.fetchall()

        # upcoming scheduled counts only — past lessons fall off the balance automatically
        cur.execute("""
            SELECT studio_student_id, duration_min, COUNT(*)
            FROM lessons
            WHERE teacher_id = %s AND studio_student_id IS NOT NULL AND status = 'booked'
              AND lesson_date >= CURRENT_DATE
            GROUP BY studio_student_id, duration_min
        """, (teacher["id"],))
        scheduled_rows = cur.fetchall()

    pool_by_student = {}
    pool_by_family = {}
    for pool_sid, pool_fid, pool_dur, pool_paid in pool_rows:
        if pool_fid:
            fam_durs = pool_by_family.setdefault(pool_fid, {})
            fam_durs[pool_dur] = fam_durs.get(pool_dur, 0) + int(pool_paid)
        elif pool_sid:
            stu_durs = pool_by_student.setdefault(pool_sid, {})
            stu_durs[pool_dur] = stu_durs.get(pool_dur, 0) + int(pool_paid)

    scheduled_by_student = {}
    for ss_id, dur, cnt in scheduled_rows:
        scheduled_by_student.setdefault(ss_id, {})[dur] = cnt

    # Get teacher settings once
    with db_cursor() as cur2:
        cur2.execute(
            "SELECT free_cancels_per_student, packages_enabled, package_size FROM studio_teacher_settings WHERE teacher_id = %s",
            (teacher["id"],)
        )
        fc_row = cur2.fetchone()
    free_cancels_allowed = fc_row[0] if fc_row else 0
    packages_enabled = bool(fc_row[1]) if fc_row else False
    package_size = fc_row[2] if fc_row else 4

    # Build family-aggregate scheduled counts so all siblings share one pool view
    scheduled_by_family = {}
    for pool_r in student_rows:
        _ss_id = pool_r[0]
        _fam_id = pool_r[5]
        if _fam_id:
            for dur, cnt in scheduled_by_student.get(_ss_id, {}).items():
                scheduled_by_family.setdefault(_fam_id, {})
                scheduled_by_family[_fam_id][dur] = scheduled_by_family[_fam_id].get(dur, 0) + cnt

    out = []
    for r in student_rows:
        ss_id, name, email, p_name, p_email, fam_id, fam_name, fam_p_name, fam_p_email, free_cancels_used = r

        attendance = att_rows.get(ss_id, {"present": 0, "absent": 0})

        if fam_id:
            paid_map = pool_by_family.get(fam_id, {})
            sched = scheduled_by_family.get(fam_id, {})
        else:
            paid_map = pool_by_student.get(ss_id, {})
            sched = scheduled_by_student.get(ss_id, {})

        all_durs = set(sched.keys()) | set(paid_map.keys())
        payments = []
        for dur in sorted(all_durs):
            paid = paid_map.get(dur, 0)
            scheduled_cnt = sched.get(dur, 0)
            payments.append({
                "duration_min": dur,
                "lessons_paid": paid,
                "scheduled": scheduled_cnt,
                "remaining": paid - scheduled_cnt,
            })

        out.append({
            "id": ss_id,
            "name": name,
            "email": email,
            "parent_name": p_name or fam_p_name,
            "parent_email": p_email or fam_p_email,
            "family_id": fam_id,
            "family_name": fam_name,
            "attendance": {"present": int(attendance["present"]), "absent": int(attendance["absent"])},
            "payments": payments,
            "free_cancels_used": free_cancels_used or 0,
            "free_cancels_allowed": free_cancels_allowed,
            "packages_enabled": packages_enabled,
            "package_size": package_size or 4,
        })
    return out


@app.post("/studio-teacher/students")
def studio_teacher_add_student(payload: dict, request: Request):
    teacher = require_studio_teacher(request)
    name = (payload.get("name") or "").strip()
    if not name:
        return {"status": "fail", "message": "Name is required"}
    email = (payload.get("email") or "").strip().lower() or None
    parent_name = (payload.get("parent_name") or "").strip() or None
    parent_email = (payload.get("parent_email") or "").strip().lower() or None
    family_id = payload.get("family_id") or None
    if family_id:
        family_id = int(family_id)

    with db_cursor(commit=True) as cur:
        if not family_id and parent_email:
            cur.execute(
                "SELECT id FROM studio_families WHERE teacher_id = %s AND parent_email = %s LIMIT 1",
                (teacher["id"], parent_email)
            )
            fam_row = cur.fetchone()
            if fam_row:
                family_id = fam_row[0]

        cur.execute("""
            INSERT INTO studio_students (teacher_id, name, email, parent_name, parent_email, family_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (teacher["id"], name, email, parent_name, parent_email, family_id))
        student_id = cur.fetchone()[0]
    return {"status": "success", "id": student_id}


@app.post("/studio-teacher/families")
def studio_teacher_add_family(payload: dict, request: Request):
    teacher = require_studio_teacher(request)
    family_name = (payload.get("family_name") or "").strip()
    if not family_name:
        return {"status": "fail", "message": "Family name is required"}
    parent_name = (payload.get("parent_name") or "").strip() or None
    parent_email = (payload.get("parent_email") or "").strip().lower() or None
    children = payload.get("children") or []

    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO studio_families (teacher_id, family_name, parent_name, parent_email)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (teacher["id"], family_name, parent_name, parent_email))
        fam_id = cur.fetchone()[0]

        students_added = 0
        for child in children:
            child_name = (child.get("name") or "").strip()
            child_email = (child.get("email") or "").strip().lower() or None
            if not child_name:
                continue
            # Find existing student by email or name, update their family_id
            matched = False
            if child_email:
                cur.execute(
                    "UPDATE studio_students SET family_id = %s WHERE teacher_id = %s AND LOWER(email) = %s RETURNING id",
                    (fam_id, teacher["id"], child_email)
                )
                if cur.fetchone():
                    matched = True
            if not matched:
                cur.execute(
                    "UPDATE studio_students SET family_id = %s WHERE teacher_id = %s AND LOWER(name) = LOWER(%s) AND family_id IS NULL RETURNING id",
                    (fam_id, teacher["id"], child_name)
                )
                if cur.fetchone():
                    matched = True
            if not matched:
                cur.execute(
                    "INSERT INTO studio_students (teacher_id, name, email, family_id) VALUES (%s, %s, %s, %s)",
                    (teacher["id"], child_name, child_email, fam_id)
                )
            students_added += 1

    return {"status": "success", "id": fam_id, "students_added": students_added}


@app.get("/studio-teacher/families")
def studio_teacher_families(request: Request):
    teacher = require_studio_teacher(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, family_name, parent_name, parent_email
            FROM studio_families WHERE teacher_id = %s ORDER BY family_name
        """, (teacher["id"],))
        rows = cur.fetchall()
    return [{"id": r[0], "family_name": r[1], "parent_name": r[2], "parent_email": r[3]} for r in rows]


@app.patch("/studio-teacher/family/{family_id}")
def studio_teacher_update_family(family_id: int, payload: dict, request: Request):
    teacher = require_studio_teacher(request)
    family_name = (payload.get("family_name") or "").strip()
    if not family_name:
        return {"status": "fail", "message": "Family name is required"}
    parent_name = (payload.get("parent_name") or "").strip() or None
    parent_email = (payload.get("parent_email") or "").strip().lower() or None
    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE studio_families
            SET family_name = %s, parent_name = %s, parent_email = %s
            WHERE id = %s AND teacher_id = %s
        """, (family_name, parent_name, parent_email, family_id, teacher["id"]))
        if cur.rowcount == 0:
            return {"status": "fail", "message": "Family not found"}
    return {"status": "success"}


@app.patch("/studio-teacher/student/{student_id}")
def studio_teacher_update_student(student_id: int, payload: dict, request: Request):
    teacher = require_studio_teacher(request)
    name = (payload.get("name") or "").strip()
    if not name:
        return {"status": "fail", "message": "Name is required"}
    email = (payload.get("email") or "").strip().lower() or None
    parent_name = (payload.get("parent_name") or "").strip() or None
    parent_email = (payload.get("parent_email") or "").strip().lower() or None
    family_id = payload.get("family_id") or None
    if family_id:
        family_id = int(family_id)

    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE studio_students
            SET name = %s, email = %s, parent_name = %s, parent_email = %s, family_id = %s
            WHERE id = %s AND teacher_id = %s
        """, (name, email, parent_name, parent_email, family_id, student_id, teacher["id"]))
        if cur.rowcount == 0:
            return {"status": "fail", "message": "Student not found"}
    return {"status": "success"}


@app.delete("/studio-teacher/student/{student_id}")
def studio_teacher_delete_student(student_id: int, request: Request):
    teacher = require_studio_teacher(request)
    with db_cursor(commit=True) as cur:
        # Nullify lessons so history is preserved but student link is removed
        cur.execute("""
            UPDATE lessons SET studio_student_id = NULL
            WHERE studio_student_id = %s AND teacher_id = %s
        """, (student_id, teacher["id"]))
        cur.execute("""
            DELETE FROM studio_payment_pools
            WHERE student_id = %s AND teacher_id = %s
        """, (student_id, teacher["id"]))
        cur.execute("""
            DELETE FROM studio_students WHERE id = %s AND teacher_id = %s
        """, (student_id, teacher["id"]))
        if cur.rowcount == 0:
            return {"status": "fail", "message": "Student not found"}
    return {"status": "success"}


@app.get("/studio-teacher/student/{student_id}/payment-balance")
def studio_teacher_payment_balance(student_id: int, request: Request, duration_min: int = 30):
    teacher = require_studio_teacher(request)
    with db_cursor() as cur:
        balance = _studio_payment_balance(cur, teacher["id"], student_id, duration_min)
    return balance


@app.patch("/studio-teacher/student/{student_id}/payments")
def studio_teacher_update_payments(student_id: int, payload: dict, request: Request):
    teacher = require_studio_teacher(request)
    payments = payload.get("payments") or []
    if not isinstance(payments, list):
        return {"status": "fail", "message": "payments must be a list"}

    with db_cursor() as cur:
        cur.execute(
            "SELECT family_id FROM studio_students WHERE id = %s AND teacher_id = %s",
            (student_id, teacher["id"])
        )
        row = cur.fetchone()
    if not row:
        return {"status": "fail", "message": "Student not found"}
    family_id = row[0]

    with db_cursor(commit=True) as cur:
        for entry in payments:
            dur = int(entry.get("duration_min") or 30)
            paid = int(entry.get("lessons_paid") or 0)
            if family_id:
                cur.execute("""
                    INSERT INTO studio_payment_pools (teacher_id, family_id, duration_min, lessons_paid, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (teacher_id, family_id, duration_min) WHERE family_id IS NOT NULL
                    DO UPDATE SET lessons_paid = EXCLUDED.lessons_paid, updated_at = NOW()
                """, (teacher["id"], family_id, dur, paid))
            else:
                cur.execute("""
                    INSERT INTO studio_payment_pools (teacher_id, student_id, duration_min, lessons_paid, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (teacher_id, student_id, duration_min) WHERE student_id IS NOT NULL
                    DO UPDATE SET lessons_paid = EXCLUDED.lessons_paid, updated_at = NOW()
                """, (teacher["id"], student_id, dur, paid))
    return {"status": "success"}


@app.post("/studio-teacher/student/{student_id}/payment-reminder")
def studio_teacher_payment_reminder(student_id: int, request: Request):
    teacher = require_studio_teacher(request)
    with db_cursor() as cur:
        cur.execute(
            "SELECT name, email, parent_name, parent_email, family_id FROM studio_students WHERE id = %s AND teacher_id = %s",
            (student_id, teacher["id"])
        )
        row = cur.fetchone()
        if not row:
            return {"status": "fail", "message": "Student not found"}
        name, email, parent_name, parent_email, family_id = row

        # Get all upcoming booked durations (family-aware)
        if family_id:
            cur.execute("""
                SELECT DISTINCT duration_min FROM lessons
                WHERE status = 'booked' AND lesson_date >= CURRENT_DATE
                  AND studio_student_id IN (
                      SELECT id FROM studio_students WHERE family_id = %s AND teacher_id = %s
                  )
            """, (family_id, teacher["id"]))
        else:
            cur.execute("""
                SELECT DISTINCT duration_min FROM lessons
                WHERE studio_student_id = %s AND status = 'booked' AND lesson_date >= CURRENT_DATE
            """, (student_id,))
        durations = [r[0] for r in cur.fetchall()]

        # Teacher's lesson rates and payment handles
        cur.execute("""
            SELECT lesson_rates, payment_venmo, payment_zelle, payment_cashapp, payment_paypal
            FROM studio_teacher_settings WHERE teacher_id = %s
        """, (teacher["id"],))
        settings_row = cur.fetchone()
        lesson_rates = (settings_row[0] or []) if settings_row else []
        rate_map = {r["duration_min"]: r["rate_cents"] for r in lesson_rates}
        payment_handles = {}
        if settings_row:
            for method, val in zip(["venmo", "zelle", "cashapp", "paypal"], settings_row[1:]):
                if val:
                    payment_handles[method] = val

        # Build line items for overdue durations
        line_items = []
        for dur in sorted(durations):
            balance = _studio_payment_balance(cur, teacher["id"], student_id, dur)
            owed_count = balance["scheduled"] - balance["lessons_paid"]
            if owed_count <= 0:
                continue
            rate_cents = rate_map.get(dur, 0)
            line_items.append({
                "duration_min": dur,
                "owed_count": owed_count,
                "rate_cents": rate_cents,
                "owed_dollars": owed_count * rate_cents / 100,
            })

        if not line_items:
            return {"status": "fail", "message": "Student is fully paid — no reminder needed."}

    sent = _send_payment_reminder_email(
        teacher.get("fullname", "Your teacher"),
        name, email, parent_name, parent_email,
        line_items, payment_handles
    )
    return {"status": "success" if sent else "fail"}


@app.post("/studio-teacher/payment-reminder-all")
def studio_teacher_payment_reminder_all(payload: dict, request: Request):
    """Send payment reminders to students/families with an outstanding balance.

    Optional payload: {"student_ids": [1, 2, 3]} — limits to those billing units.
    If omitted, all overdue units are contacted.
    """
    teacher = require_studio_teacher(request)
    filter_ids = set(payload.get("student_ids") or [])
    teacher_name = teacher.get("fullname") or "Your teacher"

    with db_cursor() as cur:
        # Fetch teacher's rates and payment handles once
        cur.execute("""
            SELECT lesson_rates, payment_venmo, payment_zelle, payment_cashapp, payment_paypal
            FROM studio_teacher_settings WHERE teacher_id = %s
        """, (teacher["id"],))
        s_row = cur.fetchone()
        rate_map = {r["duration_min"]: r["rate_cents"] for r in (s_row[0] or [])} if s_row else {}
        payment_handles = {}
        if s_row:
            for method, val in zip(["venmo", "zelle", "cashapp", "paypal"], s_row[1:]):
                if val:
                    payment_handles[method] = val

        # All students with their family info
        cur.execute("""
            SELECT ss.id, ss.name, ss.email, ss.parent_name, ss.parent_email, ss.family_id,
                   sf.family_name, sf.parent_name, sf.parent_email
            FROM studio_students ss
            LEFT JOIN studio_families sf ON sf.id = ss.family_id
            WHERE ss.teacher_id = %s
        """, (teacher["id"],))
        students = cur.fetchall()

        # All upcoming booked durations per student
        cur.execute("""
            SELECT studio_student_id, duration_min
            FROM lessons
            WHERE teacher_id = %s AND status = 'booked' AND lesson_date >= CURRENT_DATE
              AND studio_student_id IS NOT NULL
        """, (teacher["id"],))
        upcoming = cur.fetchall()

    # Build set of (student_id, duration_min) pairs that have upcoming lessons
    upcoming_by_student = {}
    for ss_id, dur in upcoming:
        upcoming_by_student.setdefault(ss_id, set()).add(dur)

    # Group by billing unit: family_id (for family members) or student_id (for solos)
    # Each billing unit gets at most one email
    billing_units = {}  # key: ("family", fam_id) or ("student", ss_id)
    for row in students:
        ss_id, name, email, p_name, p_email, fam_id, fam_name, fam_p_name, fam_p_email = row
        if fam_id:
            key = ("family", fam_id)
            if key not in billing_units:
                billing_units[key] = {
                    "student_name": fam_name or name,
                    "email": None,
                    "parent_name": fam_p_name or p_name,
                    "parent_email": fam_p_email or p_email,
                    "member_ids": [],
                    "family_id": fam_id,
                }
            billing_units[key]["member_ids"].append(ss_id)
        else:
            key = ("student", ss_id)
            billing_units[key] = {
                "student_name": name,
                "email": email,
                "parent_name": p_name,
                "parent_email": p_email,
                "member_ids": [ss_id],
                "family_id": None,
            }

    sent_count = 0
    already_paid = 0

    with db_cursor() as cur:
        for key, unit in billing_units.items():
            # If caller specified a filter, skip units not in it
            representative_id = unit["member_ids"][0]
            if filter_ids and representative_id not in filter_ids:
                continue

            # Collect all durations with upcoming lessons across all members
            all_durs = set()
            for mid in unit["member_ids"]:
                all_durs |= upcoming_by_student.get(mid, set())

            if not all_durs:
                continue  # no upcoming lessons, skip

            # Build line items for overdue durations
            line_items = []
            for dur in sorted(all_durs):
                bal = _studio_payment_balance(cur, teacher["id"], representative_id, dur)
                owed_count = bal["scheduled"] - bal["lessons_paid"]
                if owed_count <= 0:
                    continue
                rate_cents = rate_map.get(dur, 0)
                line_items.append({
                    "duration_min": dur,
                    "owed_count": owed_count,
                    "rate_cents": rate_cents,
                    "owed_dollars": owed_count * rate_cents / 100,
                })

            if not line_items:
                already_paid += 1
                continue

            ok = _send_payment_reminder_email(
                teacher_name,
                unit["student_name"],
                unit["email"],
                unit["parent_name"],
                unit["parent_email"],
                line_items,
                payment_handles,
            )
            if ok:
                sent_count += 1

    return {"status": "success", "sent": sent_count, "already_paid": already_paid}


@app.get("/studio-teacher/student/{student_id}/payment-transactions")
def studio_teacher_get_transactions(student_id: int, request: Request):
    teacher = require_studio_teacher(request)
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, family_id FROM studio_students WHERE id = %s AND teacher_id = %s",
            (student_id, teacher["id"])
        )
        row = cur.fetchone()
        if not row:
            return {"status": "fail", "message": "Student not found"}
        _, family_id = row
        if family_id:
            cur.execute("""
                SELECT id, student_id, duration_min, lessons_count, is_package,
                       package_size, amount_cents, note, created_at
                FROM studio_payment_transactions
                WHERE teacher_id = %s AND family_id = %s
                ORDER BY created_at DESC
            """, (teacher["id"], family_id))
        else:
            cur.execute("""
                SELECT id, student_id, duration_min, lessons_count, is_package,
                       package_size, amount_cents, note, created_at
                FROM studio_payment_transactions
                WHERE teacher_id = %s AND student_id = %s
                ORDER BY created_at DESC
            """, (teacher["id"], student_id))
        rows = cur.fetchall()
    txns = []
    for r in rows:
        txns.append({
            "id": r[0], "student_id": r[1], "duration_min": r[2],
            "lessons_count": r[3], "is_package": r[4], "package_size": r[5],
            "amount_cents": r[6], "note": r[7],
            "created_at": r[8].isoformat() if r[8] else None,
        })
    return {"status": "success", "transactions": txns}


@app.post("/studio-teacher/student/{student_id}/payment-transaction")
def studio_teacher_add_transaction(student_id: int, payload: dict, request: Request):
    teacher = require_studio_teacher(request)
    with db_cursor() as cur:
        cur.execute(
            "SELECT family_id FROM studio_students WHERE id = %s AND teacher_id = %s",
            (student_id, teacher["id"])
        )
        row = cur.fetchone()
        if not row:
            return {"status": "fail", "message": "Student not found"}
        family_id = row[0]
    try:
        duration_min = int(payload["duration_min"])
        lessons_count = int(payload["lessons_count"])
    except (KeyError, ValueError, TypeError):
        return {"status": "fail", "message": "duration_min and lessons_count are required integers"}
    is_package = bool(payload.get("is_package", False))
    package_size = int(payload.get("package_size") or 0) or None
    amount_cents = None
    if payload.get("amount_cents") is not None:
        try:
            amount_cents = int(payload["amount_cents"])
        except (ValueError, TypeError):
            pass
    note = (payload.get("note") or "").strip() or None
    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO studio_payment_transactions
                (teacher_id, family_id, student_id, duration_min, lessons_count,
                 is_package, package_size, amount_cents, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (teacher["id"], family_id, student_id, duration_min, lessons_count,
              is_package, package_size, amount_cents, note))
        new_id = cur.fetchone()[0]
    return {"status": "success", "id": new_id}


@app.delete("/studio-teacher/payment-transaction/{txn_id}")
def studio_teacher_delete_transaction(txn_id: int, request: Request):
    teacher = require_studio_teacher(request)
    with db_cursor(commit=True) as cur:
        cur.execute(
            "DELETE FROM studio_payment_transactions WHERE id = %s AND teacher_id = %s RETURNING id",
            (txn_id, teacher["id"])
        )
        deleted = cur.fetchone()
    if not deleted:
        return {"status": "fail", "message": "Transaction not found"}
    return {"status": "success"}


@app.post("/studio-teacher/invite")
def studio_teacher_invite(payload: dict, request: Request):
    """Studio teacher invites a studio_member to join their studio."""
    teacher = require_studio_teacher(request)
    email = (payload.get("email") or "").strip().lower()
    fullname_hint = (payload.get("fullname_hint") or "").strip() or None

    if not email or not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        return {"status": "fail", "message": "Please enter a valid email."}

    with db_cursor() as cur:
        cur.execute(
            "SELECT 1 FROM users WHERE org_id = %s AND email = %s",
            (teacher["org_id"], email)
        )
        if cur.fetchone():
            return {"status": "fail", "message": "A user with that email already exists."}

    token = secrets.token_urlsafe(32)
    expires = datetime.now(EST) + timedelta(days=INVITE_TOKEN_DAYS)

    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE invitations SET accepted_at = NOW()
            WHERE email = %s AND org_id = %s AND accepted_at IS NULL
        """, (email, teacher["org_id"]))
        cur.execute("""
            INSERT INTO invitations (token, email, role, org_id, invited_by,
                                     fullname_hint, expires_at, teacher_type)
            VALUES (%s, %s, 'studio_member', %s, %s, %s, %s, 'vocal')
        """, (token, email, teacher["org_id"], teacher["id"], fullname_hint, expires))

    invite_url = f"{APP_URL}/accept-invite?token={token}"
    with db_cursor() as cur:
        cur.execute("SELECT name FROM organizations WHERE id = %s", (teacher["org_id"],))
        org_row = cur.fetchone()
    org_name = org_row[0] if org_row else ""

    html, text = render_invite_email(invite_url, "studio_member", fullname_hint or "", teacher.get("fullname", ""), org_name)
    sent = send_email(email, "You've been invited to CountrPnt", html, text)
    return {"status": "success", "email_sent": sent}




@app.post("/studio-teacher/email-student")
def studio_teacher_email_student(payload: dict, request: Request):
    """Email a specific studio student directly."""
    teacher = require_studio_teacher(request)
    student_id = payload.get("student_id")
    subject = (payload.get("subject") or "").strip()
    body = (payload.get("body") or "").strip()
    if not subject or not body:
        return {"status": "fail", "message": "Subject and message are required"}

    with db_cursor() as cur:
        cur.execute(
            "SELECT name, email, parent_email FROM studio_students WHERE id = %s AND teacher_id = %s",
            (student_id, teacher["id"])
        )
        row = cur.fetchone()
    if not row:
        return {"status": "fail", "message": "Student not found"}
    name, email, parent_email = row
    recipient = email or parent_email
    if not recipient:
        return {"status": "fail", "message": "No email address on file for this student"}

    teacher_name = teacher.get("fullname") or "Your teacher"
    teacher_email = teacher.get("email") or None
    html = f"<p>{body.replace(chr(10), '<br>')}</p><p style='margin-top:16px;color:#888'>— {teacher_name}</p>"
    text = f"{body}\n\n— {teacher_name}"
    sent = send_email(
        to=recipient,
        subject=subject,
        html_body=html,
        text_body=text,
        from_name=f"{teacher_name} via Countrpnt",
        from_address=EMAIL_FROM,
        reply_to=teacher_email,
    )
    return {"status": "success" if sent else "fail", "message": "" if sent else "Failed to send email"}


@app.post("/studio-teacher/email-today")
def studio_teacher_email_today(payload: dict, request: Request):
    """Email all students with lessons today."""
    teacher = require_studio_teacher(request)
    subject = (payload.get("subject") or "").strip()
    body = (payload.get("body") or "").strip()
    if not subject or not body:
        return {"status": "fail", "message": "Subject and message are required"}

    today = datetime.now(EST).date()
    teacher_name = teacher.get("fullname") or "Your teacher"
    teacher_email = teacher.get("email") or None

    with db_cursor() as cur:
        cur.execute("""
            SELECT DISTINCT
                COALESCE(ss.email, l.external_email) AS email,
                COALESCE(ss.parent_email, '') AS parent_email,
                COALESCE(ss.name, l.external_name, 'Student') AS name
            FROM lessons l
            LEFT JOIN studio_students ss ON ss.id = l.studio_student_id
            WHERE l.teacher_id = %s AND l.lesson_date = %s AND l.status = 'booked'
        """, (teacher["id"], today))
        rows = cur.fetchall()

    sent_count = 0
    already_emailed = set()
    html_body = f"<p>{body.replace(chr(10), '<br>')}</p><p style='margin-top:16px;color:#888'>— {teacher_name}</p>"
    text_body = f"{body}\n\n— {teacher_name}"
    for email, parent_email, name in rows:
        recipient = email or parent_email or None
        if not recipient or recipient in already_emailed:
            continue
        already_emailed.add(recipient)
        send_email(
            to=recipient,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            from_name=f"{teacher_name} via Countrpnt",
            from_address=EMAIL_FROM,
            reply_to=teacher_email,
        )
        sent_count += 1

    return {"status": "success", "sent": sent_count}


# ========================================================
# ORCHESTRA MANAGER
# ========================================================

def require_orchestra_admin(request: Request):
    user = require_user(request, role="admin")
    if user.get("org_type") != "orchestra":
        raise HTTPException(status_code=403, detail="Orchestra org required")
    return user

def require_orchestra_user(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    if user.get("org_type") != "orchestra":
        raise HTTPException(status_code=403, detail="Orchestra org required")
    return user

# -- Page -----------------------------------------------------------------

@app.get("/orchestra/manager", response_class=HTMLResponse)
def orchestra_manager_page(request: Request):
    return templates.TemplateResponse(request, "orchestra/manager.html")

@app.get("/orchestra/sub-response/{token}", response_class=HTMLResponse)
def orchestra_sub_response_page(token: str, r: str = ""):
    """One-click accept/decline page for orchestra subs."""
    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT osc.id, osc.sub_id, osc.sub_request_id, osc.response,
                   os2.fullname, osr.rehearsal_id, osr.section_id, osr.status
            FROM orchestra_sub_contacts osc
            JOIN orchestra_subs os2 ON os2.id = osc.sub_id
            JOIN orchestra_sub_requests osr ON osr.id = osc.sub_request_id
            WHERE osc.token = %s
        """, (token,))
        row = cur.fetchone()
        if not row:
            return HTMLResponse("<p>Link not found or expired.</p>", status_code=404)
        sc_id, sub_id, req_id, existing_response, fullname, rehearsal_id, section_id, req_status = row

        if r not in ("accepted", "declined"):
            return HTMLResponse(f"<p>Hi {fullname}! Use the link in your email to accept or decline.</p>")

        if existing_response != "pending":
            return HTMLResponse(f"<p>You already {existing_response} this request. Thanks!</p>")

        cur.execute("UPDATE orchestra_sub_contacts SET response=%s, responded_at=NOW() WHERE id=%s", (r, sc_id))
        if r == "accepted":
            cur.execute("""
                UPDATE orchestra_sub_requests SET status='filled', filled_by_sub_id=%s WHERE id=%s
            """, (sub_id, req_id))
            cur.execute("""
                UPDATE orchestra_sub_contacts SET response='declined', responded_at=NOW()
                WHERE sub_request_id=%s AND id != %s AND response='pending'
            """, (req_id, sc_id))

    verb = "accepted" if r == "accepted" else "declined"
    return HTMLResponse(f"<p>Thanks, {fullname}! You've {verb} the sub request.</p>")


# -- Orchestra Sections (read-only for manager) ---------------------------

@app.get("/orchestra/sections")
def orchestra_get_sections(request: Request):
    user = require_orchestra_user(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, name, instrument, chair_count
            FROM orchestra_sections WHERE org_id=%s ORDER BY name
        """, (user["org_id"],))
        return [{"id": r[0], "name": r[1], "instrument": r[2], "chair_count": r[3] or 5}
                for r in cur.fetchall()]


@app.patch("/orchestra/sections/{section_id}/chair-count")
def orchestra_set_chair_count(section_id: int, payload: dict, request: Request):
    user = require_orchestra_admin(request)
    chair_count = int(payload.get("chair_count", 1))
    chair_count = max(1, min(chair_count, 200))
    with db_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE orchestra_sections SET chair_count=%s WHERE id=%s AND org_id=%s RETURNING chair_count",
            (chair_count, section_id, user["org_id"]),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Section not found")
    return {"status": "success", "chair_count": row[0]}


# -- Members --------------------------------------------------------------

@app.get("/orchestra/members")
def orchestra_get_members(request: Request):
    user = require_orchestra_user(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT om.id, om.fullname, om.email, om.phone, om.instrument,
                   om.section_family, om.section_id, os2.name AS section_name,
                   om.user_id, om.notes, om.part_label, om.doublings
            FROM orchestra_members om
            LEFT JOIN orchestra_sections os2 ON os2.id = om.section_id
            WHERE om.org_id=%s AND om.active=true
            ORDER BY om.section_family, om.fullname
        """, (user["org_id"],))
        return [{"id": r[0], "fullname": r[1], "email": r[2] or "", "phone": r[3] or "",
                 "instrument": r[4] or "", "section_family": r[5] or "other",
                 "section_id": r[6], "section_name": r[7] or "",
                 "user_id": r[8], "notes": r[9] or "", "part_label": r[10] or "",
                 "doublings": r[11] or ""}
                for r in cur.fetchall()]


@app.post("/orchestra/members")
def orchestra_add_member(payload: dict, request: Request):
    user = require_orchestra_admin(request)
    fullname = (payload.get("fullname") or "").strip()
    if not fullname:
        return {"status": "fail", "message": "Name required"}
    email = (payload.get("email") or "").strip().lower() or None
    phone = (payload.get("phone") or "").strip() or None
    instrument = (payload.get("instrument") or "").strip() or None
    section_family = (payload.get("section_family") or "other").strip()
    section_id = payload.get("section_id")
    notes = (payload.get("notes") or "").strip() or None
    part_label = (payload.get("part_label") or "").strip() or None
    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO orchestra_members (org_id, fullname, email, phone, instrument,
                section_family, section_id, notes, part_label)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (user["org_id"], fullname, email, phone, instrument,
              section_family, section_id, notes, part_label))
        new_id = cur.fetchone()[0]
    return {"status": "success", "id": new_id}


@app.patch("/orchestra/members/{member_id}")
def orchestra_update_member(member_id: int, payload: dict, request: Request):
    user = require_orchestra_admin(request)
    fields, vals = [], []
    for col in ("fullname", "email", "phone", "instrument", "section_family", "notes", "part_label", "doublings"):
        if col in payload:
            fields.append(f"{col}=%s")
            vals.append((payload[col] or "").strip() or None)
    if "section_id" in payload:
        fields.append("section_id=%s")
        vals.append(payload["section_id"])
    if "active" in payload:
        fields.append("active=%s")
        vals.append(bool(payload["active"]))
    if not fields:
        return {"status": "ok"}
    vals += [member_id, user["org_id"]]
    with db_cursor(commit=True) as cur:
        cur.execute(f"UPDATE orchestra_members SET {', '.join(fields)} WHERE id=%s AND org_id=%s", vals)
    return {"status": "success"}


@app.delete("/orchestra/members/{member_id}")
def orchestra_delete_member(member_id: int, request: Request):
    user = require_orchestra_admin(request)
    with db_cursor(commit=True) as cur:
        cur.execute("UPDATE orchestra_members SET active=false WHERE id=%s AND org_id=%s",
                    (member_id, user["org_id"]))
    return {"status": "success"}


# -- Concerts (reuse operas table) ----------------------------------------

@app.get("/orchestra/concerts")
def orchestra_get_concerts(request: Request):
    user = require_orchestra_user(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, opera_name, start_date, end_date
            FROM operas WHERE org_id=%s ORDER BY start_date DESC NULLS LAST, id DESC
        """, (user["org_id"],))
        return [{"id": r[0], "title": r[1], "start_date": str(r[2]) if r[2] else None,
                 "end_date": str(r[3]) if r[3] else None}
                for r in cur.fetchall()]


@app.post("/orchestra/concerts")
def orchestra_create_concert(payload: dict, request: Request):
    user = require_orchestra_admin(request)
    title = (payload.get("title") or "").strip()
    if not title:
        return {"status": "fail", "message": "Title required"}
    start_date = payload.get("start_date") or None
    end_date = payload.get("end_date") or None
    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO operas (org_id, opera_name, start_date, end_date)
            VALUES (%s,%s,%s,%s) RETURNING id
        """, (user["org_id"], title, start_date, end_date))
        new_id = cur.fetchone()[0]
    return {"status": "success", "id": new_id}


@app.patch("/orchestra/concerts/{concert_id}")
def orchestra_update_concert(concert_id: int, payload: dict, request: Request):
    user = require_orchestra_admin(request)
    fields, vals = [], []
    if "title" in payload:
        fields.append("opera_name=%s")
        vals.append((payload["title"] or "").strip())
    if "start_date" in payload:
        fields.append("start_date=%s")
        vals.append(payload["start_date"] or None)
    if "end_date" in payload:
        fields.append("end_date=%s")
        vals.append(payload["end_date"] or None)
    if not fields:
        return {"status": "ok"}
    vals += [concert_id, user["org_id"]]
    with db_cursor(commit=True) as cur:
        cur.execute(f"UPDATE operas SET {', '.join(fields)} WHERE id=%s AND org_id=%s", vals)
    return {"status": "success"}


@app.delete("/orchestra/concerts/{concert_id}")
def orchestra_delete_concert(concert_id: int, request: Request):
    user = require_orchestra_admin(request)
    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM operas WHERE id=%s AND org_id=%s", (concert_id, user["org_id"]))
    return {"status": "success"}


# -- Concert Pieces -------------------------------------------------------

@app.get("/orchestra/concerts/{concert_id}/pieces")
def orchestra_get_pieces(concert_id: int, request: Request):
    user = require_orchestra_user(request)
    with db_cursor() as cur:
        cur.execute("SELECT 1 FROM operas WHERE id=%s AND org_id=%s", (concert_id, user["org_id"]))
        if not cur.fetchone():
            raise HTTPException(status_code=404)
        cur.execute("""
            SELECT id, title, composer, opus, duration_min, sort_order
            FROM concert_pieces WHERE opera_id=%s ORDER BY sort_order, id
        """, (concert_id,))
        return [{"id": r[0], "title": r[1], "composer": r[2] or "", "opus": r[3] or "",
                 "duration_min": r[4], "sort_order": r[5]}
                for r in cur.fetchall()]


@app.post("/orchestra/concerts/{concert_id}/pieces")
def orchestra_add_piece(concert_id: int, payload: dict, request: Request):
    user = require_orchestra_admin(request)
    title = (payload.get("title") or "").strip()
    if not title:
        return {"status": "fail", "message": "Title required"}
    with db_cursor(commit=True) as cur:
        cur.execute("SELECT 1 FROM operas WHERE id=%s AND org_id=%s", (concert_id, user["org_id"]))
        if not cur.fetchone():
            return {"status": "fail", "message": "Concert not found"}
        cur.execute("""
            SELECT COALESCE(MAX(sort_order),0)+1 FROM concert_pieces WHERE opera_id=%s
        """, (concert_id,))
        sort_order = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO concert_pieces (opera_id, title, composer, opus, duration_min, sort_order)
            VALUES (%s,%s,%s,%s,%s,%s) RETURNING id
        """, (concert_id, title,
              (payload.get("composer") or "").strip() or None,
              (payload.get("opus") or "").strip() or None,
              payload.get("duration_min") or None, sort_order))
        new_id = cur.fetchone()[0]
    return {"status": "success", "id": new_id}


@app.patch("/orchestra/pieces/{piece_id}")
def orchestra_update_piece(piece_id: int, payload: dict, request: Request):
    user = require_orchestra_admin(request)
    fields, vals = [], []
    for col in ("title", "composer", "opus"):
        if col in payload:
            fields.append(f"{col}=%s")
            vals.append((payload[col] or "").strip() or None)
    if "duration_min" in payload:
        fields.append("duration_min=%s")
        vals.append(payload["duration_min"] or None)
    if "sort_order" in payload:
        fields.append("sort_order=%s")
        vals.append(int(payload["sort_order"]))
    if not fields:
        return {"status": "ok"}
    vals.append(piece_id)
    with db_cursor(commit=True) as cur:
        cur.execute(f"""
            UPDATE concert_pieces SET {', '.join(fields)}
            WHERE id=%s AND opera_id IN (SELECT id FROM operas WHERE org_id=%s)
        """, vals + [user["org_id"]])
    return {"status": "success"}


@app.delete("/orchestra/pieces/{piece_id}")
def orchestra_delete_piece(piece_id: int, request: Request):
    user = require_orchestra_admin(request)
    with db_cursor(commit=True) as cur:
        cur.execute("""
            DELETE FROM concert_pieces WHERE id=%s
              AND opera_id IN (SELECT id FROM operas WHERE org_id=%s)
        """, (piece_id, user["org_id"]))
    return {"status": "success"}


# -- Piece Seating --------------------------------------------------------

@app.get("/orchestra/pieces/{piece_id}/seats")
def orchestra_get_piece_seats(piece_id: int, request: Request):
    user = require_orchestra_user(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT ps.id, ps.section_id, os2.name AS section_name, ps.chair_number,
                   ps.part_number, ps.member_id, om.fullname,
                   ps.external_name, ps.external_email
            FROM piece_seats ps
            JOIN concert_pieces cp ON cp.id = ps.piece_id
            JOIN operas o ON o.id = cp.opera_id
            JOIN orchestra_sections os2 ON os2.id = ps.section_id
            LEFT JOIN orchestra_members om ON om.id = ps.member_id
            WHERE ps.piece_id=%s AND o.org_id=%s
            ORDER BY os2.name, ps.part_number, ps.chair_number
        """, (piece_id, user["org_id"]))
        return [{"id": r[0], "section_id": r[1], "section_name": r[2],
                 "chair_number": r[3], "part_number": r[4],
                 "member_id": r[5],
                 "member_name": r[6] if r[5] else r[7],
                 "external_name": None if r[5] else r[7],
                 "external_email": None if r[5] else r[8]}
                for r in cur.fetchall()]


@app.post("/orchestra/pieces/{piece_id}/seats")
def orchestra_assign_piece_seat(piece_id: int, payload: dict, request: Request):
    user = require_orchestra_admin(request)
    section_id = payload.get("section_id")
    chair_number = payload.get("chair_number")
    part_number = payload.get("part_number", 1)
    member_id = payload.get("member_id")
    external_name = (payload.get("external_name") or "").strip() or None
    external_email = (payload.get("external_email") or "").strip() or None

    if not section_id or not chair_number:
        return {"status": "fail", "message": "section_id and chair_number required"}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT cp.id FROM concert_pieces cp
            JOIN operas o ON o.id = cp.opera_id
            WHERE cp.id=%s AND o.org_id=%s
        """, (piece_id, user["org_id"]))
        if not cur.fetchone():
            return {"status": "fail", "message": "Piece not found"}

        if member_id:
            cur.execute("""
                INSERT INTO piece_seats (piece_id, section_id, chair_number, part_number,
                    member_id, external_name, external_email)
                VALUES (%s,%s,%s,%s,%s,NULL,NULL)
                ON CONFLICT (piece_id, section_id, chair_number, part_number)
                DO UPDATE SET member_id=EXCLUDED.member_id, external_name=NULL, external_email=NULL
            """, (piece_id, section_id, chair_number, part_number, member_id))
        elif external_name:
            cur.execute("""
                INSERT INTO piece_seats (piece_id, section_id, chair_number, part_number,
                    member_id, external_name, external_email)
                VALUES (%s,%s,%s,%s,NULL,%s,%s)
                ON CONFLICT (piece_id, section_id, chair_number, part_number)
                DO UPDATE SET member_id=NULL, external_name=EXCLUDED.external_name,
                              external_email=EXCLUDED.external_email
            """, (piece_id, section_id, chair_number, part_number, external_name, external_email))
        else:
            cur.execute("""
                DELETE FROM piece_seats
                WHERE piece_id=%s AND section_id=%s AND chair_number=%s AND part_number=%s
            """, (piece_id, section_id, chair_number, part_number))
    return {"status": "success"}


# -- Rehearsals -----------------------------------------------------------

@app.get("/orchestra/rehearsals")
def orchestra_get_rehearsals(request: Request):
    user = require_orchestra_user(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT r.id, r.start_time, r.end_time, r.location, r.notes,
                   r.attendance_type, r.opera_id, o.opera_name,
                   ARRAY(
                       SELECT ors2.section_id
                       FROM orchestra_rehearsal_sections ors2
                       WHERE ors2.rehearsal_id = r.id
                   ) AS section_ids
            FROM rehearsals r
            LEFT JOIN operas o ON o.id = r.opera_id
            WHERE r.org_id=%s AND r.rehearsal_type='orchestra'
            ORDER BY r.start_time DESC
        """, (user["org_id"],))
        rows = cur.fetchall()
    return [{"id": r[0], "start_time": str(r[1]), "end_time": str(r[2]) if r[2] else None,
             "location": r[3] or "", "notes": r[4] or "",
             "attendance_type": r[5] or "full",
             "concert_id": r[6], "concert_title": r[7] or "",
             "section_ids": r[8] or []}
            for r in rows]


@app.post("/orchestra/rehearsals")
def orchestra_create_rehearsal(payload: dict, request: Request):
    user = require_orchestra_admin(request)
    start_time = payload.get("start_time")
    end_time = payload.get("end_time")
    location = (payload.get("location") or "").strip() or None
    notes = (payload.get("notes") or "").strip() or None
    concert_id = payload.get("concert_id")  # optional
    attendance_type = payload.get("attendance_type", "full")
    section_ids = payload.get("section_ids") or []  # for sectionals

    if not start_time:
        return {"status": "fail", "message": "start_time required"}
    if attendance_type not in ("full", "sectional"):
        attendance_type = "full"

    with db_cursor(commit=True) as cur:
        if concert_id:
            cur.execute("SELECT 1 FROM operas WHERE id=%s AND org_id=%s", (concert_id, user["org_id"]))
            if not cur.fetchone():
                return {"status": "fail", "message": "Concert not found"}

        cur.execute("""
            INSERT INTO rehearsals (org_id, opera_id, start_time, end_time, location, notes,
                attendance_type, rehearsal_type)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'orchestra') RETURNING id
        """, (user["org_id"], concert_id, start_time, end_time, location, notes, attendance_type))
        reh_id = cur.fetchone()[0]

        if attendance_type == "sectional" and section_ids:
            for sid in section_ids:
                cur.execute("SAVEPOINT reh_section")
                try:
                    cur.execute("""
                        INSERT INTO orchestra_rehearsal_sections (rehearsal_id, section_id)
                        VALUES (%s,%s) ON CONFLICT DO NOTHING
                    """, (reh_id, sid))
                    cur.execute("RELEASE SAVEPOINT reh_section")
                except Exception as e:
                    cur.execute("ROLLBACK TO SAVEPOINT reh_section")
                    print(f"[orchestra_reh] skipped section {sid}: {e}")

    # Email notification
    with db_cursor() as cur:
        cur.execute("""
            SELECT om.fullname, om.email FROM orchestra_members om
            WHERE om.org_id=%s AND om.active=true AND om.email IS NOT NULL
        """, (user["org_id"],))
        members = cur.fetchall()
        if attendance_type == "sectional" and section_ids:
            cur.execute("""
                SELECT om.fullname, om.email FROM orchestra_members om
                WHERE om.org_id=%s AND om.active=true AND om.email IS NOT NULL
                  AND om.section_id = ANY(%s)
            """, (user["org_id"], section_ids))
            members = cur.fetchall()

    start_dt = start_time if hasattr(start_time, "strftime") else None
    date_str = str(start_time)[:16]
    for name, email in members:
        if email:
            html = f"<p>Hi {html_mod.escape(name)},</p><p>A rehearsal has been scheduled for {date_str}.</p>"
            text = f"Hi {name},\n\nA rehearsal has been scheduled for {date_str}."
            if location:
                html += f"<p>Location: {html_mod.escape(location)}</p>"
                text += f"\nLocation: {location}"
            if notes:
                html += f"<p>Notes: {html_mod.escape(notes)}</p>"
                text += f"\nNotes: {notes}"
            send_email(email, "Orchestra Rehearsal Scheduled", html, text)

    return {"status": "success", "id": reh_id}


@app.post("/orchestra/rehearsals/bulk")
def orchestra_create_rehearsals_bulk(payload: dict, request: Request):
    from datetime import date as date_type, timedelta
    user = require_orchestra_admin(request)
    start_date = payload.get("start_date")
    end_date = payload.get("end_date")
    days = payload.get("days", [])
    start_time_str = payload.get("start_time")
    end_time_str = payload.get("end_time") or None
    location = (payload.get("location") or "").strip() or None
    notes = (payload.get("notes") or "").strip() or None
    concert_id = payload.get("concert_id")  # optional
    attendance_type = payload.get("attendance_type", "full")
    section_ids = payload.get("section_ids") or []  # for sectionals

    if attendance_type not in ("full", "sectional"):
        attendance_type = "full"

    if not start_date or not end_date or not days or not start_time_str:
        return {"status": "fail", "message": "Start date, end date, days, and start time are required"}

    DAY_MAP = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
               "friday": 4, "saturday": 5, "sunday": 6}
    day_nums = [DAY_MAP[d.lower()] for d in days if d.lower() in DAY_MAP]
    if not day_nums:
        return {"status": "fail", "message": "No valid days selected"}

    try:
        sd = date_type.fromisoformat(start_date)
        ed = date_type.fromisoformat(end_date)
    except Exception:
        return {"status": "fail", "message": "Invalid date format"}

    if ed < sd:
        return {"status": "fail", "message": "End date must be after start date"}
    if (ed - sd).days > 365:
        return {"status": "fail", "message": "Date range cannot exceed one year"}

    with db_cursor() as cur:
        if concert_id:
            cur.execute("SELECT 1 FROM operas WHERE id=%s AND org_id=%s", (concert_id, user["org_id"]))
            if not cur.fetchone():
                return {"status": "fail", "message": "Concert not found"}

    rehearsal_dates = []
    current = sd
    while current <= ed:
        if current.weekday() in day_nums:
            rehearsal_dates.append(current)
        current += timedelta(days=1)

    if not rehearsal_dates:
        return {"status": "fail", "message": "No rehearsals fall in that date range"}
    if len(rehearsal_dates) > 100:
        return {"status": "fail", "message": f"Too many rehearsals ({len(rehearsal_dates)}). Narrow your date range."}

    created = 0
    with db_cursor(commit=True) as cur:
        for rdate in rehearsal_dates:
            cur.execute("SAVEPOINT bulk_reh")
            try:
                start_dt = datetime.fromisoformat(f"{rdate}T{start_time_str}")
                end_dt = datetime.fromisoformat(f"{rdate}T{end_time_str}") if end_time_str else None
                cur.execute("""
                    INSERT INTO rehearsals (org_id, opera_id, start_time, end_time, location, notes,
                        attendance_type, rehearsal_type)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'orchestra') RETURNING id
                """, (user["org_id"], concert_id, start_dt, end_dt, location, notes, attendance_type))
                rid = cur.fetchone()[0]

                if attendance_type == "sectional" and section_ids:
                    for sid in section_ids:
                        cur.execute(
                            "INSERT INTO orchestra_rehearsal_sections (rehearsal_id, section_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                            (rid, sid)
                        )

                cur.execute("RELEASE SAVEPOINT bulk_reh")
                created += 1
            except Exception as e:
                cur.execute("ROLLBACK TO SAVEPOINT bulk_reh")
                print(f"[orchestra_bulk] skipped {rdate}: {e}")

    return {"status": "success", "created": created}


@app.patch("/orchestra/rehearsals/{rehearsal_id}")
def orchestra_update_rehearsal(rehearsal_id: int, payload: dict, request: Request):
    user = require_orchestra_admin(request)
    fields, vals = [], []
    for col in ("location", "notes"):
        if col in payload:
            fields.append(f"{col}=%s")
            vals.append((payload[col] or "").strip() or None)
    if "start_time" in payload:
        fields.append("start_time=%s")
        vals.append(payload["start_time"])
    if "end_time" in payload:
        fields.append("end_time=%s")
        vals.append(payload["end_time"] or None)
    if not fields:
        return {"status": "ok"}
    vals += [rehearsal_id, user["org_id"]]
    with db_cursor(commit=True) as cur:
        cur.execute(f"""
            UPDATE rehearsals SET {', '.join(fields)}
            WHERE id=%s AND org_id=%s AND rehearsal_type='orchestra'
        """, vals)
    return {"status": "success"}


@app.delete("/orchestra/rehearsals/{rehearsal_id}")
def orchestra_delete_rehearsal(rehearsal_id: int, request: Request):
    user = require_orchestra_admin(request)
    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM rehearsals WHERE id=%s AND org_id=%s AND rehearsal_type='orchestra'",
                    (rehearsal_id, user["org_id"]))
    return {"status": "success"}


# -- Attendance -----------------------------------------------------------

@app.get("/orchestra/rehearsals/{rehearsal_id}/attendance")
def orchestra_get_attendance(rehearsal_id: int, request: Request):
    user = require_orchestra_user(request)
    with db_cursor() as cur:
        cur.execute("SELECT 1 FROM rehearsals WHERE id=%s AND org_id=%s", (rehearsal_id, user["org_id"]))
        if not cur.fetchone():
            raise HTTPException(status_code=404)
        cur.execute("""
            SELECT om.id, om.fullname, om.section_family, om.instrument,
                   oa.status, oa.notes, om.section_id, os2.name AS section_name
            FROM orchestra_members om
            LEFT JOIN orchestra_attendance oa
                ON oa.member_id = om.id AND oa.rehearsal_id = %s
            LEFT JOIN orchestra_sections os2 ON os2.id = om.section_id
            WHERE om.org_id=%s AND om.active=true
            ORDER BY om.section_family, om.fullname
        """, (rehearsal_id, user["org_id"]))
        return [{"member_id": r[0], "fullname": r[1], "section_family": r[2] or "other",
                 "instrument": r[3] or "", "status": r[4], "notes": r[5] or "",
                 "section_id": r[6], "section_name": r[7] or "Other"}
                for r in cur.fetchall()]


@app.post("/orchestra/rehearsals/{rehearsal_id}/attendance")
def orchestra_set_attendance(rehearsal_id: int, payload: dict, request: Request):
    user = require_orchestra_admin(request)
    member_id = payload.get("member_id")
    status = payload.get("status")  # attended | absent | excused | None to clear
    notes = (payload.get("notes") or "").strip() or None

    if not member_id:
        return {"status": "fail", "message": "member_id required"}
    if status and status not in ("attended", "absent", "excused"):
        return {"status": "fail", "message": "Invalid status"}

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT 1 FROM rehearsals WHERE id=%s AND org_id=%s", (rehearsal_id, user["org_id"]))
        if not cur.fetchone():
            return {"status": "fail", "message": "Rehearsal not found"}
        if status:
            cur.execute("""
                INSERT INTO orchestra_attendance (rehearsal_id, member_id, status, notes)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (rehearsal_id, member_id) DO UPDATE SET status=EXCLUDED.status, notes=EXCLUDED.notes
            """, (rehearsal_id, member_id, status, notes))
        else:
            cur.execute("DELETE FROM orchestra_attendance WHERE rehearsal_id=%s AND member_id=%s",
                        (rehearsal_id, member_id))
    return {"status": "success"}


# -- Subs -----------------------------------------------------------------

@app.get("/orchestra/subs")
def orchestra_get_subs(request: Request, section_id: Optional[int] = None):
    user = require_orchestra_user(request)
    org_id = user["org_id"]
    with db_cursor() as cur:
        if section_id:
            cur.execute("""
                SELECT s.id, s.fullname, s.email, s.phone, s.is_preferred, s.notes,
                       os2.name, s.section_id, s.preferred_rank,
                       COUNT(CASE WHEN osc.response='accepted' THEN 1 END),
                       COUNT(CASE WHEN osc.response='declined' THEN 1 END)
                FROM orchestra_subs s
                JOIN orchestra_sections os2 ON os2.id = s.section_id
                LEFT JOIN orchestra_sub_contacts osc ON osc.sub_id = s.id
                WHERE s.org_id=%s AND s.section_id=%s AND s.active=true
                GROUP BY s.id, os2.id
                ORDER BY s.is_preferred DESC, s.preferred_rank NULLS LAST, s.fullname
            """, (org_id, section_id))
        else:
            cur.execute("""
                SELECT s.id, s.fullname, s.email, s.phone, s.is_preferred, s.notes,
                       os2.name, s.section_id, s.preferred_rank,
                       COUNT(CASE WHEN osc.response='accepted' THEN 1 END),
                       COUNT(CASE WHEN osc.response='declined' THEN 1 END)
                FROM orchestra_subs s
                JOIN orchestra_sections os2 ON os2.id = s.section_id
                LEFT JOIN orchestra_sub_contacts osc ON osc.sub_id = s.id
                WHERE s.org_id=%s AND s.active=true
                GROUP BY s.id, os2.id
                ORDER BY os2.name, s.is_preferred DESC, s.preferred_rank NULLS LAST, s.fullname
            """, (org_id,))
        return [{"id": r[0], "fullname": r[1], "email": r[2], "phone": r[3] or "",
                 "is_preferred": r[4], "notes": r[5] or "", "section_name": r[6],
                 "section_id": r[7], "preferred_rank": r[8],
                 "accepted_count": r[9], "declined_count": r[10]}
                for r in cur.fetchall()]


@app.post("/orchestra/subs")
def orchestra_add_sub(payload: dict, request: Request):
    user = require_orchestra_admin(request)
    fullname = (payload.get("fullname") or "").strip()
    email = (payload.get("email") or "").strip().lower()
    section_id = payload.get("section_id")
    if not fullname or not email or not section_id:
        return {"status": "fail", "message": "Name, email, and section required"}
    phone = (payload.get("phone") or "").strip() or None
    is_preferred = bool(payload.get("is_preferred", False))
    notes = (payload.get("notes") or "").strip() or None
    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO orchestra_subs (org_id, section_id, fullname, email, phone, is_preferred, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (user["org_id"], section_id, fullname, email, phone, is_preferred, notes))
        new_id = cur.fetchone()[0]
    return {"status": "success", "id": new_id}


@app.patch("/orchestra/subs/{sub_id}")
def orchestra_update_sub(sub_id: int, payload: dict, request: Request):
    user = require_orchestra_admin(request)
    fields, vals = [], []
    for col in ("fullname", "email", "phone", "notes"):
        if col in payload:
            fields.append(f"{col}=%s")
            vals.append((payload[col] or "").strip() or None)
    if "is_preferred" in payload:
        fields.append("is_preferred=%s")
        vals.append(bool(payload["is_preferred"]))
    if "preferred_rank" in payload:
        fields.append("preferred_rank=%s")
        raw_rank = payload["preferred_rank"]
        vals.append(int(raw_rank) if raw_rank is not None else None)
    if "active" in payload:
        fields.append("active=%s")
        vals.append(bool(payload["active"]))
    if not fields:
        return {"status": "ok"}
    vals += [sub_id, user["org_id"]]
    with db_cursor(commit=True) as cur:
        cur.execute(f"UPDATE orchestra_subs SET {', '.join(fields)} WHERE id=%s AND org_id=%s", vals)
    return {"status": "success"}


@app.delete("/orchestra/subs/{sub_id}")
def orchestra_delete_sub(sub_id: int, request: Request):
    user = require_orchestra_admin(request)
    with db_cursor(commit=True) as cur:
        cur.execute("UPDATE orchestra_subs SET active=false WHERE id=%s AND org_id=%s",
                    (sub_id, user["org_id"]))
    return {"status": "success"}


# -- Sub Calling ----------------------------------------------------------

def _advance_preferred_orch_sub(req_id: int, rehearsal_id: int, section_id: int) -> bool:
    with db_cursor() as cur:
        cur.execute("""
            SELECT s.id, s.fullname, s.email
            FROM orchestra_subs s
            WHERE s.section_id=%s AND s.is_preferred=true AND s.active=true
              AND s.id NOT IN (SELECT sub_id FROM orchestra_sub_contacts WHERE sub_request_id=%s)
            ORDER BY s.preferred_rank NULLS LAST, s.fullname LIMIT 1
        """, (section_id, req_id))
        row = cur.fetchone()
    if row:
        _send_orch_sub_emails([{"id": row[0], "fullname": row[1], "email": row[2]}],
                              req_id, rehearsal_id, section_id, "preferred")
        with db_cursor(commit=True) as cur:
            cur.execute("""
                UPDATE orchestra_sub_requests SET status='preferred_sent',
                    preferred_sent_at=COALESCE(preferred_sent_at,NOW())
                WHERE id=%s AND status NOT IN ('filled','cancelled')
            """, (req_id,))
        return True
    # Preferred exhausted — bulk
    with db_cursor() as cur:
        cur.execute("""
            SELECT s.id, s.fullname, s.email FROM orchestra_subs s
            WHERE s.section_id=%s AND s.active=true
              AND s.id NOT IN (SELECT sub_id FROM orchestra_sub_contacts WHERE sub_request_id=%s)
        """, (section_id, req_id))
        remaining = [{"id": r[0], "fullname": r[1], "email": r[2]} for r in cur.fetchall()]
    if remaining:
        _send_orch_sub_emails(remaining, req_id, rehearsal_id, section_id, "regular")
    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE orchestra_sub_requests SET status='all_sent', all_sent_at=NOW()
            WHERE id=%s AND status NOT IN ('filled','cancelled')
        """, (req_id,))
    return False


def _send_orch_sub_emails(sub_list: list, sub_request_id: int, rehearsal_id: int,
                          section_id: int, tier: str, custom_message: str = None) -> int:
    with db_cursor() as cur:
        cur.execute("""
            SELECT r.start_time, r.location, r.notes, os2.name, o.name, o.id
            FROM rehearsals r
            JOIN orchestra_sections os2 ON os2.id=%s
            JOIN organizations o ON o.id = r.org_id
            WHERE r.id=%s
        """, (section_id, rehearsal_id))
        reh = cur.fetchone()
    if not reh:
        return 0
    start_dt = reh[0]
    rdate = start_dt.strftime("%A, %B %-d") if hasattr(start_dt, "strftime") else str(start_dt)
    rstart = start_dt.strftime("%H:%M") if hasattr(start_dt, "strftime") else ""
    section_name, org_name, org_id = reh[3], reh[4], reh[5]

    with db_cursor() as cur:
        cur.execute("""
            SELECT fullname, email FROM users
            WHERE org_id=%s AND role IN ('head_admin','orchestra_admin')
            ORDER BY CASE role WHEN 'head_admin' THEN 0 ELSE 1 END LIMIT 1
        """, (org_id,))
        adm = cur.fetchone()
    admin_name = adm[0] if adm else None
    admin_email = adm[1] if adm else None

    sent = 0
    for sub in sub_list:
        token = secrets.token_urlsafe(32)
        with db_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO orchestra_sub_contacts (sub_request_id, sub_id, tier, token)
                VALUES (%s,%s,%s,%s) ON CONFLICT (sub_request_id, sub_id) DO NOTHING
            """, (sub_request_id, sub["id"], tier, token))
            if cur.rowcount == 0:
                continue
        accept_url = f"{APP_URL}/orchestra/sub-response/{token}?r=accepted"
        decline_url = f"{APP_URL}/orchestra/sub-response/{token}?r=declined"
        custom_block = f"<p><em>{custom_message}</em></p>" if custom_message else ""
        html = f"""
            <p>Hi {sub['fullname']},</p>
            <p>The <strong>{org_name}</strong> orchestra is looking for a sub for the
            <strong>{section_name}</strong> section.</p>
            <p><strong>Date:</strong> {rdate} at {rstart}</p>
            {f"<p><strong>Location:</strong> {reh[1]}</p>" if reh[1] else ""}
            {f"<p><strong>Notes:</strong> {reh[2]}</p>" if reh[2] else ""}
            {custom_block}
            <p style='margin-top:16px;'>
              <a href='{accept_url}' style='background:#4caf50;color:#fff;padding:10px 20px;
                 border-radius:4px;text-decoration:none;margin-right:8px;'>✓ Accept</a>
              <a href='{decline_url}' style='background:#e53935;color:#fff;padding:10px 20px;
                 border-radius:4px;text-decoration:none;'>✗ Decline</a>
            </p>
            {f"<p style='margin-top:12px;color:#888;font-size:.85em;'>Questions? Reply to {admin_email}</p>" if admin_email else ""}
        """
        text = (f"Hi {sub['fullname']},\n\n{org_name} needs a sub for {section_name}.\n"
                f"Date: {rdate} at {rstart}\n"
                f"{f'Location: {reh[1]}' if reh[1] else ''}\n\n"
                f"Accept: {accept_url}\nDecline: {decline_url}")
        if send_email(sub["email"], f"Sub needed — {section_name} | {org_name}", html, text,
                      reply_to=admin_email):
            sent += 1
    return sent


@app.post("/orchestra/sub-request")
def orchestra_create_sub_request(payload: dict, request: Request):
    user = require_orchestra_admin(request)
    rehearsal_id = payload.get("rehearsal_id")
    section_id = payload.get("section_id")
    if not rehearsal_id or not section_id:
        return {"status": "fail", "message": "rehearsal_id and section_id required"}
    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT id FROM orchestra_sub_requests
            WHERE rehearsal_id=%s AND section_id=%s AND status NOT IN ('filled','cancelled')
        """, (rehearsal_id, section_id))
        existing = cur.fetchone()
        if existing:
            return {"status": "ok", "sub_request_id": existing[0], "existing": True}
        cur.execute("""
            INSERT INTO orchestra_sub_requests (rehearsal_id, section_id, created_by)
            VALUES (%s,%s,%s) RETURNING id
        """, (rehearsal_id, section_id, user["id"]))
        req_id = cur.fetchone()[0]
    return {"status": "success", "sub_request_id": req_id}


@app.get("/orchestra/sub-requests/{rehearsal_id}")
def orchestra_get_sub_requests(rehearsal_id: int, request: Request):
    require_orchestra_user(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT osr.id, osr.section_id, os2.name, osr.status,
                   osr.preferred_sent_at, osr.all_sent_at,
                   s2.fullname AS filled_by, osr.section_contacted_at, osr.absence_request_id
            FROM orchestra_sub_requests osr
            JOIN orchestra_sections os2 ON os2.id = osr.section_id
            LEFT JOIN orchestra_subs s2 ON s2.id = osr.filled_by_sub_id
            WHERE osr.rehearsal_id=%s
        """, (rehearsal_id,))
        rows = cur.fetchall()

    # Passive 8-hour escalation check for section_sent requests
    for r in rows:
        if r[3] == "section_sent" and r[7] and r[8]:
            _check_section_escalation(r[0], rehearsal_id, r[1], r[8])

    # Re-fetch after possible escalation
    with db_cursor() as cur:
        cur.execute("""
            SELECT osr.id, osr.section_id, os2.name, osr.status,
                   osr.preferred_sent_at, osr.all_sent_at,
                   s2.fullname AS filled_by
            FROM orchestra_sub_requests osr
            JOIN orchestra_sections os2 ON os2.id = osr.section_id
            LEFT JOIN orchestra_subs s2 ON s2.id = osr.filled_by_sub_id
            WHERE osr.rehearsal_id=%s
        """, (rehearsal_id,))
        rows = cur.fetchall()

    return [{"id": r[0], "section_id": r[1], "section_name": r[2], "status": r[3],
             "preferred_sent_at": str(r[4]) if r[4] else None,
             "all_sent_at": str(r[5]) if r[5] else None,
             "filled_by_name": r[6]}
            for r in rows]


@app.post("/orchestra/sub-request/{req_id}/contact-preferred")
def orchestra_contact_preferred(req_id: int, request: Request):
    require_orchestra_admin(request)
    with db_cursor() as cur:
        cur.execute("SELECT rehearsal_id, section_id, status FROM orchestra_sub_requests WHERE id=%s", (req_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404)
    rehearsal_id, section_id, status = row
    if status == "filled":
        return {"status": "fail", "message": "Already filled"}
    _advance_preferred_orch_sub(req_id, rehearsal_id, section_id)
    return {"status": "success"}


@app.post("/orchestra/sub-request/{req_id}/contact-all")
def orchestra_contact_all(req_id: int, request: Request):
    require_orchestra_admin(request)
    with db_cursor() as cur:
        cur.execute("SELECT rehearsal_id, section_id, status FROM orchestra_sub_requests WHERE id=%s", (req_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404)
    rehearsal_id, section_id, status = row
    if status == "filled":
        return {"status": "fail", "message": "Already filled"}
    with db_cursor() as cur:
        cur.execute("""
            SELECT s.id, s.fullname, s.email FROM orchestra_subs s
            WHERE s.section_id=%s AND s.active=true
              AND s.id NOT IN (SELECT sub_id FROM orchestra_sub_contacts WHERE sub_request_id=%s)
        """, (section_id, req_id))
        remaining = [{"id": r[0], "fullname": r[1], "email": r[2]} for r in cur.fetchall()]
    sent = _send_orch_sub_emails(remaining, req_id, rehearsal_id, section_id, "regular")
    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE orchestra_sub_requests SET status='all_sent', all_sent_at=NOW()
            WHERE id=%s AND status NOT IN ('filled','cancelled')
        """, (req_id,))
    return {"status": "success", "sent": sent}


@app.post("/orchestra/sub-request/{req_id}/cancel")
def orchestra_cancel_sub_request(req_id: int, request: Request):
    require_orchestra_admin(request)
    with db_cursor(commit=True) as cur:
        cur.execute("UPDATE orchestra_sub_requests SET status='cancelled' WHERE id=%s", (req_id,))
    return {"status": "success"}


# -- Invitations ----------------------------------------------------------

@app.post("/orchestra/invite")
def orchestra_send_invite(payload: dict, request: Request):
    user = require_orchestra_admin(request)
    email = (payload.get("email") or "").strip().lower()
    fullname = (payload.get("fullname") or "").strip() or None
    if not email:
        return {"status": "fail", "message": "Email required"}

    token = secrets.token_urlsafe(32)
    expires = datetime.now(pytz.utc) + timedelta(days=7)
    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO invitations (email, role, org_id, token, expires_at, invited_by, fullname_hint)
            VALUES (%s,'orchestra_member',%s,%s,%s,%s,%s)
            ON CONFLICT (email, org_id) DO UPDATE SET token=EXCLUDED.token, expires_at=EXCLUDED.expires_at
        """, (email, user["org_id"], token, expires, user["id"], fullname))

    invite_url = f"{APP_URL}/invite/{token}"
    org_name = "Orchestra"  # could be fetched; good enough for now
    html = f"<p>You've been invited to join {org_name} on Countrpnt.</p><p><a href='{invite_url}'>Accept Invitation</a></p>"
    text = f"You've been invited to join {org_name} on Countrpnt.\nAccept: {invite_url}"
    send_email(email, f"Invitation to join {org_name}", html, text)
    return {"status": "success"}


@app.get("/orchestra/invitations")
def orchestra_get_invitations(request: Request):
    user = require_orchestra_admin(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT email, role, expires_at, fullname_hint
            FROM invitations WHERE org_id=%s ORDER BY expires_at DESC
        """, (user["org_id"],))
        return [{"email": r[0], "role": r[1], "expires_at": str(r[2]), "fullname": r[3] or ""}
                for r in cur.fetchall()]


# ========================================================
# ORCHESTRA ABSENCE + SECTION COVERAGE
# ========================================================

def _trigger_section_coverage(absence_request_id: int, rehearsal_id: int, absent_member_id: int, admin_user_id: int = None):
    """
    Email all other active members in the absent member's section.
    Creates an orchestra_sub_request linked to the absence with status='section_sent'.
    After 8 hrs or all decline, the caller of coverage-response auto-escalates to preferred subs.
    admin_user_id must be a valid users.id — used as created_by on orchestra_sub_requests.
    """
    with db_cursor() as cur:
        cur.execute("""
            SELECT om.section_id, om.fullname, os2.name AS section_name,
                   r.start_time, r.location, r.notes, o.name AS org_name, o.id AS org_id
            FROM orchestra_members om
            LEFT JOIN orchestra_sections os2 ON os2.id = om.section_id
            JOIN rehearsals r ON r.id = %s
            JOIN organizations o ON o.id = r.org_id
            WHERE om.id = %s
        """, (rehearsal_id, absent_member_id))
        row = cur.fetchone()
    if not row:
        return
    section_id, absent_name, section_name, start_time, location, notes, org_name, org_id = row
    if not section_id:
        # No section — nothing to do
        return

    # Get other members in this section who have email
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, fullname, email FROM orchestra_members
            WHERE section_id=%s AND active=true AND email IS NOT NULL AND id != %s
        """, (section_id, absent_member_id))
        section_members = [{"id": r[0], "fullname": r[1], "email": r[2]} for r in cur.fetchall()]

    # Upsert a sub request linked to this absence, in 'section_sent' status
    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT id FROM orchestra_sub_requests
            WHERE rehearsal_id=%s AND section_id=%s AND status NOT IN ('filled','cancelled')
        """, (rehearsal_id, section_id))
        existing = cur.fetchone()
        if existing:
            req_id = existing[0]
            cur.execute("""
                UPDATE orchestra_sub_requests
                SET status='section_sent', section_contacted_at=NOW(), absence_request_id=%s
                WHERE id=%s
            """, (absence_request_id, req_id))
        else:
            cur.execute("""
                INSERT INTO orchestra_sub_requests
                    (rehearsal_id, section_id, created_by, status, section_contacted_at, absence_request_id)
                VALUES (%s, %s, %s, 'section_sent', NOW(), %s) RETURNING id
            """, (rehearsal_id, section_id, admin_user_id, absence_request_id))
            req_id = cur.fetchone()[0]

    if not section_members:
        # No section mates with email — go straight to preferred subs
        _advance_preferred_orch_sub(req_id, rehearsal_id, section_id)
        return

    rdate = start_time.strftime("%A, %B %-d") if hasattr(start_time, "strftime") else str(start_time)
    rtime = start_time.strftime("%H:%M") if hasattr(start_time, "strftime") else ""

    for m in section_members:
        token = secrets.token_urlsafe(32)
        with db_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO orchestra_section_coverage_contacts
                    (absence_request_id, member_id, token)
                VALUES (%s, %s, %s)
                ON CONFLICT (absence_request_id, member_id) DO NOTHING
            """, (absence_request_id, m["id"], token))
            if cur.rowcount == 0:
                continue

        accept_url = f"{APP_URL}/orchestra/coverage-response/{token}?r=accepted"
        decline_url = f"{APP_URL}/orchestra/coverage-response/{token}?r=declined"
        html = f"""
            <p>Hi {m['fullname']},</p>
            <p><strong>{absent_name}</strong> is unable to attend the
            <strong>{org_name}</strong> {section_name} rehearsal:</p>
            <p><strong>Date:</strong> {rdate} at {rtime}</p>
            {f"<p><strong>Location:</strong> {location}</p>" if location else ""}
            {f"<p><strong>Notes:</strong> {notes}</p>" if notes else ""}
            <p>Are you able to cover their part?</p>
            <p style='margin-top:16px;'>
              <a href='{accept_url}' style='background:#4caf50;color:#fff;padding:10px 20px;
                 border-radius:4px;text-decoration:none;margin-right:8px;'>✓ Yes, I can cover</a>
              <a href='{decline_url}' style='background:#e53935;color:#fff;padding:10px 20px;
                 border-radius:4px;text-decoration:none;'>✗ Not available</a>
            </p>
        """
        text = (f"Hi {m['fullname']},\n\n{absent_name} can't make the {section_name} rehearsal "
                f"on {rdate} at {rtime}.\nCan you cover?\n\nYes: {accept_url}\nNo: {decline_url}")
        send_email(m["email"], f"Coverage needed — {section_name} | {org_name}", html, text)


def _check_section_escalation(req_id: int, rehearsal_id: int, section_id: int, absence_request_id: int):
    """Check if section coverage has stalled — escalate to preferred subs if so."""
    with db_cursor() as cur:
        cur.execute("""
            SELECT section_contacted_at, status FROM orchestra_sub_requests WHERE id=%s
        """, (req_id,))
        row = cur.fetchone()
    if not row or row[1] not in ('section_sent',):
        return
    section_contacted_at, _ = row

    # Check if 8 hours have passed
    from datetime import timezone
    now_utc = datetime.now(timezone.utc)
    contacted = section_contacted_at
    if not hasattr(contacted, 'tzinfo') or contacted.tzinfo is None:
        contacted = contacted.replace(tzinfo=timezone.utc)
    elapsed_hours = (now_utc - contacted).total_seconds() / 3600

    if elapsed_hours >= 8:
        _advance_preferred_orch_sub(req_id, rehearsal_id, section_id)
        return

    # Check if all section members have declined
    with db_cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FILTER (WHERE response='pending'),
                   COUNT(*) FILTER (WHERE response='declined')
            FROM orchestra_section_coverage_contacts
            WHERE absence_request_id=%s
        """, (absence_request_id,))
        pending, declined = cur.fetchone()
    if pending == 0 and declined > 0:
        # Everyone said no → escalate
        _advance_preferred_orch_sub(req_id, rehearsal_id, section_id)


# ── Coverage response (section member one-click) --------------------------

@app.get("/orchestra/coverage-response/{token}", response_class=HTMLResponse)
def orchestra_coverage_response(token: str, r: str = ""):
    with db_cursor() as cur:
        cur.execute("""
            SELECT oscc.id, oscc.member_id, oscc.absence_request_id, oscc.response,
                   om.fullname,
                   oar.rehearsal_id, oar.member_id AS absent_member_id
            FROM orchestra_section_coverage_contacts oscc
            JOIN orchestra_members om ON om.id = oscc.member_id
            JOIN orchestra_absence_requests oar ON oar.id = oscc.absence_request_id
            WHERE oscc.token = %s
        """, (token,))
        row = cur.fetchone()
    if not row:
        return HTMLResponse("<p>Link not found or expired.</p>", status_code=404)
    contact_id, member_id, absence_req_id, existing_resp, fullname, rehearsal_id, absent_mid = row

    if r not in ("accepted", "declined"):
        return HTMLResponse(f"<p>Hi {fullname}! Use the link in your email to respond.</p>")
    if existing_resp != "pending":
        return HTMLResponse(f"<p>You already responded ({existing_resp}). Thanks!</p>")

    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE orchestra_section_coverage_contacts
            SET response=%s, responded_at=NOW() WHERE id=%s
        """, (r, contact_id))

    if r == "accepted":
        # Mark sub request as filled via internal coverage
        with db_cursor(commit=True) as cur:
            cur.execute("""
                UPDATE orchestra_sub_requests SET status='filled'
                WHERE absence_request_id=%s AND status NOT IN ('filled','cancelled')
            """, (absence_req_id,))
            # Decline all other pending coverage contacts
            cur.execute("""
                UPDATE orchestra_section_coverage_contacts
                SET response='declined', responded_at=NOW()
                WHERE absence_request_id=%s AND id != %s AND response='pending'
            """, (absence_req_id, contact_id))
        return HTMLResponse(f"<p>Thanks, {fullname}! Your coverage has been recorded. The admin has been notified.</p>")
    else:
        # Declined — check if we need to escalate
        with db_cursor() as cur:
            cur.execute("""
                SELECT osr.id, osr.section_id, osr.status
                FROM orchestra_sub_requests osr
                WHERE osr.absence_request_id=%s AND osr.status NOT IN ('filled','cancelled')
                LIMIT 1
            """, (absence_req_id,))
            sr = cur.fetchone()
        if sr:
            _check_section_escalation(sr[0], rehearsal_id, sr[1], absence_req_id)
        return HTMLResponse(f"<p>Thanks, {fullname}. We'll continue looking for coverage.</p>")


# ── Member: submit absence request (standalone orchestra org) -------------

def _require_orchestra_member_account(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    if user.get("org_type") != "orchestra" or user.get("role") != "orchestra_member":
        raise HTTPException(status_code=403)
    return user


@app.post("/orchestra/member-absence-request")
def orchestra_member_submit_absence(payload: dict, request: Request):
    user = _require_orchestra_member_account(request)
    rehearsal_id = payload.get("rehearsal_id")
    reason = (payload.get("reason") or "").strip()
    note = (payload.get("note") or "").strip() or None
    if not rehearsal_id or not reason:
        return {"status": "fail", "message": "rehearsal_id and reason required"}

    # Find this user's orchestra_members row
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, section_id, fullname FROM orchestra_members
            WHERE org_id=%s AND (user_id=%s OR email=%s) AND active=true LIMIT 1
        """, (user["org_id"], user["id"], user.get("email", "")))
        member_row = cur.fetchone()

    if not member_row:
        return {"status": "fail", "message": "Your roster entry was not found. Contact your orchestra manager."}
    member_id, _, member_name = member_row

    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO orchestra_absence_requests (rehearsal_id, member_id, reason, note, status)
            VALUES (%s, %s, %s, %s, 'pending')
            ON CONFLICT (rehearsal_id, member_id) DO UPDATE
                SET reason=EXCLUDED.reason, note=EXCLUDED.note, status='pending'
            RETURNING id
        """, (rehearsal_id, member_id, reason, note))
        new_id = cur.fetchone()[0]

    # Notify admins
    with db_cursor() as cur:
        cur.execute("""
            SELECT u.email, r.start_time FROM rehearsals r
            JOIN organizations o ON o.id = r.org_id
            JOIN users u ON u.org_id = o.id AND u.role IN ('head_admin','orchestra_admin')
            WHERE r.id=%s AND o.id=%s AND u.email IS NOT NULL
        """, (rehearsal_id, user["org_id"]))
        admin_rows = cur.fetchall()

    if admin_rows:
        start_time = admin_rows[0][1]
        rdate = start_time.strftime("%A, %B %-d") if hasattr(start_time, "strftime") else str(start_time)
        for admin_email, _ in admin_rows:
            html = (f"<p><strong>{member_name}</strong> has requested an absence for the rehearsal "
                    f"on <strong>{rdate}</strong>.</p>"
                    f"<p><strong>Reason:</strong> {reason}</p>"
                    + (f"<p><strong>Note:</strong> {note}</p>" if note else "")
                    + f"<p>Approve or deny from the rehearsal attendance panel.</p>")
            text = f"{member_name} has requested absence for {rdate}.\nReason: {reason}"
            send_email(admin_email, f"Absence request — {member_name}", html, text)

    return {"status": "success", "absence_request_id": new_id}


@app.delete("/orchestra/member-absence-request/{rehearsal_id}")
def orchestra_member_cancel_absence(rehearsal_id: int, request: Request):
    user = _require_orchestra_member_account(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT om.id FROM orchestra_members om
            WHERE om.org_id=%s AND (om.user_id=%s OR om.email=%s) AND om.active=true LIMIT 1
        """, (user["org_id"], user["id"], user.get("email", "")))
        row = cur.fetchone()
    if not row:
        return {"status": "fail"}
    member_id = row[0]
    with db_cursor(commit=True) as cur:
        cur.execute("""
            DELETE FROM orchestra_absence_requests WHERE rehearsal_id=%s AND member_id=%s
        """, (rehearsal_id, member_id))
    return {"status": "success"}


@app.get("/orchestra/member-absence-requests")
def orchestra_member_get_absences(request: Request):
    user = _require_orchestra_member_account(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT om.id FROM orchestra_members om
            WHERE om.org_id=%s AND (om.user_id=%s OR om.email=%s) AND om.active=true LIMIT 1
        """, (user["org_id"], user["id"], user.get("email", "")))
        row = cur.fetchone()
    if not row:
        return []
    member_id = row[0]
    with db_cursor() as cur:
        cur.execute("""
            SELECT rehearsal_id, status FROM orchestra_absence_requests WHERE member_id=%s
        """, (member_id,))
        return [{"rehearsal_id": r[0], "status": r[1]} for r in cur.fetchall()]


# ── Admin: view + approve/deny + direct-mark absence ----------------------

@app.get("/orchestra/rehearsals/{rehearsal_id}/absence-requests")
def orchestra_get_absence_requests(rehearsal_id: int, request: Request):
    require_orchestra_admin(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT oar.id, oar.member_id, om.fullname, om.section_id, os2.name,
                   oar.reason, oar.note, oar.status, oar.created_at
            FROM orchestra_absence_requests oar
            JOIN orchestra_members om ON om.id = oar.member_id
            LEFT JOIN orchestra_sections os2 ON os2.id = om.section_id
            WHERE oar.rehearsal_id=%s
            ORDER BY oar.created_at
        """, (rehearsal_id,))
        return [{"id": r[0], "member_id": r[1], "fullname": r[2],
                 "section_id": r[3], "section_name": r[4] or "",
                 "reason": r[5] or "", "note": r[6] or "",
                 "status": r[7], "created_at": str(r[8])}
                for r in cur.fetchall()]


@app.post("/orchestra/absence-request/{absence_id}/approve")
def orchestra_approve_absence(absence_id: int, request: Request):
    user = require_orchestra_admin(request)
    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE orchestra_absence_requests
            SET status='approved', reviewed_at=NOW(), reviewed_by=%s
            WHERE id=%s RETURNING rehearsal_id, member_id
        """, (user["id"], absence_id))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404)
    rehearsal_id, member_id = row

    # Notify member if they have an account email
    with db_cursor() as cur:
        cur.execute("""
            SELECT om.fullname, u.email, r.start_time
            FROM orchestra_members om
            LEFT JOIN users u ON u.id = om.user_id
            JOIN rehearsals r ON r.id=%s
            WHERE om.id=%s
        """, (rehearsal_id, member_id))
        mrow = cur.fetchone()
    if mrow and mrow[1]:
        rdate = mrow[2].strftime("%A, %B %-d") if hasattr(mrow[2], "strftime") else str(mrow[2])
        send_email(mrow[1], f"Absence approved — {rdate}",
                   f"<p>Hi {mrow[0]},</p><p>Your absence for the rehearsal on {rdate} has been approved.</p>",
                   f"Hi {mrow[0]},\n\nYour absence for {rdate} has been approved.")

    _trigger_section_coverage(absence_id, rehearsal_id, member_id, admin_user_id=user["id"])
    return {"status": "success"}


@app.post("/orchestra/absence-request/{absence_id}/deny")
def orchestra_deny_absence(absence_id: int, request: Request):
    user = require_orchestra_admin(request)
    with db_cursor() as cur:
        cur.execute("""
            SELECT rehearsal_id, member_id FROM orchestra_absence_requests WHERE id=%s
        """, (absence_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404)
    rehearsal_id, member_id = row

    with db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM orchestra_absence_requests WHERE id=%s", (absence_id,))

    # Notify member if they have an account
    with db_cursor() as cur:
        cur.execute("""
            SELECT om.fullname, u.email, r.start_time
            FROM orchestra_members om LEFT JOIN users u ON u.id=om.user_id
            JOIN rehearsals r ON r.id=%s WHERE om.id=%s
        """, (rehearsal_id, member_id))
        mrow = cur.fetchone()
    if mrow and mrow[1]:
        rdate = mrow[2].strftime("%A, %B %-d") if hasattr(mrow[2], "strftime") else str(mrow[2])
        send_email(mrow[1], f"Absence not approved — {rdate}",
                   f"<p>Hi {mrow[0]},</p><p>Your absence request for {rdate} was not approved. "
                   f"Please contact your manager if you have questions.</p>",
                   f"Hi {mrow[0]},\n\nYour absence for {rdate} was not approved.")
    return {"status": "success"}


@app.post("/orchestra/admin-mark-absent")
def orchestra_admin_mark_absent(payload: dict, request: Request):
    """Admin directly marks a member absent — triggers section coverage immediately."""
    user = require_orchestra_admin(request)
    rehearsal_id = payload.get("rehearsal_id")
    member_id = payload.get("member_id")
    reason = (payload.get("reason") or "Admin marked absent").strip()
    if not rehearsal_id or not member_id:
        return {"status": "fail", "message": "rehearsal_id and member_id required"}

    # Record attendance only — no sub notifications for day-of marking
    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO orchestra_attendance (rehearsal_id, member_id, status, notes)
            VALUES (%s,%s,'absent',%s)
            ON CONFLICT (rehearsal_id, member_id) DO UPDATE SET status='absent', notes=EXCLUDED.notes
        """, (rehearsal_id, member_id, reason))
    return {"status": "success"}
