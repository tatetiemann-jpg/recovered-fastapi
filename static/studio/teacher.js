// ============================================================
// STUDIO TEACHER DASHBOARD
// ============================================================

const VALID_TEACHER_TABS = ["lessons", "schedule", "students", "messages"];
const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH_NAMES = ["January","February","March","April","May","June",
                     "July","August","September","October","November","December"];

let calYear, calMonth;
let allStudents = [];      // cached list from GET /studio-teacher/students
let allFamilies = [];      // cached list from GET /studio-teacher/families
let lessonModalDate = null;
let lessonModalWeekday = null;
let selectedStudentId = null; // for lesson modal
let mwSelectedStudentId = null; // for multi-week modal
let activeStudentId = null; // for student detail modal

// ============================================================
// INIT
// ============================================================

document.addEventListener("DOMContentLoaded", () => {
    loadMe();
    initTabs();
    initLogout();
    initCalendarNav();
    initModals();
    initDmTab();
});

async function loadMe() {
    try {
        const res = await fetch(`${API}/me`, { credentials: "include" });
        const data = await res.json();
        if (!data.logged_in) { location.href = "/login"; return; }
        if (data.role !== "studio_teacher") { location.href = "/login"; return; }
        document.getElementById("welcome").textContent = `Welcome, ${data.fullname || data.username}`;
        if (typeof window.setCharacterTheme === "function") window.setCharacterTheme(data.theme);
    } catch (e) {
        console.error(e);
    }
}

function initLogout() {
    document.getElementById("logout-btn")?.addEventListener("click", async () => {
        await fetch(`${API}/logout`, { method: "POST", credentials: "include" });
        location.href = "/login";
    });
    document.getElementById("open-edit-account-btn")?.addEventListener("click", () => {
        document.getElementById("edit-account-modal")?.classList.remove("hidden");
    });
}

// ============================================================
// TABS
// ============================================================

function initTabs() {
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });
    switchTab("lessons");
}

function switchTab(tab) {
    if (!VALID_TEACHER_TABS.includes(tab)) return;
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.toggle("active", p.dataset.tabPanel === tab));
    if (tab === "lessons") loadLessons();
    if (tab === "schedule") initCalendar();
    if (tab === "students") loadStudents();
    if (tab === "messages") loadDmInbox();
}

// ============================================================
// LESSONS TAB
// ============================================================

async function loadLessons() {
    try {
        const res = await fetch(`${API}/studio-teacher/lessons`, { credentials: "include" });
        const lessons = await res.json();
        const today = lessons.filter(l => l.is_today);
        const upcoming = lessons.filter(l => !l.is_today && !l.is_past);
        const past7 = lessons.filter(l => l.is_past);

        renderLessonList("today-lessons-list", today, "No lessons today.");
        renderLessonList("upcoming-lessons-list", upcoming, "No upcoming lessons.");
    } catch (e) {
        console.error(e);
    }
}

function renderLessonList(containerId, lessons, emptyMsg) {
    const el = document.getElementById(containerId);
    if (!el) return;
    if (!lessons.length) {
        el.innerHTML = `<em class="empty-note">${emptyMsg}</em>`;
        return;
    }
    el.innerHTML = lessons.map(l => {
        const dateStr = formatDateLabel(l.date);
        const zoom = l.zoom_link
            ? `<a href="${escHtml(l.zoom_link)}" target="_blank" rel="noopener" class="zoom-link">Zoom</a>`
            : "";
        const attBtns = l.is_past ? `
            <button class="chip ${l.attendance === 'present' ? 'active' : ''}" onclick="markAttendance(${l.id}, 'present')">Present</button>
            <button class="chip ${l.attendance === 'absent' ? 'active' : ''}" onclick="markAttendance(${l.id}, 'absent')">Absent</button>
        ` : "";
        return `
            <div class="lesson-card ${l.is_past ? 'lesson-past' : ''}">
                <div class="lesson-card-info">
                    <strong>${escHtml(l.student_name || "Unknown")}</strong>
                    <span class="hint">${dateStr} · ${l.time} · ${l.duration_min} min</span>
                    ${zoom}
                </div>
                <div class="lesson-card-actions" style="display:flex;gap:var(--space-2);align-items:center;flex-wrap:wrap;">
                    ${attBtns}
                    <button class="subtle-btn" onclick="cancelLesson(${l.id})">Cancel</button>
                </div>
            </div>
        `;
    }).join("");
}

async function markAttendance(lessonId, att) {
    await fetch(`${API}/studio-teacher/lesson/${lessonId}/attendance`, {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ attendance: att }),
    });
    loadLessons();
}

async function cancelLesson(lessonId) {
    if (!confirm("Cancel this lesson?")) return;
    await fetch(`${API}/studio-teacher/lesson/${lessonId}`, {
        method: "DELETE",
        credentials: "include",
    });
    loadLessons();
    loadCalendar(calYear, calMonth);
}

// ============================================================
// DAY DETAIL MODAL
// ============================================================

let dayDetailDate = null;

async function openDayDetail(dateStr) {
    dayDetailDate = dateStr;
    const modal = document.getElementById("day-detail-modal");
    document.getElementById("dd-date-label").textContent = formatDateLabel(dateStr);
    document.getElementById("dd-list").innerHTML = '<em class="empty-note">Loading…</em>';
    modal.classList.remove("hidden");

    const resp = await fetch(`${API}/studio-teacher/lessons-for-date?date=${dateStr}`, { credentials: "include" });
    const lessons = await resp.json();

    if (!lessons.length) {
        document.getElementById("dd-list").innerHTML = '<em class="empty-note">No lessons scheduled.</em>';
    } else {
        document.getElementById("dd-list").innerHTML = lessons.map(l => `
            <div class="lesson-card">
                <div class="lesson-card-info">
                    <strong>${escHtml(l.student_name)}</strong>
                    <span class="hint">${formatTime12(l.time)} &middot; ${l.duration_min} min</span>
                    ${l.zoom_link ? `<a class="zoom-link" href="${escHtml(l.zoom_link)}" target="_blank">Zoom</a>` : ""}
                </div>
                <div class="lesson-card-actions">
                    <button class="subtle-btn" onclick="cancelLessonFromDetail(${l.id})">Cancel</button>
                </div>
            </div>
        `).join("");
    }

    document.getElementById("dd-add-btn").onclick = () => {
        modal.classList.add("hidden");
        openLessonModal(dateStr);
    };
}

async function cancelLessonFromDetail(lessonId) {
    if (!confirm("Cancel this lesson?")) return;
    await fetch(`${API}/studio-teacher/lesson/${lessonId}`, { method: "DELETE", credentials: "include" });
    loadLessons();
    loadCalendar(calYear, calMonth);
    if (dayDetailDate) openDayDetail(dayDetailDate);
}

