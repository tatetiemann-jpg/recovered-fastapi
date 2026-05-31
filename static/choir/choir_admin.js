// ======================================================
// CHOIR ADMIN DASHBOARD
// ======================================================

const VALID_CHOIR_ADMIN_TABS = ["schedule", "upcoming", "subs", "sections", "ensemble", "messages", "invitations"];

let choirSections = [];
let choirRehearsals = [];
let activeRehearsalId = null;
let activeChoirNotesId = null;
let activeChoirEditId = null;
let allChoirMembersCache = null;


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


// ── Tab switching ────────────────────────────────────────────────────────────

function setActiveTab(tabName) {
    if (!VALID_CHOIR_ADMIN_TABS.includes(tabName)) tabName = "schedule";
    document.querySelectorAll(".tab-btn").forEach(btn =>
        btn.classList.toggle("active", btn.dataset.tab === tabName));
    document.querySelectorAll(".tab-panel").forEach(panel =>
        panel.classList.toggle("active", panel.dataset.tabPanel === tabName));
    if (window.location.hash !== `#${tabName}`)
        history.replaceState(null, "", `#${tabName}`);
    if (tabName === "upcoming") loadUpcoming();
    if (tabName === "subs") loadSubRoster();
    if (tabName === "sections") loadSections();
    if (tabName === "ensemble") loadEnsembleMembers();
    if (tabName === "messages") loadDmTab();
    if (tabName === "invitations") loadInvitations();
}

function getTabFromURL() {
    const hash = window.location.hash.replace("#", "");
    return VALID_CHOIR_ADMIN_TABS.includes(hash) ? hash : "schedule";
}


// ── Sections (shared data) ───────────────────────────────────────────────────

async function loadSectionsData() {
    try {
        const res = await fetch(`${API}/choir/sections`, { credentials: "include" });
        const data = await res.json();
        choirSections = Array.isArray(data) ? data : [];
        populateSectionSelects();
    } catch (e) { console.error(e); }
    await renderUnifiedCaller("unified-caller");
}


function populateSectionSelects() {
    const selects = ["sub-section-filter", "new-sub-section"];
    selects.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        const current = el.value;
        const placeholder = id === "sub-section-filter" ? "— All sections —" : "— select —";
        el.innerHTML = `<option value="">${placeholder}</option>`;
        choirSections.forEach(s => {
            const opt = document.createElement("option");
            opt.value = s.id;
            opt.textContent = s.name;
            el.appendChild(opt);
        });
        if (current) el.value = current;
    });

}

// ── Unified caller (sections + individual members combined) ──────────────────

async function renderUnifiedCaller(containerId, preCheckedSectionIds = [], preCheckedMemberIds = []) {
    const box = document.getElementById(containerId);
    if (!box) return;

    if (!allChoirMembersCache) {
        try {
            const res = await fetch(`${API}/choir/members/all`, { credentials: "include" });
            allChoirMembersCache = await res.json();
        } catch (e) {
            box.innerHTML = `<em class="empty-note">Failed to load members.</em>`;
            return;
        }
    }

    const radioName = containerId === "edit-unified-caller" ? "edit-reh-choir-type" : "reh-choir-type";
    const type = document.querySelector(`input[name='${radioName}']:checked`)?.value || "choir";
    box.innerHTML = "";

    if (type === "choir") {
        const members = allChoirMembersCache.filter(m => m.role === "choir_member");
        if (!members.length) {
            box.innerHTML = `<em class="empty-note">No choir members yet.</em>`;
            return;
        }

        const SATB_ORDER = ["soprano", "alto", "tenor", "bass", "baritone", "mezzo", "mezzo-soprano", "countertenor"];
        const satbKey = name => {
            const n = name.toLowerCase();
            const idx = SATB_ORDER.findIndex(v => n.includes(v));
            return idx < 0 ? 99 : idx;
        };
        const sortedSections = [...choirSections].sort((a, b) => satbKey(a.name) - satbKey(b.name) || a.name.localeCompare(b.name));

        const bySection = new Map();
        sortedSections.forEach(s => bySection.set(s.name, { sectionId: s.id, members: [] }));
        members.forEach(m => {
            const key = m.section_name || "Other";
            if (!bySection.has(key)) bySection.set(key, { sectionId: m.section_id, members: [] });
            bySection.get(key).members.push(m);
        });

        bySection.forEach(({ sectionId, members: list }, sectionName) => {
            if (!list.length) return;

            const allPreSelected = sectionId != null && preCheckedSectionIds.includes(sectionId);

            const group = document.createElement("div");
            group.className = "unified-section-group";
            if (sectionId) group.dataset.sectionId = sectionId;

            // Header: [chevron | name + count | Select All btn]
            const header = document.createElement("div");
            header.className = "unified-section-header";

            const chevron = document.createElement("button");
            chevron.type = "button";
            chevron.className = "section-chevron-btn";
            chevron.setAttribute("aria-label", "Expand section");
            chevron.textContent = "▶";

            const nameArea = document.createElement("span");
            nameArea.className = "section-name-area";

            const nameSpan = document.createElement("span");
            nameSpan.className = "section-name";
            nameSpan.textContent = sectionName;

            const countSpan = document.createElement("span");
            countSpan.className = "section-count";
            countSpan.textContent = `(${list.length})`;

            nameArea.appendChild(nameSpan);
            nameArea.appendChild(countSpan);

            const selectAllBtn = document.createElement("button");
            selectAllBtn.type = "button";
            selectAllBtn.className = "section-select-all-btn";
            selectAllBtn.textContent = "Select all";

            header.appendChild(chevron);
            header.appendChild(nameArea);
            header.appendChild(selectAllBtn);
            group.appendChild(header);

            const inner = document.createElement("div");
            inner.className = "unified-section-inner";
            if (!allPreSelected) inner.classList.add("hidden");
            else group.classList.add("open");

            list.forEach(m => {
                const pill = document.createElement("span");
                pill.className = "caller-pill";
                pill.dataset.memberId = m.id;
                pill.textContent = m.fullname;
                if (allPreSelected || preCheckedMemberIds.includes(m.id)) pill.classList.add("selected");
                pill.addEventListener("click", () => {
                    pill.classList.toggle("selected");
                    updateSectionCount(header, inner, list.length);
                });
                inner.appendChild(pill);
            });
            group.appendChild(inner);

            // Chevron: expand/collapse only
            chevron.addEventListener("click", () => {
                const isOpen = group.classList.toggle("open");
                inner.classList.toggle("hidden", !isOpen);
                chevron.setAttribute("aria-label", isOpen ? "Collapse section" : "Expand section");
            });

            // Select All btn: toggle all members on/off
            selectAllBtn.addEventListener("click", () => {
                const pills = [...inner.querySelectorAll(".caller-pill")];
                const allOn = pills.every(p => p.classList.contains("selected"));
                pills.forEach(p => p.classList.toggle("selected", !allOn));
                selectAllBtn.textContent = allOn ? "Select all" : "Deselect all";
                updateSectionCount(header, inner, list.length);
                if (!allOn) {
                    group.classList.add("open");
                    inner.classList.remove("hidden");
                }
            });

            // Initial count
            updateSectionCount(header, inner, list.length);
            box.appendChild(group);
        });
    } else {
        const members = allChoirMembersCache.filter(m => m.role === "ensemble_member");
        if (!members.length) {
            box.innerHTML = `<em class="empty-note">No ensemble members yet.</em>`;
            return;
        }
        const wrap = document.createElement("div");
        wrap.className = "caller-flat-list";
        members.forEach(m => {
            const pill = document.createElement("span");
            pill.className = "caller-pill";
            pill.dataset.memberId = m.id;
            pill.textContent = m.fullname + (m.instrument ? ` (${m.instrument})` : "");
            if (preCheckedMemberIds.includes(m.id)) pill.classList.add("selected");
            pill.addEventListener("click", () => pill.classList.toggle("selected"));
            wrap.appendChild(pill);
        });
        box.appendChild(wrap);
    }
}

