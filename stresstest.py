"""
Coaching Scheduler — Stress Test

Simulates N students concurrently:
    1. Logging in
    2. Loading their dashboard (/student/today)
    3. Attempting to book a lesson — with retries if the slot gets taken

Reports per-user outcomes and a summary with response times.

Usage:
    python stress_test.py [num_users]

After the test, you'll likely want to clear any successful bookings:
    DELETE FROM lessons WHERE created_at > NOW() - INTERVAL '10 minutes';
"""
import sys
import time
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

import requests


BASE_URL = "https://coachingscheduler-production.up.railway.app"
PASSWORD = "TestStudent2026!"

# Max times a user will retry booking when their slot gets taken.
# Simulates real behavior: "oh, that one's gone, let me try another."
MAX_BOOK_ATTEMPTS = 3

STUDENT_USERNAMES = (
    [f"soprano{i}" for i in range(1, 13)]
    + [f"mezzo{i}" for i in range(13, 18)]
    + [f"tenor{i}" for i in range(18, 26)]
    + [f"baritone{i}" for i in range(26, 34)]
    + [f"bass{i}" for i in range(34, 38)]
    + [f"bassbar{i}" for i in range(38, 41)]
    + [f"soprano{i}" for i in range(41, 61)]
    + [f"mezzo{i}" for i in range(61, 76)]
    + [f"tenor{i}" for i in range(76, 91)]
    + [f"baritone{i}" for i in range(91, 101)]
)

BOOK_BARRIER = None


@dataclass
class Outcome:
    username: str
    login_ok: bool = False
    login_ms: float = 0.0
    dashboard_ok: bool = False
    dashboard_ms: float = 0.0
    booking_window_open: bool = False
    book_attempted: bool = False
    book_ok: bool = False
    book_ms: float = 0.0
    book_attempts: int = 0
    teacher_id: Optional[int] = None
    slot_time: Optional[str] = None
    failure_reason: str = ""
    attempt_log: list = field(default_factory=list)


def pick_fresh_slot(session, username, teachers, already_tried):
    """Find an available teacher+slot we haven't tried yet."""
    for t in teachers:
        if t.get("status") != "available":
            continue
        if t.get("morning", 0) + t.get("afternoon", 0) == 0:
            continue

        periods = []
        if t.get("morning", 0) > 0:
            periods.append("morning")
        if t.get("afternoon", 0) > 0:
            periods.append("afternoon")

        for period in periods:
            try:
                r = session.get(
                    f"{BASE_URL}/student/teacher-slots",
                    params={
                        "username": username,
                        "teacher": t["id"],
                        "period": period,
                    },
                    timeout=10,
                )
                slots = r.json()
                if not isinstance(slots, list):
                    continue

                fresh = [s for s in slots if (t["id"], s) not in already_tried]
                if fresh:
                    return t["id"], random.choice(fresh), period
            except Exception:
                continue

    return None, None, None