// ============================================================
// SCHEDULE TAB — CALENDAR
// ============================================================

function initCalendar() {
    const today = new Date();
    calYear = today.getFullYear();
    calMonth = today.getMonth() + 1;
    loadCalendar(calYear, calMonth);
}

function initCalendarNav() {
    document.getElementById("cal-prev-btn")?.addEventListener("click", () => {
        calMonth--;
        if (calMonth < 1) { calMonth = 12; calYear--; }
        loadCalendar(calYear, calMonth);
    });
    document.getElementById("cal-next-btn")?.addEventListener("click", () => {
        calMonth++;
        if (calMonth > 12) { calMonth = 1; calYear++; }
        loadCalendar(calYear, calMonth);
    });
}

async function loadCalendar(year, month) {
    calYear = year;
    calMonth = month;

    const label = document.getElementById("cal-month-label");
    if (label) label.textContent = `${MONTH_NAMES[month - 1]} ${year}`;

    try {
        const res = await fetch(`${API}/studio-teacher/calendar?year=${year}&month=${month}`, { credentials: "include" });
        const data = await res.json();
        renderCalendar(year, month, data.available_days || [], data.lessons || []);
    } catch (e) {
        console.error(e);
    }
}

function renderCalendar(year, month, availDays, lessonData) {
    const cal = document.getElementById("studio-calendar");
    if (!cal) return;

    const availSet = new Set(availDays);
    const lessonMap = {};
    lessonData.forEach(l => { lessonMap[l.day] = l; });

    const firstDay = new Date(year, month - 1, 1).getDay(); // 0=Sun
    const daysInMonth = new Date(year, month, 0).getDate();
    const today = new Date();
    const todayDay = today.getFullYear() === year && (today.getMonth() + 1) === month ? today.getDate() : -1;

    let html = `<div class="cal-grid">`;

    // Weekday headers — clickable if any available day falls on that weekday
    const weekdayHasAvail = Array(7).fill(false);
    for (let d = 1; d <= daysInMonth; d++) {
        const dow = new Date(year, month - 1, d).getDay();
        if (availSet.has(d)) weekdayHasAvail[dow] = true;
    }

    DAY_NAMES.forEach((name, dow) => {
        const clickable = weekdayHasAvail[dow];
        html += `<div class="cal-weekday-header ${clickable ? 'cal-clickable' : ''}"
            ${clickable ? `onclick="openWeekdayModal(${dow})"` : ""}>${name}</div>`;
    });

    // Empty cells before first day
    for (let i = 0; i < firstDay; i++) {
        html += `<div class="cal-cell cal-empty"></div>`;
    }

    for (let d = 1; d <= daysInMonth; d++) {
        const avail = availSet.has(d);
        const info = lessonMap[d];
        const isToday = d === todayDay;
        let cls = "cal-cell";
        if (avail) cls += " cal-available";
        if (isToday) cls += " cal-today";
        if (!avail) cls += " cal-unavailable";

        const dateStr = `${year}-${String(month).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
        const dot = info
            ? `<span class="cal-dot ${info.has_yellow ? 'cal-dot-yellow' : 'cal-dot-green'}"
                 onclick="event.stopPropagation();openDayDetail('${dateStr}')">${info.count}</span>`
            : "";

        const onclick = avail ? `onclick="openLessonModal('${dateStr}')"` : "";
        html += `<div class="${cls}" ${onclick}><span class="cal-day-num">${d}</span>${dot}</div>`;
    }

    html += `</div>`;
    cal.innerHTML = html;
}

// ============================================================
// LESSON MODAL (single day)
// ============================================================

async function openLessonModal(dateStr) {
    lessonModalDate = dateStr;
    selectedStudentId = null;

    document.getElementById("lesson-modal-title").textContent = "Add Lesson";
    document.getElementById("lesson-modal-date-label").textContent = formatDateLabel(dateStr);
    document.getElementById("lesson-student-search").value = "";
    document.getElementById("lesson-student-dropdown").classList.add("hidden");
    document.getElementById("lesson-student-balance").textContent = "";
    document.getElementById("lesson-free-type-row").classList.add("hidden");
    document.getElementById("lesson-student-email").value = "";
    document.getElementById("lesson-zoom-link").value = "";
    document.getElementById("lesson-time-select").innerHTML = `<option value="">— loading —</option>`;
    document.getElementById("lesson-overrun-warning").classList.add("hidden");
    document.getElementById("lesson-modal-msg").textContent = "";

    // Reset duration chips to 30
    setActiveChip("lesson-duration-chips", "30");

    document.getElementById("lesson-modal").classList.remove("hidden");

    await fetchAndPopulateSlots("lesson-time-select", dateStr, 30);
}

async function fetchAndPopulateSlots(selectId, dateStr, dur) {
    const sel = document.getElementById(selectId);
    try {
        const res = await fetch(`${API}/studio-teacher/available-slots?date=${dateStr}`, { credentials: "include" });
        const data = await res.json();
        const slots = data.slots || [];
        if (!slots.length) {
            sel.innerHTML = `<option value="">No available slots</option>`;
        } else {
            sel.innerHTML = `<option value="">— select time —</option>` +
                slots.map(s => `<option value="${s}">${formatTime12(s)}</option>`).join("");
        }
    } catch (e) {
        sel.innerHTML = `<option value="">Error loading slots</option>`;
    }
}

function initLessonModal() {
    // Duration chip selection
    document.getElementById("lesson-duration-chips")?.addEventListener("click", e => {
        const chip = e.target.closest(".chip");
        if (!chip) return;
        setActiveChip("lesson-duration-chips", chip.dataset.dur);
        if (lessonModalDate) fetchAndPopulateSlots("lesson-time-select", lessonModalDate, chip.dataset.dur);
        if (selectedStudentId) updateBalanceDisplay(selectedStudentId, chip.dataset.dur, "lesson-student-balance", "lesson-overrun-warning");
    });

    // Student search
    const search = document.getElementById("lesson-student-search");
    const dropdown = document.getElementById("lesson-student-dropdown");
    search?.addEventListener("input", () => {
        const q = search.value.trim().toLowerCase();
        if (!q) { dropdown.classList.add("hidden"); return; }
        const matches = allStudents.filter(s => s.name.toLowerCase().includes(q) || (s.email || "").toLowerCase().includes(q));
        if (!matches.length) {
            dropdown.innerHTML = `<div class="student-option student-option-free">Use "<strong>${escHtml(search.value)}</strong>" as name</div>`;
            dropdown.classList.remove("hidden");
            dropdown.querySelector(".student-option-free")?.addEventListener("click", () => {
                selectedStudentId = null;
                document.getElementById("lesson-free-type-row").classList.remove("hidden");
                document.getElementById("lesson-student-balance").textContent = "";
                dropdown.classList.add("hidden");
            });
        } else {
            dropdown.innerHTML = matches.slice(0, 8).map(s =>
                `<div class="student-option" data-id="${s.id}">${escHtml(s.name)}${s.family_name ? ` <span class="hint">(${escHtml(s.family_name)})</span>` : ""}</div>`
            ).join("");
            dropdown.classList.remove("hidden");
            dropdown.querySelectorAll(".student-option[data-id]").forEach(opt => {
                opt.addEventListener("click", () => {
                    const id = parseInt(opt.dataset.id);
                    const student = allStudents.find(s => s.id === id);
                    selectedStudentId = id;
                    search.value = student.name;
                    document.getElementById("lesson-free-type-row").classList.add("hidden");
                    dropdown.classList.add("hidden");
                    const dur = getActiveChip("lesson-duration-chips") || "30";
                    updateBalanceDisplay(id, dur, "lesson-student-balance", "lesson-overrun-warning");
                });
            });
        }
    });

    // Submit
    document.getElementById("lesson-submit-btn")?.addEventListener("click", submitLessonModal);
    document.getElementById("lesson-cancel-btn")?.addEventListener("click", () => {
        document.getElementById("lesson-modal").classList.add("hidden");
    });
}

async function updateBalanceDisplay(studentId, durStr, balanceElId, warningElId) {
    const dur = parseInt(durStr) || 30;
    const balEl = document.getElementById(balanceElId);
    const warnEl = document.getElementById(warningElId);
    try {
        const res = await fetch(`${API}/studio-teacher/student/${studentId}/payment-balance?duration_min=${dur}`, { credentials: "include" });
        const b = await res.json();
        if (b.remaining !== undefined) {
            const remaining = b.remaining;
            const paid = b.lessons_paid;
            if (paid === 0) {
                balEl.textContent = "No lessons on file as paid.";
            } else {
                balEl.textContent = `${remaining} of ${paid} paid lessons remaining (${dur} min).`;
            }
            if (remaining <= 0) {
                warnEl?.classList.remove("hidden");
            } else {
                warnEl?.classList.add("hidden");
            }
        }
    } catch (e) { /* ignore */ }
}

async function submitLessonModal() {
    const btn = document.getElementById("lesson-submit-btn");
    const msg = document.getElementById("lesson-modal-msg");
    const timeVal = document.getElementById("lesson-time-select").value;
    const dur = getActiveChip("lesson-duration-chips") || "30";
    const zoomLink = document.getElementById("lesson-zoom-link").value.trim();

    let name = document.getElementById("lesson-student-search").value.trim();
    let email = null;
    if (!selectedStudentId) {
        email = document.getElementById("lesson-student-email").value.trim().toLowerCase() || null;
    }

    if (!name) { msg.textContent = "Please enter a student name."; return; }
    if (!timeVal) { msg.textContent = "Please select a time."; return; }

    btn.disabled = true;
    btn.textContent = "Saving…";
    try {
        const res = await fetch(`${API}/studio-teacher/lesson`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                date: lessonModalDate,
                time: timeVal,
                duration_min: parseInt(dur),
                external_name: selectedStudentId ? null : name,
                external_email: email,
                studio_student_id: selectedStudentId,
                zoom_link: zoomLink || null,
            }),
        });
        const data = await res.json();
        if (data.status === "success") {
            document.getElementById("lesson-modal").classList.add("hidden");
            loadCalendar(calYear, calMonth);
            loadLessons();
        } else {
            msg.textContent = data.message || "Failed to add lesson.";
        }
    } catch (e) {
        msg.textContent = "Server error.";
    }
    btn.disabled = false;
    btn.textContent = "Save";
}

// ============================================================
// MULTI-WEEK MODAL (weekday header click)
// ============================================================

async function openWeekdayModal(dow) {
    lessonModalWeekday = dow;
    mwSelectedStudentId = null;

    document.getElementById("multiweek-modal-title").textContent = `Add Lessons — ${DAY_NAMES[dow]}s`;
    document.getElementById("multiweek-modal-sub").textContent =
        `Schedule lessons on upcoming ${DAY_NAMES[dow]}s this month.`;
    document.getElementById("mw-student-search").value = "";
    document.getElementById("mw-student-dropdown").classList.add("hidden");
    document.getElementById("mw-student-balance").textContent = "";
    document.getElementById("mw-free-type-row").classList.add("hidden");
    document.getElementById("mw-student-email").value = "";
    document.getElementById("mw-zoom-link").value = "";
    document.getElementById("mw-modal-msg").textContent = "";
    setActiveChip("mw-duration-chips", "30");
    setActiveChip("mw-count-chips", "1");

    // Load slots for next occurrence of this weekday
    const nextDate = nextWeekdayDate(calYear, calMonth, dow);
    if (nextDate) {
        await fetchAndPopulateSlots("mw-time-select", nextDate, 30);
    } else {
        document.getElementById("mw-time-select").innerHTML = `<option value="">No available dates</option>`;
    }

    document.getElementById("multiweek-modal").classList.remove("hidden");
}

function nextWeekdayDate(year, month, targetDow) {
    for (let d = 1; d <= 31; d++) {
        const dt = new Date(year, month - 1, d);
        if (dt.getMonth() !== month - 1) break;
        if (dt.getDay() === targetDow) {
            return `${year}-${String(month).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
        }
    }
    return null;
}