function updateSectionCount(header, inner, total) {
    const n = inner.querySelectorAll(".caller-pill.selected").length;
    const countSpan = header.querySelector(".section-count");
    if (countSpan) countSpan.textContent = n > 0 ? `(${n} / ${total})` : `(${total})`;
    header.classList.toggle("some-selected", n > 0 && n < total);
    header.classList.toggle("all-selected", n > 0 && n === total);
}

function collectFromUnifiedCaller(containerId) {
    const box = document.getElementById(containerId);
    const sections = [];
    const members = [];
    box.querySelectorAll(".unified-section-group").forEach(group => {
        const sectionId = group.dataset.sectionId ? Number(group.dataset.sectionId) : null;
        const pills = [...group.querySelectorAll(".caller-pill")];
        const selected = pills.filter(p => p.classList.contains("selected"));
        if (!selected.length) return;
        if (sectionId && selected.length === pills.length) {
            sections.push(sectionId);
        } else {
            selected.forEach(p => members.push(Number(p.dataset.memberId)));
        }
    });
    // Ensemble flat members
    box.querySelectorAll(".caller-flat-list .caller-pill.selected").forEach(p => {
        members.push(Number(p.dataset.memberId));
    });
    return { sections, members };
}


// ── Schedule tab ─────────────────────────────────────────────────────────────

async function createRehearsal() {
    const msg = document.getElementById("reh-msg");
    msg.textContent = "";
    const date = document.getElementById("reh-date").value;
    const start = document.getElementById("reh-start").value;
    const end = document.getElementById("reh-end").value;
    const location = document.getElementById("reh-location").value.trim();
    const notes = document.getElementById("reh-notes").value.trim();
    const { sections, members } = collectFromUnifiedCaller("unified-caller");
    const choir_type = document.querySelector("input[name='reh-choir-type']:checked")?.value || "choir";
    const materials_url = document.getElementById("reh-materials-url").value.trim();

    if (!date || !start) { msg.textContent = "Date and start time are required."; return; }

    try {
        const res = await fetch(`${API}/choir/rehearsals`, {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ date, start_time: start, end_time: end || null, location, notes, sections, members, choir_type, materials_url: materials_url || null }),
        });
        const data = await res.json();
        if (data.status === "success") {
            msg.className = "msg success-msg";
            msg.textContent = "Rehearsal scheduled!";
            document.getElementById("reh-date").value = "";
            document.getElementById("reh-start").value = "";
            document.getElementById("reh-end").value = "";
            document.getElementById("reh-location").value = "";
            document.getElementById("reh-notes").value = "";
            document.getElementById("reh-materials-url").value = "";
            document.querySelectorAll("#unified-caller .caller-pill").forEach(p => p.classList.remove("selected"));
            document.querySelectorAll("#unified-caller .unified-section-group").forEach(g => {
                g.classList.remove("open", "some-selected", "all-selected");
                g.querySelector(".unified-section-inner")?.classList.add("hidden");
                const count = g.querySelector(".section-count");
                if (count) {
                    const total = g.querySelectorAll(".caller-pill").length;
                    count.textContent = `(${total})`;
                }
            });
        } else {
            msg.className = "msg";
            msg.textContent = data.message || "Failed to schedule.";
        }
    } catch (e) { console.error(e); msg.textContent = "Server error."; }
}


// ── Upcoming tab ─────────────────────────────────────────────────────────────

