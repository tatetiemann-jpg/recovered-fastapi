// ======================================================
// CHOIR MEMBER DASHBOARD
// ======================================================

const VALID_CHOIR_MEMBER_TABS = ["upcoming", "subs"];

let myAbsences = new Set();   // rehearsal IDs the member has marked absent
let mySubRequestId = null;    // active sub request in the modal
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
        const [rehRes, absRes] = await Promise.all([
            fetch(`${API}/choir/rehearsals`, { credentials: "include" }),
            fetch(`${API}/choir/my-absences`, { credentials: "include" }),
        ]);
        const rehearsals = await rehRes.json();
        const absentIds = await absRes.json();
        myAbsences = new Set(absentIds);

        if (!rehearsals.length) {
            list.innerHTML = `<em class="empty-note">No upcoming rehearsals scheduled.</em>`;
            return;
        }

        list.innerHTML = "";
        rehearsals.forEach(r => buildRehearsalCard(r, list));
    } catch (e) {
        console.error(e);
        list.innerHTML = `<em class="empty-note">Failed to load rehearsals.</em>`;
    }
}

function buildRehearsalCard(r, container) {
    const absent = myAbsences.has(r.id);
    const card = document.createElement("div");
    card.className = `rehearsal-card${absent ? " teacher-card-cancelled" : ""}`;
    card.dataset.rehearsalId = r.id;

    const timeRange = fmtTime(r.start_time) + (r.end_time ? ` – ${fmtTime(r.end_time)}` : "");

    card.innerHTML = `
        <div class="rehearsal-row-header">
            <div>
                <strong>${fmtDate(r.date)}</strong>
                <div class="rehearsal-roles">${timeRange}</div>
                ${r.location ? `<div class="rehearsal-cast">${escapeHtml(r.location)}</div>` : ""}
                ${r.notes ? `<em class="rehearsal-leaders">${escapeHtml(r.notes)}</em>` : ""}
            </div>
            <div class="rehearsal-row-actions">
                ${absent
                    ? `<button class="subtle-btn undo-absent-btn" data-id="${r.id}">I can attend</button>
                       <button class="slot-pill-btn find-sub-btn" data-id="${r.id}"
                           style="font-size:.82rem;padding:6px 12px;">Find a sub</button>`
                    : `<button class="cancel-lesson-btn cant-make-btn" data-id="${r.id}">I can't make it</button>`
                }
            </div>
        </div>
        ${absent ? `<p class="hint cancelled-badge" style="display:inline-block;margin-top:var(--space-2);">Marked absent</p>` : ""}
    `;

    card.querySelector(".cant-make-btn")?.addEventListener("click", () => markAbsent(r.id));
    card.querySelector(".undo-absent-btn")?.addEventListener("click", () => undoAbsent(r.id));
    card.querySelector(".find-sub-btn")?.addEventListener("click", () => openFindSubModal(r.id, r.date));
    container.appendChild(card);
}

async function markAbsent(rehearsalId) {
    const reason = prompt("Reason (optional):") ?? null;
    if (reason === null) return; // user cancelled
    try {
        await fetch(`${API}/choir/absence-request`, {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ rehearsal_id: rehearsalId, reason }),
        });
        myAbsences.add(rehearsalId);
        refreshRehearsalCard(rehearsalId);
    } catch (e) { alert("Server error."); }
}

async function undoAbsent(rehearsalId) {
    if (!confirm("Cancel your absence for this rehearsal?")) return;
    try {
        await fetch(`${API}/choir/absence-request/${rehearsalId}`, {
            method: "DELETE", credentials: "include",
        });
        myAbsences.delete(rehearsalId);
        refreshRehearsalCard(rehearsalId);
    } catch (e) { alert("Server error."); }
}

function refreshRehearsalCard(rehearsalId) {
    // Re-fetch and redraw just this card
    fetch(`${API}/choir/rehearsals`, { credentials: "include" })
        .then(r => r.json())
        .then(rehearsals => {
            const r = rehearsals.find(reh => reh.id === rehearsalId);
            if (!r) return;
            const old = document.querySelector(`[data-rehearsal-id="${rehearsalId}"]`);
            if (!old) return;
            const parent = old.parentElement;
            old.remove();
            const tmp = document.createElement("div");
            buildRehearsalCard(r, tmp);
            // Insert in same position as original
            const allCards = [...parent.querySelectorAll(".rehearsal-card")];
            const nextCard = allCards.find(c => {
                const cid = Number(c.dataset.rehearsalId);
                const rd = rehearsals.find(x => x.id === cid);
                return rd && rd.date >= r.date && cid !== rehearsalId;
            });
            if (nextCard) parent.insertBefore(tmp.firstChild, nextCard);
            else parent.appendChild(tmp.firstChild);
        })
        .catch(console.error);
}


// ── Find-a-sub modal ──────────────────────────────────────────────────────────

async function openFindSubModal(rehearsalId, dateISO) {
    activeFindSubRehearsalId = rehearsalId;
    mySubRequestId = null;
    const modal = document.getElementById("find-sub-modal");
    const title = document.getElementById("find-sub-title");
    const rehLabel = document.getElementById("find-sub-rehearsal");
    const statusBox = document.getElementById("find-sub-status-box");
    const msg = document.getElementById("find-sub-msg");

    title.textContent = "Find a Sub";
    rehLabel.textContent = fmtDate(dateISO);
    statusBox.style.display = "none";
    msg.textContent = "";
    document.getElementById("contact-preferred-btn").disabled = false;
    document.getElementById("contact-all-btn").classList.add("hidden");
    modal.classList.remove("hidden");

    // Create (or retrieve existing) sub request for my section
    try {
        const res = await fetch(`${API}/choir/sub-request`, {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ rehearsal_id: rehearsalId }),
        });
        const data = await res.json();
        if (data.sub_request_id) {
            mySubRequestId = data.sub_request_id;
            await refreshFindSubStatus();
        }
    } catch (e) { console.error(e); }
}

async function refreshFindSubStatus() {
    if (!mySubRequestId) return;
    try {
        const res = await fetch(`${API}/choir/sub-request/${mySubRequestId}`, { credentials: "include" });
        const data = await res.json();
        const statusBox = document.getElementById("find-sub-status-box");
        const label = document.getElementById("find-sub-status-label");
        const contactsList = document.getElementById("find-sub-contacts-list");
        const preferredBtn = document.getElementById("contact-preferred-btn");
        const allBtn = document.getElementById("contact-all-btn");

        if (data.status === "filled") {
            label.textContent = `✓ Sub confirmed: ${data.filled_by_name}`;
            label.style.color = "var(--success)";
            preferredBtn.disabled = true;
            allBtn.classList.add("hidden");
            statusBox.style.display = "block";
            contactsList.innerHTML = "";
            return;
        }

        if (data.contacts.length) {
            statusBox.style.display = "block";
            label.textContent = "Contact status:";
            label.style.color = "";
            contactsList.innerHTML = "";

            const preferred = data.contacts.filter(c => c.is_preferred);
            const regular = data.contacts.filter(c => !c.is_preferred);
            if (preferred.length) {
                contactsList.insertAdjacentHTML("beforeend",
                    `<div class="manage-voice-header" style="font-size:.75rem;">Preferred</div>`);
                preferred.forEach(c => contactsList.insertAdjacentHTML("beforeend",
                    renderContactLine(c)));
            }
            if (regular.length) {
                contactsList.insertAdjacentHTML("beforeend",
                    `<div class="manage-voice-header" style="font-size:.75rem;">Regular</div>`);
                regular.forEach(c => contactsList.insertAdjacentHTML("beforeend",
                    renderContactLine(c)));
            }

            // Show "contact all" once preferred have been sent
            if (data.preferred_sent_at) {
                allBtn.classList.remove("hidden");
                preferredBtn.disabled = true;
                preferredBtn.textContent = "Preferred contacted";
            }
        }
    } catch (e) { console.error(e); }
}

function renderContactLine(c) {
    const color = { accepted: "var(--success)", declined: "var(--danger)", pending: "var(--text-muted)" }[c.response];
    const label = { accepted: "Accepted ✓", declined: "Declined ✗", pending: "Pending…" }[c.response];
    return `<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:.88rem;">
        <span>${escapeHtml(c.name)}</span>
        <span style="color:${color};">${label}</span>
    </div>`;
}

async function memberContactPreferred() {
    const msg = document.getElementById("find-sub-msg");
    msg.textContent = "";
    try {
        const res = await fetch(`${API}/choir/sub-request/${mySubRequestId}/contact-preferred`, {
            method: "POST", credentials: "include",
        });
        const data = await res.json();
        if (data.status === "success") {
            msg.className = "msg success-msg";
            msg.textContent = `Emails sent to ${data.sent} preferred sub(s). They'll reply directly.`;
            await refreshFindSubStatus();
        } else {
            msg.className = "msg"; msg.textContent = data.message || "Failed.";
        }
    } catch (e) { msg.textContent = "Server error."; }
}

async function memberContactAll() {
    const msg = document.getElementById("find-sub-msg");
    msg.textContent = "";
    try {
        const res = await fetch(`${API}/choir/sub-request/${mySubRequestId}/contact-all`, {
            method: "POST", credentials: "include",
        });
        const data = await res.json();
        if (data.status === "success") {
            msg.className = "msg success-msg";
            msg.textContent = `Emails sent to ${data.sent} additional sub(s).`;
            await refreshFindSubStatus();
        } else {
            msg.className = "msg"; msg.textContent = data.message || "Failed.";
        }
    } catch (e) { msg.textContent = "Server error."; }
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
    document.getElementById("contact-preferred-btn").addEventListener("click", memberContactPreferred);
    document.getElementById("contact-all-btn").addEventListener("click", memberContactAll);

    // Initial load
    loadUpcoming();

    // Refresh when tab becomes visible
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") loadUpcoming();
    });
});