function getAllWeekdayDates(year, month, targetDow, count) {
    const dates = [];
    for (let d = 1; d <= 31 && dates.length < count; d++) {
        const dt = new Date(year, month - 1, d);
        if (dt.getMonth() !== month - 1) break;
        if (dt.getDay() === targetDow) {
            dates.push(`${year}-${String(month).padStart(2,'0')}-${String(d).padStart(2,'0')}`);
        }
    }
    return dates;
}

function initMultiweekModal() {
    document.getElementById("mw-duration-chips")?.addEventListener("click", e => {
        const chip = e.target.closest(".chip");
        if (!chip) return;
        setActiveChip("mw-duration-chips", chip.dataset.dur);
        const firstDate = nextWeekdayDate(calYear, calMonth, lessonModalWeekday);
        if (firstDate) fetchAndPopulateSlots("mw-time-select", firstDate, chip.dataset.dur);
        if (mwSelectedStudentId) updateBalanceDisplay(mwSelectedStudentId, chip.dataset.dur, "mw-student-balance", null);
    });

    document.getElementById("mw-count-chips")?.addEventListener("click", e => {
        const chip = e.target.closest(".chip");
        if (!chip) return;
        setActiveChip("mw-count-chips", chip.dataset.count);
    });

    const mwSearch = document.getElementById("mw-student-search");
    const mwDrop = document.getElementById("mw-student-dropdown");
    mwSearch?.addEventListener("input", () => {
        const q = mwSearch.value.trim().toLowerCase();
        if (!q) { mwDrop.classList.add("hidden"); return; }
        const matches = allStudents.filter(s => s.name.toLowerCase().includes(q) || (s.email || "").toLowerCase().includes(q));
        if (!matches.length) {
            mwDrop.innerHTML = `<div class="student-option student-option-free">Use "<strong>${escHtml(mwSearch.value)}</strong>" as name</div>`;
            mwDrop.classList.remove("hidden");
            mwDrop.querySelector(".student-option-free")?.addEventListener("click", () => {
                mwSelectedStudentId = null;
                document.getElementById("mw-free-type-row").classList.remove("hidden");
                mwDrop.classList.add("hidden");
            });
        } else {
            mwDrop.innerHTML = matches.slice(0, 8).map(s =>
                `<div class="student-option" data-id="${s.id}">${escHtml(s.name)}${s.family_name ? ` <span class="hint">(${escHtml(s.family_name)})</span>` : ""}</div>`
            ).join("");
            mwDrop.classList.remove("hidden");
            mwDrop.querySelectorAll(".student-option[data-id]").forEach(opt => {
                opt.addEventListener("click", () => {
                    const id = parseInt(opt.dataset.id);
                    const student = allStudents.find(s => s.id === id);
                    mwSelectedStudentId = id;
                    mwSearch.value = student.name;
                    document.getElementById("mw-free-type-row").classList.add("hidden");
                    mwDrop.classList.add("hidden");
                    const dur = getActiveChip("mw-duration-chips") || "30";
                    updateBalanceDisplay(id, dur, "mw-student-balance", null);
                });
            });
        }
    });

    document.getElementById("mw-submit-btn")?.addEventListener("click", submitMultiweekModal);
    document.getElementById("mw-cancel-btn")?.addEventListener("click", () => {
        document.getElementById("multiweek-modal").classList.add("hidden");
    });
}

