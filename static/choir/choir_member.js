// ======================================================
// CHOIR MEMBER DASHBOARD
// ======================================================

const VALID_CHOIR_MEMBER_TABS = ["upcoming", "subs", "messages"];

let myAbsences = new Set();
let mySubStatus = {};       // rehearsal_id -> { status, filled_by_name }
let activeFindSubRehearsalId = null;


// ── Helpers ──────────────────────────────────────────────────────────────────

function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso + "T00:00:00");
    return d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

function fmtTime(hhmm) {
    if (!hhmm) return "";
    const [h, m] = hhmm.split(":").map(Number);
    const suffix = h >= 12 ? "PM" : "AM";
    const h12 = ((h + 11) % 12) + 1;
    return `${h12}:${String(m).padStart(2, "0")} ${suffix}`;
}


// ── Tab switching ─────────────────────────────────────────────────────────────

function setActiveTab(tabName) {
    if (!VALID_CHOIR_MEMBER_TABS.includes(tabName)) tabName = "upcoming";
    document.querySelectorAll(".tab-btn").forEach(btn =>
        btn.classList.toggle("active", btn.dataset.tab === tabName));
    document.querySelectorAll(".tab-panel").forEach(panel =>
        panel.classList.toggle("active", panel.dataset.tabPanel === tabName));
    if (window.location.hash !== `#${tabName}`)
        history.replaceState(null, "", `#${tabName}`);
    if (tabName === "subs") loadMySubs();
    if (tabName === "messages") loadDmTab();
}

function getTabFromURL() {
    const hash = window.location.hash.replace("#", "");
    return VALID_CHOIR_MEMBER_TABS.includes(hash) ? hash : "upcoming";
}


// ── Upcoming rehearsals ───────────────────────────────────────────────────────

async function loadUpcoming() {
    const list = document.getElementById("upcoming-list");
    list.innerHTML = `<em class="empty-note">Loading…</em>`;

    try {
        const [rehRes, absRes, subRes] = await Promise.all([
            fetch(`${API}/choir/rehearsals`, { credentials: "include" }),
            fetch(`${API}/choir/my-absences`, { credentials: "include" }),
            fetch(`${API}/choir/my-sub-status`, { credentials: "include" }),
        ]);
        const rehearsals = await rehRes.json();
        const absentIds = await absRes.json();
        const subStatuses = await subRes.json();
        myAbsences = new Set(absentIds);
        mySubStatus = {};
        (Array.isArray(subStatuses) ? subStatuses : []).forEach(s => {
            mySubStatus[s.rehearsal_id] = s;
        });

        if (!rehearsals.length) {
            list.innerHTML = `<em class="empty-note">No upcoming rehearsals scheduled.</em>`;
            return;
        }

        list.innerHTML = "";

        const now = new Date();
        const todayStr = now.toLocaleDateString("en-CA");
        const endOfWeek = new Date(now);
        endOfWeek.setDate(now.getDate() + (6 - now.getDay()));
        const endOfWeekStr = endOfWeek.toLocaleDateString("en-CA");
        const endOfMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0);
        const endOfMonthStr = endOfMonth.toLocaleDateString("en-CA");

        const buckets = { today: [], week: [], month: [], year: [] };
        rehearsals.forEach(r => {
            if (r.date === todayStr) buckets.today.push(r);
            else if (r.date <= endOfWeekStr) buckets.week.push(r);
            else if (r.date <= endOfMonthStr) buckets.month.push(r);
            else buckets.year.push(r);
        });

        // Today section — always visible
        const todayHdr = document.createElement("div");
        todayHdr.className = "timeline-today-header";
        todayHdr.textContent = "Today";
        list.appendChild(todayHdr);
        if (buckets.today.length) {
            buckets.today.forEach(r => buildRehearsalCard(r, list));
        } else {
            const empty = document.createElement("em");
            empty.className = "empty-note";
            empty.textContent = "No rehearsals today.";
            list.appendChild(empty);
        }

        // Collapsible sections
        [{ key: "week", label: "This Week" }, { key: "month", label: "This Month" }, { key: "year", label: "This Year" }]
            .forEach(({ key, label }) => {
                if (!buckets[key].length) return;
                const toggle = document.createElement("button");
                toggle.className = "timeline-toggle";
                toggle.innerHTML = `${label} <span class="timeline-count">(${buckets[key].length})</span> <span class="timeline-chevron">▶</span>`;
                const body = document.createElement("div");
                body.className = "timeline-body hidden";
                buckets[key].forEach(r => buildRehearsalCard(r, body));
                toggle.addEventListener("click", () => {
                    const collapsed = body.classList.toggle("hidden");
                    toggle.querySelector(".timeline-chevron").textContent = collapsed ? "▶" : "▼";
                });
                list.appendChild(toggle);
                list.appendChild(body);
            });
    } catch (e) {
        console.error(e);
        list.innerHTML = `<em class="empty-note">Failed to load rehearsals.</em>`;
    }
}

