// ======================================================
// ENSEMBLE MEMBER DASHBOARD
// ======================================================

let allEnsembleRehearsals = [];
let myEnsembleAbsences = new Set();
let absenceTargetRehearsalId = null;
let selectedAbsenceReason = null;

function fmtTime(hhmm) {
    if (!hhmm) return "";
    const [h, m] = hhmm.split(":").map(Number);
    const suffix = h >= 12 ? "PM" : "AM";
    const h12 = ((h + 11) % 12) + 1;
    return `${h12}:${String(m).padStart(2, "0")} ${suffix}`;
}

function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso + "T00:00:00");
    return d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function buildEnsembleRehearsalCard(r) {
    const rEnd = r.end_time ? new Date(r.date + "T" + r.end_time) : new Date(r.date + "T23:59");
    const isPast = rEnd < new Date();
    const absent = myEnsembleAbsences.has(r.id);
    const card = document.createElement("div");
    card.className = "rehearsal-card" + (isPast ? " rehearsal-card--passed" : "");
    card.dataset.id = r.id;
    card.innerHTML = `
        <div class="rehearsal-row-header">
            <div>
                <strong>${fmtDate(r.date)}</strong>
                <div class="rehearsal-roles">${fmtTime(r.start_time)}${r.end_time ? " – " + fmtTime(r.end_time) : ""}</div>
                ${r.location ? `<div class="rehearsal-cast">${escapeHtml(r.location)}</div>` : ""}
                ${r.notes ? `<em class="rehearsal-notes-preview">${renderNotes(r.notes)}</em>` : ""}
                ${r.materials_url ? `<a href="${escapeHtml(r.materials_url)}" target="_blank" rel="noopener" class="materials-link">View Materials</a>` : ""}
            </div>
            <div class="rehearsal-row-actions">
                ${isPast
                    ? `<span class="lesson-status-tag lesson-status-tag--passed">Passed</span>`
                    : absent
                        ? `<button class="cancel-lesson-btn undo-absent-btn" data-id="${r.id}">I can attend</button>`
                        : `<button class="cancel-lesson-btn cant-make-btn" data-id="${r.id}">I can't make it</button>`
                }
            </div>
        </div>
    `;
    if (!isPast) {
        if (absent) {
            card.querySelector(".undo-absent-btn").addEventListener("click", () => undoEnsembleAbsent(r.id));
        } else {
            card.querySelector(".cant-make-btn").addEventListener("click", () => markEnsembleAbsent(r.id));
        }
    }
    return card;
}

function renderRehearsalTimeline(container, rehearsals) {
    container.innerHTML = "";
    if (!rehearsals.length) {
        container.innerHTML = `<em class="empty-note">No upcoming rehearsals.</em>`;
        return;
    }

    const now = new Date();
    const upcoming18h = new Date(now.getTime() + 18 * 60 * 60 * 1000);
    const week7 = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);
    const endOfMonthStr = new Date(now.getFullYear(), now.getMonth() + 1, 0).toLocaleDateString("en-CA");

    const buckets = { upcoming: [], week: [], month: [], year: [] };
    rehearsals.forEach(r => {
        const rStart = new Date(r.date + "T" + (r.start_time || "00:00"));
        if (rStart <= upcoming18h) buckets.upcoming.push(r);
        else if (rStart <= week7) buckets.week.push(r);
        else if (r.date <= endOfMonthStr) buckets.month.push(r);
        else buckets.year.push(r);
    });

    const todayHeader = document.createElement("div");
    todayHeader.className = "timeline-today-header";
    todayHeader.textContent = "Upcoming";
    container.appendChild(todayHeader);

    if (buckets.upcoming.length) {
        buckets.upcoming.forEach(r => container.appendChild(buildEnsembleRehearsalCard(r)));
    } else {
        const empty = document.createElement("p");
        empty.className = "hint";
        empty.textContent = "No rehearsals in the next 18 hours.";
        container.appendChild(empty);
    }

    [
        { key: "week", label: "This Week" },
        { key: "month", label: "This Month" },
        { key: "year", label: "This Year" },
    ].forEach(({ key, label }) => {
        const group = buckets[key] || [];
        if (!group.length) return;
        const section = document.createElement("div");
        section.className = "timeline-section";
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "timeline-toggle";
        toggle.innerHTML = `${label} <span class="timeline-chevron">&#9654;</span> <span class="timeline-count">(${group.length})</span>`;
        const body = document.createElement("div");
        body.className = "timeline-body hidden";
        toggle.addEventListener("click", () => {
            const collapsed = body.classList.toggle("hidden");
            toggle.querySelector(".timeline-chevron").innerHTML = collapsed ? "&#9654;" : "&#9660;";
        });
        group.forEach(r => body.appendChild(buildEnsembleRehearsalCard(r)));
        section.appendChild(toggle);
        section.appendChild(body);
        container.appendChild(section);
    });
}