async function submitMultiweekModal() {
    const btn = document.getElementById("mw-submit-btn");
    const msg = document.getElementById("mw-modal-msg");
    const timeVal = document.getElementById("mw-time-select").value;
    const dur = parseInt(getActiveChip("mw-duration-chips") || "30");
    const count = parseInt(getActiveChip("mw-count-chips") || "1");
    const zoomLink = document.getElementById("mw-zoom-link").value.trim() || null;
    let name = document.getElementById("mw-student-search").value.trim();
    let email = !mwSelectedStudentId ? (document.getElementById("mw-student-email").value.trim().toLowerCase() || null) : null;

    if (!name) { msg.textContent = "Please enter a student name."; return; }
    if (!timeVal) { msg.textContent = "Please select a time."; return; }

    const dates = getAllWeekdayDates(calYear, calMonth, lessonModalWeekday, count);
    if (!dates.length) { msg.textContent = "No dates available."; return; }

    btn.disabled = true;
    btn.textContent = "Scheduling…";

    const lessons = dates.map(dateStr => ({
        date: dateStr,
        time: timeVal,
        duration_min: dur,
        external_name: mwSelectedStudentId ? null : name,
        external_email: email,
        studio_student_id: mwSelectedStudentId,
        zoom_link: zoomLink,
    }));

    try {
        const res = await fetch(`${API}/studio-teacher/lessons-bulk`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lessons }),
        });
        const data = await res.json();
        btn.disabled = false;
        btn.textContent = "Schedule";
        if (data.added < dates.length) {
            msg.textContent = `${data.added}/${dates.length} lessons added.`;
        } else {
            document.getElementById("multiweek-modal").classList.add("hidden");
            loadCalendar(calYear, calMonth);
            loadLessons();
        }
    } catch (e) {
        btn.disabled = false;
        btn.textContent = "Schedule";
        msg.textContent = "Error scheduling lessons.";
    }
}

// ============================================================
// PARSE FROM LIST MODAL
// ============================================================

function initParseModal() {
    document.getElementById("open-parse-btn")?.addEventListener("click", () => {
        document.getElementById("parse-text").value = "";
        document.getElementById("parse-msg").textContent = "";
        document.getElementById("parse-preview-section").classList.add("hidden");
        document.getElementById("parse-modal").classList.remove("hidden");
    });

    document.getElementById("parse-submit-btn")?.addEventListener("click", submitParseModal);
    document.getElementById("parse-cancel-btn")?.addEventListener("click", () => {
        document.getElementById("parse-modal").classList.add("hidden");
    });
    document.getElementById("parse-confirm-btn")?.addEventListener("click", confirmParsedLessons);
}

let parsedLessons = [];

async function submitParseModal() {
    const btn = document.getElementById("parse-submit-btn");
    const msg = document.getElementById("parse-msg");
    const text = document.getElementById("parse-text").value.trim();
    if (!text) { msg.textContent = "Please paste your schedule."; return; }

    btn.disabled = true;
    btn.textContent = "Parsing…";
    msg.textContent = "";
    try {
        const res = await fetch(`${API}/studio-teacher/lessons-parse`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text }),
        });
        const data = await res.json();
        if (data.status !== "success") {
            msg.textContent = data.message || "Parse failed.";
        } else {
            parsedLessons = data.lessons || [];
            renderParsePreview(parsedLessons);
        }
    } catch (e) {
        msg.textContent = "Server error.";
    }
    btn.disabled = false;
    btn.textContent = "Parse";
}

