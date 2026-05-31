// ======================================================
// STUDENT DASHBOARD
// ======================================================

const VALID_STUDENT_TABS = ["today", "book", "notes", "messages"];


// -------------------- FORMATTING HELPERS --------------------

function formatSlotTime(hhmm) {
    if (!hhmm) return "";
    const [h, m] = hhmm.split(":").map(Number);
    const suffix = h >= 12 ? "PM" : "AM";
    const hour12 = ((h + 11) % 12) + 1;
    return `${hour12}:${String(m).padStart(2, "0")} ${suffix}`;
}

function formatRehearsalTime(iso) {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function formatTodayHeader(dateISO) {
    if (!dateISO) return "Today";
    const d = new Date(dateISO + "T00:00:00");
    return d.toLocaleDateString(undefined, {
        weekday: "long", month: "long", day: "numeric"
    });
}

function formatShortDate(iso) {
    if (!iso) return "";
    const d = new Date(iso + "T00:00:00");
    return d.toLocaleDateString(undefined, {
        weekday: "short", month: "short", day: "numeric"
    });
}

function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}


// -------------------- TAB SWITCHING --------------------

function setActiveTab(tabName) {
    if (!VALID_STUDENT_TABS.includes(tabName)) tabName = "today";

    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.tab === tabName);
    });

    document.querySelectorAll(".tab-panel").forEach(panel => {
        panel.classList.toggle("active", panel.dataset.tabPanel === tabName);
    });

    if (window.location.hash !== `#${tabName}`) {
        history.replaceState(null, "", `#${tabName}`);
    }
    if (tabName === "messages") loadDmTab();
}

function getTabFromURL() {
    const hash = window.location.hash.replace("#", "");
    return VALID_STUDENT_TABS.includes(hash) ? hash : "today";
}


// -------------------- TODAY: load all info --------------------

async function loadToday() {
    try {
        const res = await fetch(`${API}/student/today`, { credentials: "include" });
        const data = await res.json();

        renderMyLessonToday(data);
        renderBookingSection(data);
    } catch (e) {
        console.error("Failed to load today's info:", e);
        document.getElementById("teachers-list").textContent = "Failed to load teachers.";
        document.getElementById("my-lesson-today").textContent = "Failed to load lesson info.";
    }
}


// -------------------- TODAY: My Lesson today --------------------

async function renderMyLessonToday(todayData) {
    const box = document.getElementById("my-lesson-today");
    const header = document.getElementById("today-lesson-header");

    header.textContent = `My Lesson — ${formatTodayHeader(todayData.date)}`;

    // Fetch student's active lessons and filter to the bookable date
    try {
        const res = await fetch(`${API}/student/lessons`, { credentials: "include" });
        const lessons = await res.json();

        const lessonToday = (Array.isArray(lessons) ? lessons : []).find(l => l.date === todayData.date);

        if (!lessonToday) {
            box.innerHTML = `<em class="empty-note">No lesson booked. Use the <strong>Book</strong> tab to reserve a coaching.</em>`;
            return;
        }

        const canCancel = canStudentCancelLesson(lessonToday, todayData.date);
        const cancelBtn = canCancel
            ? `<button class="cancel-lesson-btn" data-lesson-id="${lessonToday.id}">Cancel</button>`
            : `<button class="cancel-lesson-btn" disabled title="Too close to lesson time to cancel">Cancel</button>`;

        box.innerHTML = `
            <div class="my-lesson-card">
                <div class="my-lesson-time">${formatSlotTime(lessonToday.time)}</div>
                <div class="my-lesson-teacher">with <strong>${escapeHtml(lessonToday.teacher)}</strong></div>
                <div class="my-lesson-actions">${cancelBtn}</div>
                ${!canCancel
                    ? `<p class="hint">Cancellation closes 1 hour before the lesson.</p>`
                    : `<p class="hint">You can cancel up to 1 hour before the lesson.</p>`}
            </div>
        `;

        // Wire up cancel button
        box.querySelector(".cancel-lesson-btn:not([disabled])")?.addEventListener("click", (e) => {
            const lessonId = e.currentTarget.dataset.lessonId;
            handleCancelLesson(lessonId);
        });
    } catch (e) {
        console.error(e);
        box.innerHTML = `<em class="empty-note">Failed to load lesson info.</em>`;
    }
}