async function loadUpcoming() {
    const list = document.getElementById("upcoming-list");
    list.innerHTML = `<em class="empty-note">Loading…</em>`;
    try {
        const res = await fetch(`${API}/choir/rehearsals`, { credentials: "include" });
        const rehearsals = await res.json();
        choirRehearsals = Array.isArray(rehearsals) ? rehearsals : [];
        if (!choirRehearsals.length) {
            list.innerHTML = `<em class="empty-note">No upcoming rehearsals.</em>`;
            return;
        }
        list.innerHTML = "";

        const now = new Date();
        const currentKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;

        const byMonth = {};
        choirRehearsals.forEach(r => {
            const d = new Date(r.date + "T00:00:00");
            const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
            const label = d.toLocaleDateString(undefined, { month: "long", year: "numeric" });
            if (!byMonth[key]) byMonth[key] = { label, rehearsals: [] };
            byMonth[key].rehearsals.push(r);
        });

        Object.entries(byMonth).forEach(([key, { label, rehearsals: group }]) => {
            const open = key === currentKey;

            const monthHdr = document.createElement("div");
            monthHdr.className = "upcoming-month-header";
            monthHdr.innerHTML = `<span class="month-chevron">${open ? "▼" : "▶"}</span> ${label}`;

            const body = document.createElement("div");
            body.className = "upcoming-month-body";
            if (!open) body.classList.add("collapsed");

            monthHdr.addEventListener("click", () => {
                const nowCollapsed = body.classList.toggle("collapsed");
                monthHdr.querySelector(".month-chevron").textContent = nowCollapsed ? "▶" : "▼";
            });

            list.appendChild(monthHdr);

            group.forEach(r => {
                const isEnsemble = r.choir_type === "ensemble";
                const calledNames = choirSections
                    .filter(s => r.called_sections.includes(s.id))
                    .map(s => s.name).join(", ") || (isEnsemble ? "Full ensemble" : "Full choir");
                const typeLabel = isEnsemble ? "Contemporary Ensemble" : "Choir";

                const card = document.createElement("div");
                card.className = "rehearsal-card";
                card.dataset.rehearsalId = r.id;
                card.innerHTML = `
                    <div class="rehearsal-row-header">
                        <div>
                            <strong>${fmtDate(r.date)}</strong>
                            <span class="rehearsal-type-badge ${isEnsemble ? "badge-ensemble" : "badge-choir"}">${typeLabel}</span>
                            <div class="rehearsal-roles">${fmtTime(r.start_time)}${r.end_time ? " – " + fmtTime(r.end_time) : ""}</div>
                            ${r.location ? `<div class="rehearsal-cast">${escapeHtml(r.location)}</div>` : ""}
                            <div class="rehearsal-cast">${escapeHtml(calledNames)}</div>
                            ${r.notes ? `<em class="rehearsal-notes-preview">${renderNotes(r.notes)}</em>` : ""}
                            ${r.materials_url ? `<a href="${escapeHtml(r.materials_url)}" target="_blank" rel="noopener" class="materials-link">View Materials</a>` : ""}
                        </div>
                        <div class="rehearsal-row-actions">
                            <button class="subtle-btn reh-notes-btn" data-id="${r.id}">Rehearsal Notes</button>
                            <button class="subtle-btn edit-reh-btn" data-id="${r.id}">Edit</button>
                            <button class="subtle-btn view-absences-btn" data-id="${r.id}" data-date="${r.date}">
                                Absences &amp; Subs
                            </button>
                            <button class="subtle-btn danger-btn delete-reh-btn" data-id="${r.id}">
                                Delete
                            </button>
                        </div>
                    </div>
                    ${(r.individual_members && r.individual_members.length) ? `
                    <div class="individual-members-section">
                        <div class="individual-members-header">
                            <span class="individual-members-label">Individually called</span>
                        </div>
                        <div class="individual-members-list">${renderIndividualMembers(r.individual_members)}</div>
                    </div>` : ""}
                `;
                card.querySelector(".reh-notes-btn").addEventListener("click", () => openChoirNotesModal(r.id));
                card.querySelector(".edit-reh-btn").addEventListener("click", () => openChoirEditModal(r.id));
                card.querySelector(".view-absences-btn").addEventListener("click", () => openAbsenceModal(r.id, r.date));
                card.querySelector(".delete-reh-btn").addEventListener("click", () => deleteRehearsal(r.id));
                body.appendChild(card);
            });
            list.appendChild(body);
        });
    } catch (e) { console.error(e); list.innerHTML = `<em class="empty-note">Failed to load.</em>`; }
}

// ── Choir rehearsal notes ────────────────────────────────────────────────────

function openChoirNotesModal(id) {
    activeChoirNotesId = Number(id);
    const r = choirRehearsals.find(x => x.id === activeChoirNotesId);
    document.getElementById("choir-notes-modal-title").textContent =
        r ? `Rehearsal Notes — ${fmtDate(r.date)}` : "Rehearsal Notes";
    renderChoirNotesExisting(r ? r.notes : null);
    document.getElementById("choir-notes-textarea").value = r ? (r.notes || "") : "";
    document.getElementById("choir-notes-msg").textContent = "";
    document.getElementById("choir-notes-modal").classList.remove("hidden");
}

function renderChoirNotesExisting(notes) {
    const existing = document.getElementById("choir-notes-existing");
    if (notes) {
        existing.innerHTML = `
            <div class="notes-existing-wrap">
                <div class="notes-existing-text">${notes.split("\n").map(p => `<p>${renderNotes(p)}</p>`).join("")}</div>
                <button type="button" class="notes-delete-btn" title="Delete notes" aria-label="Delete notes">&#x2715;</button>
            </div>`;
        existing.querySelector(".notes-delete-btn").addEventListener("click", deleteChoirNotes);
        existing.classList.remove("hidden");
    } else {
        existing.innerHTML = "";
        existing.classList.add("hidden");
    }
}

async function deleteChoirNotes() {
    if (!confirm("Delete these rehearsal notes?")) return;
    try {
        await fetch(`${API}/choir/rehearsals/${activeChoirNotesId}/notes`, {
            method: "DELETE", credentials: "include",
        });
        const r = choirRehearsals.find(x => x.id === activeChoirNotesId);
        if (r) r.notes = null;
        renderChoirNotesExisting(null);
        document.getElementById("choir-notes-textarea").value = "";
        document.getElementById("choir-notes-msg").textContent = "";
    } catch (e) { console.error(e); }
}

async function sendChoirNotes() {
    const notes = document.getElementById("choir-notes-textarea").value.trim();
    const msg = document.getElementById("choir-notes-msg");
    msg.textContent = "";
    if (!notes) { msg.textContent = "Notes cannot be empty."; return; }
    const btn = document.getElementById("send-choir-notes-btn");
    btn.disabled = true;
    btn.textContent = "Sending…";
    try {
        const res = await fetch(`${API}/choir/rehearsals/${activeChoirNotesId}/notes`, {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ notes }),
        });
        const data = await res.json();
        if (data.status === "success") {
            msg.className = "msg success-msg";
            msg.textContent = `Notes sent to ${data.emailed} recipient${data.emailed !== 1 ? "s" : ""}.`;
            const r = choirRehearsals.find(x => x.id === activeChoirNotesId);
            if (r) r.notes = notes;
            setTimeout(() => document.getElementById("choir-notes-modal").classList.add("hidden"), 1500);
        } else {
            msg.className = "msg";
            msg.textContent = data.message || "Failed.";
        }
    } catch (e) { msg.textContent = "Server error."; }
    finally { btn.disabled = false; btn.textContent = "Send Notes"; }
}