function renderParsePreview(lessons) {
    const container = document.getElementById("parse-preview-groups");
    const section = document.getElementById("parse-preview-section");
    if (!lessons.length) {
        document.getElementById("parse-msg").textContent = "No lessons found in text.";
        section.classList.add("hidden");
        return;
    }

    // Group by student name (case-insensitive) preserving insertion order
    const groupMap = new Map();
    lessons.forEach(l => {
        const key = (l.student_name || "Unknown").trim().toLowerCase();
        if (!groupMap.has(key)) {
            groupMap.set(key, { name: l.student_name || "", email: l.email || "", lessons: [] });
        }
        const g = groupMap.get(key);
        if (!g.email && l.email) g.email = l.email;
        g.lessons.push(l);
    });

    container.innerHTML = Array.from(groupMap.values()).map(g => {
        const emailMissing = !g.email;
        const lessonRows = g.lessons.map(l => `
            <div class="parse-lesson-row">
                <input class="pr-date" type="date" value="${escHtml(l.date || "")}">
                <input class="pr-time" type="time" value="${escHtml(l.time || "")}">
                <select class="pr-dur">
                    <option value="30" ${(l.duration_min||30)==30?"selected":""}>30 min</option>
                    <option value="45" ${l.duration_min==45?"selected":""}>45 min</option>
                    <option value="60" ${l.duration_min==60?"selected":""}>60 min</option>
                    <option value="90" ${l.duration_min==90?"selected":""}>90 min</option>
                </select>
                <input class="pr-zoom" type="url" placeholder="Zoom (optional)" value="${escHtml(l.zoom_link || "")}">
                <button class="subtle-btn" onclick="removeParsedLesson(this)">Remove</button>
            </div>
        `).join("");

        return `
            <div class="parse-student-group">
                <div class="parse-student-header">
                    <div class="parse-student-name-wrap">
                        <label>Student</label>
                        <input class="pr-name" type="text" value="${escHtml(g.name)}" placeholder="Full name">
                    </div>
                    <div class="parse-student-email-wrap">
                        <label>Email</label>
                        <input class="pr-email" type="email"
                            placeholder="email — required to contact"
                            value="${escHtml(g.email)}"
                            style="${emailMissing ? 'border-color:var(--color-warning,#b45309);' : ''}">
                    </div>
                </div>
                <div class="parse-student-lessons">
                    <div class="parse-lesson-header hint"><span>Date</span><span>Time</span><span>Duration</span><span>Zoom</span><span></span></div>
                    ${lessonRows}
                </div>
            </div>
        `;
    }).join("");

    section.classList.remove("hidden");
}

function removeParsedLesson(btn) {
    const row = btn.closest(".parse-lesson-row");
    const group = row.closest(".parse-student-group");
    row.remove();
    if (!group.querySelectorAll(".parse-lesson-row").length) group.remove();
    const container = document.getElementById("parse-preview-groups");
    if (!container.querySelectorAll(".parse-student-group").length) {
        document.getElementById("parse-preview-section").classList.add("hidden");
    }
}

async function confirmParsedLessons() {
    const btn = document.getElementById("parse-confirm-btn");
    btn.disabled = true;
    btn.textContent = "Adding…";

    const lessons = [];
    for (const group of document.querySelectorAll(".parse-student-group")) {
        const name = group.querySelector(".pr-name")?.value?.trim();
        const email = group.querySelector(".pr-email")?.value?.trim().toLowerCase() || null;
        for (const row of group.querySelectorAll(".parse-lesson-row")) {
            const date = row.querySelector(".pr-date")?.value?.trim();
            const time = row.querySelector(".pr-time")?.value?.trim();
            const dur = parseInt(row.querySelector(".pr-dur")?.value || "30");
            const zoom = row.querySelector(".pr-zoom")?.value?.trim() || null;
            if (!date || !time || !name) continue;
            lessons.push({ date, time, duration_min: dur, external_name: name, external_email: email, zoom_link: zoom });
        }
    }

    try {
        const res = await fetch(`${API}/studio-teacher/lessons-bulk`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lessons }),
        });
        const data = await res.json();
        document.getElementById("parse-msg").textContent = `${data.added || 0} lesson(s) added.`;
    } catch (e) {
        document.getElementById("parse-msg").textContent = "Error adding lessons.";
    }

    btn.disabled = false;
    btn.textContent = "Add All";
    document.getElementById("parse-preview-section").classList.add("hidden");
    loadCalendar(calYear, calMonth);
    loadLessons();
    setTimeout(() => document.getElementById("parse-modal").classList.add("hidden"), 1500);
}

// ============================================================
// AVAILABILITY MODAL
// ============================================================

function initAvailabilityModal() {
    document.getElementById("open-avail-btn")?.addEventListener("click", openAvailabilityModal);
    document.getElementById("cancel-request-btn")?.addEventListener("click", () => {
        document.getElementById("request-modal").classList.add("hidden");
    });

    document.querySelectorAll("input[name='request-scope']").forEach(radio => {
        radio.addEventListener("change", () => {
            const weekInput = document.getElementById("request-week-start");
            if (weekInput) weekInput.disabled = radio.value !== "one_time";
        });
    });

    document.getElementById("submit-request-btn")?.addEventListener("click", submitAvailability);
}

async function openAvailabilityModal() {
    try {
        const res = await fetch(`${API}/teacher/weekly`, { credentials: "include" });
        const rows = await res.json();
        renderAvailabilityForm(rows);
    } catch (e) {
        renderAvailabilityForm([]);
    }
    document.getElementById("request-modal").classList.remove("hidden");
}

function renderAvailabilityForm(existing) {
    const container = document.getElementById("request-days");
    if (!container) return;
    const DAYS = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"];
    const byWeekday = {};
    existing.forEach(r => { byWeekday[r.weekday] = r; });

    container.innerHTML = DAYS.map((name, i) => {
        const row = byWeekday[i];
        const checked = row ? "checked" : "";
        const start = row ? row.start : "09:00";
        const end = row ? row.end : "17:00";
        return `
            <div class="avail-day-row">
                <label class="avail-day-label">
                    <input type="checkbox" class="avail-day-check" data-weekday="${i}" ${checked}>
                    ${name}
                </label>
                <div class="avail-times" style="display:${row ? 'flex' : 'none'};gap:var(--space-2);align-items:center;">
                    <input type="time" class="avail-start" data-weekday="${i}" value="${start}">
                    <span>to</span>
                    <input type="time" class="avail-end" data-weekday="${i}" value="${end}">
                </div>
            </div>
        `;
    }).join("");

    container.querySelectorAll(".avail-day-check").forEach(cb => {
        cb.addEventListener("change", () => {
            const timesDiv = cb.closest(".avail-day-row").querySelector(".avail-times");
            timesDiv.style.display = cb.checked ? "flex" : "none";
        });
    });
}

async function submitAvailability() {
    const btn = document.getElementById("submit-request-btn");
    const msg = document.getElementById("request-msg");
    const scope = document.querySelector("input[name='request-scope']:checked")?.value || "permanent";
    const weekStart = document.getElementById("request-week-start")?.value || null;

    const schedule = [];
    document.querySelectorAll(".avail-day-check:checked").forEach(cb => {
        const wd = parseInt(cb.dataset.weekday);
        const row = cb.closest(".avail-day-row");
        const start = row.querySelector(".avail-start")?.value;
        const end = row.querySelector(".avail-end")?.value;
        if (start && end) schedule.push({ weekday: wd, start_time: start, end_time: end });
    });

    btn.disabled = true;
    btn.textContent = "Saving…";
    try {
        const res = await fetch(`${API}/teacher/update-availability`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scope, effective_week_start: weekStart, schedule }),
        });
        const data = await res.json();
        if (data.status === "success") {
            msg.textContent = "Saved!";
            setTimeout(() => {
                document.getElementById("request-modal").classList.add("hidden");
                msg.textContent = "";
            }, 1000);
            loadCalendar(calYear, calMonth);
        } else {
            msg.textContent = data.message || "Failed to save.";
        }
    } catch (e) {
        msg.textContent = "Server error.";
    }
    btn.disabled = false;
    btn.textContent = "Save Changes";
}

