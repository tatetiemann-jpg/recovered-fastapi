"""
Coaching Scheduler — FastAPI backend.

All users (students, teachers, admins) live in one `users` table,
distinguished by the `role` column. Organizations are stored in `organizations`
and referenced by org_id. For now we hardcode the org to "boa" since the
app supports one festival at a time.
"""
from fastapi import FastAPI, Request, Query, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
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
            cur.execute("ALTER TABLE invitations ADD COLUMN IF NOT EXISTS instrument VARCHAR(100);")
            cur.execute("ALTER TABLE rehearsals ADD COLUMN IF NOT EXISTS choir_type VARCHAR(20) DEFAULT 'choir';")
            cur.execute("ALTER TABLE rehearsals ADD COLUMN IF NOT EXISTS materials_url TEXT;")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rehearsal_members (
                    rehearsal_id INT REFERENCES rehearsals(id) ON DELETE CASCADE,
                    user_id INT REFERENCES users(id) ON DELETE CASCADE,
                    PRIMARY KEY (rehearsal_id, user_id)
                );
            """)
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
    except Exception:
        conn.rollback()
        raise
    finally:
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


def require_head_admin(request: Request):
    """Returns the current user only if they are head_admin or system_admin."""
    user = require_user(request)
    if user["role"] not in ("head_admin", "system_admin"):
        raise HTTPException(status_code=403, detail="Head admin access required")
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

def is_booking_window_open_for(target_date, tz=None) -> bool:
    """
    Returns True if the booking window for `target_date` is currently open.

    Booking window rules:
      - Opens at 9:00 PM the day before target_date
      - Closes at 6:00 PM on target_date itself
      - All times in the org's local timezone (defaults to US/Eastern)
    """
    if tz is None:
        tz = EST
    now_local = datetime.now(tz)
    window_open = tz.localize(datetime.combine(target_date - timedelta(days=1), dtime(21, 0)))
    window_close = tz.localize(datetime.combine(target_date, dtime(18, 0)))
    return window_open <= now_local <= window_close

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
            SELECT teacher_id, lesson_time
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
    for teacher_id, lesson_time in booked_rows:
        if lesson_time is not None:
            booked_by_teacher.setdefault(teacher_id, set()).add(lesson_time)

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


def get_available_slots(teacher_id: int, target_date, student_id: Optional[int] = None, conflict_ctx: Optional[dict] = None, avail_ctx: Optional[dict] = None, tz=None):
    """
    Build the list of available 30-min slots for a given teacher on a given date,
    applying all filtering rules:
      - exception rows for this date (if any), which fully override the weekly template
      - otherwise, the teacher's weekly availability for that weekday
      - lunch hour (1pm–2pm) excluded
      - past times on today excluded
      - slots already booked by another student excluded
      - if student_id given, slots conflicting with that student's rehearsals excluded

    Returns a list of "HH:MM" strings.
    """
    _tz = tz if tz is not None else EST
    now_local = datetime.now(_tz)
    weekday = target_date.weekday()

    if avail_ctx is not None:
        # Fast path: use pre-fetched bulk data
        if teacher_id in avail_ctx["has_any_exception_by_teacher"]:
            ranges = avail_ctx["exceptions_by_teacher"].get(teacher_id, [])
        else:
            ranges = avail_ctx["weekly_by_teacher"].get(teacher_id, [])
        booked = avail_ctx["booked_by_teacher"].get(teacher_id, set())
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
                SELECT lesson_time FROM lessons
                WHERE teacher_id = %s AND lesson_date = %s AND status = 'booked'
            """, (teacher_id, target_date))
            booked = {r[0] for r in cur.fetchall() if r[0]}

    slots = []
    for start_t, end_t in ranges:
        cur_dt = _tz.localize(datetime.combine(target_date, start_t))
        end_dt = _tz.localize(datetime.combine(target_date, end_t))

        while cur_dt < end_dt:
            slot_time = cur_dt.time()

            # skip past slots on today
            if target_date == now_local.date() and cur_dt <= now_local:
                cur_dt += timedelta(minutes=30)
                continue

            # skip slots that overlap the lunch hour
            # (slot is 30 min starting at slot_time; exclude if it overlaps 13:00–14:00)
            slot_end_t = (cur_dt + timedelta(minutes=30)).time()
            if slot_time < LUNCH_END and slot_end_t > LUNCH_START:
                cur_dt += timedelta(minutes=30)
                continue

            # skip slots already booked
            if slot_time in booked:
                cur_dt += timedelta(minutes=30)
                continue

            # skip slots that conflict with the student's rehearsals
            if student_id is not None:
                if conflict_ctx is not None:
                    if check_rehearsal_conflict_cached(conflict_ctx, target_date, slot_time, tz=_tz):
                        cur_dt += timedelta(minutes=30)
                        continue
                elif get_student_rehearsal_conflicts(student_id, target_date, slot_time, tz=_tz):
                    cur_dt += timedelta(minutes=30)
                    continue

            slots.append(cur_dt.strftime("%H:%M"))
            cur_dt += timedelta(minutes=30)

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


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    return templates.TemplateResponse(request, "admin.html")


@app.get("/teacher", response_class=HTMLResponse)
def teacher_page(request: Request):
    return templates.TemplateResponse(request, "teacher.html")


@app.get("/student", response_class=HTMLResponse)
def student_page(request: Request):
    return templates.TemplateResponse(request, "student.html")


@app.get("/orchestra-member", response_class=HTMLResponse)
def orchestra_member_page(request: Request):
    return templates.TemplateResponse(request, "orchestra_member.html")


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
    response.delete_cookie("session", path="/")
    return {"success": True}


@app.get("/me")
def me(request: Request):
    """Returns the current user's basic info. Used by frontend on page load."""
    user = current_user(request)
    if not user:
        return {"logged_in": False}
    org_name = None
    if user.get("org_id"):
        with db_cursor() as cur:
            cur.execute("SELECT name FROM organizations WHERE id = %s", (user["org_id"],))
            row = cur.fetchone()
        org_name = row[0] if row else None
    return {
        "logged_in": True,
        "username": user["username"],
        "fullname": user["fullname"],
        "role": user["role"],
        "email_verified": user.get("email_verified", True),
        "theme": user.get("theme", "queen-of-the-night"),
        "org_name": org_name,
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

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "noreply@countrpnt.com")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "Countrpnt")
APP_URL = os.environ.get("APP_URL", "https://countrpnt.com")