function buildRehearsalCard(r, container) {
    const absent = myAbsences.has(r.id);
    const sub = absent ? (mySubStatus[r.id] || null) : null;
    const card = document.createElement("div");
    card.className = `rehearsal-card${absent ? " teacher-card-cancelled" : ""}`;
    card.dataset.rehearsalId = r.id;

    const timeRange = fmtTime(r.start_time) + (r.end_time ? ` – ${fmtTime(r.end_time)}` : "");

    let subLine = "";
    let actionButtons = "";
    if (absent) {
        if (sub && sub.status === "filled") {
            subLine = `<p style="color:var(--success);font-size:.88rem;margin-top:var(--space-2);">Sub confirmed: ${escapeHtml(sub.filled_by_name)}</p>`;
            actionButtons = `<button class="subtle-btn undo-absent-btn" data-id="${r.id}">I can attend</button>`;
        } else if (sub && sub.all_declined) {
            subLine = `<p style="color:var(--danger,#b23a3a);font-size:.88rem;margin-top:var(--space-2);">All contacted subs declined. Reach out to another sub.</p>`;
            actionButtons = `<button class="subtle-btn undo-absent-btn" data-id="${r.id}">I can attend</button>
                             <button class="slot-pill-btn find-sub-btn" data-id="${r.id}" style="font-size:.82rem;padding:6px 12px;">Find a sub</button>`;
        } else if (sub) {
            subLine = `<p style="color:var(--text-muted);font-size:.88rem;margin-top:var(--space-2);">Sub search in progress...</p>`;
            actionButtons = `<button class="subtle-btn undo-absent-btn" data-id="${r.id}">I can attend</button>
                             <button class="slot-pill-btn find-sub-btn" data-id="${r.id}" style="font-size:.82rem;padding:6px 12px;">Find a sub</button>`;
        } else {
            actionButtons = `<button class="subtle-btn undo-absent-btn" data-id="${r.id}">I can attend</button>
                             <button class="slot-pill-btn find-sub-btn" data-id="${r.id}" style="font-size:.82rem;padding:6px 12px;">Find a sub</button>`;
        }
    } else {
        actionButtons = `<button class="cancel-lesson-btn cant-make-btn" data-id="${r.id}">I can't make it</button>`;
    }

    card.innerHTML = `
        <div class="rehearsal-row-header">
            <div>
                <strong>${fmtDate(r.date)}</strong>
                <div class="rehearsal-roles">${timeRange}</div>
                ${r.location ? `<div class="rehearsal-cast">${escapeHtml(r.location)}</div>` : ""}
                ${r.notes ? `<em class="rehearsal-leaders">${renderNotes(r.notes)}</em>` : ""}
                ${r.materials_url ? `<a href="${escapeHtml(r.materials_url)}" target="_blank" rel="noopener" class="materials-link">View Materials</a>` : ""}
            </div>
            <div class="rehearsal-row-actions">${actionButtons}</div>
        </div>
        ${absent ? `<p class="hint cancelled-badge" style="display:inline-block;margin-top:var(--space-2);">Marked absent</p>` : ""}
        ${subLine}
    `;

    card.querySelector(".cant-make-btn")?.addEventListener("click", () => markAbsent(r.id));
    card.querySelector(".undo-absent-btn")?.addEventListener("click", () => undoAbsent(r.id));
    card.querySelector(".find-sub-btn")?.addEventListener("click", () => openFindSubModal(r.id, r.date));
    container.appendChild(card);
}