// ============================================================
// STUDENTS TAB
// ============================================================

async function loadStudents() {
    try {
        const [studRes, famRes] = await Promise.all([
            fetch(`${API}/studio-teacher/students`, { credentials: "include" }),
            fetch(`${API}/studio-teacher/families`, { credentials: "include" }),
        ]);
        allStudents = await studRes.json();
        allFamilies = await famRes.json();
        renderStudentList(allStudents);
        populateFamilySelect();
    } catch (e) {
        console.error(e);
    }
}

function populateFamilySelect() {
    const sel = document.getElementById("as-family-select");
    if (!sel) return;
    sel.innerHTML = `<option value="">— solo student —</option>` +
        allFamilies.map(f => `<option value="${f.id}">${escHtml(f.family_name)}</option>`).join("");
}

function renderStudentList(students) {
    const el = document.getElementById("studio-students-list");
    if (!el) return;

    // Build family map from students
    const familiesWithMembers = {};
    const solos = [];
    students.forEach(s => {
        if (s.family_id) {
            if (!familiesWithMembers[s.family_id]) familiesWithMembers[s.family_id] = { name: s.family_name, members: [] };
            familiesWithMembers[s.family_id].members.push(s);
        } else {
            solos.push(s);
        }
    });

    // Merge in any families that exist but have no students yet
    allFamilies.forEach(f => {
        if (!familiesWithMembers[f.id]) {
            familiesWithMembers[f.id] = { name: f.family_name, members: [] };
        }
    });

    const familyList = Object.values(familiesWithMembers);
    const hasFamilies = familyList.length > 0;

    if (!hasFamilies && !solos.length) {
        el.innerHTML = `<em class="empty-note">No students yet. Add a student to get started.</em>`;
        return;
    }

    let html = "";

    if (hasFamilies) {
        html += `<div class="student-section-header">Families</div>`;
        familyList.forEach(fam => {
            const count = fam.members.length;
            html += `<div class="student-family-block">
                <div class="student-family-header"><strong>${escHtml(fam.name)}</strong>
                    <span class="hint" style="font-weight:400"> · ${count} student${count !== 1 ? "s" : ""}</span>
                </div>`;
            if (count) {
                fam.members.forEach(s => { html += renderStudentRow(s); });
            } else {
                html += `<div class="empty-note" style="font-size:0.85rem;padding:var(--space-2) 0;">No students yet — add a student and assign them to this family.</div>`;
            }
            html += `</div>`;
        });
    }

    if (solos.length) {
        html += `<div class="student-section-header" style="margin-top:${hasFamilies ? 'var(--space-4)' : '0'}">Individual Students</div>`;
        solos.forEach(s => { html += renderStudentRow(s); });
    }

    el.innerHTML = html;
}

function renderStudentRow(s, familyName) {
    const payBadge = paymentBadge(s.payments);
    const att = s.attendance || { present: 0, absent: 0 };
    const noEmailBadge = !s.email
        ? `<span class="pay-badge pay-warn" title="Add an email so this student can be messaged">No email</span>`
        : "";
    return `
        <div class="student-row" onclick="openStudentDetail(${s.id})">
            <div>
                <strong>${escHtml(s.name)}</strong>
                ${s.email ? `<span class="hint"> · ${escHtml(s.email)}</span>` : ""}
                ${s.parent_name ? `<br><span class="hint">Parent: ${escHtml(s.parent_name)}</span>` : ""}
            </div>
            <div style="display:flex;gap:var(--space-2);align-items:center;flex-wrap:wrap;">
                <span class="hint">${att.present} attended · ${att.absent} missed</span>
                ${payBadge}
                ${noEmailBadge}
            </div>
        </div>
    `;
}

function paymentBadge(payments) {
    if (!payments || !payments.length) return `<span class="pay-badge pay-none">No payment record</span>`;
    // Only consider durations that have actual activity
    const active = payments.filter(p => p.scheduled > 0 || p.lessons_paid > 0);
    if (!active.length) return `<span class="pay-badge pay-none">No payment record</span>`;
    if (active.some(p => p.remaining < 0)) return `<span class="pay-badge pay-overrun">Payment needed</span>`;
    // remaining >= 0 for all active durations — fully paid or prepaid
    const surplus = active.filter(p => p.remaining > 0);
    if (!surplus.length) return `<span class="pay-badge pay-ok">Fully paid</span>`;
    return `<span class="pay-badge pay-ok">${surplus.map(p => `${p.remaining}×${p.duration_min}min`).join(", ")} prepaid</span>`;
}

// ============================================================
// STUDENT DETAIL MODAL
// ============================================================

function openStudentDetail(studentId) {
    const s = allStudents.find(s => s.id === studentId);
    if (!s) return;
    activeStudentId = studentId;

    document.getElementById("sd-name").textContent = s.name;
    document.getElementById("sd-email").textContent = s.email || "";
    document.getElementById("sd-parent").textContent = s.parent_name
        ? `Parent: ${s.parent_name}${s.parent_email ? ` (${s.parent_email})` : ""}`
        : "";
    document.getElementById("sd-family").textContent = s.family_name ? `Family: ${s.family_name}` : "";
    document.getElementById("sd-present-count").textContent = s.attendance?.present ?? 0;
    document.getElementById("sd-absent-count").textContent = s.attendance?.absent ?? 0;
    document.getElementById("sd-msg").textContent = "";

    const payEl = document.getElementById("sd-payment-summary");
    if (!s.payments || !s.payments.length) {
        payEl.innerHTML = `<em class="hint">No payment records.</em>`;
    } else {
        payEl.innerHTML = s.payments
            .filter(p => p.scheduled > 0 || p.lessons_paid > 0)
            .map(p => {
                const remaining = p.remaining;
                const cls = remaining >= 0 ? "pay-ok" : "pay-overrun";
                const outstanding = remaining < 0 ? Math.abs(remaining) : 0;
                const label = remaining > 0 ? `${remaining} prepaid` : remaining === 0 ? "Fully paid" : `${outstanding} outstanding`;
                return `<div class="payment-row">
                    <strong>${p.duration_min} min:</strong>
                    <span class="pay-badge ${cls}">${p.scheduled} scheduled · ${p.lessons_paid} paid · ${label}</span>
                </div>`;
            }).join("");
    }

    document.getElementById("student-detail-modal").classList.remove("hidden");
}