PASSWORD_RESET_TOKEN_HOURS = 1


def send_email(to: str, subject: str, html_body: str, text_body: str) -> bool:
    """
    Send an email via Resend. Returns True on success, False on failure.
    Logs but does not raise — email failures should not break the API.
    """
    if not RESEND_API_KEY:
        print("[email] RESEND_API_KEY not configured; skipping send.")
        return False

    # Test-domain safety: redirect all mail to the override address
    real_to = to

    resend.api_key = RESEND_API_KEY
    try:
        resend.Emails.send({
            "from": f"{EMAIL_FROM_NAME} <{EMAIL_FROM}>",
            "to": [real_to],
            "subject": subject,
            "html": html_body,
            "text": text_body,
        })
        print(f"[email debug] FROM: {EMAIL_FROM} <{EMAIL_FROM}>")
        print(f"[email] Sent to {real_to}: {subject}"
              )
        return True
    except Exception as e:
        print(f"[email] Failed to send to {real_to}: {e}")
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
    Accept an email; if it matches a user, send a password reset link.
    Always returns success so we don't leak which addresses are registered.
    """
    email = (payload.get("email") or "").strip().lower()
    if not email:
        return {"success": True}  # No-op, but don't leak that the field was empty

    # Find the user by email globally — emails are unique across the system
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, fullname FROM users
            WHERE email = %s
        """, (email,))
        row = cur.fetchone()

    if not row:
        # Pretend success even though no user was found
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
    }
    role_label = role_labels.get(role, "Teacher")
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

    if teacher_type not in ("vocal", "instrumental"):
        teacher_type = "vocal"

    # Enforce invite hierarchy:
    #   system_admin   → can only invite head_admin (platform-level role)
    #   head_admin     → can invite admin, orchestra_admin, teacher
    #   admin          → can invite teacher only
    #   orchestra_admin → can invite teacher only
    org_type = admin_user.get("org_type", "opera")
    allowed_by_role = {
        "system_admin":    {"head_admin", "admin", "student"},
        "head_admin":      {"admin", "orchestra_admin", "teacher"},
        "admin":           {"teacher", "student", "choir_member", "ensemble_member"} if org_type == "choir" else {"teacher"},
        "orchestra_admin": {"teacher"},
    }
    allowed = allowed_by_role.get(admin_user["role"], set())
    if role not in allowed:
        return {"status": "fail", "message": f"Your role cannot invite '{role}'."}

    if not email or not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        return {"status": "fail", "message": "Please enter a valid email."}

    # system_admin inviting a head_admin can specify (or create) an org for them
    org_name = (payload.get("org_name") or "").strip()
    org_slug = (payload.get("org_slug") or "").strip().lower()

    new_org_type = (payload.get("org_type") or "opera").strip()
    if new_org_type not in ("opera", "choir"):
        new_org_type = "opera"

    if admin_user["role"] == "system_admin" and org_slug:
        # Validate slug format
        if not re.match(r"^[a-z0-9-]+$", org_slug):
            return {"status": "fail", "message": "Organization ID may only contain lowercase letters, numbers, and hyphens."}
        existing = get_org_id(org_slug)
        if existing:
            org_id = existing
            # Update org_type if supplied
            with db_cursor(commit=True) as cur:
                cur.execute("UPDATE organizations SET org_type=%s WHERE id=%s", (new_org_type, org_id))
        elif org_name:
            # Create the org on the fly with the specified type
            with db_cursor(commit=True) as cur:
                cur.execute("""
                    INSERT INTO organizations (name, slug, org_type)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                """, (org_name, org_slug, new_org_type))
                org_id = cur.fetchone()[0]
            _org_id_cache[org_slug] = org_id
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
                                     teacher_type, teacher_instruments, instrument)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (token, email, role, org_id, admin_user["id"],
              fullname_hint, specialty_hint, expires,
              teacher_type, teacher_instruments, instrument))

    invite_url = f"{APP_URL}/accept-invite?token={token}"

    # Look up org name to include in the email
    with db_cursor() as cur:
        cur.execute("SELECT name FROM organizations WHERE id = %s", (org_id,))
        org_row = cur.fetchone()
    resolved_org_name = org_row[0] if org_row else org_name or ""

    html, text = render_invite_email(invite_url, role, fullname_hint or "", admin_user.get("fullname", ""), resolved_org_name)
    sent = send_email(email, "You've been invited to CountrPnt", html, text)

    return {"status": "success", "email_sent": sent}


@app.get("/admin/invitations")
def admin_invitations(request: Request):
    """List pending and recent invitations for the admin's org."""
    user = require_user(request, role="admin")

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
                WHERE i.org_id = %s
                ORDER BY i.created_at DESC
                LIMIT 100
            """, (user["org_id"],))
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
                   COALESCE(o.org_type, 'opera') AS org_type, i.instrument
            FROM invitations i
            LEFT JOIN organizations o ON o.id = i.org_id
            WHERE i.token = %s
        """, (token,))
        row = cur.fetchone()

    if not row:
        return {"valid": False, "message": "Invalid invitation link."}

    email, role, fname, spec, expires, accepted, t_type, t_instruments, org_name, org_id, org_type, inv_instrument = row
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
                   teacher_type, teacher_instruments, instrument
            FROM invitations
            WHERE token = %s
        """, (token,))
        row = cur.fetchone()

        if not row:
            return {"status": "fail", "message": "Invalid invitation link."}

        email, role, org_id, expires, accepted, t_type, t_instruments, inv_instrument = row
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
                    section_id, instrument
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'bcrypt', TRUE, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                org_id, username, email, hash_password_bcrypt(password),
                fullname, role, voice_type, specialty, theme, t_type, t_instruments,
                section_id, inv_instrument
            ))
            user_id = cur.fetchone()[0]
        except pg_errors.UniqueViolation:
            return {"status": "fail", "message": "Username already in use. Pick a different one."}

        cur.execute(
            "UPDATE invitations SET accepted_at = NOW() WHERE token = %s",
            (token,)
        )

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
    """List non-choir organizations for the public signup dropdown. Choir orgs are invite-only."""
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, slug, name FROM organizations
            WHERE COALESCE(org_type, 'opera') != 'choir'
            ORDER BY name
        """)
        rows = cur.fetchall()
    return [{"id": r[0], "slug": r[1], "name": r[2]} for r in rows]


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
def admin_casts(opera_id: int):
    with db_cursor() as cur:
        cur.execute("""
            SELECT id, name FROM casts
            WHERE opera_id=%s
            ORDER BY name
        """, (opera_id,))
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1]} for r in rows]