function canStudentCancelLesson(lesson, bookableDate) {
    // Must be today AND >= 1 hour before lesson time
    if (lesson.date !== bookableDate) return false;
    if (!lesson.time) return false;

    // Build lesson datetime in local time (close enough to server's EST for this check)
    const [h, m] = lesson.time.split(":").map(Number);
    const [y, mo, d] = lesson.date.split("-").map(Number);
    const lessonDT = new Date(y, mo - 1, d, h, m);
    const now = new Date();
    const oneHourBefore = new Date(lessonDT.getTime() - 60 * 60 * 1000);

    return now < oneHourBefore;
}

async function handleCancelLesson(lessonId) {
    const ok = confirm("Cancel this lesson? This can't be undone.");
    if (!ok) return;

    try {
        const res = await fetch(`${API}/student/cancel-lesson`, {
            credentials: "include",
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                lesson_id: Number(lessonId)
            })
        });
        const data = await res.json();

        if (data.status === "success") {
            alert("Lesson cancelled.");
            loadToday();  // refresh everything
        } else {
            alert(data.message || "Failed to cancel.");
        }
    } catch (e) {
        console.error(e);
        alert("Server error.");
    }
}


// -------------------- TODAY: Rehearsals --------------------

let allStudentRehearsals = [];
let myAbsences = new Set();

function openStudentViewNotes(rehearsalId) {
    const r = allStudentRehearsals.find(x => x.id === rehearsalId);
    if (!r) return;
    document.getElementById("view-notes-title").textContent = `Rehearsal Notes — ${r.opera}`;
    const body = document.getElementById("view-notes-body");
    body.innerHTML = r.notes
        ? r.notes.split("\n").map(l => `<p style="margin:0 0 6px;">${renderNotes(l)}</p>`).join("")
        : `<em class="empty-note">No notes for this rehearsal yet.</em>`;
    document.getElementById("reh-view-notes-modal").classList.remove("hidden");
}

function renderRehearsalTimeline(container, rehearsals, absences, buildCard) {
    const today = new Date();
    const todayStr = today.toLocaleDateString("en-CA");

    const endOfWeek = new Date(today);
    endOfWeek.setDate(today.getDate() + 7);
    const endOfWeekStr = endOfWeek.toLocaleDateString("en-CA");

    const endOfMonth = new Date(today.getFullYear(), today.getMonth() + 1, 0);
    const endOfMonthStr = endOfMonth.toLocaleDateString("en-CA");

    const windowStart = new Date(today);
    windowStart.setDate(windowStart.getDate() - 1);
    windowStart.setHours(21, 0, 0, 0);
    const windowEnd = new Date(today);
    windowEnd.setHours(18, 0, 0, 0);

    const buckets = { today: [], week: [], month: [], year: [] };
    rehearsals.forEach(r => {
        const rStart = new Date(r.start);
        const rDate = rStart.toLocaleDateString("en-CA");
        if (rStart >= windowStart && rStart <= windowEnd) buckets.today.push(r);
        else if (rDate <= endOfWeekStr) buckets.week.push(r);
        else if (rDate <= endOfMonthStr) buckets.month.push(r);
        else buckets.year.push(r);
    });

    container.innerHTML = "";

    const todayHdr = document.createElement("div");
    todayHdr.className = "timeline-today-header";
    todayHdr.textContent = "Today";
    container.appendChild(todayHdr);

    if (buckets.today.length) {
        buckets.today.forEach(r => container.appendChild(buildCard(r, absences)));
    } else {
        const empty = document.createElement("em");
        empty.className = "empty-note";
        empty.textContent = "No rehearsals today.";
        container.appendChild(empty);
    }

    [{ key: "week", label: "This Week" }, { key: "month", label: "This Month" }, { key: "year", label: "This Year" }]
        .forEach(({ key, label }) => {
            if (!buckets[key].length) return;
            const toggle = document.createElement("button");
            toggle.className = "timeline-toggle";
            toggle.innerHTML = `${label} <span class="timeline-count">(${buckets[key].length})</span> <span class="timeline-chevron">▶</span>`;
            const body = document.createElement("div");
            body.className = "timeline-body hidden";
            buckets[key].forEach(r => body.appendChild(buildCard(r, absences)));
            toggle.addEventListener("click", () => {
                const collapsed = body.classList.toggle("hidden");
                toggle.querySelector(".timeline-chevron").textContent = collapsed ? "▶" : "▼";
            });
            container.appendChild(toggle);
            container.appendChild(body);
        });
}