async function markAbsent(rehearsalId) {
    if (!confirm("Mark yourself as absent for this rehearsal?")) return;
    try {
        await fetch(`${API}/choir/absence-request`, {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ rehearsal_id: rehearsalId, reason: null }),
        });
        myAbsences.add(rehearsalId);
        refreshRehearsalCard(rehearsalId);
    } catch (e) { alert("Server error."); }
}

async function undoAbsent(rehearsalId) {
    const sub = mySubStatus[rehearsalId];
    if (sub && sub.status === "filled") {
        const subName = sub.filled_by_name || "The confirmed sub";
        if (!confirm(`${subName} will be notified by email that they are no longer needed for this rehearsal. Continue?`)) return;
    } else {
        if (!confirm("Cancel your absence for this rehearsal?")) return;
    }
    try {
        await fetch(`${API}/choir/absence-request/${rehearsalId}`, {
            method: "DELETE", credentials: "include",
        });
        myAbsences.delete(rehearsalId);
        delete mySubStatus[rehearsalId];
        refreshRehearsalCard(rehearsalId);
    } catch (e) { alert("Server error."); }
}

function refreshRehearsalCard(rehearsalId) {
    Promise.all([
        fetch(`${API}/choir/rehearsals`, { credentials: "include" }).then(r => r.json()),
        fetch(`${API}/choir/my-sub-status`, { credentials: "include" }).then(r => r.json()),
    ]).then(([rehearsals, subStatuses]) => {
        (Array.isArray(subStatuses) ? subStatuses : []).forEach(s => {
            mySubStatus[s.rehearsal_id] = s;
        });
        const r = rehearsals.find(reh => reh.id === rehearsalId);
        if (!r) return;
        const old = document.querySelector(`[data-rehearsal-id="${rehearsalId}"]`);
        if (!old) return;
        const parent = old.parentElement;
        old.remove();
        const tmp = document.createElement("div");
        buildRehearsalCard(r, tmp);
        const allCards = [...parent.querySelectorAll(".rehearsal-card")];
        const nextCard = allCards.find(c => {
            const cid = Number(c.dataset.rehearsalId);
            const rd = rehearsals.find(x => x.id === cid);
            return rd && rd.date >= r.date && cid !== rehearsalId;
        });
        if (nextCard) parent.insertBefore(tmp.firstChild, nextCard);
        else parent.appendChild(tmp.firstChild);
    }).catch(console.error);
}


// ── Find-a-sub modal ──────────────────────────────────────────────────────────