@app.get("/admin/valid-roles")
def valid_roles(
    student_id: int,
    opera_id: int,
    cast_id: Optional[int] = Query(default=None)
):
    voice_compatibility = {
        "bass": ["bass", "bass-baritone", "spoken"],
        "baritone": ["baritone", "bass-baritone", "spoken"],
        "tenor": ["tenor", "spoken"],
        "mezzo-soprano": ["mezzo-soprano", "spoken"],
        "soprano": ["soprano", "spoken"],
    }

    with db_cursor() as cur:
        cur.execute("SELECT voice_type FROM users WHERE id = %s", (student_id,))
        row = cur.fetchone()
        voice_type = row[0] if row and row[0] else None

        if voice_type:
            allowed = voice_compatibility.get(voice_type.lower(), [voice_type.lower()])
        else:
            allowed = []

        cur.execute("""
            SELECT role_name FROM opera_roles
            WHERE opera_id = %s
              AND (
                    LOWER(voice_type) = ANY(%s)
                 OR voice_type = 'Any'
                 OR voice_type IS NULL
              )
            ORDER BY role_name
        """, (opera_id, allowed))
        roles = [r[0] for r in cur.fetchall()]

        # Ensure "Chorus" at top, once.
        roles = [r for r in roles if r.lower() != "chorus"]
        roles.insert(0, "Chorus")

        current_role = "Chorus"
        if cast_id is not None:
            cur.execute("""
                SELECT role_name FROM student_roles
                WHERE student_id=%s AND opera_id=%s AND cast_id=%s
            """, (student_id, opera_id, cast_id))
            r = cur.fetchone()
            if r and r[0]:
                current_role = r[0]

    return {"roles": roles, "current_role": current_role}


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

                created += 1
            except Exception as e:
                print(f"[admin_bulk] skipped {rdate}: {e}")

    return {"status": "success", "created": created}


def render_rehearsal_notes_email(opera_name: str, date_str: str, time_str: str, notes: str):
    html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:600px;margin:auto;padding:24px;">
<h2 style="color:#333;margin-bottom:4px;">Rehearsal Notes: {opera_name}</h2>
<p style="color:#888;margin-top:0;">{date_str} &middot; {time_str}</p>
<hr style="border:none;border-top:1px solid #eee;margin:16px 0;">
<div style="white-space:pre-wrap;font-size:15px;line-height:1.6;color:#222;">{notes}</div>
</body></html>"""
    text = f"Rehearsal Notes: {opera_name}\n{date_str} \xb7 {time_str}\n\n{notes}"
    return html, text


def render_choir_notes_email(date_str: str, time_str: str, notes: str):
    html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:600px;margin:auto;padding:24px;">
<h2 style="color:#333;margin-bottom:4px;">Rehearsal Notes</h2>
<p style="color:#888;margin-top:0;">{date_str} &middot; {time_str}</p>
<hr style="border:none;border-top:1px solid #eee;margin:16px 0;">
<div style="white-space:pre-wrap;font-size:15px;line-height:1.6;color:#222;">{notes}</div>
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

        # Org admins
        cur.execute("""
            SELECT fullname, email FROM users
            WHERE org_id = %s AND role IN ('admin', 'head_admin', 'system_admin')
              AND email IS NOT NULL
        """, (org_id,))
        recipients = {email: name for name, email in cur.fetchall() if email}

        # Called students / orchestra members
        if rehearsal_type == "orchestra":
            cur.execute("""
                SELECT fullname, email FROM users
                WHERE org_id = %s AND role = 'orchestra_member' AND email IS NOT NULL
            """, (org_id,))
            for name, email in cur.fetchall():
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
                SELECT DISTINCT u.fullname, u.email FROM users u
                JOIN student_roles sr ON sr.student_id = u.id
                WHERE sr.opera_id = %s AND sr.role_name = ANY(%s)
                  AND u.role = 'student' AND u.email IS NOT NULL
            """, (opera_id, role_names))
            for name, email in cur.fetchall():
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
        }
        for r in base_rows
    ]

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
                   u.fullname, u.voice_type
            FROM student_roles sr
            JOIN users u ON u.id = sr.student_id
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

    # Build assignments lookup: {(cast_id, role_name): student_info}
    assignments = {}
    for s_id, c_id, r_name, name, voice in assignment_rows:
        if c_id is None:
            continue  # skip any bad data
        assignments[(c_id, r_name)] = {
            "student_id": s_id,
            "name": name,
            "voice_type": voice,
        }

    # Chorus count: assigned students who have no principal role
    principal_student_ids = {a[0] for a in assignment_rows}
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
    }
@app.post("/admin/assign-principal")
def admin_assign_principal(payload: dict):
    """
    Assign or clear a principal role.

    Payload shape for ASSIGN:
      { opera_id, cast_id, role_name, student_id }

    Payload shape for CLEAR (pass student_id=null):
      { opera_id, cast_id, role_name, student_id: null }
    """
    opera_id = payload.get("opera_id")
    cast_id = payload.get("cast_id")
    role_name = payload.get("role_name")
    student_id = payload.get("student_id")  # may be null to clear

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
    """Returns staff for this opera + all teachers available to add."""
    user = require_user(request, role="admin")
    org_id = user["org_id"]
    with db_cursor() as cur:
        # Current staff on this opera
        cur.execute("""
            SELECT os.id, os.teacher_id, u.fullname, os.staff_role
            FROM opera_staff os
            JOIN users u ON u.id = os.teacher_id
            WHERE os.opera_id = %s
            ORDER BY os.staff_role, u.fullname
        """, (opera_id,))
        staff = [
            {
                "id": r[0],
                "teacher_id": r[1],
                "teacher_name": r[2],
                "staff_role": r[3],
            }
            for r in cur.fetchall()
        ]

        # All teachers in org
        cur.execute("""
            SELECT id, fullname
            FROM users
            WHERE org_id = %s AND role = 'teacher'
            ORDER BY fullname
        """, (org_id,))
        teachers = [{"id": r[0], "name": r[1]} for r in cur.fetchall()]

    return {"staff": staff, "teachers": teachers}