// ── Choir rehearsal edit ──────────────────────────────────────────────────────

async function openChoirEditModal(id) {
    activeChoirEditId = Number(id);
    const r = choirRehearsals.find(x => x.id === activeChoirEditId);
    if (!r) return;
    document.getElementById("edit-choir-reh-start").value = r.start_time || "";
    document.getElementById("edit-choir-reh-end").value = r.end_time || "";
    document.getElementById("edit-choir-reh-location").value = r.location || "";
    document.getElementById("edit-choir-reh-notes").value = r.notes || "";
    document.getElementById("edit-choir-reh-materials").value = r.materials_url || "";
    document.getElementById("choir-reh-edit-msg").textContent = "";
    const ctype = r.choir_type || "choir";
    document.querySelectorAll("input[name='edit-reh-choir-type']").forEach(rb => {
        rb.checked = rb.value === ctype;
    });
    const preMembers = (r.individual_members || []).map(m => m.id);
    await renderUnifiedCaller("edit-unified-caller", r.called_sections || [], preMembers);
    document.getElementById("choir-reh-edit-modal").classList.remove("hidden");
}

async function saveChoirRehearsalEdit() {
    const msg = document.getElementById("choir-reh-edit-msg");
    msg.textContent = "";
    const start_time = document.getElementById("edit-choir-reh-start").value;
    const end_time = document.getElementById("edit-choir-reh-end").value;
    const location = document.getElementById("edit-choir-reh-location").value.trim();
    const notes = document.getElementById("edit-choir-reh-notes").value.trim();
    const { sections, members } = collectFromUnifiedCaller("edit-unified-caller");
    const choir_type = document.querySelector("input[name='edit-reh-choir-type']:checked")?.value || "choir";
    const materials_url = document.getElementById("edit-choir-reh-materials").value.trim();
    if (!start_time) { msg.textContent = "Start time is required."; return; }
    const btn = document.getElementById("save-choir-reh-edit-btn");
    btn.disabled = true;
    btn.textContent = "Saving…";
    try {
        const res = await fetch(`${API}/choir/rehearsals/${activeChoirEditId}`, {
            method: "PUT", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ start_time, end_time: end_time || null, location, notes, sections, members, choir_type, materials_url: materials_url || null }),
        });
        const data = await res.json();
        if (data.status === "success") {
            document.getElementById("choir-reh-edit-modal").classList.add("hidden");
            await loadUpcoming();
        } else {
            msg.className = "msg"; msg.textContent = data.message || "Failed.";
        }
    } catch (e) { msg.textContent = "Server error."; }
    finally { btn.disabled = false; btn.textContent = "Save Changes"; }
}


// ── Individual member calling ─────────────────────────────────────────────────

function renderIndividualMembers(members) {
    if (!members || !members.length) return "";
    return members.map(m =>
        `<span class="individual-member-chip">
            ${escapeHtml(m.fullname)}${m.instrument ? ` <em>(${escapeHtml(m.instrument)})</em>` : ""}
        </span>`
    ).join("");
}


async function deleteRehearsal(id) {
    if (!confirm("Delete this rehearsal? This cannot be undone.")) return;
    try {
        await fetch(`${API}/choir/rehearsal/${id}`, { method: "DELETE", credentials: "include" });
        loadUpcoming();
    } catch (e) { alert("Server error."); }
}


// ── Absence modal ─────────────────────────────────────────────────────────────

async function openAbsenceModal(rehearsalId, dateISO) {
    activeRehearsalId = rehearsalId;
    document.getElementById("absence-modal-title").textContent = `Absences — ${fmtDate(dateISO)}`;
    document.getElementById("absence-modal").classList.remove("hidden");
    document.getElementById("absence-modal-list").innerHTML = `<em class="empty-note">Loading…</em>`;

    try {
        const [absRes, subRes] = await Promise.all([
            fetch(`${API}/choir/absences/${rehearsalId}`, { credentials: "include" }),
            fetch(`${API}/choir/sub-requests/${rehearsalId}`, { credentials: "include" }),
        ]);
        const absences = await absRes.json();
        const subReqs = await subRes.json();
        renderAbsenceList(absences, Array.isArray(subReqs) ? subReqs : []);
    } catch (e) { console.error(e); }
}

function renderAbsenceList(absences, subReqs) {
    const box = document.getElementById("absence-modal-list");
    if (!absences.length) {
        box.innerHTML = `<em class="empty-note">No absences reported.</em>`;
        return;
    }

    // Build lookup: section_id -> sub_request
    const subBySec = {};
    (subReqs || []).forEach(r => { subBySec[r.section_id] = r; });

    box.innerHTML = "";
    const bySec = {};
    absences.forEach(a => {
        if (!bySec[a.section]) bySec[a.section] = [];
        bySec[a.section].push(a);
    });
    Object.entries(bySec).forEach(([sec, singers]) => {
        const heading = document.createElement("div");
        heading.className = "manage-voice-header";
        heading.textContent = sec;
        box.appendChild(heading);
        singers.forEach(a => {
            const req = subBySec[a.section_id];
            const filled = req && req.status === "filled" && req.filled_by_name;
            const row = document.createElement("div");
            row.style.cssText = "padding:4px 0;display:flex;justify-content:space-between;align-items:center;gap:8px;";
            row.innerHTML = `
                <span>
                    ${escapeHtml(a.singer)}
                    ${a.reason ? `<em style="color:var(--text-muted);font-size:.88rem;"> — ${escapeHtml(a.reason)}</em>` : ""}
                    ${filled ? `<span style="color:var(--success);font-size:.88rem;margin-left:6px;">Sub confirmed: ${escapeHtml(req.filled_by_name)}</span>` : ""}
                </span>
                ${!filled ? `<button class="subtle-btn find-sub-from-admin-btn"
                    data-section-id="${a.section_id}" data-section="${escapeHtml(a.section)}">
                    Find sub
                </button>` : ""}
            `;
            if (!filled) {
                row.querySelector(".find-sub-from-admin-btn").addEventListener("click", e => {
                    const btn = e.currentTarget;
                    openFindSubModal(activeRehearsalId, Number(btn.dataset.sectionId), btn.dataset.section);
                });
            }
            box.appendChild(row);
        });
    });
}

