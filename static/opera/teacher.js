// ======================================================
// TEACHER DASHBOARD
// ======================================================

const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const VALID_TEACHER_TABS = ["today", "notes", "availability", "messages"];

let currentAvailability = [];
let currentLessonId = null;
let currentLessonShared = false;
let autosaveTimer = null;
let pendingCancelLessonId = null;  // For cancel confirm modal


// -------------------- FORMATTING HELPERS --------------------

function formatSlotTime(hhmm) {
    if (!hhmm) return "";
    const [h, m] = hhmm.split(":").map(Number);
    const suffix = h >= 12 ? "PM" : "AM";
    const hour12 = ((h + 11) % 12) + 1;
    return `${hour12}:${String(m).padStart(2, "0")} ${suffix}`;
}

function formatDateHeader(iso) {
    if (!iso) return "";
    const d = new Date(iso + "T00:00:00");
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

function formatCancelledTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});
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
    if (!VALID_TEACHER_TABS.includes(tabName)) tabName = "today";

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
    return VALID_TEACHER_TABS.includes(hash) ? hash : "today";
}


// -------------------- TODAY'S LESSONS --------------------

async function loadTodaysLessons() {
    const grid = document.getElementById("today-lessons-grid");
    const header = document.getElementById("today-header");
    const cancelledBox = document.getElementById("today-cancelled-box");
    const cancelledGrid = document.getElementById("today-cancelled-grid");

    if (!USERNAME) {
        grid.innerHTML = `<em class="empty-note">Not logged in.</em>`;
        return;
    }

    try {
        const res = await fetch(`${API}/teacher/today`, { credentials: "include" });
        const data = await res.json();

        header.textContent = formatDateHeader(data.date);

        // Active lessons
        if (!data.lessons || data.lessons.length === 0) {
            grid.className = "";
            grid.innerHTML = `<em class="empty-note">No lessons scheduled.</em>`;
        } else {
            grid.className = "teachers-grid";
            grid.innerHTML = "";

            data.lessons.forEach(l => {
                const card = document.createElement("div");
                card.className = "teacher-card";
                card.innerHTML = `
                    <h3>${escapeHtml(l.student)}</h3>
                    <div class="lesson-time-display">${formatSlotTime(l.time)}</div>
                    <div class="teacher-card-actions">
                        <button class="slot-pill-btn notes-btn"
                                data-lesson-id="${l.id}"
                                data-student-name="${escapeHtml(l.student)}">
                            ð Notes
                        </button>
                        <button class="slot-pill-btn cancel-pill-btn teacher-cancel-btn"
                                data-lesson-id="${l.id}"
                                data-student-name="${escapeHtml(l.student)}">
                            Cancel
                        </button>
                    </div>
                `;
                grid.appendChild(card);
            });

            grid.querySelectorAll(".notes-btn").forEach(btn => {
                btn.addEventListener("click", () => {
                    openNotesModal(btn.dataset.lessonId, btn.dataset.studentName);
                });
            });
            grid.querySelectorAll(".teacher-cancel-btn").forEach(btn => {
                btn.addEventListener("click", () => {
                    openCancelConfirm(btn.dataset.lessonId, btn.dataset.studentName);
                });
            });
        }

        // Cancelled lessons (only show section if there are any)
        if (data.cancelled && data.cancelled.length > 0) {
            cancelledBox.classList.remove("hidden");
            cancelledGrid.className = "teachers-grid";
            cancelledGrid.innerHTML = "";

            data.cancelled.forEach(l => {
                const card = document.createElement("div");
                card.className = "teacher-card teacher-card-cancelled";
                const cancelledAtStr = l.cancelled_at
                    ? `Cancelled at ${formatCancelledTime(l.cancelled_at)}`
                    : "Cancelled";
                card.innerHTML = `
                    <h3>${escapeHtml(l.student)}</h3>
                    <div class="lesson-time-display strikethrough">${formatSlotTime(l.time)}</div>
                    <div class="cancelled-badge">${cancelledAtStr}</div>
                `;
                cancelledGrid.appendChild(card);
            });
        } else {
            cancelledBox.classList.add("hidden");
        }
    } catch (e) {
        console.error(e);
        grid.innerHTML = `<em class="empty-note">Failed to load lessons.</em>`;
    }
}