@app.post("/admin/assign-staff")
def admin_assign_staff(payload: dict):
    opera_id = payload.get("opera_id")
    teacher_id = payload.get("teacher_id")
    staff_role = payload.get("staff_role")

    if not (opera_id and teacher_id and staff_role):
        return {"status": "fail", "message": "Missing fields"}

    if staff_role not in ("director", "assistant_director", "conductor", "assistant_conductor"):
        return {"status": "fail", "message": "Invalid staff role"}

    with db_cursor(commit=True) as cur:
        cur.execute("SELECT 1 FROM operas WHERE id=%s", (opera_id,))
        if not cur.fetchone():
            return {"status": "fail", "message": "Opera not found"}

        cur.execute("SELECT 1 FROM users WHERE id=%s AND role='teacher'", (teacher_id,))
        if not cur.fetchone():
            return {"status": "fail", "message": "Teacher not found"}

        try:
            cur.execute("""
                INSERT INTO opera_staff (opera_id, teacher_id, staff_role)
                VALUES (%s, %s, %s)
            """, (opera_id, teacher_id, staff_role))
        except pg_errors.UniqueViolation:
            return {"status": "fail", "message": "This teacher already has that role on this opera"}

    return {"status": "success"}


@app.post("/admin/remove-staff")
def admin_remove_staff(payload: dict):
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

def get_bookable_date(tz=None):
    """
    Returns the date the dashboard should be oriented toward.

    Timeline across a 24-hour day:
      00:00 – 17:59  →  today        (window open since 9 PM yesterday, closes 6 PM today)
      18:00 – 20:59  →  tomorrow     (today's window has closed; showing tomorrow as "next up")
      21:00 – 23:59  →  tomorrow     (tomorrow's window is now open)

    Whether booking is actually OPEN for the returned date is a separate
    question — use is_booking_window_open_for() for that.
    """
    if tz is None:
        tz = EST
    now_local = datetime.now(tz)
    if now_local.hour >= 18:
        return now_local.date() + timedelta(days=1)
    return now_local.date()