def simulate_user(username: str) -> Outcome:
    outcome = Outcome(username=username)
    session = requests.Session()

    # Login
    t0 = time.perf_counter()
    try:
        r = session.post(
            f"{BASE_URL}/login",
            json={"username": username, "password": PASSWORD, "org": "boa"},
            timeout=15,
        )
        outcome.login_ms = (time.perf_counter() - t0) * 1000
        data = r.json()
        if data.get("success"):
            outcome.login_ok = True
        else:
            outcome.failure_reason = f"login: {data.get('message', 'unknown')}"
            return outcome
    except Exception as e:
        outcome.login_ms = (time.perf_counter() - t0) * 1000
        outcome.failure_reason = f"login exception: {type(e).__name__}"
        return outcome

    # Dashboard
    t0 = time.perf_counter()
    try:
        r = session.get(
            f"{BASE_URL}/student/today",
            params={"username": username},
            timeout=20,
        )
        outcome.dashboard_ms = (time.perf_counter() - t0) * 1000
        data = r.json()
        outcome.dashboard_ok = True
        outcome.booking_window_open = bool(data.get("booking_open"))
        teachers = data.get("teachers") or []
    except Exception as e:
        outcome.dashboard_ms = (time.perf_counter() - t0) * 1000
        outcome.failure_reason = f"dashboard exception: {type(e).__name__}"
        return outcome

    if not outcome.booking_window_open:
        outcome.failure_reason = "window closed"

    # Barrier
    if BOOK_BARRIER is not None:
        try:
            BOOK_BARRIER.wait(timeout=60)
        except threading.BrokenBarrierError:
            pass

    # Book with retries
    already_tried = set()
    book_start = time.perf_counter()

    for attempt in range(1, MAX_BOOK_ATTEMPTS + 1):
        outcome.book_attempts = attempt

        teacher_id, slot_time, period = pick_fresh_slot(session, username, teachers, already_tried)

        if teacher_id is None:
            outcome.attempt_log.append(f"attempt {attempt}: no slots left to try")
            outcome.failure_reason = "no slots available"
            break

        already_tried.add((teacher_id, slot_time))
        outcome.teacher_id = teacher_id
        outcome.slot_time = slot_time
        outcome.book_attempted = True

        try:
            payload = {
                "username": username,
                "date": data.get("date"),
                "teacher_id": teacher_id,
                "time": slot_time,
            }
            r = session.post(f"{BASE_URL}/student/book", json=payload, timeout=20)
            result = r.json()

            if result.get("status") == "success":
                outcome.book_ok = True
                outcome.failure_reason = ""
                outcome.attempt_log.append(f"attempt {attempt}: booked {slot_time}")
                break
            else:
                msg = result.get("message", "unknown")
                outcome.attempt_log.append(f"attempt {attempt}: {slot_time} → {msg}")
                outcome.failure_reason = f"book: {msg}"

                # Hard stops — no point retrying
                if "already have a lesson" in msg.lower():
                    break
                if "window" in msg.lower() or "closed" in msg.lower():
                    break
                if "maximum" in msg.lower():
                    break

                # Otherwise assume it's a slot-taken race and try another slot.
                # Refresh teacher list so we see latest counts.
                try:
                    r2 = session.get(
                        f"{BASE_URL}/student/today",
                        params={"username": username},
                        timeout=10,
                    )
                    fresh_data = r2.json()
                    teachers = fresh_data.get("teachers") or teachers
                except Exception:
                    pass

        except Exception as e:
            outcome.attempt_log.append(f"attempt {attempt}: exception {type(e).__name__}")
            outcome.failure_reason = f"book exception: {type(e).__name__}"
            break

    outcome.book_ms = (time.perf_counter() - book_start) * 1000

    return outcome


def format_row(o: Outcome) -> str:
    status_icon = "✅" if o.book_ok else ("⚠️ " if o.login_ok and o.dashboard_ok else "❌")

    if o.book_ok:
        tail = f"booked {o.slot_time} (teacher {o.teacher_id}) after {o.book_attempts} attempt{'s' if o.book_attempts > 1 else ''}"
    elif o.book_attempts > 0:
        tail = f"{o.failure_reason or 'unknown'} (gave up after {o.book_attempts} tries)"
    else:
        tail = o.failure_reason or "unknown"

    return (
        f"{status_icon} {o.username:<14} "
        f"login:{o.login_ms:6.0f}ms "
        f"dash:{o.dashboard_ms:6.0f}ms "
        f"book:{o.book_ms:6.0f}ms "
        f"tries:{o.book_attempts} "
        f"— {tail}"
    )