// -------------------- CANCEL LESSON (teacher) --------------------

function openCancelConfirm(lessonId, studentName) {
    pendingCancelLessonId = Number(lessonId);
    const modal = document.getElementById("cancel-lesson-modal");
    const text = document.getElementById("cancel-lesson-text");
    const msg = document.getElementById("cancel-lesson-msg");

    text.textContent = `Cancel ${studentName}'s lesson?`;
    msg.textContent = "";
    modal.classList.remove("hidden");
}

function closeCancelConfirm() {
    pendingCancelLessonId = null;
    document.getElementById("cancel-lesson-modal").classList.add("hidden");
}

async function confirmCancel() {
    if (!pendingCancelLessonId) return;

    const msg = document.getElementById("cancel-lesson-msg");
    msg.textContent = "Cancellingâ¦";

    try {
        const res = await fetch(`${API}/student/cancel-lesson`, {
            credentials: "include",
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                lesson_id: pendingCancelLessonId
            })
        });
        const data = await res.json();

        if (data.status === "success") {
            msg.textContent = "Cancelled.";
            setTimeout(() => {
                closeCancelConfirm();
                loadTodaysLessons();
            }, 600);
        } else {
            msg.textContent = data.message || "Failed to cancel.";
        }
    } catch (e) {
        console.error(e);
        msg.textContent = "Server error.";
    }
}


// -------------------- LESSON NOTES MODAL --------------------

async function openNotesModal(lessonId, studentName) {
    currentLessonId = Number(lessonId);
    currentLessonShared = false;

    const modal = document.getElementById("notes-modal");
    const title = document.getElementById("notes-modal-title");
    const subtitle = document.getElementById("notes-modal-subtitle");
    const lockBanner = document.getElementById("notes-locked-banner");
    const sharedBanner = document.getElementById("notes-shared-banner");
    const saveStatus = document.getElementById("notes-save-status");
    const saveBtn = document.getElementById("notes-save-btn");
    const sendBtn = document.getElementById("notes-send-btn");
    const pieceEl = document.getElementById("notes-piece");
    const techniqueEl = document.getElementById("notes-technique");
    const otherEl = document.getElementById("notes-other");

    title.textContent = `Notes â ${studentName}`;
    subtitle.textContent = "Loadingâ¦";
    lockBanner.classList.add("hidden");
    sharedBanner.classList.add("hidden");
    saveStatus.textContent = "";
    pieceEl.value = "";
    techniqueEl.value = "";
    otherEl.value = "";
    pieceEl.disabled = true;
    techniqueEl.disabled = true;
    otherEl.disabled = true;
    saveBtn.disabled = true;
    sendBtn.disabled = true;
    sendBtn.textContent = "Send to student";

    modal.classList.remove("hidden");

    try {
        const res = await fetch(
            `${API}/teacher/lesson-notes?lesson_id=${lessonId}`,
            { credentials: "include" }
        );
        const data = await res.json();

        if (data.status !== "success") {
            subtitle.textContent = data.message || "Failed to load notes.";
            return;
        }

        const dateStr = formatShortDate(data.lesson_date);
        const timeStr = formatSlotTime(data.lesson_time);
        subtitle.textContent = `${dateStr} Â· ${timeStr}`;

        pieceEl.value = data.piece || "";
        techniqueEl.value = data.technique || "";
        otherEl.value = data.other || "";

        currentLessonShared = !!data.shared_with_student;
        if (currentLessonShared) {
            sharedBanner.classList.remove("hidden");
            sendBtn.textContent = "â Shared";
            sendBtn.disabled = true;
        }

        if (!data.editable) {
            lockBanner.classList.remove("hidden");
            // Still allow viewing, disable editing + sending
            sendBtn.disabled = true;
        } else {
            pieceEl.disabled = false;
            techniqueEl.disabled = false;
            otherEl.disabled = false;
            saveBtn.disabled = false;
            if (!currentLessonShared) {
                sendBtn.disabled = false;
            }
        }
    } catch (e) {
        console.error(e);
        subtitle.textContent = "Failed to load notes.";
    }
}