@app.get("/teacher/today")
def teacher_today(request: Request):
    """Teacher's lessons for the currently-relevant day, split by active/cancelled."""
    teacher = require_user(request, role="teacher")
    target_date = get_teacher_viewing_date(get_org_tz(teacher))

    with db_cursor() as cur:
        cur.execute("""
            SELECT
                lessons.id,
                u.fullname,
                lessons.lesson_time,
                lessons.status,
                lessons.cancelled_at
            FROM lessons
            JOIN users u ON u.id = lessons.student_id
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
    teacher = require_user(request, role="teacher")
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
    teacher = require_user(request, role="teacher")

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



# ========================================================
# STUDENT
# ========================================================

@app.get("/student/lessons")
def student_lessons(request: Request):
    student = require_user(request, role="student")
    with db_cursor() as cur:
        cur.execute("""
            SELECT l.id, l.lesson_date, l.lesson_time, u.fullname
            FROM lessons l
            JOIN users u ON u.id = l.teacher_id
            WHERE l.student_id = %s AND l.status = 'booked'
            ORDER BY l.lesson_date, l.lesson_time
        """, (student["id"],))
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "date": r[1].isoformat() if r[1] else None,
            "time": r[2].strftime("%H:%M") if r[2] else None,
            "teacher": r[3],
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
    now_local = datetime.now(org_tz)
    target_date = get_bookable_date(org_tz)
    booking_open = is_booking_window_open_for(target_date, org_tz)
    # "Pending" means the dashboard shows the next day, but booking hasn't opened yet
    # (between 6 PM and 9 PM).
    booking_pending = (not booking_open) and (18 <= now_local.hour < 21)

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
    }
@app.get("/student/teacher-slots")
def student_teacher_slots(request: Request, teacher: int, period: str):
    """
    Returns the list of bookable 30-min slots for a given teacher today,
    filtered to either morning or afternoon.

    period must be 'morning' or 'afternoon'.
    """
    if period not in ("morning", "afternoon"):
        return []

    student = require_user(request, role="student")
    org_tz = get_org_tz(student)

    target_date = get_bookable_date(org_tz)
    if not is_booking_window_open_for(target_date, org_tz):
        return []

    conflict_ctx = get_student_conflict_context(student["id"])
    all_slots = get_available_slots(teacher, target_date, student_id=student["id"], conflict_ctx=conflict_ctx, tz=org_tz)
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
            WHERE r.end_time >= NOW()
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
    if not rehearsal_id:
        return {"status": "fail", "message": "rehearsal_id required"}

    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO absence_requests (rehearsal_id, singer_id)
            VALUES (%s, %s) ON CONFLICT DO NOTHING
            """,
            (rehearsal_id, student["id"]),
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
        subject = f"Absence Notice – {student['fullname']} – {opera_name}"
        html_body = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:600px;margin:auto;padding:24px;">
<h2 style="color:#333;">Absence Notice</h2>
<p><strong>{student['fullname']}</strong> has marked themselves absent for the
<strong>{opera_name}</strong> rehearsal on <strong>{date_str}</strong>.</p>
</body></html>"""
        text_body = f"Absence Notice\n{student['fullname']} has marked themselves absent for the {opera_name} rehearsal on {date_str}."
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

    # Can only book for the currently bookable date (today before 9 PM, tomorrow after)
    if lesson_date != get_bookable_date(org_tz):
        return {"status": "fail", "message": "Coachings can only be booked for the current bookable day"}

    # Booking window check (9 PM previous day to 6 PM current day)
    if not is_booking_window_open_for(lesson_date, org_tz):
        return {
            "status": "fail",
            "message": "Booking is closed. The window opens at 9 PM the day before and closes at 6 PM."
        }

    # Block past times today
    slot_dt = org_tz.localize(datetime.combine(lesson_date, lesson_time))
    if slot_dt <= datetime.now(org_tz):
        return {"status": "fail", "message": "Cannot book past times"}

    # Block slots during the lunch hour (1 PM – 2 PM)
    slot_end_t = (slot_dt + timedelta(minutes=30)).time()
    if lesson_time < LUNCH_END and slot_end_t > LUNCH_START:
        return {"status": "fail", "message": "This slot overlaps the lunch break"}

    # Rehearsal conflict check — stops students from booking over their rehearsals
    if get_student_rehearsal_conflicts(student_id, lesson_date, lesson_time, tz=org_tz):
        return {
            "status": "fail",
            "message": "This time conflicts with one of your rehearsals."
        }

    with db_cursor(commit=True) as cur:
        # Daily limit: one ACTIVE lesson per student per day (cancelled don't count)
        cur.execute("""
            SELECT COUNT(*) FROM lessons
            WHERE student_id=%s AND lesson_date=%s AND status='booked'
        """, (student_id, lesson_date))
        if cur.fetchone()[0] >= 1:
            return {"status": "fail", "message": "You already have a lesson booked that day"}

        # Teacher limit: max 5 ACTIVE lessons with same teacher
        cur.execute("""
            SELECT COUNT(*) FROM lessons
            WHERE student_id=%s AND teacher_id=%s AND status='booked'
        """, (student_id, teacher_id))
        if cur.fetchone()[0] >= 5:
            return {"status": "fail", "message": "Maximum lessons with this teacher reached"}

        # Try to insert — UNIQUE(teacher_id, lesson_date, lesson_time) prevents
        # double-booking atomically, no race condition.
        try:
            cur.execute("""
                INSERT INTO lessons (teacher_id, student_id, lesson_date, lesson_time)
                VALUES (%s, %s, %s, %s)
            """, (teacher_id, student_id, lesson_date, lesson_time))
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

            # Enforce 1-hour cutoff for students
            user_tz = get_org_tz(user)
            lesson_dt = user_tz.localize(datetime.combine(lesson_date, lesson_time))
            cutoff = lesson_dt - timedelta(hours=1)
            if datetime.now(user_tz) >= cutoff:
                return {
                    "status": "fail",
                    "message": "Can't cancel within 1 hour of the lesson."
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
    now_local = datetime.now(org_tz)
    target_date = get_bookable_date(org_tz)
    booking_open = is_booking_window_open_for(target_date, org_tz)
    booking_pending = (not booking_open) and (18 <= now_local.hour < 21)

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

            slots = get_available_slots(t_id, target_date, avail_ctx=avail_ctx, tz=org_tz)
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
              AND r.end_time >= NOW()
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
    if not rehearsal_id:
        return {"status": "fail", "message": "rehearsal_id required"}

    with db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO absence_requests (rehearsal_id, singer_id)
            VALUES (%s, %s) ON CONFLICT DO NOTHING
            """,
            (rehearsal_id, member["id"]),
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
            SELECT email FROM users
            WHERE org_id=%s AND role IN ('admin','head_admin','orchestra_admin') AND email IS NOT NULL
            """,
            (member["org_id"],),
        )
        admin_emails = [r[0] for r in cur.fetchall()]

    if row and admin_emails:
        start_dt, opera_name = row
        org_tz = get_org_tz(member)
        local_dt = start_dt.astimezone(org_tz) if start_dt.tzinfo else start_dt
        date_str = local_dt.strftime("%A, %B %-d, %Y")
        subject = f"Absence Notice – {member['fullname']} – {opera_name}"
        html_body = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:600px;margin:auto;padding:24px;">
<h2 style="color:#333;">Absence Notice</h2>
<p><strong>{member['fullname']}</strong> has marked themselves absent for the
<strong>{opera_name}</strong> orchestra rehearsal on <strong>{date_str}</strong>.</p>
</body></html>"""
        text_body = f"Absence Notice\n{member['fullname']} has marked themselves absent for the {opera_name} orchestra rehearsal on {date_str}."
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
    with db_cursor() as cur:
        cur.execute("""
            SELECT l.id, l.lesson_date, l.lesson_time, u.fullname, l.status
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
        }
        for r in rows
    ]


@app.get("/orchestra-member/teacher-slots")
def orchestra_member_teacher_slots(request: Request, teacher: int, period: str):
    """Available slots for a given instrumental teacher today."""
    if period not in ("morning", "afternoon"):
        return []

    member = require_user(request, role="orchestra_member")
    org_tz = get_org_tz(member)

    target_date = get_bookable_date(org_tz)
    if not is_booking_window_open_for(target_date, org_tz):
        return []

    all_slots = get_available_slots(teacher, target_date, tz=org_tz)
    return [s for s in all_slots if classify_slot_time(s) == period]


@app.post("/orchestra-member/book")
def orchestra_member_book(payload: dict, request: Request):
    """Book a coaching with an instrumental teacher."""
    member = require_user(request, role="orchestra_member")
    member_id = member["id"]

    date_str = payload.get("date")
    teacher_id = payload.get("teacher_id")
    time_str = payload.get("time")

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
    if lesson_date != get_bookable_date(org_tz):
        return {"status": "fail", "message": "Coachings can only be booked for the current bookable day"}

    if not is_booking_window_open_for(lesson_date, org_tz):
        return {"status": "fail", "message": "Booking is closed."}

    slot_dt = org_tz.localize(datetime.combine(lesson_date, lesson_time))
    if slot_dt <= datetime.now(org_tz):
        return {"status": "fail", "message": "Cannot book past times"}

    slot_end_t = (slot_dt + timedelta(minutes=30)).time()
    if lesson_time < LUNCH_END and slot_end_t > LUNCH_START:
        return {"status": "fail", "message": "This slot overlaps the lunch break"}

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT COUNT(*) FROM lessons
            WHERE student_id=%s AND lesson_date=%s AND status='booked'
        """, (member_id, lesson_date))
        if cur.fetchone()[0] >= 1:
            return {"status": "fail", "message": "You already have a lesson booked that day"}

        cur.execute("""
            SELECT COUNT(*) FROM lessons
            WHERE student_id=%s AND teacher_id=%s AND status='booked'
        """, (member_id, teacher_id))
        if cur.fetchone()[0] >= 5:
            return {"status": "fail", "message": "Maximum lessons with this teacher reached"}

        try:
            cur.execute("""
                INSERT INTO lessons (teacher_id, student_id, lesson_date, lesson_time)
                VALUES (%s, %s, %s, %s)
            """, (teacher_id, member_id, lesson_date, lesson_time))
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
        lesson_dt = member_tz.localize(datetime.combine(lesson_date, lesson_time))
        cutoff = lesson_dt - timedelta(hours=1)
        if datetime.now(member_tz) >= cutoff:
            return {"status": "fail", "message": "Too close to lesson time to cancel"}

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
            cur.execute("""
                INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
                VALUES (%s, %s, %s, %s, 5)
            """, (org_id, name, instrument, i))

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
            SELECT id, fullname, instrument
            FROM users
            WHERE org_id = %s AND role = 'orchestra_member'
            ORDER BY instrument, fullname
        """, (org_id,))
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "instrument": r[2] or ""} for r in rows]


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
            SELECT ose.id, ose.section_id, ose.chair_number, ose.member_id, u.fullname
            FROM orchestra_seats ose
            LEFT JOIN users u ON u.id = ose.member_id
            WHERE ose.opera_id = %s
            ORDER BY ose.section_id, ose.chair_number
        """, (opera_id,))
        rows = cur.fetchall()

    return [
        {
            "id": r[0], "section_id": r[1], "chair_number": r[2],
            "member_id": r[3], "member_name": r[4],
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
                INSERT INTO orchestra_seats (opera_id, section_id, chair_number, member_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (opera_id, section_id, chair_number)
                DO UPDATE SET member_id = EXCLUDED.member_id
            """, (opera_id, section_id, chair_number, member_id))
        else:
            cur.execute("""
                INSERT INTO orchestra_seats (opera_id, section_id, chair_number, member_id)
                VALUES (%s, %s, %s, NULL)
                ON CONFLICT (opera_id, section_id, chair_number)
                DO UPDATE SET member_id = NULL
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
                SELECT DISTINCT u.id, u.fullname, u.email
                FROM users u
                JOIN student_assignments sa ON sa.student_id = u.id
                WHERE sa.opera_id = %s AND u.role = 'student'
            """, (opera_id,))
        elif scope == "cast":
            cur.execute("""
                SELECT DISTINCT u.id, u.fullname, u.email
                FROM users u
                JOIN student_assignments sa ON sa.student_id = u.id
                WHERE sa.opera_id = %s AND sa.cast_id = %s AND u.role = 'student'
            """, (opera_id, cast_id))
        else:
            cur.execute("""
                SELECT DISTINCT u.id, u.fullname, u.email
                FROM users u
                JOIN student_roles sr ON sr.student_id = u.id
                WHERE sr.opera_id = %s AND sr.role_name = %s AND u.role = 'student'
            """, (opera_id, role_name))

        singers = cur.fetchall()

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


# -- Page routes --------------------------------------------------------------

@app.get("/choir/admin", response_class=HTMLResponse)
def choir_admin_page(request: Request):
    return templates.TemplateResponse(request, "choir_admin.html")

@app.get("/choir/member", response_class=HTMLResponse)
def choir_member_page(request: Request):
    return templates.TemplateResponse(request, "choir_member.html")

@app.get("/choir/sub-response/{token}", response_class=HTMLResponse)
def choir_sub_response_page(token: str, r: Optional[str] = None, request: Request = None):
    """Public page â€” sub clicks Accept or Decline from their email link."""
    if not r or r not in ("accepted", "declined"):
        return templates.TemplateResponse(request, "choir_sub_response.html",
            {"message": "Invalid response link.", "success": False})

    with db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT sc.id, sc.sub_request_id, sc.response, sc.sub_id,
                   sr.status, sr.rehearsal_id, sr.section_id,
                   s.fullname, s.email
            FROM sub_contacts sc
            JOIN sub_requests sr ON sr.id = sc.sub_request_id
            JOIN subs s ON s.id = sc.sub_id
            WHERE sc.token = %s
        """, (token,))
        row = cur.fetchone()

        if not row:
            return templates.TemplateResponse(request, "choir_sub_response.html",
                {"message": "This link is invalid or has expired.", "success": False})

        sc_id, req_id, existing_response, sub_id, req_status, rehearsal_id, section_id, sub_name, sub_email = row

        if existing_response != "pending":
            return templates.TemplateResponse(request, "choir_sub_response.html",
                {"message": "You have already responded - thank you!", "success": True})

        if req_status == "filled":
            cur.execute("UPDATE sub_contacts SET response='declined', responded_at=NOW() WHERE id=%s", (sc_id,))
            return templates.TemplateResponse(request, "choir_sub_response.html",
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

            if reh and admin_row:
                rdate = reh[0].strftime("%A, %B %-d") if hasattr(reh[0], "strftime") else str(reh[0])
                html_body = (f"<p><strong>{sub_name}</strong> accepted the sub for "
                             f"<strong>{reh[1]}</strong> on {rdate}.</p>")
                text_body = f"{sub_name} accepted the sub for {reh[1]} on {rdate}."
                send_email(admin_row[0], f"Sub confirmed - {reh[1]}", html_body, text_body)

            return templates.TemplateResponse(request, "choir_sub_response.html",
                {"message": f"You are confirmed! Thank you, {sub_name}. See you at rehearsal.", "success": True, "sub_token": token})

        # r == "declined": notify the choir member who created the request
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

    return templates.TemplateResponse(request, "choir_sub_response.html",
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
            WHERE org_id = %s AND role = 'student'
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
    section_id = user.get("section_id")
    if not section_id and role != "admin":
        voice_type = (user.get("voice_type") or "").lower()
        if voice_type:
            with db_cursor() as cur:
                cur.execute("""
                    SELECT id FROM choir_sections
                    WHERE org_id = %s AND LOWER(name) = %s
                """, (org_id, voice_type))
                row = cur.fetchone()
                if row:
                    section_id = row[0]

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
            })
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
                created += 1
            except Exception:
                pass

    return {"status": "success", "created": created}


# -- Sub roster ---------------------------------------------------------------

@app.get("/choir/subs")
def choir_get_subs(request: Request, section_id: Optional[int] = None):
    user = require_choir_member(request)
    org_id = user["org_id"]
    if user["role"] != "admin":
        section_id = user.get("section_id")
        if not section_id:
            voice_type = (user.get("voice_type") or "").lower()
            if not voice_type:
                return []
            with db_cursor() as cur:
                cur.execute("""
                    SELECT id FROM choir_sections
                    WHERE org_id = %s AND LOWER(name) = %s
                """, (org_id, voice_type))
                row = cur.fetchone()
                if not row:
                    return []
                section_id = row[0]

    with db_cursor() as cur:
        if section_id:
            cur.execute("""
                SELECT s.id, s.fullname, s.email, s.phone, s.is_preferred, s.notes,
                       cs.name, s.section_id
                FROM subs s JOIN choir_sections cs ON cs.id = s.section_id
                WHERE s.org_id=%s AND s.section_id=%s AND s.active=true
                ORDER BY s.is_preferred DESC, s.fullname
            """, (org_id, section_id))
        else:
            cur.execute("""
                SELECT s.id, s.fullname, s.email, s.phone, s.is_preferred, s.notes,
                       cs.name, s.section_id
                FROM subs s JOIN choir_sections cs ON cs.id = s.section_id
                WHERE s.org_id=%s AND s.active=true
                ORDER BY cs.sort_order, s.is_preferred DESC, s.fullname
            """, (org_id,))
        return [{"id": r[0], "fullname": r[1], "email": r[2], "phone": r[3] or "",
                 "is_preferred": r[4], "notes": r[5] or "",
                 "section_name": r[6], "section_id": r[7]} for r in cur.fetchall()]

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
            WHERE u.org_id=%s AND u.role='student'
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
    if not rehearsal_id:
        return {"status": "fail", "message": "rehearsal_id required"}
    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO absence_requests (rehearsal_id, singer_id, reason)
            VALUES (%s, %s, %s)
            ON CONFLICT (rehearsal_id, singer_id) DO UPDATE SET reason=EXCLUDED.reason
        """, (rehearsal_id, user["id"], reason))
    return {"status": "success"}

@app.delete("/choir/absence-request/{rehearsal_id}")
def choir_cancel_absence(rehearsal_id: int, request: Request):
    user = require_choir_member(request)
    org_id = user["org_id"]

    section_id = user.get("section_id")
    if not section_id:
        voice_type = (user.get("voice_type") or "").lower()
        if voice_type:
            with db_cursor() as cur:
                cur.execute("""
                    SELECT id FROM choir_sections
                    WHERE org_id = %s AND LOWER(name) = %s LIMIT 1
                """, (org_id, voice_type))
                row = cur.fetchone()
                if row:
                    section_id = row[0]

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

@app.get("/choir/absences/{rehearsal_id}")
def choir_get_absences(rehearsal_id: int, request: Request):
    user = require_choir_admin(request)
    org_id = user["org_id"]
    with db_cursor() as cur:
        cur.execute("""
            SELECT ar.singer_id, u.fullname, u.section_id, u.voice_type, ar.reason
            FROM absence_requests ar
            JOIN users u ON u.id = ar.singer_id
            WHERE ar.rehearsal_id = %s
            ORDER BY u.fullname
        """, (rehearsal_id,))
        rows = cur.fetchall()

        result = []
        for singer_id, fullname, section_id, voice_type, reason in rows:
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
                "singer_id": singer_id,
                "singer": fullname,
                "section_id": resolved_id,
                "section": section_name or "?",
                "reason": reason or "",
            })
        return result

@app.get("/choir/my-absences")
def choir_my_absences(request: Request):
    user = require_choir_member(request)
    with db_cursor() as cur:
        cur.execute("SELECT rehearsal_id FROM absence_requests WHERE singer_id=%s", (user["id"],))
        return [r[0] for r in cur.fetchall()]


@app.get("/choir/my-sub-status")
def choir_my_sub_status(request: Request):
    """For each rehearsal the member is absent from, return the sub_request status for their section."""
    user = require_choir_member(request)
    org_id = user["org_id"]

    section_id = user.get("section_id")
    if not section_id:
        voice_type = (user.get("voice_type") or "").lower()
        if voice_type:
            with db_cursor() as cur:
                cur.execute("""
                    SELECT id FROM choir_sections
                    WHERE org_id = %s AND LOWER(name) = %s LIMIT 1
                """, (org_id, voice_type))
                row = cur.fetchone()
                if row:
                    section_id = row[0]

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
                      token: str, admin_name: str = None, admin_email: str = None) -> tuple:
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


def _send_sub_emails(sub_list: list, sub_request_id: int, rehearsal_id: int,
                     section_id: int, tier: str) -> int:
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
                                       admin_name, admin_email)
        if send_email(sub["email"],
                      f"Sub needed - {section_name} | {org_name}", html, text):
            sent += 1
    return sent


@app.post("/choir/sub-request")
def choir_create_sub_request(payload: dict, request: Request):
    user = require_choir_member(request)
    rehearsal_id = payload.get("rehearsal_id")
    org_id = user["org_id"]

    # Resolve the user's own section_id (from profile or voice_type fallback)
    user_section_id = user.get("section_id")
    if not user_section_id:
        voice_type = (user.get("voice_type") or "").lower()
        if voice_type:
            with db_cursor() as cur:
                cur.execute("""
                    SELECT id FROM choir_sections
                    WHERE org_id = %s AND LOWER(name) = %s LIMIT 1
                """, (org_id, voice_type))
                row = cur.fetchone()
                if row:
                    user_section_id = row[0]

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

    with db_cursor() as cur:
        cur.execute("""
            SELECT id, fullname, email FROM subs
            WHERE section_id=%s AND is_preferred=true AND active=true
        """, (section_id,))
        preferred = [{"id": r[0], "fullname": r[1], "email": r[2]} for r in cur.fetchall()]

    sent = _send_sub_emails(preferred, sub_request_id, rehearsal_id, section_id, "preferred")

    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE sub_requests SET status='preferred_sent', preferred_sent_at=NOW()
            WHERE id=%s AND status='open'
        """, (sub_request_id,))

    return {"status": "success", "sent": sent, "total_preferred": len(preferred)}


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
            SELECT u.id, u.org_id, u.section_id, u.voice_type
            FROM users u WHERE u.calendar_token=%s
        """, (token,))
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Calendar not found")

    user_id, org_id, section_id, voice_type = row

    if not section_id and voice_type:
        with db_cursor() as cur:
            cur.execute("""
                SELECT id FROM choir_sections
                WHERE org_id=%s AND LOWER(name)=%s LIMIT 1
            """, (org_id, voice_type.lower()))
            sr = cur.fetchone()
            if sr:
                section_id = sr[0]

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
    """24-hour escalation: email all uncontacted subs for stale open requests.
    Protect with CRON_SECRET env var; call from an external scheduler."""
    import os as _os
    cron_secret = _os.environ.get("CRON_SECRET", "")
    if not cron_secret or request.headers.get("x-cron-secret") != cron_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

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

    return {"status": "ok", "escalated": escalated, "checked": len(stale)}


@app.post("/choir/contact-sub")
def choir_contact_one_sub(payload: dict, request: Request):
    """Email a single sub for a rehearsal section. Creates the sub_request if needed."""
    user = require_choir_member(request)
    rehearsal_id = payload.get("rehearsal_id")
    section_id = payload.get("section_id")
    sub_id = payload.get("sub_id")
    org_id = user["org_id"]
    if not all([rehearsal_id, section_id, sub_id]):
        return {"status": "fail", "message": "Missing required fields"}

    # Non-admin members may only contact subs for their own section
    if user["role"] != "admin":
        user_section_id = user.get("section_id")
        if not user_section_id:
            voice_type = (user.get("voice_type") or "").lower()
            if voice_type:
                with db_cursor() as cur:
                    cur.execute("""
                        SELECT id FROM choir_sections
                        WHERE org_id = %s AND LOWER(name) = %s LIMIT 1
                    """, (org_id, voice_type))
                    row = cur.fetchone()
                    if row:
                        user_section_id = row[0]
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
    sent = _send_sub_emails([sub], sub_request_id, rehearsal_id, section_id, tier)
    if sent == 0:
        return {"status": "fail", "message": "Already contacted or email failed"}
    return {"status": "success"}


@app.post("/choir/contact-preferred-subs")
def choir_contact_preferred_subs(payload: dict, request: Request):
    """Email all preferred subs for the member's section. Creates sub_request if needed."""
    user = require_choir_member(request)
    rehearsal_id = payload.get("rehearsal_id")
    org_id = user["org_id"]
    if not rehearsal_id:
        return {"status": "fail", "message": "rehearsal_id required"}

    section_id = user.get("section_id")
    if not section_id:
        voice_type = (user.get("voice_type") or "").lower()
        if voice_type:
            with db_cursor() as cur:
                cur.execute("""
                    SELECT id FROM choir_sections
                    WHERE org_id = %s AND LOWER(name) = %s LIMIT 1
                """, (org_id, voice_type))
                row = cur.fetchone()
                if row:
                    section_id = row[0]
    if not section_id:
        return {"status": "fail", "message": "Could not resolve your section"}

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

    with db_cursor() as cur:
        cur.execute("""
            SELECT id, fullname, email FROM subs
            WHERE section_id = %s AND is_preferred = true AND active = true
        """, (section_id,))
        preferred = [{"id": r[0], "fullname": r[1], "email": r[2]} for r in cur.fetchall()]

    if not preferred:
        return {"status": "fail", "message": "No preferred subs found for your section"}

    sent = _send_sub_emails(preferred, sub_request_id, rehearsal_id, section_id, "preferred")

    with db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE sub_requests SET status = 'preferred_sent', preferred_sent_at = NOW()
            WHERE id = %s AND status = 'open'
        """, (sub_request_id,))

    return {"status": "success", "sent": sent, "total": len(preferred)}


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
    return templates.TemplateResponse(request, "ensemble_member.html")


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
            LEFT JOIN rehearsal_sections rs ON rs.rehearsal_id = r.id
            LEFT JOIN choir_sections cs ON cs.id = rs.section_id
            LEFT JOIN rehearsal_members rm ON rm.rehearsal_id = r.id AND rm.user_id = %s
            WHERE r.org_id = %s
              AND r.choir_type = 'ensemble'
              AND r.start_time >= %s
              AND (
                  (SELECT COUNT(*) FROM rehearsal_sections rs2 WHERE rs2.rehearsal_id = r.id) = 0
                  OR cs.org_id = %s
                  OR rm.user_id IS NOT NULL
              )
            ORDER BY r.start_time
        """, (user_id, org_id, today, org_id))
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
            "SELECT rehearsal_id FROM absence_requests WHERE singer_id = %s",
            (user["id"],)
        )
        rows = cur.fetchall()
    return [r[0] for r in rows]


