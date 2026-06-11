// ============================================================
// STUDIO TEACHER DASHBOARD
// ============================================================

const VALID_TEACHER_TABS = ["lessons", "schedule", "students"];
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
let teacherRates = {};     // { duration_min: { rate_cents, package_rate_cents } }

// ============================================================
// INIT
// ============================================================

document.addEventListener("DOMContentLoaded", () => {
    loadMe();
    initTabs();
    initLogout();
    initCalendarNav();
    initModals();
    initEmailModal();
    loadTeacherRates();
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
    // Show studio settings section when modal opens and populate fields
    document.getElementById("open-edit-account-btn")?.addEventListener("click", () => {
        document.getElementById("studio-settings-section")?.classList.remove("hidden");
        loadStudioSettingsFields();
    });
    // Save studio settings alongside account changes
    document.getElementById("save-account-btn")?.addEventListener("click", saveStudioSettings);
    // Wire up rate checkboxes to show/hide price inputs
    document.querySelectorAll(".edit-rate-check").forEach(cb => {
        cb.addEventListener("change", () => {
            const wrap = cb.closest(".rate-row").querySelector(".rate-price-wrap");
            wrap.classList.toggle("hidden", !cb.checked);
            if (cb.checked) wrap.querySelector(".edit-rate-input").focus();
        });
    });
}

async function loadTeacherRates() {
    try {
        const res = await fetch(`${API}/studio-teacher/settings`, { credentials: "include" });
        const s = await res.json();
        teacherRates = {};
        (s.lesson_rates || []).forEach(r => {
            teacherRates[r.duration_min] = { rate_cents: r.rate_cents, package_rate_cents: r.package_rate_cents ?? null };
        });
    } catch (e) {}
}

async function loadStudioSettingsFields() {
    try {
        const res = await fetch(`${API}/studio-teacher/settings`, { credentials: "include" });
        const s = await res.json();

        // Payment handles
        document.getElementById("edit-pay-venmo").value  = s.payment_venmo  || "";
        document.getElementById("edit-pay-zelle").value  = s.payment_zelle  || "";
        document.getElementById("edit-pay-cashapp").value = s.payment_cashapp || "";
        document.getElementById("edit-pay-paypal").value = s.payment_paypal  || "";

        // Cancellation policy
        document.getElementById("edit-cancel-hours").value = s.cancel_hours ?? "";
        document.getElementById("edit-cancel-charge").checked = !!s.cancel_charge;
        document.getElementById("edit-cancel-free-count").value = s.free_cancels_per_student ?? 0;

        // Rates — check the box and fill the price for each stored duration
        const rateMap = {};
        const pkgRateMap = {};
        teacherRates = {};
        (s.lesson_rates || []).forEach(r => {
            rateMap[r.duration_min] = r.rate_cents;
            if (r.package_rate_cents != null) pkgRateMap[r.duration_min] = r.package_rate_cents;
            teacherRates[r.duration_min] = { rate_cents: r.rate_cents, package_rate_cents: r.package_rate_cents ?? null };
        });
        document.querySelectorAll(".edit-rate-check").forEach(cb => {
            const dur = parseInt(cb.dataset.dur);
            const wrap = cb.closest(".rate-row").querySelector(".rate-price-wrap");
            const input = wrap.querySelector(".edit-rate-input");
            const pkgInput = wrap.querySelector(".edit-pkg-rate-input");
            if (rateMap[dur] !== undefined) {
                cb.checked = true;
                wrap.classList.remove("hidden");
                input.value = (rateMap[dur] / 100).toFixed(0);
                if (pkgInput) pkgInput.value = pkgRateMap[dur] != null ? (pkgRateMap[dur] / 100).toFixed(0) : "";
            } else {
                cb.checked = false;
                wrap.classList.add("hidden");
                input.value = "";
                if (pkgInput) pkgInput.value = "";
            }
        });

        // Packages
        const pkgsToggle = document.getElementById("edit-packages-enabled");
        const pkgSizeWrap = document.getElementById("edit-package-size-wrap");
        if (pkgsToggle) {
            pkgsToggle.checked = !!s.packages_enabled;
            if (pkgSizeWrap) pkgSizeWrap.classList.toggle("hidden", !s.packages_enabled);
            document.querySelectorAll(".edit-pkg-rate-wrap").forEach(el =>
                el.classList.toggle("hidden", !s.packages_enabled));
        }
        const pkgSizeEl = document.getElementById("edit-package-size");
        if (pkgSizeEl) pkgSizeEl.value = s.package_size || 4;
    } catch (e) {
        console.error("Failed to load studio settings:", e);
    }
}

async function saveStudioSettings() {
    const lesson_rates = [];
    document.querySelectorAll(".edit-rate-check:checked").forEach(cb => {
        const dur = parseInt(cb.dataset.dur);
        const rateInput = cb.closest(".rate-row").querySelector(".edit-rate-input");
        const pkgRateInput = cb.closest(".rate-row").querySelector(".edit-pkg-rate-input");
        const rate = parseFloat(rateInput?.value || "0") || 0;
        const pkgRate = pkgRateInput?.value ? (parseFloat(pkgRateInput.value) || null) : null;
        lesson_rates.push({ duration_min: dur, rate, package_rate: pkgRate });
    });

    const packagesEnabled = document.getElementById("edit-packages-enabled")?.checked || false;
    const packageSize = parseInt(document.getElementById("edit-package-size")?.value || "4") || 4;

    const payload = {
        payment_venmo:   document.getElementById("edit-pay-venmo")?.value.trim()   || null,
        payment_zelle:   document.getElementById("edit-pay-zelle")?.value.trim()   || null,
        payment_cashapp: document.getElementById("edit-pay-cashapp")?.value.trim() || null,
        payment_paypal:  document.getElementById("edit-pay-paypal")?.value.trim()  || null,
        lesson_rates,
        cancel_hours: parseInt(document.getElementById("edit-cancel-hours")?.value || "0") || null,
        cancel_charge: document.getElementById("edit-cancel-charge")?.checked || false,
        free_cancels_per_student: parseInt(document.getElementById("edit-cancel-free-count")?.value || "0") || 0,
        packages_enabled: packagesEnabled,
        package_size: packageSize,
    };

    const msgEl = document.getElementById("studio-settings-msg");
    try {
        const res = await fetch(`${API}/studio-teacher/settings`, {
            method: "PATCH",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (data.status === "success" && msgEl) {
            msgEl.textContent = "Studio settings saved.";
            msgEl.className = "hint success-msg";
            setTimeout(() => { if (msgEl) { msgEl.textContent = ""; msgEl.className = "hint"; } }, 2000);
            loadTeacherRates(); // keep auto-fill in sync
        }
    } catch (e) {
        console.error("Failed to save studio settings:", e);
        if (msgEl) { msgEl.textContent = "Failed to save settings."; msgEl.className = "hint error-msg"; }
    }
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
        // Show email button only when there are students with emails today
        const emailBtn = document.getElementById("email-today-btn");
        if (emailBtn) emailBtn.classList.toggle("hidden", !today.length);
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
                <div class="lesson-card-actions">
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

function updateLessonSummary() {
    const dur = getActiveChip("lesson-duration-chips") || "30";
    const el = document.getElementById("lesson-summary");
    if (el) el.textContent = `${dur} min lesson selected`;
}

function updateMwSummary() {
    const dur = getActiveChip("mw-duration-chips") || "30";
    const count = getActiveChip("mw-count-chips") || "1";
    const dayName = lessonModalWeekday !== null ? DAY_NAMES[lessonModalWeekday] + "s" : "selected day";
    const el = document.getElementById("mw-summary");
    if (el) el.textContent = `Scheduling ${count} × ${dur} min lesson${parseInt(count) > 1 ? "s" : ""} on ${dayName}`;
}

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

    setActiveChip("lesson-duration-chips", "30");
    updateLessonSummary();

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
        updateLessonSummary();
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
    updateMwSummary();

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
        updateMwSummary();
        const firstDate = nextWeekdayDate(calYear, calMonth, lessonModalWeekday);
        if (firstDate) fetchAndPopulateSlots("mw-time-select", firstDate, chip.dataset.dur);
        if (mwSelectedStudentId) updateBalanceDisplay(mwSelectedStudentId, chip.dataset.dur, "mw-student-balance", null);
    });

    document.getElementById("mw-count-chips")?.addEventListener("click", e => {
        const chip = e.target.closest(".chip");
        if (!chip) return;
        setActiveChip("mw-count-chips", chip.dataset.count);
        updateMwSummary();
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
        Object.entries(familiesWithMembers).forEach(([famIdStr, fam]) => {
            const famId = parseInt(famIdStr);
            const count = fam.members.length;
            const famData = allFamilies.find(f => f.id === famId) || {};
            const parentLine = famData.parent_name
                ? `${escHtml(famData.parent_name)}${famData.parent_email ? ` · ${escHtml(famData.parent_email)}` : ""}`
                : (famData.parent_email ? escHtml(famData.parent_email) : "");
            // Family-level payment badge (use first member since all share same pool)
            const famPayBadge = fam.members.length ? paymentBadge(fam.members[0].payments) : "";
            const firstMemberId = fam.members.length ? fam.members[0].id : null;
            html += `<div class="student-family-block">
                <div class="student-family-header">
                    <div class="student-family-header-left">
                        <div class="student-family-name">${escHtml(fam.name)}</div>
                        ${parentLine ? `<div class="student-family-parent">Parent · ${parentLine}</div>` : ""}
                        ${famPayBadge ? `<div style="margin-top:4px;">${famPayBadge}</div>` : ""}
                    </div>
                    <div style="display:flex;gap:var(--space-2);align-items:center;flex-wrap:wrap;justify-content:flex-end;">
                        ${firstMemberId ? `<button class="subtle-btn" onclick="openRecordPaymentModal(${firstMemberId});event.stopPropagation();">Record Payment</button>` : ""}
                        ${firstMemberId ? `<button class="subtle-btn" onclick="openPaymentHistoryModal(${firstMemberId});event.stopPropagation();">Payment History</button>` : ""}
                        <button class="subtle-btn" onclick="openEditFamilyModal(${famId});event.stopPropagation();">Edit</button>
                        <span class="student-family-count">${count} student${count !== 1 ? "s" : ""}</span>
                    </div>
                </div>
                <div class="student-family-children">`;
            if (count) {
                fam.members.forEach(s => { html += renderStudentRow(s, true); });
            } else {
                html += `<div class="empty-note" style="font-size:0.85rem;padding:var(--space-3);">No students yet — add a student and assign them to this family.</div>`;
            }
            html += `</div></div>`;
        });
    }

    if (solos.length) {
        html += `<div class="student-section-header" style="margin-top:${hasFamilies ? 'var(--space-4)' : '0'}">Individual Students</div>`;
        solos.forEach(s => { html += `<div class="student-solo-card">${renderStudentRow(s)}</div>`; });
    }

    el.innerHTML = html;
}

function renderStudentRow(s, inFamily = false) {
    const att = s.attendance || { present: 0, absent: 0 };
    // Payment badge and "no email" badge are shown only for solo students;
    // families display these at the family block level.
    const payBadge = inFamily ? "" : paymentBadge(s.payments);
    const noEmailBadge = (!inFamily && !s.email && !s.parent_email)
        ? `<span class="pay-badge pay-warn" title="Add an email so this student can be messaged">No email</span>`
        : "";
    return `
        <div class="student-row" onclick="openStudentDetail(${s.id})">
            <div>
                <strong>${escHtml(s.name)}</strong>
                ${s.email ? `<span class="hint"> · ${escHtml(s.email)}</span>` : ""}
            </div>
            <div style="display:flex;gap:var(--space-2);align-items:center;flex-wrap:wrap;min-width:0;justify-content:flex-end;">
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

    // Free cancel status
    const fcEl = document.getElementById("sd-free-cancel");
    if (fcEl) {
        const allowed = s.free_cancels_allowed || 0;
        const used = s.free_cancels_used || 0;
        if (allowed > 0) {
            const available = used < allowed;
            fcEl.textContent = available ? "Free cancel: Available" : "Free cancel: Used";
            fcEl.className = "hint " + (available ? "pay-ok" : "pay-warn");
            fcEl.style.display = "";
        } else {
            fcEl.style.display = "none";
        }
    }
    document.getElementById("sd-msg").textContent = "";
    document.getElementById("sd-edit-form").classList.add("hidden");
    document.getElementById("sd-edit-msg").textContent = "";

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

    document.getElementById("sd-edit-btn")?.addEventListener("click", () => {
        const s = allStudents.find(s => s.id === activeStudentId);
        if (!s) return;
        document.getElementById("sd-edit-name").value = s.name || "";
        document.getElementById("sd-edit-email").value = s.email || "";
        document.getElementById("sd-edit-parent-name").value = s.parent_name || "";
        document.getElementById("sd-edit-parent-email").value = s.parent_email || "";
        // Populate family select
        const famSel = document.getElementById("sd-edit-family");
        famSel.innerHTML = `<option value="">— solo student —</option>` +
            allFamilies.map(f => `<option value="${f.id}" ${s.family_id === f.id ? "selected" : ""}>${escHtml(f.family_name)}</option>`).join("");
        document.getElementById("sd-edit-form").classList.remove("hidden");
        document.getElementById("sd-edit-name").focus();
    });

    document.getElementById("sd-edit-cancel-btn")?.addEventListener("click", () => {
        document.getElementById("sd-edit-form").classList.add("hidden");
    });

    document.getElementById("sd-delete-btn")?.addEventListener("click", async () => {
        const s = allStudents.find(s => s.id === activeStudentId);
        if (!s) return;
        if (!confirm(`Delete ${s.name}? This cannot be undone.`)) return;
        try {
            const res = await fetch(`${API}/studio-teacher/student/${activeStudentId}`, {
                method: "DELETE",
                credentials: "include",
            });
            const data = await res.json();
            if (data.status === "success") {
                document.getElementById("student-detail-modal").classList.add("hidden");
                await loadStudents();
            } else {
                document.getElementById("sd-edit-msg").textContent = data.message || "Failed to delete.";
            }
        } catch (e) {
            document.getElementById("sd-edit-msg").textContent = "Server error.";
        }
    });

    document.getElementById("sd-edit-save-btn")?.addEventListener("click", async () => {
        const btn = document.getElementById("sd-edit-save-btn");
        const msg = document.getElementById("sd-edit-msg");
        const name = document.getElementById("sd-edit-name").value.trim();
        if (!name) { msg.textContent = "Name is required."; return; }

        btn.disabled = true;
        btn.textContent = "Saving…";
        try {
            const res = await fetch(`${API}/studio-teacher/student/${activeStudentId}`, {
                method: "PATCH",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    name,
                    email: document.getElementById("sd-edit-email").value.trim().toLowerCase() || null,
                    parent_name: document.getElementById("sd-edit-parent-name").value.trim() || null,
                    parent_email: document.getElementById("sd-edit-parent-email").value.trim().toLowerCase() || null,
                    family_id: document.getElementById("sd-edit-family").value || null,
                }),
            });
            const data = await res.json();
            if (data.status === "success") {
                await loadStudents();
                openStudentDetail(activeStudentId); // re-render with updated data
            } else {
                msg.textContent = data.message || "Failed.";
            }
        } catch (e) {
            msg.textContent = "Server error.";
        }
        btn.disabled = false;
        btn.textContent = "Save Changes";
    });

    document.getElementById("sd-message-btn")?.addEventListener("click", () => {
        const s = allStudents.find(s => s.id === activeStudentId);
        if (!s || (!s.email && !s.parent_email)) {
            document.getElementById("sd-msg").textContent = "No email address on file for this student.";
            return;
        }
        document.getElementById("student-detail-modal").classList.add("hidden");
        openEmailModal({ mode: "student", studentId: s.id, studentName: s.name });
    });

    document.getElementById("sd-record-payment-btn")?.addEventListener("click", () => {
        document.getElementById("student-detail-modal").classList.add("hidden");
        openRecordPaymentModal(activeStudentId);
    });

    document.getElementById("sd-payment-history-btn")?.addEventListener("click", () => {
        document.getElementById("student-detail-modal").classList.add("hidden");
        openPaymentHistoryModal(activeStudentId);
    });

    document.getElementById("sd-reminder-btn")?.addEventListener("click", async () => {
        const s = allStudents.find(s => s.id === activeStudentId);
        const isFullyPaid = !s?.payments?.length || s.payments.every(p => p.remaining >= 0);
        if (isFullyPaid) {
            document.getElementById("sd-msg").textContent = "Student is fully paid — no reminder needed.";
            return;
        }
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
                ? "Reminder sent!" : (data.message || "Failed to send reminder.");
        } catch (e) {
            document.getElementById("sd-msg").textContent = "Server error.";
        }
        btn.disabled = false;
        btn.textContent = "Send Payment Reminder";
    });
}

// ============================================================
// RECORD PAYMENT MODAL
// ============================================================

let recordPaymentStudentId = null;

function openRecordPaymentModal(studentId) {
    recordPaymentStudentId = studentId;
    const s = allStudents.find(s => s.id === studentId);
    if (!s) return;

    const label = s.family_name ? `${s.family_name} (Family)` : s.name;
    document.getElementById("rp-title").textContent = `Record Payment — ${label}`;
    document.getElementById("rp-subtitle").textContent = "Add a payment event to the student's transaction log.";
    document.getElementById("rp-count").value = "1";
    document.getElementById("rp-amount").value = "";
    document.getElementById("rp-note").value = "";
    document.getElementById("rp-is-package").checked = false;
    document.getElementById("rp-count-label").textContent = "Number of lessons paid";
    document.getElementById("rp-msg").textContent = "";

    const teacherHasPackages = allStudents.some(st => st.packages_enabled);
    document.getElementById("rp-package-row").classList.toggle("hidden", !teacherHasPackages);

    document.getElementById("record-payment-modal").classList.remove("hidden");
    autofillPaymentAmount();
}

function autofillPaymentAmount() {
    const s = allStudents.find(s => s.id === recordPaymentStudentId);
    const dur = parseInt(document.getElementById("rp-duration").value);
    const isPackage = document.getElementById("rp-is-package").checked;
    const count = parseInt(document.getElementById("rp-count").value) || 0;
    const pkgSize = s?.package_size || 4;
    const rates = teacherRates[dur];
    if (!rates || count <= 0) { document.getElementById("rp-amount").value = ""; return; }

    let totalCents;
    if (isPackage && rates.package_rate_cents != null) {
        totalCents = count * pkgSize * rates.package_rate_cents;
    } else if (!isPackage && rates.rate_cents) {
        totalCents = count * rates.rate_cents;
    } else {
        document.getElementById("rp-amount").value = "";
        return;
    }
    document.getElementById("rp-amount").value = (totalCents / 100).toFixed(2);
}

function initRecordPaymentModal() {
    document.getElementById("rp-submit-btn")?.addEventListener("click", submitRecordPayment);
    document.getElementById("rp-cancel-btn")?.addEventListener("click", () => {
        document.getElementById("record-payment-modal").classList.add("hidden");
        openStudentDetail(recordPaymentStudentId);
    });

    const recalc = () => autofillPaymentAmount();

    document.getElementById("rp-duration")?.addEventListener("change", recalc);
    document.getElementById("rp-count")?.addEventListener("input", recalc);
    document.getElementById("rp-is-package")?.addEventListener("change", function () {
        const s = allStudents.find(s => s.id === recordPaymentStudentId);
        const pkgSize = s?.package_size || 4;
        document.getElementById("rp-count-label").textContent = this.checked
            ? "Number of packages purchased" : "Number of lessons paid";
        if (this.checked) {
            document.getElementById("rp-count").value = "1";
            document.getElementById("rp-count").title = `1 package = ${pkgSize} lessons`;
        } else {
            document.getElementById("rp-count").title = "";
        }
        autofillPaymentAmount();
    });
}

async function submitRecordPayment() {
    const btn = document.getElementById("rp-submit-btn");
    const msg = document.getElementById("rp-msg");
    const dur = parseInt(document.getElementById("rp-duration").value);
    const isPackage = document.getElementById("rp-is-package").checked;
    const countRaw = parseInt(document.getElementById("rp-count").value) || 0;
    const amountRaw = parseFloat(document.getElementById("rp-amount").value);
    const note = document.getElementById("rp-note").value.trim();

    if (countRaw <= 0) {
        msg.textContent = "Please enter a valid count.";
        return;
    }

    const s = allStudents.find(s => s.id === recordPaymentStudentId);
    const pkgSize = s?.package_size || 4;
    const lessonsCount = isPackage ? countRaw * pkgSize : countRaw;
    const amountCents = !isNaN(amountRaw) && amountRaw > 0 ? Math.round(amountRaw * 100) : null;

    btn.disabled = true;
    btn.textContent = "Saving…";
    try {
        const res = await fetch(`${API}/studio-teacher/student/${recordPaymentStudentId}/payment-transaction`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                duration_min: dur,
                lessons_count: lessonsCount,
                is_package: isPackage,
                package_size: isPackage ? pkgSize : null,
                amount_cents: amountCents,
                note: note || null,
            }),
        });
        const data = await res.json();
        if (data.status === "success") {
            msg.textContent = "Saved!";
            await loadStudents();
            setTimeout(() => {
                document.getElementById("record-payment-modal").classList.add("hidden");
                msg.textContent = "";
                openStudentDetail(recordPaymentStudentId);
            }, 600);
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
// PAYMENT HISTORY MODAL
// ============================================================

let paymentHistoryStudentId = null;

async function openPaymentHistoryModal(studentId) {
    paymentHistoryStudentId = studentId;
    const s = allStudents.find(s => s.id === studentId);
    const label = s?.family_name ? `${s.family_name} (Family)` : (s?.name || "Student");
    document.getElementById("ph-title").textContent = `Payment History — ${label}`;
    document.getElementById("ph-subtitle").textContent = "All recorded payment transactions.";
    document.getElementById("ph-msg").textContent = "";
    document.getElementById("ph-list").innerHTML = `<em class="hint">Loading…</em>`;
    document.getElementById("payment-history-modal").classList.remove("hidden");
    await refreshPaymentHistory(studentId);
}

async function refreshPaymentHistory(studentId) {
    const list = document.getElementById("ph-list");
    try {
        const res = await fetch(`${API}/studio-teacher/student/${studentId}/payment-transactions`, { credentials: "include" });
        const data = await res.json();
        if (data.status !== "success") { list.innerHTML = `<em class="hint">Could not load history.</em>`; return; }
        const txns = data.transactions;
        if (!txns.length) { list.innerHTML = `<em class="hint">No transactions yet.</em>`; return; }
        list.innerHTML = txns.map(t => {
            const date = t.created_at ? new Date(t.created_at).toLocaleDateString() : "—";
            const pkgTag = t.is_package ? ` (pkg ×${t.package_size || "?"})` : "";
            const amtStr = t.amount_cents != null ? ` · $${(t.amount_cents / 100).toFixed(2)} paid` : "";
            const noteStr = t.note ? ` · ${escHtml(t.note)}` : "";
            return `<div class="payment-txn-row" style="display:flex;justify-content:space-between;align-items:center;padding:var(--space-2) 0;border-bottom:1px solid var(--border);">
                <div>
                    <strong>+${t.lessons_count} lesson${t.lessons_count !== 1 ? "s" : ""}</strong> (${t.duration_min} min${pkgTag})
                    <span class="hint">${amtStr}${noteStr}</span><br>
                    <span class="hint" style="font-size:0.8rem;">${date}</span>
                </div>
                <button class="subtle-btn" style="color:var(--color-error,#dc2626);font-size:0.8rem;" onclick="deleteTransaction(${t.id})">Remove</button>
            </div>`;
        }).join("");
    } catch (e) {
        list.innerHTML = `<em class="hint">Server error.</em>`;
    }
}

async function deleteTransaction(txnId) {
    if (!confirm("Remove this payment record?")) return;
    try {
        const res = await fetch(`${API}/studio-teacher/payment-transaction/${txnId}`, {
            method: "DELETE",
            credentials: "include",
        });
        const data = await res.json();
        if (data.status === "success") {
            await loadStudents();
            await refreshPaymentHistory(paymentHistoryStudentId);
        } else {
            document.getElementById("ph-msg").textContent = data.message || "Failed.";
        }
    } catch (e) {
        document.getElementById("ph-msg").textContent = "Server error.";
    }
}

function initPaymentHistoryModal() {
    document.getElementById("ph-add-btn")?.addEventListener("click", () => {
        document.getElementById("payment-history-modal").classList.add("hidden");
        openRecordPaymentModal(paymentHistoryStudentId);
    });
    document.getElementById("ph-close-btn")?.addEventListener("click", () => {
        document.getElementById("payment-history-modal").classList.add("hidden");
        openStudentDetail(paymentHistoryStudentId);
    });
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

function buildRemindAllRecipients() {
    const recipients = [];
    const familiesSeen = new Set();

    for (const s of allStudents) {
        const hasDebt = s.payments && s.payments.some(p => p.remaining < 0);
        if (!hasDebt) continue;

        if (s.family_id) {
            if (familiesSeen.has(s.family_id)) continue;
            familiesSeen.add(s.family_id);
            const contactEmail = s.parent_email || s.email;
            if (!contactEmail) continue;
            recipients.push({
                representativeId: s.id,
                label: s.family_name || s.name,
                sublabel: s.parent_name ? `Parent: ${s.parent_name}` : null,
                email: contactEmail,
            });
        } else {
            const contactEmail = s.parent_email || s.email;
            if (!contactEmail) continue;
            recipients.push({
                representativeId: s.id,
                label: s.name,
                sublabel: s.parent_name ? `Parent: ${s.parent_name}` : null,
                email: contactEmail,
            });
        }
    }
    return recipients;
}

function updateRemindCount() {
    const checked = document.querySelectorAll(".remind-recipient-check:checked").length;
    document.getElementById("remind-recipient-count").textContent = checked;
}

function initRemindAllBtn() {
    document.getElementById("remind-all-btn")?.addEventListener("click", () => {
        const recipients = buildRemindAllRecipients();
        const listEl = document.getElementById("remind-recipients-list");
        const modal = document.getElementById("remind-all-modal");

        if (!recipients.length) {
            alert("All students are paid up — no reminders needed.");
            return;
        }

        listEl.innerHTML = recipients.map(r => `
            <label style="display:flex;align-items:center;gap:var(--space-2);cursor:pointer;">
                <input type="checkbox" class="remind-recipient-check" data-id="${r.representativeId}" checked>
                <span>
                    <strong>${escHtml(r.label)}</strong>
                    ${r.sublabel ? `<span class="hint"> · ${escHtml(r.sublabel)}</span>` : ""}
                    <span class="hint"> — ${escHtml(r.email)}</span>
                </span>
            </label>
        `).join("");

        listEl.querySelectorAll(".remind-recipient-check").forEach(cb => {
            cb.addEventListener("change", updateRemindCount);
        });

        document.getElementById("remind-recipient-count").textContent = recipients.length;
        document.getElementById("remind-all-msg").textContent = "";
        modal.classList.remove("hidden");
    });

    document.getElementById("remind-all-cancel-btn")?.addEventListener("click", () => {
        document.getElementById("remind-all-modal").classList.add("hidden");
    });

    document.getElementById("remind-all-confirm-btn")?.addEventListener("click", async () => {
        const btn = document.getElementById("remind-all-confirm-btn");
        const msg = document.getElementById("remind-all-msg");
        const studentIds = Array.from(document.querySelectorAll(".remind-recipient-check:checked"))
            .map(cb => parseInt(cb.dataset.id));

        if (!studentIds.length) {
            msg.textContent = "No recipients selected.";
            return;
        }

        btn.disabled = true;
        btn.textContent = "Sending…";
        try {
            const res = await fetch(`${API}/studio-teacher/payment-reminder-all`, {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ student_ids: studentIds }),
            });
            const data = await res.json();
            if (data.status === "success") {
                msg.textContent = `Sent ${data.sent} reminder${data.sent !== 1 ? "s" : ""}.`;
                setTimeout(() => {
                    document.getElementById("remind-all-modal").classList.add("hidden");
                }, 1200);
            } else {
                msg.textContent = "Failed to send reminders.";
            }
        } catch (e) {
            msg.textContent = "Server error.";
        }
        btn.disabled = false;
        btn.textContent = "Send Reminders";
    });
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
// EDIT FAMILY MODAL
// ============================================================

let editingFamilyId = null;

function openEditFamilyModal(familyId) {
    const f = allFamilies.find(f => f.id === familyId);
    if (!f) return;
    editingFamilyId = familyId;
    document.getElementById("ef-name").value = f.family_name || "";
    document.getElementById("ef-parent-name").value = f.parent_name || "";
    document.getElementById("ef-parent-email").value = f.parent_email || "";
    document.getElementById("ef-msg").textContent = "";
    document.getElementById("edit-family-modal").classList.remove("hidden");
}

function initEditFamilyModal() {
    document.getElementById("ef-submit-btn")?.addEventListener("click", submitEditFamily);
    document.getElementById("ef-cancel-btn")?.addEventListener("click", () => {
        document.getElementById("edit-family-modal").classList.add("hidden");
    });
}

async function submitEditFamily() {
    const btn = document.getElementById("ef-submit-btn");
    const msg = document.getElementById("ef-msg");
    const name = document.getElementById("ef-name").value.trim();
    if (!name) { msg.textContent = "Family name is required."; return; }

    btn.disabled = true;
    btn.textContent = "Saving…";
    try {
        const res = await fetch(`${API}/studio-teacher/family/${editingFamilyId}`, {
            method: "PATCH",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                family_name: name,
                parent_name: document.getElementById("ef-parent-name").value.trim() || null,
                parent_email: document.getElementById("ef-parent-email").value.trim().toLowerCase() || null,
            }),
        });
        const data = await res.json();
        if (data.status === "success") {
            await loadStudents();
            document.getElementById("edit-family-modal").classList.add("hidden");
        } else {
            msg.textContent = data.message || "Failed.";
        }
    } catch (e) {
        msg.textContent = "Server error.";
    }
    btn.disabled = false;
    btn.textContent = "Save Changes";
}

// ============================================================
// MESSAGES TAB (reuse DM pattern from app.js)
// ============================================================

// ============================================================
// EMAIL COMPOSE MODAL
// ============================================================

let emailModalMode = null; // "student" | "today"
let emailModalStudentId = null;

function openEmailModal({ mode, studentId = null, studentName = null }) {
    emailModalMode = mode;
    emailModalStudentId = studentId;
    document.getElementById("email-subject").value = "";
    document.getElementById("email-body").value = "";
    document.getElementById("email-modal-msg").textContent = "";
    if (mode === "today") {
        document.getElementById("email-modal-title").textContent = "Email Today's Students";
        document.getElementById("email-modal-to").textContent = "Sends to all students with lessons today who have an email on file.";
    } else {
        document.getElementById("email-modal-title").textContent = `Email ${studentName || "Student"}`;
        document.getElementById("email-modal-to").textContent = studentName ? `To: ${studentName}` : "";
    }
    document.getElementById("email-modal").classList.remove("hidden");
}

function initEmailModal() {
    document.getElementById("email-cancel-btn")?.addEventListener("click", () => {
        document.getElementById("email-modal").classList.add("hidden");
    });

    document.getElementById("email-today-btn")?.addEventListener("click", () => {
        openEmailModal({ mode: "today" });
    });

    document.getElementById("email-send-btn")?.addEventListener("click", async () => {
        const btn = document.getElementById("email-send-btn");
        const msg = document.getElementById("email-modal-msg");
        const subject = document.getElementById("email-subject").value.trim();
        const body = document.getElementById("email-body").value.trim();
        if (!subject || !body) { msg.textContent = "Subject and message are required."; return; }

        btn.disabled = true;
        btn.textContent = "Sending…";
        try {
            let endpoint, payload;
            if (emailModalMode === "today") {
                endpoint = `${API}/studio-teacher/email-today`;
                payload = { subject, body };
            } else {
                endpoint = `${API}/studio-teacher/email-student`;
                payload = { student_id: emailModalStudentId, subject, body };
            }
            const res = await fetch(endpoint, {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (data.status === "success") {
                msg.textContent = emailModalMode === "today"
                    ? `Sent to ${data.sent} student${data.sent !== 1 ? "s" : ""}.`
                    : "Email sent!";
                setTimeout(() => document.getElementById("email-modal").classList.add("hidden"), 1200);
            } else {
                msg.textContent = data.message || "Failed to send.";
            }
        } catch (e) {
            msg.textContent = "Server error.";
        }
        btn.disabled = false;
        btn.textContent = "Send";
    });
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
    initRemindAllBtn();
    initAddFamilyModal();
    initEditFamilyModal();
    initStudentDetailModal();
    initRecordPaymentModal();
    initPaymentHistoryModal();

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