function closeNotesModal() {
    if (autosaveTimer) {
        clearTimeout(autosaveTimer);
        autosaveTimer = null;
    }
    currentLessonId = null;
    currentLessonShared = false;
    document.getElementById("notes-modal").classList.add("hidden");

    // Refresh whichever view is showing underneath so changes appear immediately
    if (!document.getElementById("student-history-view").classList.contains("hidden")) {
        const view = document.getElementById("student-history-view");
        const sid = view.dataset.studentId;
        const sname = view.dataset.studentName;
        if (sid) openStudentHistory(Number(sid), sname);
    }
    // Also refresh today's lessons (in case the notes came from there)
    loadTodaysLessons();
}

async function saveNotes(isAutosave = false) {
    if (!currentLessonId) return;

    const piece = document.getElementById("notes-piece").value;
    const technique = document.getElementById("notes-technique").value;
    const other = document.getElementById("notes-other").value;
    const saveStatus = document.getElementById("notes-save-status");

    if (!isAutosave) saveStatus.textContent = "Savingâ¦";

    try {
        const res = await fetch(`${API}/teacher/lesson-notes`, {
            credentials: "include",
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                lesson_id: currentLessonId,
                piece, technique, other
            })
        });
        const data = await res.json();

        if (data.status === "success") {
            const time = new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
            saveStatus.textContent = isAutosave ? `Auto-saved ${time}` : `Saved ${time}`;
            // Manual save (not autosave) â close modal after brief confirmation
            if (!isAutosave) {
                setTimeout(() => closeNotesModal(), 800);
            }
        } else {
            saveStatus.textContent = data.message || "Save failed";
        }
    } catch (e) {
        console.error(e);
        saveStatus.textContent = "Save failed";
    }
}

function scheduleAutosave() {
    if (autosaveTimer) clearTimeout(autosaveTimer);
    document.getElementById("notes-save-status").textContent = "Editingâ¦";
    autosaveTimer = setTimeout(() => saveNotes(true), 1000);
}

async function sendNotesToStudent() {
    if (!currentLessonId || currentLessonShared) return;

    const ok = confirm("Share these notes with the student? They'll see them on their dashboard. This can't be undone, and any future edits will stay visible to them.");
    if (!ok) return;

    const sendBtn = document.getElementById("notes-send-btn");
    const saveStatus = document.getElementById("notes-save-status");
    sendBtn.disabled = true;
    saveStatus.textContent = "Sharingâ¦";

    // First, save any unsaved content
    await saveNotes(false);

    try {
        const res = await fetch(`${API}/teacher/share-notes`, {
            credentials: "include",
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                lesson_id: currentLessonId
            })
        });
        const data = await res.json();

        if (data.status === "success") {
            saveStatus.textContent = "Shared with student.";
            currentLessonShared = true;
            document.getElementById("notes-shared-banner").classList.remove("hidden");
            sendBtn.textContent = "â Shared";
            setTimeout(() => closeNotesModal(), 800);
        } else {
            saveStatus.textContent = data.message || "Failed to share.";
            sendBtn.disabled = false;
        }
    } catch (e) {
        console.error(e);
        saveStatus.textContent = "Server error.";
        sendBtn.disabled = false;
    }
}
async function sendNotesFromHistory(lessonId, studentName) {
    const ok = confirm(`Send ${studentName}'s lesson notes to them? They'll see the notes on their dashboard. This can't be undone.`);
    if (!ok) return;

    try {
        const res = await fetch(`${API}/teacher/share-notes`, {
            credentials: "include",
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                lesson_id: Number(lessonId)
            })
        });
        const data = await res.json();

        if (data.status === "success") {
            // Refresh the history view in place
            const view = document.getElementById("student-history-view");
            const studentId = view.dataset.studentId;
            const name = view.dataset.studentName;
            if (studentId) {
                openStudentHistory(Number(studentId), name);
            }
        } else {
            alert(data.message || "Failed to share notes.");
        }
    } catch (e) {
        console.error(e);
        alert("Server error.");
    }
}