@app.post("/ensemble/absence")
def ensemble_mark_absent(payload: dict, request: Request):
    user = require_ensemble_member(request)
    rehearsal_id = payload.get("rehearsal_id")
    if not rehearsal_id:
        return {"status": "fail", "message": "rehearsal_id required"}
    with db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO absence_requests (rehearsal_id, singer_id)
            VALUES (%s, %s) ON CONFLICT DO NOTHING
        """, (rehearsal_id, user["id"]))
        cur.execute("""
            SELECT r.start_time, o.name
            FROM rehearsals r
            JOIN organizations o ON o.id = r.org_id
            WHERE r.id = %s
        """, (rehearsal_id,))
        row = cur.fetchone()
        cur.execute(
            "SELECT email FROM users WHERE org_id = %s AND role IN ('admin', 'head_admin') AND email IS NOT NULL",
            (user["org_id"],)
        )
        admins = [r[0] for r in cur.fetchall()]
    if row:
        reh_date = row[0].strftime("%B %-d, %Y") if hasattr(row[0], "strftime") else str(row[0])
        org_name = row[1]
        subject = f"{user['fullname']} marked absent — {reh_date}"
        html_body = f"""<p><strong>{user['fullname']}</strong> ({user.get('instrument','')}) has marked themselves absent for the ensemble rehearsal on <strong>{reh_date}</strong> at {org_name}.</p>"""
        text_body = f"{user['fullname']} ({user.get('instrument','')}) marked absent for ensemble rehearsal on {reh_date} at {org_name}."
        for email in admins:
            send_email(email, subject, html_body, text_body)
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
            SELECT u.id, u.fullname, u.role, u.instrument, s.name AS section_name
            FROM users u
            LEFT JOIN sections s ON s.id = u.section_id
            WHERE u.org_id = %s AND u.role IN ('choir_member', 'ensemble_member')
            ORDER BY s.name NULLS LAST, u.fullname
        """, (user["org_id"],))
        rows = cur.fetchall()
    result = []
    for uid, fullname, role, instrument, section_name in rows:
        result.append({
            "id": uid,
            "fullname": fullname,
            "role": role,
            "section_name": section_name or "",
            "instrument": instrument or "",
        })
    return result