// ── Find-sub modal ────────────────────────────────────────────────────────────

let findSubRehearsalId = null;
let findSubSectionId = null;

async function openFindSubModal(rehearsalId, sectionId, sectionName) {
    findSubRehearsalId = rehearsalId;
    findSubSectionId = sectionId;
    document.getElementById("find-sub-title").textContent = `Find a Sub — ${sectionName}`;
    document.getElementById("find-sub-hint").textContent = "";
    document.getElementById("find-sub-list").innerHTML = `<em class="empty-note">Loading…</em>`;
    document.getElementById("find-sub-modal").classList.remove("hidden");

    try {
        const res = await fetch(`${API}/choir/subs?section_id=${sectionId}`, { credentials: "include" });
        const subs = await res.json();

        const list = document.getElementById("find-sub-list");
        if (!subs.length) {
            list.innerHTML = `<em class="empty-note">No subs in the roster for this section yet.</em>`;
            return;
        }

        list.innerHTML = "";
        const preferred = subs.filter(s => s.is_preferred);
        const regular = subs.filter(s => !s.is_preferred);

        if (preferred.length) {
            const hdr = document.createElement("div");
            hdr.className = "section-group-title";
            hdr.textContent = "Preferred";
            list.appendChild(hdr);
            preferred.forEach(s => list.appendChild(buildFindSubRow(s)));
        }
        if (regular.length) {
            const hdr = document.createElement("div");
            hdr.className = "section-group-title";
            hdr.style.marginTop = "var(--space-4)";
            hdr.textContent = "Regular";
            list.appendChild(hdr);
            regular.forEach(s => list.appendChild(buildFindSubRow(s)));
        }
    } catch (e) {
        document.getElementById("find-sub-list").innerHTML = `<em class="empty-note">Failed to load.</em>`;
    }
}

function buildFindSubRow(sub) {
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
    btn.addEventListener("click", () => openCustomSubMsgModal(sub.id, sub.fullname, btn));
    return row;
}

let _customSubMsgPendingId = null;
let _customSubMsgPendingBtn = null;

function openCustomSubMsgModal(subId, subName, btn) {
    _customSubMsgPendingId = subId;
    _customSubMsgPendingBtn = btn;
    document.getElementById("custom-sub-msg-name").textContent = subName;
    document.getElementById("custom-sub-msg-compose").classList.add("hidden");
    document.getElementById("custom-sub-msg-choice").classList.remove("hidden");
    document.getElementById("custom-sub-msg-text").value = "";
    document.getElementById("custom-sub-msg-status").textContent = "";
    document.getElementById("custom-sub-msg-modal").classList.remove("hidden");
}

async function contactOneSub(subId, btn, customMessage) {
    btn.disabled = true;
    btn.textContent = "Sending…";
    try {
        const body = {
            rehearsal_id: Number(findSubRehearsalId),
            section_id: Number(findSubSectionId),
            sub_id: subId,
        };
        if (customMessage) body.custom_message = customMessage;
        const res = await fetch(`${API}/choir/contact-sub`, {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.status === "success") {
            btn.textContent = "Sent ✓";
            btn.style.color = "var(--success)";
        } else {
            if (data.message === "Already contacted") {
                btn.textContent = "Already contacted";
                btn.style.color = "var(--muted)";
            } else {
                btn.textContent = "Email failed";
            }
            btn.disabled = false;
        }
    } catch (e) {
        btn.textContent = "Error";
        btn.disabled = false;
    }
}


// ── Sub roster tab ────────────────────────────────────────────────────────────

async function loadSubRoster() {
    const list = document.getElementById("sub-roster-list");
    list.innerHTML = `<em class="empty-note">Loading…</em>`;
    const filter = document.getElementById("sub-section-filter").value;

    try {
        const url = filter
            ? `${API}/choir/subs?section_id=${filter}`
            : `${API}/choir/subs`;
        const res = await fetch(url, { credentials: "include" });
        const subs = await res.json();

        if (!subs.length) {
            list.innerHTML = `<em class="empty-note">No subs in this section yet.</em>`;
            return;
        }

        // Group by section
        const bySec = {};
        subs.forEach(s => {
            if (!bySec[s.section_name]) bySec[s.section_name] = { preferred: [], regular: [] };
            (s.is_preferred ? bySec[s.section_name].preferred : bySec[s.section_name].regular).push(s);
        });

        list.innerHTML = "";
        Object.entries(bySec).forEach(([secName, groups]) => {
            const secHeading = document.createElement("div");
            secHeading.style.cssText = "font-weight:700;font-size:1rem;margin:var(--space-4) 0 var(--space-2);padding-bottom:var(--space-1);border-bottom:2px solid var(--border);";
            secHeading.textContent = secName;
            list.appendChild(secHeading);

            if (groups.preferred.length) {
                const ph = document.createElement("div");
                ph.className = "manage-voice-header";
                ph.textContent = "Preferred Subs";
                list.appendChild(ph);
                groups.preferred.forEach(s => list.appendChild(buildSubRow(s)));
            }
            if (groups.regular.length) {
                const rh = document.createElement("div");
                rh.className = "manage-voice-header";
                rh.textContent = "Regular Subs";
                list.appendChild(rh);
                groups.regular.forEach(s => list.appendChild(buildSubRow(s)));
            }
        });
    } catch (e) { list.innerHTML = `<em class="empty-note">Failed to load.</em>`; }
}

function buildSubRow(s) {
    const row = document.createElement("div");
    row.className = "staff-row";
    row.style.padding = "var(--space-2) 0";
    row.innerHTML = `
        <div style="flex:1;">
            <div style="font-weight:600;">${escapeHtml(s.fullname)}</div>
            <div style="font-size:.85rem;color:var(--text-muted);">
                ${escapeHtml(s.email)}${s.phone ? " · " + escapeHtml(s.phone) : ""}
                ${s.notes ? ` — <em>${escapeHtml(s.notes)}</em>` : ""}
            </div>
        </div>
        <button class="subtle-btn toggle-preferred-btn"
            data-id="${s.id}" data-preferred="${s.is_preferred}">
            ${s.is_preferred ? "★ Preferred" : "☆ Make preferred"}
        </button>
        <button class="subtle-btn remove-sub-btn" data-id="${s.id}" style="color:var(--danger);">Remove</button>
    `;
    row.querySelector(".toggle-preferred-btn").addEventListener("click", async (e) => {
        const btn = e.currentTarget;
        const isNowPreferred = btn.dataset.preferred === "true";
        await fetch(`${API}/choir/sub/${s.id}`, {
            method: "PATCH", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ is_preferred: !isNowPreferred }),
        });
        loadSubRoster();
    });
    row.querySelector(".remove-sub-btn").addEventListener("click", async () => {
        if (!confirm(`Remove ${s.fullname} from the sub list?`)) return;
        await fetch(`${API}/choir/sub/${s.id}`, { method: "DELETE", credentials: "include" });
        loadSubRoster();
    });
    return row;
}