// -------------------- LESSON NOTES TAB â students list --------------------

async function loadStudentsList() {
    const box = document.getElementById("students-list");

    try {
        const res = await fetch(`${API}/teacher/students`, { credentials: "include" });
        const data = await res.json();

        if (!Array.isArray(data) || data.length === 0) {
            box.innerHTML = `<em class="empty-note">You haven't had any lessons yet.</em>`;
            return;
        }

        box.className = "teachers-grid";
        box.innerHTML = "";

        data.forEach(s => {
            const card = document.createElement("div");
            card.className = "teacher-card student-list-card";
            card.dataset.studentId = s.id;
            const lessonsLabel = s.lesson_count === 1 ? "lesson" : "lessons";
            card.innerHTML = `
                <h3>${escapeHtml(s.name)}</h3>
                <div class="student-meta">
                    <span class="slot-pill">${s.lesson_count} ${lessonsLabel}</span>
                    ${s.voice_type ? `<span class="student-voice">${escapeHtml(s.voice_type)}</span>` : ""}
                </div>
                <div class="student-meta-secondary">
                    Most recent: ${formatShortDate(s.most_recent)}
                </div>
            `;
            card.addEventListener("click", () => openStudentHistory(s.id, s.name));
            box.appendChild(card);
        });
    } catch (e) {
        console.error(e);
        box.innerHTML = `<em class="empty-note">Failed to load students.</em>`;
    }
}


// -------------------- LESSON NOTES TAB â single-student history --------------------