function initStudentDetailModal() {
    document.getElementById("sd-close-btn")?.addEventListener("click", () => {
        document.getElementById("student-detail-modal").classList.add("hidden");
    });

    document.getElementById("sd-message-btn")?.addEventListener("click", () => {
        const s = allStudents.find(s => s.id === activeStudentId);
        if (!s || !s.email) {
            document.getElementById("sd-msg").textContent = "No email on file for this student.";
            return;
        }
        document.getElementById("student-detail-modal").classList.add("hidden");
        switchTab("messages");
        // Pre-fill DM scope to direct and set recipient
        document.getElementById("dm-scope").value = "direct";
        document.getElementById("dm-scope").dispatchEvent(new Event("change"));
        const searchEl = document.getElementById("dm-recipient-search");
        if (searchEl) {
            searchEl.value = s.name;
            searchEl.dispatchEvent(new Event("input"));
        }
    });

    document.getElementById("sd-update-payments-btn")?.addEventListener("click", () => {
        openUpdatePaymentsModal(activeStudentId);
    });

    document.getElementById("sd-reminder-btn")?.addEventListener("click", async () => {
        const btn = document.getElementById("sd-reminder-btn");
        btn.disabled = true;
        btn.textContent = "Sending…";
        try {
            const res = await fetch(`${API}/studio-teacher/student/${activeStudentId}/payment-reminder`, {
                method: "POST",
                credentials: "include",
            });
            const data = await res.json();
            document.getElementById("sd-msg").textContent = data.status === "success"
                ? "Reminder sent!" : "Failed to send reminder.";
        } catch (e) {
            document.getElementById("sd-msg").textContent = "Server error.";
        }
        btn.disabled = false;
        btn.textContent = "Send Payment Reminder";
    });
}

// ============================================================
// UPDATE PAYMENTS MODAL
// ============================================================

function openUpdatePaymentsModal(studentId) {
    const s = allStudents.find(s => s.id === studentId);
    if (!s) return;

    document.getElementById("up-title").textContent = `Update Payments — ${s.name}`;
    document.getElementById("up-msg").textContent = "";

    const rowsEl = document.getElementById("up-rows");
    const existing = s.payments || [];
    const durs = existing.length ? existing.map(p => p.duration_min) : [30];

    rowsEl.innerHTML = durs.map(d => {
        const p = existing.find(e => e.duration_min === d);
        return `<div class="payment-input-row" data-dur="${d}">
            <label>${d} min lessons</label>
            <input type="number" min="0" class="up-paid-input" data-dur="${d}" value="${p ? p.lessons_paid : 0}">
            <span class="hint">lessons paid</span>
        </div>`;
    }).join("");

    document.getElementById("update-payments-modal").classList.remove("hidden");
}

function initUpdatePaymentsModal() {
    document.getElementById("up-add-row-btn")?.addEventListener("click", () => {
        const rowsEl = document.getElementById("up-rows");
        const existing = Array.from(rowsEl.querySelectorAll(".payment-input-row")).map(r => parseInt(r.dataset.dur));
        const available = [30, 45, 60, 90].filter(d => !existing.includes(d));
        if (!available.length) return;
        const d = available[0];
        const div = document.createElement("div");
        div.className = "payment-input-row";
        div.dataset.dur = d;
        div.innerHTML = `<label>${d} min lessons</label>
            <input type="number" min="0" class="up-paid-input" data-dur="${d}" value="0">
            <span class="hint">lessons paid</span>`;
        rowsEl.appendChild(div);
    });

    document.getElementById("up-submit-btn")?.addEventListener("click", submitUpdatePayments);
    document.getElementById("up-cancel-btn")?.addEventListener("click", () => {
        document.getElementById("update-payments-modal").classList.add("hidden");
    });
}

async function submitUpdatePayments() {
    const btn = document.getElementById("up-submit-btn");
    const msg = document.getElementById("up-msg");
    const payments = [];
    document.querySelectorAll(".up-paid-input").forEach(inp => {
        payments.push({ duration_min: parseInt(inp.dataset.dur), lessons_paid: parseInt(inp.value) || 0 });
    });

    btn.disabled = true;
    btn.textContent = "Saving…";
    try {
        const res = await fetch(`${API}/studio-teacher/student/${activeStudentId}/payments`, {
            method: "PATCH",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ payments }),
        });
        const data = await res.json();
        if (data.status === "success") {
            msg.textContent = "Saved!";
            await loadStudents();
            // Re-open detail with refreshed data
            setTimeout(() => {
                document.getElementById("update-payments-modal").classList.add("hidden");
                msg.textContent = "";
                openStudentDetail(activeStudentId);
            }, 800);
        } else {
            msg.textContent = data.message || "Failed.";
        }
    } catch (e) {
        msg.textContent = "Server error.";
    }
    btn.disabled = false;
    btn.textContent = "Save";
}

// ============================================================
// ADD STUDENT / ADD FAMILY
// ============================================================

function initAddStudentModal() {
    document.getElementById("add-student-btn")?.addEventListener("click", () => {
        document.getElementById("as-name").value = "";
        document.getElementById("as-email").value = "";
        document.getElementById("as-parent-name").value = "";
        document.getElementById("as-parent-email").value = "";
        document.getElementById("as-msg").textContent = "";
        document.getElementById("add-student-modal").classList.remove("hidden");
    });

    document.getElementById("as-submit-btn")?.addEventListener("click", submitAddStudent);
    document.getElementById("as-cancel-btn")?.addEventListener("click", () => {
        document.getElementById("add-student-modal").classList.add("hidden");
    });
}

async function submitAddStudent() {
    const btn = document.getElementById("as-submit-btn");
    const msg = document.getElementById("as-msg");
    const name = document.getElementById("as-name").value.trim();
    if (!name) { msg.textContent = "Name is required."; return; }

    btn.disabled = true;
    btn.textContent = "Adding…";
    try {
        const res = await fetch(`${API}/studio-teacher/students`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                name,
                email: document.getElementById("as-email").value.trim().toLowerCase() || null,
                parent_name: document.getElementById("as-parent-name").value.trim() || null,
                parent_email: document.getElementById("as-parent-email").value.trim().toLowerCase() || null,
                family_id: document.getElementById("as-family-select").value || null,
            }),
        });
        const data = await res.json();
        if (data.status === "success") {
            document.getElementById("add-student-modal").classList.add("hidden");
            await loadStudents();
        } else {
            msg.textContent = data.message || "Failed.";
        }
    } catch (e) {
        msg.textContent = "Server error.";
    }
    btn.disabled = false;
    btn.textContent = "Add Student";
}