async function openFindSubModal(rehearsalId, dateISO) {
    activeFindSubRehearsalId = rehearsalId;
    document.getElementById("find-sub-title").textContent = "Find a Sub";
    document.getElementById("find-sub-rehearsal").textContent = fmtDate(dateISO);
    document.getElementById("find-sub-list").innerHTML = `<em class="empty-note">Loading...</em>`;
    document.getElementById("find-sub-msg").textContent = "";
    document.getElementById("find-sub-modal").classList.remove("hidden");

    try {
        const res = await fetch(`${API}/choir/subs`, { credentials: "include" });
        const subs = await res.json();
        const list = document.getElementById("find-sub-list");

        if (!Array.isArray(subs) || !subs.length) {
            list.innerHTML = `<em class="empty-note">No subs have been added for your section yet.</em>`;
            return;
        }

        list.innerHTML = "";
        const preferred = subs.filter(s => s.is_preferred);
        const regular = subs.filter(s => !s.is_preferred);

        if (preferred.length) {
            const emailAllBtn = document.createElement("button");
            emailAllBtn.className = "slot-pill-btn";
            emailAllBtn.style.cssText = "width:100%;margin-bottom:var(--space-4);font-size:.88rem;";
            emailAllBtn.textContent = "Email All Preferred Subs";
            emailAllBtn.addEventListener("click", () => memberContactAllPreferred(rehearsalId, emailAllBtn));
            list.appendChild(emailAllBtn);

            const hdr = document.createElement("div");
            hdr.className = "section-group-title";
            hdr.textContent = "Preferred";
            list.appendChild(hdr);
            preferred.forEach(s => list.appendChild(buildMemberEmailSubRow(s, rehearsalId)));
        }
        if (regular.length) {
            const hdr = document.createElement("div");
            hdr.className = "section-group-title";
            hdr.style.marginTop = "var(--space-4)";
            hdr.textContent = "Regular";
            list.appendChild(hdr);
            regular.forEach(s => list.appendChild(buildMemberEmailSubRow(s, rehearsalId)));
        }
    } catch (e) {
        document.getElementById("find-sub-list").innerHTML = `<em class="empty-note">Failed to load.</em>`;
    }
}

function buildMemberEmailSubRow(sub, rehearsalId) {
    const row = document.createElement("div");
    row.className = "staff-row";
    row.innerHTML = `
        <div style="flex:1;">
            <div style="font-weight:600;">${escapeHtml(sub.fullname)}</div>
            <div style="font-size:.85rem;color:var(--text-muted);">${escapeHtml(sub.email)}${sub.phone ? " · " + escapeHtml(sub.phone) : ""}</div>
            ${sub.notes ? `<div style="font-size:.82rem;color:var(--text-muted);font-style:italic;">${escapeHtml(sub.notes)}</div>` : ""}
        </div>
        <button class="subtle-btn email-sub-btn">Email</button>
    `;
    const btn = row.querySelector(".email-sub-btn");
    btn.addEventListener("click", () => memberContactOneSub(sub.id, sub.section_id, rehearsalId, btn));
    return row;
}

async function memberContactOneSub(subId, sectionId, rehearsalId, btn) {
    btn.disabled = true;
    btn.textContent = "Sending...";
    try {
        const res = await fetch(`${API}/choir/contact-sub`, {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                rehearsal_id: Number(rehearsalId),
                section_id: Number(sectionId),
                sub_id: subId,
            }),
        });
        const data = await res.json();
        if (data.status === "success") {
            btn.textContent = "Sent";
            btn.style.color = "var(--success)";
        } else {
            btn.textContent = data.message === "Already contacted" ? "Already sent" : "Failed";
            btn.disabled = false;
        }
    } catch (e) {
        btn.textContent = "Error";
        btn.disabled = false;
    }
}


async function memberContactAllPreferred(rehearsalId, btn) {
    btn.disabled = true;
    btn.textContent = "Sending...";
    try {
        const res = await fetch(`${API}/choir/contact-preferred-subs`, {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ rehearsal_id: Number(rehearsalId) }),
        });
        const data = await res.json();
        if (data.status === "success") {
            btn.textContent = `Sent to ${data.sent} preferred sub${data.sent !== 1 ? "s" : ""}`;
            btn.style.background = "var(--success,#2f8f6a)";
        } else {
            btn.textContent = data.message || "Failed";
            btn.disabled = false;
        }
    } catch (e) {
        btn.textContent = "Error";
        btn.disabled = false;
    }
}


// ── My Subs tab ───────────────────────────────────────────────────────────────