async function openStudentHistory(studentId, studentName) {
    document.getElementById("students-list-view").classList.add("hidden");
    const view = document.getElementById("student-history-view");
    view.classList.remove("hidden");
    view.dataset.studentId = studentId;
    view.dataset.studentName = studentName;

    document.getElementById("student-history-title").textContent = studentName;
    document.getElementById("student-history-subtitle").textContent = "Loadingâ¦";

    const list = document.getElementById("student-history-list");
    list.innerHTML = `<em class="empty-note">Loadingâ¦</em>`;

    try {
        const res = await fetch(
            `${API}/teacher/student-history?student_id=${studentId}`,
            { credentials: "include" }
        );
        const data = await res.json();

        if (!data.student) {
            document.getElementById("student-history-subtitle").textContent = "Student not found.";
            list.innerHTML = "";
            return;
        }

        const count = data.student.lesson_count;
        document.getElementById("student-history-subtitle").textContent =
            `${count} lesson${count === 1 ? "" : "s"}${data.student.voice_type ? ` Â· ${data.student.voice_type}` : ""}`;

        if (data.lessons.length === 0) {
            list.innerHTML = `<em class="empty-note">No lessons on record.</em>`;
            return;
        }

        list.innerHTML = "";
        data.lessons.forEach(l => {
            const card = document.createElement("div");
            card.className = "lesson-history-card";
            const hasNotes = l.has_notes;
            const isCancelled = l.status === "cancelled";

            const badges = [];
            if (isCancelled) {
                badges.push(`<span class="lock-badge cancelled-inline-badge">Cancelled</span>`);
            }
            if (!l.editable && !isCancelled) {
                badges.push(`<span class="lock-badge">ð Locked</span>`);
            }
            if (l.shared_with_student) {
                badges.push(`<span class="shared-badge">â Shared</span>`);
            }

            const header = `
                <div class="lesson-history-header">
                    <strong>${formatShortDate(l.date)}</strong>
                    <span class="lesson-time-small">${formatSlotTime(l.time)}</span>
                    ${badges.length > 0 ? `<span class="history-badges">${badges.join(" ")}</span>` : ""}
                </div>
            `;

            let body;
            if (!hasNotes) {
                body = `<em class="empty-note">No notes.</em>`;
            } else {
                const sections = [];
                if (l.piece) sections.push(`<div><strong>Piece:</strong> ${escapeHtml(l.piece)}</div>`);
                if (l.technique) sections.push(`<div><strong>Technique:</strong> ${escapeHtml(l.technique)}</div>`);
                if (l.other) sections.push(`<div><strong>Other:</strong> ${escapeHtml(l.other)}</div>`);
                body = `<div class="lesson-history-body">${sections.join("")}</div>`;
            }

            let buttons = "";
            if (!isCancelled) {
                const btnText = l.editable
                    ? (hasNotes ? "Edit notes" : "Add notes")
                    : "View notes";
                buttons += `<button class="subtle-btn history-edit-btn" data-lesson-id="${l.id}" data-student-name="${escapeHtml(studentName)}">${btnText}</button>`;

                // Inline "Send to student" button â only if lesson has notes, isn't already shared, and isn't locked
                if (hasNotes && !l.shared_with_student && l.editable) {
                    buttons += `<button class="send-notes-btn history-send-btn" data-lesson-id="${l.id}" data-student-name="${escapeHtml(studentName)}">Send to student</button>`;
                }
            }

            card.innerHTML = `${header}${body}${buttons ? `<div class="lesson-history-actions">${buttons}</div>` : ""}`;
            list.appendChild(card);
        });

        list.querySelectorAll(".history-edit-btn").forEach(btn => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                openNotesModal(btn.dataset.lessonId, btn.dataset.studentName);
            });
        });

        list.querySelectorAll(".history-send-btn").forEach(btn => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                sendNotesFromHistory(btn.dataset.lessonId, btn.dataset.studentName);
            });
        });
    } catch (e) {
        console.error(e);
        list.innerHTML = `<em class="empty-note">Failed to load lesson history.</em>`;
    }
}

function backToStudentsList() {
    document.getElementById("student-history-view").classList.add("hidden");
    document.getElementById("students-list-view").classList.remove("hidden");
    loadStudentsList();
}


// -------------------- WEEKLY AVAILABILITY --------------------

async function loadWeekly() {
    const out = document.getElementById("weekly-out");

    try {
        const res = await fetch(`${API}/teacher/weekly`, { credentials: "include" });
        const data = await res.json();
        currentAvailability = Array.isArray(data) ? data : [];

        if (currentAvailability.length === 0) {
            out.innerHTML = `<em class="empty-note">No availability set.</em>`;
            return;
        }

        let html = `<table><tr><th>Day</th><th>Start</th><th>End</th></tr>`;
        currentAvailability.forEach(w => {
            html += `<tr>
                <td>${DAY_NAMES[w.weekday]}</td>
                <td>${formatSlotTime(w.start)}</td>
                <td>${formatSlotTime(w.end)}</td>
            </tr>`;
        });
        html += `</table>`;
        out.innerHTML = `<div style="overflow-x:auto">${html}</div>`;
    } catch (e) {
        console.error(e);
        out.innerHTML = `<em class="empty-note">Failed to load availability.</em>`;
    }
}


// -------------------- EDIT SCHEDULE MODAL --------------------