async function addSub() {
    const msg = document.getElementById("sub-add-msg");
    msg.textContent = "";
    const payload = {
        fullname: document.getElementById("new-sub-name").value.trim(),
        email: document.getElementById("new-sub-email").value.trim(),
        phone: document.getElementById("new-sub-phone").value.trim(),
        section_id: Number(document.getElementById("new-sub-section").value),
        notes: document.getElementById("new-sub-notes").value.trim(),
        is_preferred: document.getElementById("new-sub-preferred").checked,
    };
    if (!payload.fullname || !payload.email || !payload.section_id) {
        msg.textContent = "Name, email, and section are required."; return;
    }
    try {
        const res = await fetch(`${API}/choir/subs`, {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (data.status === "success") {
            msg.className = "msg success-msg";
            msg.textContent = "Sub added!";
            ["new-sub-name", "new-sub-email", "new-sub-phone", "new-sub-notes"].forEach(id =>
                document.getElementById(id).value = "");
            document.getElementById("new-sub-preferred").checked = false;
            loadSubRoster();
        } else {
            msg.className = "msg";
            msg.textContent = data.message || "Failed to add.";
        }
    } catch (e) { msg.textContent = "Server error."; }
}




// ── Bulk schedule ─────────────────────────────────────────────────────────────

const DAY_JS = { monday: 1, tuesday: 2, wednesday: 3, thursday: 4, friday: 5, saturday: 6, sunday: 0 };

function updateBulkPreview() {
    const startDate = document.getElementById("bulk-start-date").value;
    const endDate = document.getElementById("bulk-end-date").value;
    const days = [...document.querySelectorAll("#bulk-days input:checked")].map(cb => cb.value);
    const preview = document.getElementById("bulk-preview");

    if (!startDate || !endDate || !days.length) { preview.textContent = ""; return; }

    const sd = new Date(startDate + "T00:00:00");
    const ed = new Date(endDate + "T00:00:00");
    if (ed < sd) { preview.textContent = "End date must be after start date."; return; }

    const dayNums = days.map(d => DAY_JS[d]);
    let count = 0;
    const cur = new Date(sd);
    while (cur <= ed) {
        if (dayNums.includes(cur.getDay())) count++;
        cur.setDate(cur.getDate() + 1);
    }
    preview.textContent = count > 0
        ? `This will create ${count} rehearsal${count !== 1 ? "s" : ""}.`
        : "No rehearsals match this selection.";
}

async function bulkSchedule() {
    const msg = document.getElementById("reh-msg");
    msg.textContent = "";

    const start_date = document.getElementById("bulk-start-date").value;
    const end_date = document.getElementById("bulk-end-date").value;
    const days = [...document.querySelectorAll("#bulk-days input:checked")].map(cb => cb.value);
    const start_time = document.getElementById("reh-start").value;
    const end_time = document.getElementById("reh-end").value;
    const location = document.getElementById("reh-location").value.trim();
    const notes = document.getElementById("reh-notes").value.trim();
    const sections = [...document.querySelectorAll("#section-checkboxes input:checked")]
        .map(cb => Number(cb.value));

    if (!start_date || !end_date) { msg.textContent = "Start and end dates are required."; return; }
    if (!days.length) { msg.textContent = "Select at least one day of the week."; return; }
    if (!start_time) { msg.textContent = "Start time is required."; return; }

    const btn = document.getElementById("create-reh-btn");
    btn.disabled = true;
    btn.textContent = "Scheduling...";

    try {
        const res = await fetch(`${API}/choir/rehearsals/bulk`, {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ start_date, end_date, days, start_time, end_time: end_time || null, location, notes, sections }),
        });
        const data = await res.json();
        if (data.status === "success") {
            msg.className = "msg success-msg";
            msg.textContent = `Done! ${data.created} rehearsal${data.created !== 1 ? "s" : ""} scheduled.`;
            document.getElementById("bulk-start-date").value = "";
            document.getElementById("bulk-end-date").value = "";
            document.getElementById("reh-start").value = "";
            document.getElementById("reh-end").value = "";
            document.getElementById("reh-location").value = "";
            document.getElementById("reh-notes").value = "";
            document.querySelectorAll("#bulk-days input, #section-checkboxes input")
                .forEach(cb => cb.checked = false);
            document.getElementById("bulk-preview").textContent = "";
        } else {
            msg.className = "msg";
            msg.textContent = data.message || "Failed to schedule.";
        }
    } catch (e) {
        msg.className = "msg";
        msg.textContent = "Server error.";
    } finally {
        btn.disabled = false;
        btn.textContent = "Schedule Rehearsal";
    }
}


// ── Sections tab ──────────────────────────────────────────────────────────────

const VOICE_LABELS = { soprano: "Soprano", alto: "Alto", tenor: "Tenor", bass: "Bass", other: "Other" };