async function loadMySubs() {
    const list = document.getElementById("my-subs-list");
    list.innerHTML = `<em class="empty-note">Loading…</em>`;
    try {
        const res = await fetch(`${API}/choir/subs`, { credentials: "include" });
        const subs = await res.json();

        if (!subs.length) {
            list.innerHTML = `<em class="empty-note">No subs have been added for your section yet.</em>`;
            return;
        }

        const preferred = subs.filter(s => s.is_preferred);
        const regular = subs.filter(s => !s.is_preferred);

        list.innerHTML = "";
        if (preferred.length) {
            const h = document.createElement("div");
            h.className = "manage-voice-header";
            h.textContent = "Preferred Subs";
            list.appendChild(h);
            preferred.forEach(s => list.appendChild(buildMemberSubRow(s)));
        }
        if (regular.length) {
            const h = document.createElement("div");
            h.className = "manage-voice-header";
            h.textContent = "Regular Subs";
            list.appendChild(h);
            regular.forEach(s => list.appendChild(buildMemberSubRow(s)));
        }
    } catch (e) {
        list.innerHTML = `<em class="empty-note">Failed to load.</em>`;
    }
}

function buildMemberSubRow(s) {
    const row = document.createElement("div");
    row.className = "staff-row";
    row.style.padding = "var(--space-2) 0";
    row.innerHTML = `
        <div style="flex:1;">
            <div style="font-weight:600;">
                ${s.is_preferred ? "★ " : ""}${escapeHtml(s.fullname)}
            </div>
            <div style="font-size:.85rem;color:var(--text-muted);">
                <a href="mailto:${escapeHtml(s.email)}" style="color:var(--accent);">${escapeHtml(s.email)}</a>
                ${s.phone ? ` · <a href="tel:${escapeHtml(s.phone)}" style="color:var(--text-muted);">${escapeHtml(s.phone)}</a>` : ""}
            </div>
            ${s.notes ? `<div style="font-size:.82rem;color:var(--text-faint);font-style:italic;">${escapeHtml(s.notes)}</div>` : ""}
        </div>
    `;
    return row;
}


// ── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
    await ME_READY;
    if (!USERNAME) return;

    // Welcome
    const label = document.getElementById("upcoming-section-label");
    if (label) label.textContent = "Rehearsals your section is called for.";

    // Tabs
    document.querySelectorAll(".tab-btn").forEach(btn =>
        btn.addEventListener("click", () => setActiveTab(btn.dataset.tab)));
    setActiveTab(getTabFromURL());
    window.addEventListener("hashchange", () => setActiveTab(getTabFromURL()));

    // Find-sub modal
    document.getElementById("find-sub-modal").addEventListener("click", e => {
        if (e.target.id === "find-sub-modal")
            document.getElementById("find-sub-modal").classList.add("hidden");
    });
    document.getElementById("find-sub-close").addEventListener("click", () =>
        document.getElementById("find-sub-modal").classList.add("hidden"));

    // Calendar subscription URL
    fetch(`${API}/choir/my-calendar-token`, { credentials: "include" })
        .then(r => r.json())
        .then(data => {
            const input = document.getElementById("calendar-url");
            if (input && data.token) {
                input.value = `${window.location.origin}/choir/calendar/${data.token}.ics`;
            }
            const copyBtn = document.getElementById("copy-calendar-url-btn");
            if (copyBtn) {
                copyBtn.addEventListener("click", () => {
                    navigator.clipboard.writeText(input.value).then(() => {
                        copyBtn.textContent = "Copied!";
                        setTimeout(() => { copyBtn.textContent = "Copy"; }, 2000);
                    });
                });
            }
        })
        .catch(() => {});

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

    // Initial load
    loadUpcoming();

    // Refresh when tab becomes visible
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") loadUpcoming();
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
    const msgs = dmView === "inbox" ? dmInbox : dmSent;
    if (!msgs.length) { list.innerHTML = `<em class="empty-note">No messages yet.</em>`; return; }
    list.innerHTML = "";
    msgs.forEach(m => {
        const isUnread = dmView === "inbox" && !m.read_at;
        const card = document.createElement("div");
        card.className = "dm-card" + (isUnread ? " dm-card--unread" : "");
        const ts = m.created_at ? new Date(m.created_at).toLocaleString(undefined,
            { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";
        let metaLabel = "";
        if (dmView === "inbox") {
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
        `;
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