function buildDayRows() {
    const container = document.getElementById("request-days");
    container.innerHTML = "";

    const currentByWeekday = {};
    currentAvailability.forEach(w => {
        currentByWeekday[w.weekday] = w;
    });

    for (let d = 0; d < 7; d++) {
        const current = currentByWeekday[d];
        const isActive = !!current;
        const startVal = current?.start || "10:00";
        const endVal = current?.end || "17:00";

        const row = document.createElement("div");
        row.className = "day-row";
        row.innerHTML = `
            <label class="day-checkbox-label">
                <input type="checkbox" class="day-check" data-weekday="${d}" ${isActive ? "checked" : ""}>
                ${DAY_NAMES[d]}
            </label>
            <input type="time" class="day-start" data-weekday="${d}" value="${startVal}">
            <span class="day-arrow">â</span>
            <input type="time" class="day-end" data-weekday="${d}" value="${endVal}">
        `;
        container.appendChild(row);
    }
}

function openRequestModal() {
    buildDayRows();
    document.getElementById("request-msg").textContent = "";

    document.querySelector('input[name="request-scope"][value="permanent"]').checked = true;
    document.getElementById("request-week-start").disabled = true;
    document.getElementById("request-week-start").value = "";

    document.getElementById("request-modal").classList.remove("hidden");
}

function closeRequestModal() {
    document.getElementById("request-modal").classList.add("hidden");
}

function onScopeChange() {
    const scope = document.querySelector('input[name="request-scope"]:checked').value;
    const weekInput = document.getElementById("request-week-start");
    weekInput.disabled = (scope !== "one_time");
    if (weekInput.disabled) weekInput.value = "";
}