function buildStudentRehearsalCard(r, absences) {
    const absent = absences.has(r.id);
    const div = document.createElement("div");
    div.className = "rehearsal-card";
    const dateStr = new Date(r.start).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
    div.innerHTML = `
        <strong>${escapeHtml(r.opera)}</strong>
        <div>Cast: ${escapeHtml(r.cast)}</div>
        <div>${dateStr} &middot; ${formatRehearsalTime(r.start)}&ndash;${formatRehearsalTime(r.end)}</div>
        ${r.notes ? `<em class="rehearsal-notes-preview">${renderNotes(r.notes)}</em>` : ""}
        <div class="rehearsal-card-footer">
            ${absent
                ? `<button class="subtle-btn undo-absent-btn" data-id="${r.id}">I can attend</button>`
                : `<button class="cancel-lesson-btn cant-make-btn" data-id="${r.id}">I can't make it</button>`
            }
            <button class="subtle-btn view-reh-notes-btn" data-id="${r.id}">View Rehearsal Notes</button>
        </div>
    `;
    div.querySelector(".view-reh-notes-btn").addEventListener("click", () => openStudentViewNotes(r.id));
    if (absent) {
        div.querySelector(".undo-absent-btn").addEventListener("click", () => undoStudentAbsent(r.id));
    } else {
        div.querySelector(".cant-make-btn").addEventListener("click", () => markStudentAbsent(r.id));
    }
    return div;
}

async function loadRehearsalTimeline() {
    const box = document.getElementById("rehearsals");
    box.innerHTML = `<em class="empty-note">Loading…</em>`;
    try {
        const [rehRes, absRes] = await Promise.all([
            fetch(`${API}/student/rehearsals`, { credentials: "include" }),
            fetch(`${API}/student/absences`, { credentials: "include" }),
        ]);
        allStudentRehearsals = await rehRes.json();
        const absIds = await absRes.json();
        myAbsences = new Set(Array.isArray(absIds) ? absIds : []);
        renderRehearsalTimeline(box, allStudentRehearsals, myAbsences, buildStudentRehearsalCard);
    } catch (e) {
        console.error(e);
        box.innerHTML = `<em class="empty-note">Failed to load rehearsals.</em>`;
    }
}

async function markStudentAbsent(rehearsalId) {
    if (!confirm("Mark yourself absent for this rehearsal? The admin will be notified.")) return;
    try {
        await fetch(`${API}/student/absence`, {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ rehearsal_id: rehearsalId }),
        });
        myAbsences.add(rehearsalId);
        renderRehearsalTimeline(document.getElementById("rehearsals"), allStudentRehearsals, myAbsences, buildStudentRehearsalCard);
    } catch (e) { alert("Server error."); }
}

async function undoStudentAbsent(rehearsalId) {
    try {
        await fetch(`${API}/student/absence/${rehearsalId}`, { method: "DELETE", credentials: "include" });
        myAbsences.delete(rehearsalId);
        renderRehearsalTimeline(document.getElementById("rehearsals"), allStudentRehearsals, myAbsences, buildStudentRehearsalCard);
    } catch (e) { alert("Server error."); }
}


// -------------------- BOOK TAB: teacher grid --------------------

function renderBookingSection(data) {
    const statusEl = document.getElementById("booking-status");
    const list = document.getElementById("teachers-list");
    list.dataset.bookableDate = data.date;

    if (data.booking_pending) {
        const dayName = formatTodayHeader(data.date);
        statusEl.textContent = `Booking for ${dayName} opens tonight at 9 PM.`;
        list.innerHTML = `<em class="empty-note">Come back at 9 PM to book.</em>`;
        return;
    }

    if (!data.booking_open) {
        statusEl.textContent = "Booking is currently closed. It opens at 9 PM the day before and closes at 6 PM.";
        list.innerHTML = `<em class="empty-note">Come back when the window opens.</em>`;
        return;
    }

    statusEl.textContent = `Booking is open for ${formatTodayHeader(data.date)} until 6 PM.`;

    if (!data.teachers || data.teachers.length === 0) {
        list.innerHTML = `<em class="empty-note">No teachers in the system yet.</em>`;
        return;
    }

    // Partition by availability, each group alphabetized
    const available = data.teachers
        .filter(t => t.status === "available")
        .sort((a, b) => a.name.localeCompare(b.name));
    const unavailable = data.teachers
        .filter(t => t.status !== "available")
        .sort((a, b) => a.name.localeCompare(b.name));

    list.innerHTML = "";

    if (available.length > 0) {
        const heading = document.createElement("h3");
        heading.className = "teacher-group-heading";
        heading.textContent = `Available (${available.length})`;
        list.appendChild(heading);

        const grid = document.createElement("div");
        grid.className = "teachers-grid";
        available.forEach(t => grid.appendChild(buildTeacherCard(t)));
        list.appendChild(grid);
    }

    if (unavailable.length > 0) {
        const heading = document.createElement("h3");
        heading.className = "teacher-group-heading unavailable-heading";
        heading.textContent = `Unavailable today (${unavailable.length})`;
        list.appendChild(heading);

        const grid = document.createElement("div");
        grid.className = "teachers-grid";
        unavailable.forEach(t => grid.appendChild(buildTeacherCard(t)));
        list.appendChild(grid);
    }

    list.querySelectorAll(".view-slots-btn").forEach(btn => {
        if (btn.disabled) return;
        btn.addEventListener("click", () => {
            openSlotPicker(
                btn.dataset.teacherId,
                btn.dataset.teacherName,
                btn.dataset.period
            );
        });
    });
}

function buildTeacherCard(t) {
    const card = document.createElement("div");
    card.className = "teacher-card";

    if (t.status === "available") {
        const morningBtn = t.morning > 0
            ? `<button class="slot-pill-btn view-slots-btn" data-teacher-id="${t.id}" data-teacher-name="${escapeHtml(t.name)}" data-period="morning"><strong>${t.morning}</strong> morning</button>`
            : `<button class="slot-pill-btn" disabled><strong>0</strong> morning</button>`;

        const afternoonBtn = t.afternoon > 0
            ? `<button class="slot-pill-btn view-slots-btn" data-teacher-id="${t.id}" data-teacher-name="${escapeHtml(t.name)}" data-period="afternoon"><strong>${t.afternoon}</strong> afternoon</button>`
            : `<button class="slot-pill-btn" disabled><strong>0</strong> afternoon</button>`;

        card.innerHTML = `
            <h3>${escapeHtml(t.name)}<span class="teacher-type-label">${escapeHtml(t.label || "")}</span></h3>
            <div class="teacher-card-actions">
                ${morningBtn}
                ${afternoonBtn}
            </div>
        `;
    } else {
        card.classList.add("teacher-card-unavailable");
        const reasonText = t.status === "all_booked"
            ? "All slots taken today"
            : "Not available today";
        card.innerHTML = `
            <h3>${escapeHtml(t.name)}<span class="teacher-type-label">${escapeHtml(t.label || "")}</span></h3>
            <div class="teacher-unavailable-reason">${reasonText}</div>
        `;
    }

    return card;
}


// -------------------- SLOT PICKER MODAL --------------------

async function openSlotPicker(teacherId, teacherName, period) {
    const modal = document.getElementById("slot-picker-modal");
    const title = document.getElementById("slot-picker-title");
    const subtitle = document.getElementById("slot-picker-subtitle");
    const grid = document.getElementById("slot-picker-grid");
    const msg = document.getElementById("slot-picker-msg");

    title.textContent = `Book with ${teacherName}`;
    subtitle.textContent = period === "morning" ? "Morning slots" : "Afternoon slots";
    grid.innerHTML = `<em class="empty-note">Loading slots…</em>`;
    msg.textContent = "";
    modal.classList.remove("hidden");

    try {
        const res = await fetch(
            `${API}/student/teacher-slots?teacher=${teacherId}&period=${period}`,
            { credentials: "include" }
        );
        const slots = await res.json();

        if (!Array.isArray(slots) || slots.length === 0) {
            grid.innerHTML = `<em class="empty-note">No slots available.</em>`;
            return;
        }

        grid.innerHTML = "";
        slots.forEach(slot => {
            const btn = document.createElement("button");
            btn.className = "slot-btn";
            btn.textContent = formatSlotTime(slot);
            btn.addEventListener("click", () => bookSlot(teacherId, teacherName, slot));
            grid.appendChild(btn);
        });
    } catch (e) {
        console.error(e);
        grid.innerHTML = `<em class="empty-note">Failed to load slots.</em>`;
    }
}

function closeSlotPicker() {
    document.getElementById("slot-picker-modal").classList.add("hidden");
}


// -------------------- BOOK A SLOT --------------------

async function bookSlot(teacherId, teacherName, time) {
    const msg = document.getElementById("slot-picker-msg");
    msg.textContent = "Booking…";

    const bookableDate = document.getElementById("teachers-list").dataset.bookableDate;
    if (!bookableDate) {
        msg.textContent = "Couldn't determine bookable date. Please refresh.";
        return;
    }

    try {
        const res = await fetch(`${API}/student/book`, {
            credentials: "include",
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                teacher_id: teacherId,
                date: bookableDate,
                time: time
            })
        });
        const data = await res.json();

        if (data.status === "success") {
            msg.textContent = "Booked!";
            setTimeout(() => {
                closeSlotPicker();
                loadToday();
                // Optional: jump to today tab so student sees their new lesson
                setActiveTab("today");
            }, 800);
        } else {
            msg.textContent = data.message || "Booking failed.";
        }
    } catch (e) {
        console.error(e);
        msg.textContent = "Booking failed due to server error.";
    }
}


// -------------------- LESSON NOTES TAB --------------------

async function loadSharedNotes() {
    const box = document.getElementById("shared-notes-list");

    try {
        const res = await fetch(`${API}/student/shared-notes`, { credentials: "include" });
        const data = await res.json();

        if (!Array.isArray(data) || data.length === 0) {
            box.innerHTML = `<em class="empty-note">No notes have been shared with you yet.</em>`;
            return;
        }

        box.innerHTML = "";
        data.forEach(n => {
            const card = document.createElement("div");
            card.className = "shared-note-card";

            const sections = [];
            if (n.piece) sections.push(`<div><strong>Piece:</strong> ${escapeHtml(n.piece)}</div>`);
            if (n.technique) sections.push(`<div><strong>Technique:</strong> ${escapeHtml(n.technique)}</div>`);
            if (n.other) sections.push(`<div><strong>Other:</strong> ${escapeHtml(n.other)}</div>`);

            const body = sections.length > 0
                ? sections.join("")
                : `<em class="empty-note">(No content)</em>`;

            card.innerHTML = `
                <div class="shared-note-header">
                    <strong>${formatShortDate(n.date)}</strong>
                    <span class="lesson-time-small">${formatSlotTime(n.time)}</span>
                    <span class="shared-note-teacher">with ${escapeHtml(n.teacher)}</span>
                </div>
                <div class="shared-note-body">${body}</div>
            `;
            box.appendChild(card);
        });
    } catch (e) {
        console.error(e);
        box.innerHTML = `<em class="empty-note">Failed to load notes.</em>`;
    }
}


// -------------------- INIT --------------------

document.addEventListener("DOMContentLoaded", async () => {
    await ME_READY;
    if (!USERNAME) return;

    // Tabs
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.addEventListener("click", () => setActiveTab(btn.dataset.tab));
    });
    setActiveTab(getTabFromURL());
    window.addEventListener("hashchange", () => setActiveTab(getTabFromURL()));

    // Slot picker
    document.getElementById("slot-picker-cancel")?.addEventListener("click", closeSlotPicker);
    document.getElementById("slot-picker-modal")?.addEventListener("click", (e) => {
        if (e.target.id === "slot-picker-modal") closeSlotPicker();
    });

    // View rehearsal notes modal
    document.getElementById("close-view-notes-btn")?.addEventListener("click", () =>
        document.getElementById("reh-view-notes-modal")?.classList.add("hidden"));
    document.getElementById("reh-view-notes-modal")?.addEventListener("click", e => {
        if (e.target.id === "reh-view-notes-modal") e.target.classList.add("hidden");
    });

    // Calendar subscription URL
    fetch(`${API}/student/my-calendar-token`, { credentials: "include" })
        .then(r => r.json())
        .then(data => {
            const input = document.getElementById("calendar-url");
            if (input && data.token) input.value = `${window.location.origin}/student/calendar/${data.token}.ics`;
            document.getElementById("copy-calendar-url-btn")?.addEventListener("click", () => {
                navigator.clipboard.writeText(input.value).then(() => {
                    const btn = document.getElementById("copy-calendar-url-btn");
                    btn.textContent = "Copied!";
                    setTimeout(() => { btn.textContent = "Copy"; }, 2000);
                });
            });
        }).catch(() => {});

    // Messages tab
    document.querySelectorAll(".dm-view-btn").forEach(btn =>
        btn.addEventListener("click", () => {
            dmView = btn.dataset.dmView;
            document.querySelectorAll(".dm-view-btn").forEach(b => b.classList.toggle("active", b === btn));
            renderDmView();
        })
    );
    document.getElementById("dm-send-btn")?.addEventListener("click", sendDm);
    refreshDmBadge();

    // Initial loads
    loadToday();
    loadRehearsalTimeline();
    loadSharedNotes();

    // Auto-refresh when the user comes back to the tab after being away
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState !== "visible") return;
        loadToday();
        loadRehearsalTimeline();
        loadSharedNotes();
    });
});


// ======================================================
// DIRECT MESSAGES MODULE
// ======================================================

let dmInbox = [];
let dmSent = [];
let dmContacts = [];
let dmSelectedRecipients = new Set();
let dmView = "inbox";

async function loadDmTab() {
    await Promise.all([loadDmMessages(), loadDmContactList()]);
    renderDmView();
    renderDmContactPicker();
    refreshDmBadge();
}

async function loadDmMessages() {
    try {
        const res = await fetch(`${API}/dm`, { credentials: "include" });
        const data = await res.json();
        dmInbox = data.inbox || [];
        dmSent = data.sent || [];
    } catch (e) { dmInbox = []; dmSent = []; }
}

async function loadDmContactList() {
    try {
        const res = await fetch(`${API}/dm/contacts`, { credentials: "include" });
        const data = await res.json();
        dmContacts = Array.isArray(data) ? data : [];
    } catch (e) { dmContacts = []; }
}

function renderDmView() {
    const list = document.getElementById("dm-list");
    if (!list) return;
    const msgs = dmView === "inbox" ? dmInbox.filter(m => !m.read_at)
               : dmView === "read"  ? dmInbox.filter(m => !!m.read_at)
               : dmSent;
    if (!msgs.length) { list.innerHTML = `<em class="empty-note">${dmView === "inbox" ? "All caught up!" : "No messages yet."}</em>`; return; }
    list.innerHTML = "";
    msgs.forEach(m => {
        const isUnread = dmView === "inbox" && !m.read_at;
        const card = document.createElement("div");
        card.className = "dm-card" + (isUnread ? " dm-card--unread" : "");
        const ts = m.created_at ? new Date(m.created_at).toLocaleString(undefined,
            { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";
        let metaLabel = "";
        if (dmView !== "sent") {
            metaLabel = `<span class="dm-sender">${escapeHtml(m.sender_name)}</span>`;
        } else {
            const rnames = (m.recipients || []).slice(0, 3).map(escapeHtml).join(", ");
            const extra = (m.recipients || []).length > 3 ? ` +${(m.recipients || []).length - 3} more` : "";
            metaLabel = `<span class="dm-recipients-label">To: ${rnames}${extra}</span>`;
        }
        card.innerHTML = `
            <div class="dm-meta">
                ${isUnread ? '<span class="dm-unread-dot"></span>' : ""}
                ${metaLabel}
                <span class="dm-time">${ts}</span>
            </div>
            <div class="dm-body">${escapeHtml(m.body)}</div>
            ${dmView !== "sent" ? `<div class="dm-reply-row"><button class="dm-reply-btn" type="button">Reply</button></div>` : ""}
        `;
        if (dmView !== "sent") {
            card.querySelector(".dm-reply-btn")?.addEventListener("click", (e) => {
                e.stopPropagation();
                if (isUnread) markDmRead(m.id, card);
                replyToDm(m.sender_id, m.sender_name);
            });
        }
        if (isUnread) card.addEventListener("click", () => markDmRead(m.id, card));
        list.appendChild(card);
    });
}

async function markDmRead(msgId, card) {
    await fetch(`${API}/dm/${msgId}/read`, { method: "POST", credentials: "include" });
    const msg = dmInbox.find(m => m.id === msgId);
    if (msg) msg.read_at = new Date().toISOString();
    card.classList.remove("dm-card--unread");
    card.querySelector(".dm-unread-dot")?.remove();
    refreshDmBadge();
}

function replyToDm(senderId, senderName) {
    dmSelectedRecipients = new Set([senderId]);
    renderFilteredPills();
    const compose = document.querySelector(".dm-compose");
    if (compose) compose.scrollIntoView({ behavior: "smooth", block: "nearest" });
    setTimeout(() => document.getElementById("dm-body")?.focus(), 300);
}

async function refreshDmBadge() {
    try {
        const res = await fetch(`${API}/dm/unread`, { credentials: "include" });
        const data = await res.json();
        const badge = document.getElementById("dm-badge");
        if (!badge) return;
        const count = data.count || 0;
        badge.textContent = count;
        badge.classList.toggle("hidden", count === 0);
    } catch (e) {}
}

function renderDmContactPicker() {
    const container = document.getElementById("dm-recipient-pills");
    const search = document.getElementById("dm-recipient-search");
    if (!container) return;
    dmSelectedRecipients = new Set();
    if (search) {
        search.classList.remove("hidden");
        search.value = "";
        search.oninput = renderFilteredPills;
    }
    renderFilteredPills();
}

function renderFilteredPills() {
    const container = document.getElementById("dm-recipient-pills");
    if (!container) return;
    const q = (document.getElementById("dm-recipient-search")?.value || "").toLowerCase();
    const byGroup = {};
    dmContacts.forEach(c => {
        if (q && !c.fullname.toLowerCase().includes(q)) return;
        const g = c.group || "Contacts";
        if (!byGroup[g]) byGroup[g] = [];
        byGroup[g].push(c);
    });
    container.innerHTML = "";
    Object.entries(byGroup).forEach(([group, members]) => {
        const lbl = document.createElement("span");
        lbl.className = "dm-pill-group-label";
        lbl.textContent = group;
        container.appendChild(lbl);
        members.forEach(c => {
            const pill = document.createElement("span");
            pill.className = "dm-pill" + (dmSelectedRecipients.has(c.id) ? " selected" : "");
            pill.textContent = c.fullname;
            pill.dataset.id = c.id;
            pill.addEventListener("click", () => {
                if (dmSelectedRecipients.has(c.id)) {
                    dmSelectedRecipients.delete(c.id);
                    pill.classList.remove("selected");
                } else {
                    dmSelectedRecipients.add(c.id);
                    pill.classList.add("selected");
                }
            });
            container.appendChild(pill);
        });
    });
    if (!container.children.length) {
        container.innerHTML = "<em class='empty-note'>No contacts found.</em>";
    }
}

async function sendDm() {
    const body = (document.getElementById("dm-body")?.value || "").trim();
    const status = document.getElementById("dm-send-status");
    if (!body) { status.textContent = "Message cannot be empty."; return; }
    const recipient_ids = [...dmSelectedRecipients];
    if (!recipient_ids.length) { status.textContent = "Select at least one recipient."; return; }
    const btn = document.getElementById("dm-send-btn");
    btn.disabled = true;
    status.textContent = "Sending...";
    try {
        const res = await fetch(`${API}/dm`, {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scope: "direct", body, recipient_ids }),
        });
        const data = await res.json();
        if (data.status === "success") {
            status.textContent = "Message sent.";
            document.getElementById("dm-body").value = "";
            dmSelectedRecipients = new Set();
            renderFilteredPills();
            await loadDmMessages();
            renderDmView();
        } else {
            status.textContent = data.message || "Failed to send.";
        }
    } catch (e) {
        status.textContent = "Error sending message.";
    } finally {
        btn.disabled = false;
    }
}