function addFamilyChildRow(name = "", email = "") {
    const list = document.getElementById("af-children-list");
    const row = document.createElement("div");
    row.className = "af-child-row";
    row.innerHTML = `
        <input class="af-child-name" type="text" placeholder="Child's name" value="${escHtml(name)}">
        <input class="af-child-email" type="email" placeholder="Email (optional)" value="${escHtml(email)}">
        <button class="subtle-btn" onclick="this.closest('.af-child-row').remove()">✕</button>
    `;
    list.appendChild(row);
}

function initAddFamilyModal() {
    document.getElementById("add-family-btn")?.addEventListener("click", () => {
        document.getElementById("af-name").value = "";
        document.getElementById("af-parent-name").value = "";
        document.getElementById("af-parent-email").value = "";
        document.getElementById("af-msg").textContent = "";
        document.getElementById("af-children-list").innerHTML = "";
        addFamilyChildRow(); // start with one blank child row
        document.getElementById("add-family-modal").classList.remove("hidden");
    });

    document.getElementById("af-add-child-btn")?.addEventListener("click", () => addFamilyChildRow());
    document.getElementById("af-submit-btn")?.addEventListener("click", submitAddFamily);
    document.getElementById("af-cancel-btn")?.addEventListener("click", () => {
        document.getElementById("add-family-modal").classList.add("hidden");
    });
}

async function submitAddFamily() {
    const btn = document.getElementById("af-submit-btn");
    const msg = document.getElementById("af-msg");
    const name = document.getElementById("af-name").value.trim();
    if (!name) { msg.textContent = "Family name is required."; return; }

    const children = Array.from(document.querySelectorAll(".af-child-row"))
        .map(row => ({
            name: row.querySelector(".af-child-name").value.trim(),
            email: row.querySelector(".af-child-email").value.trim().toLowerCase() || null,
        }))
        .filter(c => c.name);

    btn.disabled = true;
    btn.textContent = "Creating…";
    try {
        const res = await fetch(`${API}/studio-teacher/families`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                family_name: name,
                parent_name: document.getElementById("af-parent-name").value.trim() || null,
                parent_email: document.getElementById("af-parent-email").value.trim().toLowerCase() || null,
                children,
            }),
        });
        const data = await res.json();
        if (data.status === "success") {
            msg.textContent = `Family created with ${data.students_added} student${data.students_added !== 1 ? "s" : ""}!`;
            await loadStudents();
            setTimeout(() => {
                document.getElementById("add-family-modal").classList.add("hidden");
                msg.textContent = "";
            }, 1000);
        } else {
            msg.textContent = data.message || "Failed.";
        }
    } catch (e) {
        msg.textContent = "Server error.";
    }
    btn.disabled = false;
    btn.textContent = "Create Family";
}

// ============================================================
// MESSAGES TAB (reuse DM pattern from app.js)
// ============================================================

function initDmTab() {
    document.getElementById("dm-scope")?.addEventListener("change", e => {
        const isDirectEl = document.getElementById("dm-recipient-row");
        if (isDirectEl) isDirectEl.classList.toggle("hidden", e.target.value !== "direct");
    });

    document.querySelectorAll(".dm-view-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".dm-view-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            loadDmInbox(btn.dataset.dmView);
        });
    });

    document.getElementById("dm-send-btn")?.addEventListener("click", sendDm);
}

async function loadDmInbox(view = "inbox") {
    const el = document.getElementById("dm-list");
    if (!el) return;
    try {
        const res = await fetch(`${API}/dm/inbox?view=${view}`, { credentials: "include" });
        const data = await res.json();
        if (!Array.isArray(data) || !data.length) {
            el.innerHTML = `<em class="empty-note">No messages.</em>`;
            return;
        }
        el.innerHTML = data.map(m => {
            const ts = m.created_at ? new Date(m.created_at).toLocaleString() : "";
            return `<div class="dm-item ${m.unread ? 'dm-unread' : ''}">
                <div class="dm-item-from">${escHtml(m.sender_name || "")}</div>
                <div class="dm-item-body">${escHtml(m.body || "")}</div>
                <div class="dm-item-ts hint">${ts}</div>
            </div>`;
        }).join("");
    } catch (e) {
        el.innerHTML = `<em class="empty-note">Error loading messages.</em>`;
    }
}

async function sendDm() {
    const btn = document.getElementById("dm-send-btn");
    const status = document.getElementById("dm-send-status");
    const scope = document.getElementById("dm-scope").value;
    const body = document.getElementById("dm-body").value.trim();
    if (!body) { status.textContent = "Message cannot be empty."; return; }

    btn.disabled = true;
    btn.textContent = "Sending…";
    try {
        const res = await fetch(`${API}/dm/send`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scope, body, recipient_ids: [] }),
        });
        const data = await res.json();
        if (data.status === "success") {
            status.textContent = `Sent to ${data.sent_to} recipient(s).`;
            document.getElementById("dm-body").value = "";
            loadDmInbox();
        } else {
            status.textContent = data.message || "Failed to send.";
        }
    } catch (e) {
        status.textContent = "Server error.";
    }
    btn.disabled = false;
    btn.textContent = "Send Message";
}

// ============================================================
// MODAL INIT AGGREGATOR
// ============================================================

function initModals() {
    initLessonModal();
    initMultiweekModal();
    initParseModal();
    initAvailabilityModal();
    initAddStudentModal();
    initAddFamilyModal();
    initStudentDetailModal();
    initUpdatePaymentsModal();

    // Close modals on overlay click
    document.querySelectorAll(".modal-overlay").forEach(overlay => {
        overlay.addEventListener("click", e => {
            if (e.target === overlay) overlay.classList.add("hidden");
        });
    });
}

// ============================================================
// UTILITIES
// ============================================================

function setActiveChip(groupId, value) {
    document.querySelectorAll(`#${groupId} .chip`).forEach(c => {
        const key = c.dataset.dur || c.dataset.count;
        c.classList.toggle("active", key === String(value));
    });
}

function getActiveChip(groupId) {
    const active = document.querySelector(`#${groupId} .chip.active`);
    return active ? (active.dataset.dur || active.dataset.count) : null;
}

function formatDateLabel(dateStr) {
    if (!dateStr) return "";
    const [y, m, d] = dateStr.split("-").map(Number);
    const dt = new Date(y, m - 1, d);
    return dt.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" });
}

function formatTime12(t) {
    if (!t) return "";
    const [h, m] = t.split(":").map(Number);
    const ampm = h < 12 ? "AM" : "PM";
    const hr = h % 12 || 12;
    return `${hr}:${String(m).padStart(2,"0")} ${ampm}`;
}

function escHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}