def main():
    global BOOK_BARRIER

    try:
        n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    except ValueError:
        print("Usage: python stress_test.py [num_users]")
        sys.exit(1)

    if n < 1:
        print("num_users must be >= 1")
        sys.exit(1)

    pool = STUDENT_USERNAMES
    usernames = [pool[i % len(pool)] for i in range(n)]

    print(f"\n=== Stress Test: {n} concurrent users ===")
    print(f"Target: {BASE_URL}")
    print(f"Using {len(set(usernames))} unique accounts ({n} sessions)")
    print(f"Each user will retry booking up to {MAX_BOOK_ATTEMPTS} times on slot conflicts\n")

    BOOK_BARRIER = threading.Barrier(n, timeout=60)

    start = time.perf_counter()
    outcomes = []
    with ThreadPoolExecutor(max_workers=n) as executor:
        futures = [executor.submit(simulate_user, u) for u in usernames]
        for f in as_completed(futures):
            try:
                outcomes.append(f.result())
            except Exception as e:
                print(f"Thread exception: {e}")

    elapsed = time.perf_counter() - start
    outcomes.sort(key=lambda o: o.username)

    print("\n--- Per-user results ---")
    for o in outcomes:
        print(format_row(o))

    total = len(outcomes)
    login_ok = sum(1 for o in outcomes if o.login_ok)
    dash_ok = sum(1 for o in outcomes if o.dashboard_ok)
    book_ok = sum(1 for o in outcomes if o.book_ok)
    window_open = sum(1 for o in outcomes if o.booking_window_open)

    total_attempts = sum(o.book_attempts for o in outcomes)
    booked_on_retry = sum(1 for o in outcomes if o.book_ok and o.book_attempts > 1)
    booked_first_try = sum(1 for o in outcomes if o.book_ok and o.book_attempts == 1)

    login_times = [o.login_ms for o in outcomes if o.login_ok]
    dash_times = [o.dashboard_ms for o in outcomes if o.dashboard_ok]
    book_times = [o.book_ms for o in outcomes if o.book_attempted]

    def stats(times):
        if not times:
            return "(no data)"
        times_sorted = sorted(times)
        avg = sum(times) / len(times)
        p50 = times_sorted[len(times) // 2]
        p95 = times_sorted[int(len(times) * 0.95)] if len(times) > 1 else times_sorted[-1]
        return f"avg {avg:.0f}ms  p50 {p50:.0f}ms  p95 {p95:.0f}ms"

    failures = Counter()
    for o in outcomes:
        if not o.book_ok and o.failure_reason:
            reason = o.failure_reason.split(":")[0].strip()
            failures[reason] += 1

    print("\n--- Summary ---")
    print(f"Total wall-clock time:     {elapsed:.2f}s")
    print(f"Booking window was open:   {window_open}/{total}")
    print(f"Logins succeeded:          {login_ok}/{total}    {stats(login_times)}")
    print(f"Dashboard loads succeeded: {dash_ok}/{total}    {stats(dash_times)}")
    print(f"Bookings succeeded:        {book_ok}/{total}    {stats(book_times)}")
    print(f"  ↳ booked on first try:   {booked_first_try}")
    print(f"  ↳ booked after retries:  {booked_on_retry}")
    print(f"Total book attempts made:  {total_attempts} (avg {total_attempts/total:.1f} per user)")

    if failures:
        print("\nFailure breakdown:")
        for reason, count in failures.most_common():
            print(f"  {reason:<20} {count}")

    print("\n--- Interpretation ---")
    if login_ok < total:
        print(f"⚠️  {total - login_ok} logins failed. Server may be struggling.")
    if dash_ok < total:
        print(f"⚠️  {total - dash_ok} dashboard loads failed.")
    if window_open == 0 and total > 0:
        print("ℹ️  Booking window was closed — booking failures expected.")
    elif book_ok == 0 and window_open > 0:
        print("⚠️  Window was open but nobody booked. Check setup.")
    elif book_ok > 0:
        print(f"✅  {book_ok} real bookings succeeded. Clean up with:")
        print("    DELETE FROM lessons WHERE created_at > NOW() - INTERVAL '10 minutes';")

    all_times = login_times + dash_times + book_times
    if all_times and max(all_times) > 5000:
        print(f"⚠️  Slowest request was {max(all_times):.0f}ms.")


if __name__ == "__main__":
    main()