async function loadSections() {
    const roster = document.getElementById("sections-roster");
    const hint = document.getElementById("sections-reh-hint");
    roster.innerHTML = `<em class="empty-note">Loading…</em>`;
    try {
        const res = await fetch(`${API}/choir/sections/roster`, { credentials: "include" });
        const data = await res.json();

        hint.textContent = data.rehearsal_date
            ? `Attendance for next rehearsal: ${fmtDate(data.rehearsal_date)}`
            : "No upcoming rehearsals scheduled — showing roster only.";

        if (!data.groups || !data.groups.length) {
            roster.innerHTML = `<em class="empty-note">No singers yet.</em>`;
            return;
        }

        roster.innerHTML = "";
        data.groups.forEach(g => {
            const sec = document.createElement("div");
            sec.className = "section-group";
            const label = VOICE_LABELS[g.voice_type] || g.voice_type;
            let rows = g.singers.map(s => `
                <div class="staff-row">
                    <span class="staff-row-name">${escapeHtml(s.name)}</span>
                    <span class="singer-status ${s.status === "absent" ? "status-absent" : "status-attending"}">
                        ${s.status === "absent" ? "Absent" : "Attending"}
                    </span>
                </div>
            `).join("");
            sec.innerHTML = `<h3 class="section-group-title">${label}</h3>${rows}`;
            roster.appendChild(sec);
        });
    } catch (e) {
        roster.innerHTML = `<em class="empty-note">Failed to load.</em>`;
    }
}


// ── Invitations tab ──────────────────────────────────────────────────────────

async function loadInvitations() {
    const list = document.getElementById("invitations-list");
    list.innerHTML = `<em class="empty-note">Loading…</em>`;
    try {
        const res = await fetch(`${API}/admin/invitations`, { credentials: "include" });
        const data = await res.json();
        const invites = Array.isArray(data) ? data : (data.invitations || []);
        if (!invites.length) {
            list.innerHTML = `<em class="empty-note">No invitations sent yet.</em>`;
            return;
        }
        list.innerHTML = "";
        invites.forEach(inv => {
            const row = document.createElement("div");
            row.className = "invite-row";
            row.innerHTML = `
                <div class="invite-main">
                    <div>${escapeHtml(inv.email)}</div>
                    <div class="invite-meta">Sent ${inv.created_at ? new Date(inv.created_at).toLocaleDateString() : ""}</div>
                    <span class="invite-status ${inv.status || "pending"}">${inv.status || "pending"}</span>
                </div>
            `;
            list.appendChild(row);
        });
    } catch (e) { list.innerHTML = `<em class="empty-note">Failed to load.</em>`; }
}

async function loadEnsembleMembers() {
    const list = document.getElementById("ensemble-members-list");
    list.innerHTML = `<em class="empty-note">Loading…</em>`;
    try {
        const res = await fetch(`${API}/ensemble/members`, { credentials: "include" });
        const members = await res.json();
        if (!members.length) {
            list.innerHTML = `<em class="empty-note">No ensemble members yet. Invite them from the Invitations tab.</em>`;
            return;
        }
        list.innerHTML = "";
        members.forEach(m => {
            const row = document.createElement("div");
            row.className = "sections-singer-row";
            row.innerHTML = `
                <div>
                    <strong>${escapeHtml(m.fullname)}</strong>
                    ${m.instrument ? `<span class="hint" style="margin-left:var(--space-2);">${escapeHtml(m.instrument)}</span>` : ""}
                </div>
            `;
            list.appendChild(row);
        });
    } catch (e) { list.innerHTML = `<em class="empty-note">Failed to load.</em>`; }
}


async function sendInvitation() {
    const msg = document.getElementById("invite-msg");
    msg.textContent = "";
    const email = document.getElementById("invite-email").value.trim().toLowerCase();
    const fullname = document.getElementById("invite-name").value.trim();
    const role = document.querySelector("input[name='invite-member-type']:checked")?.value || "choir_member";
    const instrument = document.getElementById("invite-instrument").value.trim();
    if (!email) { msg.textContent = "Email is required."; return; }
    if (role === "ensemble_member" && !instrument) {
        msg.textContent = "Instrument / Role is required for ensemble members.";
        return;
    }
    try {
        const res = await fetch(`${API}/admin/invite`, {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, fullname: fullname || null, role, instrument: instrument || null }),
        });
        const data = await res.json();
        if (data.status === "success" || data.message?.includes("sent")) {
            msg.className = "msg success-msg";
            msg.textContent = `Invitation sent to ${email}.`;
            document.getElementById("invite-email").value = "";
            document.getElementById("invite-name").value = "";
            document.getElementById("invite-instrument").value = "";
            document.querySelector("input[name='invite-member-type'][value='choir_member']").checked = true;
            document.getElementById("invite-instrument-row").classList.add("hidden");
            loadInvitations();
        } else {
            msg.className = "msg"; msg.textContent = data.message || "Failed.";
        }
    } catch (e) { msg.textContent = "Server error."; }
}


// ── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
    await ME_READY;
    if (!USERNAME) return;

    // Tab wiring
    document.querySelectorAll(".tab-btn").forEach(btn =>
        btn.addEventListener("click", () => setActiveTab(btn.dataset.tab)));
    setActiveTab(getTabFromURL());
    window.addEventListener("hashchange", () => setActiveTab(getTabFromURL()));

    // Load shared section data first
    await loadSectionsData();

    // Schedule tab — rehearsal type toggle
    document.querySelectorAll("input[name='reh-choir-type']").forEach(radio => {
        radio.addEventListener("change", () => renderUnifiedCaller("unified-caller"));
    });
    document.querySelectorAll("input[name='edit-reh-choir-type']").forEach(radio => {
        radio.addEventListener("change", () => renderUnifiedCaller("edit-unified-caller"));
    });

    // Schedule tab — mode toggle
    document.querySelectorAll("input[name='reh-mode']").forEach(radio => {
        radio.addEventListener("change", () => {
            const isRange = radio.value === "range";
            document.getElementById("reh-single-fields").classList.toggle("hidden", isRange);
            document.getElementById("reh-bulk-fields").classList.toggle("hidden", !isRange);
        });
    });

    // Invite — member type toggle shows/hides instrument field
    document.querySelectorAll("input[name='invite-member-type']").forEach(radio => {
        radio.addEventListener("change", () => {
            document.getElementById("invite-instrument-row").classList.toggle("hidden", radio.value !== "ensemble_member");
        });
    });

    // Schedule tab — submit (delegates based on mode)
    document.getElementById("create-reh-btn").addEventListener("click", () => {
        const mode = document.querySelector("input[name='reh-mode']:checked")?.value;
        if (mode === "range") bulkSchedule(); else createRehearsal();
    });

    // Bulk preview updates
    ["bulk-start-date", "bulk-end-date"].forEach(id =>
        document.getElementById(id).addEventListener("change", updateBulkPreview));
    document.querySelectorAll("#bulk-days input").forEach(cb =>
        cb.addEventListener("change", updateBulkPreview));

    // Upcoming tab
    document.getElementById("refresh-upcoming-btn").addEventListener("click", loadUpcoming);

    // Choir rehearsal notes modal
    document.getElementById("send-choir-notes-btn").addEventListener("click", sendChoirNotes);
    document.getElementById("close-choir-notes-btn").addEventListener("click", () =>
        document.getElementById("choir-notes-modal").classList.add("hidden"));
    document.getElementById("choir-notes-modal").addEventListener("click", e => {
        if (e.target.id === "choir-notes-modal") e.target.classList.add("hidden");
    });

    // Choir rehearsal edit modal
    document.getElementById("save-choir-reh-edit-btn").addEventListener("click", saveChoirRehearsalEdit);
    document.getElementById("close-choir-reh-edit-btn").addEventListener("click", () =>
        document.getElementById("choir-reh-edit-modal").classList.add("hidden"));
    document.getElementById("choir-reh-edit-modal").addEventListener("click", e => {
        if (e.target.id === "choir-reh-edit-modal") e.target.classList.add("hidden");
    });

    // Absence modal
    document.getElementById("absence-modal-close").addEventListener("click", () => {
        document.getElementById("absence-modal").classList.add("hidden");
    });
    document.getElementById("absence-modal").addEventListener("click", e => {
        if (e.target.id === "absence-modal") document.getElementById("absence-modal").classList.add("hidden");
    });

    // Find-sub modal
    document.getElementById("find-sub-close").addEventListener("click", () => {
        document.getElementById("find-sub-modal").classList.add("hidden");
    });
    document.getElementById("find-sub-modal").addEventListener("click", e => {
        if (e.target.id === "find-sub-modal") document.getElementById("find-sub-modal").classList.add("hidden");
    });

    // Custom sub message modal
    document.getElementById("custom-sub-no-btn").addEventListener("click", () => {
        document.getElementById("custom-sub-msg-modal").classList.add("hidden");
        contactOneSub(_customSubMsgPendingId, _customSubMsgPendingBtn, null);
    });
    document.getElementById("custom-sub-yes-btn").addEventListener("click", () => {
        document.getElementById("custom-sub-msg-choice").classList.add("hidden");
        document.getElementById("custom-sub-msg-compose").classList.remove("hidden");
        document.getElementById("custom-sub-msg-text").focus();
    });
    document.getElementById("custom-sub-back-btn").addEventListener("click", () => {
        document.getElementById("custom-sub-msg-compose").classList.add("hidden");
        document.getElementById("custom-sub-msg-choice").classList.remove("hidden");
    });
    document.getElementById("custom-sub-send-btn").addEventListener("click", async () => {
        const msg = document.getElementById("custom-sub-msg-text").value.trim();
        if (!msg) {
            document.getElementById("custom-sub-msg-status").textContent = "Please enter a message.";
            return;
        }
        document.getElementById("custom-sub-msg-modal").classList.add("hidden");
        await contactOneSub(_customSubMsgPendingId, _customSubMsgPendingBtn, msg);
    });
    document.getElementById("custom-sub-msg-modal").addEventListener("click", e => {
        if (e.target.id === "custom-sub-msg-modal") document.getElementById("custom-sub-msg-modal").classList.add("hidden");
    });

    // Subs tab
    document.getElementById("sub-section-filter").addEventListener("change", loadSubRoster);
    document.getElementById("add-sub-btn").addEventListener("click", addSub);

    // Invitations tab
    document.getElementById("send-invite-btn").addEventListener("click", sendInvitation);

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

    // Initial tab load
    refreshDmBadge();
    if (getTabFromURL() === "upcoming") loadUpcoming();
    if (getTabFromURL() === "messages") loadDmTab();
});


// ======================================================
// DIRECT MESSAGES MODULE
// ======================================================

let dmInbox = [];
let dmSent = [];
let dmContacts = [];
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
    } catch (e) {
        dmInbox = [];
        dmSent = [];
    }
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
    if (!msgs.length) {
        list.innerHTML = `<em class="empty-note">No messages yet.</em>`;
        return;
    }
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
    const sel = document.getElementById("dm-recipient-select");
    if (!sel) return;
    sel.innerHTML = "";
    const byGroup = {};
    dmContacts.forEach(c => {
        const g = c.group || "Members";
        if (!byGroup[g]) byGroup[g] = [];
        byGroup[g].push(c);
    });
    Object.entries(byGroup).forEach(([group, members]) => {
        const og = document.createElement("optgroup");
        og.label = group;
        members.forEach(c => {
            const opt = document.createElement("option");
            opt.value = c.id;
            opt.textContent = c.fullname;
            og.appendChild(opt);
        });
        sel.appendChild(og);
    });
}

async function sendDm() {
    const body = (document.getElementById("dm-body")?.value || "").trim();
    const status = document.getElementById("dm-send-status");
    if (!body) { status.textContent = "Message cannot be empty."; return; }

    const scopeEl = document.getElementById("dm-scope");
    const scope = scopeEl ? scopeEl.value : "direct";
    let recipient_ids = [];
    if (scope === "direct") {
        const sel = document.getElementById("dm-recipient-select");
        recipient_ids = sel ? [...sel.selectedOptions].map(o => Number(o.value)) : [];
        if (!recipient_ids.length) { status.textContent = "Select at least one recipient."; return; }
    }

    const btn = document.getElementById("dm-send-btn");
    btn.disabled = true;
    status.textContent = "Sending…";
    try {
        const res = await fetch(`${API}/dm`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scope, body, recipient_ids }),
        });
        const data = await res.json();
        if (data.status === "success") {
            status.textContent = `Sent to ${data.sent_to} recipient${data.sent_to === 1 ? "" : "s"}.`;
            document.getElementById("dm-body").value = "";
            if (scopeEl) scopeEl.value = scopeEl.options[0]?.value || "direct";
            document.getElementById("dm-recipient-row")?.classList.add("hidden");
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