async function loadEnsembleRehearsalTimeline() {
    const box = document.getElementById("rehearsal-timeline");
    try {
        const [rehRes, absRes] = await Promise.all([
            fetch(`${API}/ensemble/rehearsals`, { credentials: "include" }),
            fetch(`${API}/ensemble/absences`, { credentials: "include" }),
        ]);
        allEnsembleRehearsals = await rehRes.json();
        const absIds = await absRes.json();
        myEnsembleAbsences = new Set(absIds);
        renderRehearsalTimeline(box, allEnsembleRehearsals);
    } catch (e) {
        console.error(e);
        box.innerHTML = `<em class="empty-note">Failed to load.</em>`;
    }
}

function markEnsembleAbsent(rehearsalId) {
    absenceTargetRehearsalId = rehearsalId;
    selectedAbsenceReason = null;
    document.querySelectorAll(".absence-reason-btn").forEach(b => b.classList.remove("selected"));
    document.getElementById("absence-note").value = "";
    document.getElementById("absence-modal-msg").textContent = "";
    document.getElementById("absence-modal").classList.remove("hidden");
}

async function submitEnsembleAbsence() {
    if (!selectedAbsenceReason) {
        document.getElementById("absence-modal-msg").textContent = "Please select a reason.";
        return;
    }
    const note = (document.getElementById("absence-note").value || "").trim();
    const btn = document.getElementById("absence-submit-btn");
    btn.disabled = true;
    try {
        await fetch(`${API}/ensemble/absence`, {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ rehearsal_id: absenceTargetRehearsalId, reason: selectedAbsenceReason, note: note || null }),
        });
        myEnsembleAbsences.add(absenceTargetRehearsalId);
        document.getElementById("absence-modal").classList.add("hidden");
        renderRehearsalTimeline(document.getElementById("rehearsal-timeline"), allEnsembleRehearsals);
    } catch (e) {
        document.getElementById("absence-modal-msg").textContent = "Server error. Try again.";
    } finally {
        btn.disabled = false;
    }
}

async function undoEnsembleAbsent(rehearsalId) {
    try {
        await fetch(`${API}/ensemble/absence/${rehearsalId}`, { method: "DELETE", credentials: "include" });
        myEnsembleAbsences.delete(rehearsalId);
        renderRehearsalTimeline(document.getElementById("rehearsal-timeline"), allEnsembleRehearsals);
    } catch (e) { console.error(e); }
}

document.addEventListener("DOMContentLoaded", async () => {
    await ME_READY;
    if (!USERNAME) return;

    // Set welcome + instrument
    try {
        const res = await fetch(`${API}/ensemble/me`, { credentials: "include" });
        const me = await res.json();
        document.getElementById("welcome").textContent = `Welcome, ${me.fullname}`;
        if (me.instrument) {
            document.getElementById("instrument-display").textContent = me.instrument;
        }
    } catch (e) { console.error(e); }

    // Tab wiring
    document.querySelectorAll(".tab-btn").forEach(btn =>
        btn.addEventListener("click", () => {
            document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
            btn.classList.add("active");
            document.querySelector(`[data-tab-panel="${btn.dataset.tab}"]`)?.classList.add("active");
            if (btn.dataset.tab === "messages") loadDmTab();
        }));
    document.querySelector(".tab-btn")?.click();

    // Absence modal
    document.querySelectorAll(".absence-reason-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".absence-reason-btn").forEach(b => b.classList.remove("selected"));
            btn.classList.add("selected");
            selectedAbsenceReason = btn.dataset.reason;
        });
    });
    document.getElementById("absence-submit-btn")?.addEventListener("click", submitEnsembleAbsence);
    document.getElementById("absence-cancel-btn")?.addEventListener("click", () =>
        document.getElementById("absence-modal").classList.add("hidden"));
    document.getElementById("absence-modal")?.addEventListener("click", e => {
        if (e.target.id === "absence-modal") e.target.classList.add("hidden");
    });

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

    await loadEnsembleRehearsalTimeline();

    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) loadEnsembleRehearsalTimeline();
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