async function submitRequest() {
    const msg = document.getElementById("request-msg");
    msg.textContent = "";

    const scope = document.querySelector('input[name="request-scope"]:checked').value;
    const weekStart = document.getElementById("request-week-start").value;

    if (scope === "one_time" && !weekStart) {
        msg.textContent = "Please pick the week your one-time change applies to.";
        return;
    }

    const schedule = [];
    for (let d = 0; d < 7; d++) {
        const check = document.querySelector(`.day-check[data-weekday="${d}"]`);
        if (!check.checked) continue;

        const start = document.querySelector(`.day-start[data-weekday="${d}"]`).value;
        const end = document.querySelector(`.day-end[data-weekday="${d}"]`).value;

        if (!start || !end) {
            msg.textContent = `${DAY_NAMES[d]} is ticked but missing a time.`;
            return;
        }
        if (start >= end) {
            msg.textContent = `${DAY_NAMES[d]}: end time must be after start time.`;
            return;
        }

        schedule.push({
            weekday: d,
            start_time: start,
            end_time: end
        });
    }

    if (schedule.length === 0 && scope === "permanent") {
        const ok = confirm("You've left all days unticked â this means no availability at all. Save anyway?");
        if (!ok) return;
    }

    msg.textContent = "Savingâ¦";

    try {
        const res = await fetch(`${API}/teacher/update-availability`, {
            credentials: "include",
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                scope,
                effective_week_start: scope === "one_time" ? weekStart : null,
                schedule,
                note: null
            })
        });
        const data = await res.json();

        if (data.status === "success") {
            msg.textContent = "Saved!";
            setTimeout(() => {
                closeRequestModal();
                loadWeekly();
            }, 700);
        } else {
            msg.textContent = data.message || "Failed to save.";
        }
    } catch (e) {
        console.error(e);
        msg.textContent = "Server error. Try again.";
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

    // Calendar subscription URL
    fetch(`${API}/teacher/my-calendar-token`, { credentials: "include" })
        .then(r => r.json())
        .then(data => {
            const input = document.getElementById("calendar-url");
            if (input && data.token) input.value = `${window.location.origin}/teacher/calendar/${data.token}.ics`;
            document.getElementById("copy-calendar-url-btn")?.addEventListener("click", () => {
                navigator.clipboard.writeText(input.value).then(() => {
                    const btn = document.getElementById("copy-calendar-url-btn");
                    btn.textContent = "Copied!";
                    setTimeout(() => { btn.textContent = "Copy"; }, 2000);
                });
            });
        }).catch(() => {});

    // Today
    loadTodaysLessons();

    // Lesson Notes tab
    loadStudentsList();
    document.getElementById("back-to-students-btn")?.addEventListener("click", backToStudentsList);

    // Weekly Availability
    loadWeekly();

    // Notes modal
    document.getElementById("notes-save-btn")?.addEventListener("click", () => saveNotes(false));
    document.getElementById("notes-close-btn")?.addEventListener("click", closeNotesModal);
    document.getElementById("notes-send-btn")?.addEventListener("click", sendNotesToStudent);
    document.getElementById("notes-modal")?.addEventListener("click", (e) => {
        if (e.target.id === "notes-modal") closeNotesModal();
    });
    ["notes-piece", "notes-technique", "notes-other"].forEach(id => {
        document.getElementById(id)?.addEventListener("input", scheduleAutosave);
    // Auto-refresh when the user comes back to the tab after being away
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState !== "visible") return;
        loadTodaysLessons();
        loadWeekly();
        if (!document.getElementById("student-history-view").classList.contains("hidden")) {
            // Teacher was viewing a student's history â refresh that
            const view = document.getElementById("student-history-view");
            const sid = view.dataset.studentId;
            const sname = view.dataset.studentName;
            if (sid) openStudentHistory(Number(sid), sname);
        } else {
            // Teacher was on the students list â refresh that instead
            loadStudentsList();
        }
    });
    });

    // Cancel lesson modal
    document.getElementById("confirm-cancel-btn")?.addEventListener("click", confirmCancel);
    document.getElementById("abort-cancel-btn")?.addEventListener("click", closeCancelConfirm);
    document.getElementById("cancel-lesson-modal")?.addEventListener("click", (e) => {
        if (e.target.id === "cancel-lesson-modal") closeCancelConfirm();
    });

    // Edit Schedule modal
    document.getElementById("open-request-btn")?.addEventListener("click", openRequestModal);
    document.getElementById("cancel-request-btn")?.addEventListener("click", closeRequestModal);
    document.getElementById("submit-request-btn")?.addEventListener("click", submitRequest);
    document.getElementById("request-modal")?.addEventListener("click", (e) => {
        if (e.target.id === "request-modal") closeRequestModal();
    });
    document.querySelectorAll('input[name="request-scope"]').forEach(r => {
        r.addEventListener("change", onScopeChange);
    });

    // Messages tab
    document.querySelectorAll(".dm-view-btn").forEach(btn =>
        btn.addEventListener("click", () => {
            dmView = btn.dataset.dmView;
            document.querySelectorAll(".dm-view-btn").forEach(b => b.classList.toggle("active", b === btn));
            renderDmView();
        })
    );
    document.getElementById("dm-scope")?.addEventListener("change", e => {
        document.getElementById("dm-recipient-row")?.classList.toggle("hidden", e.target.value !== "direct");
    });
    document.getElementById("dm-send-btn")?.addEventListener("click", sendDm);
    refreshDmBadge();
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
    const scopeEl = document.getElementById("dm-scope");
    if (scopeEl) {
        scopeEl.value = "direct";
        document.getElementById("dm-recipient-row")?.classList.remove("hidden");
    }
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
    const scopeEl = document.getElementById("dm-scope");
    const scope = scopeEl ? scopeEl.value : "direct";
    let recipient_ids = [];
    if (scope === "direct") {
        recipient_ids = [...dmSelectedRecipients];
        if (!recipient_ids.length) { status.textContent = "Select at least one recipient."; return; }
    }
    const btn = document.getElementById("dm-send-btn");
    btn.disabled = true;
    status.textContent = "Sending...";
    try {
        const res = await fetch(`${API}/dm`, {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scope, body, recipient_ids }),
        });
        const data = await res.json();
        if (data.status === "success") {
            status.textContent = `Sent to ${data.sent_to} recipient${data.sent_to === 1 ? "" : "s"}.`;
            document.getElementById("dm-body").value = "";
            if (scopeEl) scopeEl.value = scopeEl.options[0]?.value || "direct";
            document.getElementById("dm-recipient-row")?.classList.add("hidden");
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