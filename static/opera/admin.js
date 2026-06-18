// ===========================================================
// ADMIN DASHBOARD
// Sections:
//   1. Constants & helpers
//   2. Tab switching
//   3. Casting (NEW design — roles-first)
//   4. Rehearsals
//   5. Schedules (teacher availability)
//   6. Pending availability requests
//   7. Init
// ===========================================================


// -----------------------------------------------------------
// 1. CONSTANTS & HELPERS
// -----------------------------------------------------------

const output = document.getElementById("output");
const ADMIN_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const VALID_TABS = ["rehearsals", "casting", "invitations", "orgs", "orchestra", "messages"];

// Voice compatibility (which voice types can sing which roles)
const VOICE_COMPATIBILITY = {
    "soprano": ["soprano"],
    "mezzo-soprano": ["mezzo-soprano"],
    "tenor": ["tenor"],
    "baritone": ["baritone", "bass-baritone"],
    "bass": ["bass", "bass-baritone"],
    "bass-baritone": ["bass-baritone", "baritone", "bass"],
};

// Sort order for voice types
const VOICE_SORT_ORDER = [
    "soprano", "mezzo-soprano", "tenor",
    "baritone", "bass-baritone", "bass",
];

// DOM refs
const rehearsalOpera = document.getElementById("rehearsal-opera");

function adminFormatTime(hhmm) {
    if (!hhmm) return "";
    const [h, m] = hhmm.split(":").map(Number);
    const suffix = h >= 12 ? "PM" : "AM";
    const hour12 = ((h + 11) % 12) + 1;
    return `${hour12}:${String(m).padStart(2, "0")} ${suffix}`;
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


// -----------------------------------------------------------
// 2. TAB SWITCHING
// -----------------------------------------------------------

function setActiveTab(tabName) {
    if (!VALID_TABS.includes(tabName)) tabName = "rehearsals";

    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.tab === tabName);
    });

    document.querySelectorAll(".tab-panel").forEach(panel => {
        panel.classList.toggle("active", panel.dataset.tabPanel === tabName);
    });

    if (window.location.hash !== `#${tabName}`) {
        history.replaceState(null, "", `#${tabName}`);
    }

    // Lazy-load data only when the relevant tab becomes active
    if (tabName === "invitations") { loadInvitations(); loadOrgTransferRequests(); loadTeachersList(); }
    if (tabName === "orgs") loadOrgs();
    if (tabName === "orchestra") loadOrchestra();
    if (tabName === "messages") loadMessagesTab();
}

function getTabFromURL() {
    const hash = window.location.hash.replace("#", "");
    return VALID_TABS.includes(hash) ? hash : "rehearsals";
}



// -----------------------------------------------------------
// 3. CASTING (cast-column default view + role-grid modal)
// -----------------------------------------------------------

let castingData = null;
let expandedChorusCasts = new Set();  // cast ids whose chorus is expanded
let collapsedCasts = new Set();       // cast ids that are collapsed
let castingOperas = [];               // full list of operas (id + name)
let castingSelectedOperaId = null;    // which opera tab is active

async function loadCastingOperas() {
    const res = await fetch(`${API}/operas`, { credentials: "include" });
    const data = await res.json();
    castingOperas = Array.isArray(data) ? data : [];
}

function renderCastingOperaTabs() {
    // Navigation is now handled by the productions list "View Casting" button
}

let expandedCastingContainer = null;

async function toggleProductionCasting(prodId, row) {
    const inlineEl = row.querySelector(".prod-inline-casting");
    const isExpanded = !inlineEl.classList.contains("hidden");

    // Collapse all rows
    document.querySelectorAll(".production-row").forEach(r => {
        r.querySelector(".prod-inline-casting")?.classList.add("hidden");
        r.querySelector(".prod-chevron")?.classList.remove("prod-chevron--open");
        r.classList.remove("prod-expanded");
    });
    expandedCastingContainer = null;

    if (isExpanded) return;

    row.classList.add("prod-expanded");
    inlineEl.classList.remove("hidden");
    row.querySelector(".prod-chevron")?.classList.add("prod-chevron--open");
    expandedCastingContainer = inlineEl;

    inlineEl.innerHTML = `<em class="empty-note">Loading…</em>`;
    try {
        const res = await fetch(`${API}/admin/opera-casting/${prodId}`, { credentials: "include" });
        const data = await res.json();
        if (data.error) { inlineEl.innerHTML = `<em class="empty-note">${data.error}</em>`; return; }
        castingData = data;
        castingSelectedOperaId = prodId;
        renderCastColumns(inlineEl);
    } catch (e) {
        console.error(e);
        inlineEl.innerHTML = `<em class="empty-note">Failed to load casting.</em>`;
    }
}

async function loadCastingForOpera(operaId) {
    castingSelectedOperaId = Number(operaId);
    if (!expandedCastingContainer) return;
    expandedCastingContainer.innerHTML = `<em class="empty-note">Loading…</em>`;

    try {
        const res = await fetch(`${API}/admin/opera-casting/${operaId}`, { credentials: "include" });
        const data = await res.json();
        if (data.error) {
            expandedCastingContainer.innerHTML = `<em class="empty-note">${data.error}</em>`;
            return;
        }
        castingData = data;
        renderCastColumns(expandedCastingContainer);
    } catch (e) {
        console.error(e);
        expandedCastingContainer.innerHTML = `<em class="empty-note">Failed to load casting data.</em>`;
    }
}

function renderCastColumns(container) {
    if (!container) return;
    container.innerHTML = "";

    if (!castingData) return;

    // Sort roles by voice type, then alphabetically
    const sortedRoles = [...castingData.roles].sort((a, b) => {
        const ai = VOICE_SORT_ORDER.indexOf((a.voice_type || "").toLowerCase());
        const bi = VOICE_SORT_ORDER.indexOf((b.voice_type || "").toLowerCase());
        const aIdx = ai < 0 ? VOICE_SORT_ORDER.length : ai;
        const bIdx = bi < 0 ? VOICE_SORT_ORDER.length : bi;
        if (aIdx !== bIdx) return aIdx - bIdx;
        return a.name.localeCompare(b.name);
    });

    // Lookup: (cast_id, role_name) -> assignment
    const byCastRole = {};
    castingData.assignments.forEach(a => {
        byCastRole[`${a.cast_id}::${a.role_name}`] = a;
    });

    // Lookup: student_id -> [cast_id], used to compute chorus hint
    const principalCastsByStudent = {};
    castingData.assignments.forEach(a => {
        if (!principalCastsByStudent[a.student_id]) principalCastsByStudent[a.student_id] = [];
        principalCastsByStudent[a.student_id].push({cast_id: a.cast_id, role: a.role_name});
    });
    const castNameById = {};
    castingData.casts.forEach(c => { castNameById[c.id] = c.name; });

    // One column per cast
    castingData.casts.forEach(cast => {
        const col = document.createElement("div");
        col.className = "cast-column";

        const isCollapsed = collapsedCasts.has(cast.id);
        let html = `
          <div class="cast-column-title cast-column-toggle" data-cast-id="${cast.id}">
            <span>${escapeHtml(cast.name)}</span>
            <span class="cast-chevron">${isCollapsed ? "▶" : "▼"}</span>
          </div>
          <div class="cast-column-body${isCollapsed ? " hidden" : ""}">`;

        // Group roles by voice type
        const rolesByVoice = {};
        sortedRoles.forEach(r => {
            const v = (r.voice_type || "unknown").toLowerCase();
            if (!rolesByVoice[v]) rolesByVoice[v] = [];
            rolesByVoice[v].push(r);
        });

        VOICE_SORT_ORDER.forEach(voice => {
            if (!rolesByVoice[voice]) return;
            html += `<div class="cast-voice-section">`;
            html += `<h4 class="cast-voice-label">${voice}</h4>`;

            rolesByVoice[voice].forEach(role => {
                const key = `${cast.id}::${role.name}`;
                const a = byCastRole[key];

                html += `<div class="cast-role-line">`;
                html += `<div class="cast-role-name">${escapeHtml(role.name)}</div>`;
                if (a) {
                    html += `
                        <div class="cast-role-student">
                            → ${escapeHtml(a.student_name)}
                            <button class="remove-cast-role-btn"
                                    title="Remove from role"
                                    data-student-id="${a.student_id}"
                                    data-student-name="${escapeHtml(a.student_name)}"
                                    data-cast-id="${cast.id}"
                                    data-role-name="${escapeHtml(role.name)}">✕</button>
                        </div>`;
                } else {
                    html += `<div class="cast-role-student unassigned">→ (unassigned)</div>`;
                }
                html += `</div>`;
            });

            html += `</div>`;
        });

        // Chorus section for this cast (collapsed by default)
        const chorusMembers = computeChorusForCast(cast.id, principalCastsByStudent, castNameById);
        const isExpanded = expandedChorusCasts.has(cast.id);

        html += `<div class="cast-chorus-section">`;
        html += `<button class="cast-chorus-toggle" data-cast-id="${cast.id}">`;
        html += `${isExpanded ? "▼" : "▶"} Show chorus (${chorusMembers.length})`;
        html += `</button>`;

        if (isExpanded) {
            if (chorusMembers.length === 0) {
                html += `<em class="empty-note">No chorus members in this cast.</em>`;
            } else {
                const byVoice = {};
                chorusMembers.forEach(m => {
                    const v = (m.voice_type || "unknown").toLowerCase();
                    if (!byVoice[v]) byVoice[v] = [];
                    byVoice[v].push(m);
                });

                html += `<div class="cast-chorus-list">`;
                VOICE_SORT_ORDER.forEach(voice => {
                    if (!byVoice[voice]) return;
                    html += `<h5 class="cast-chorus-voice">${voice}</h5>`;
                    html += `<ul>`;
                    byVoice[voice]
                        .sort((a, b) => a.name.localeCompare(b.name))
                        .forEach(m => {
                            const hint = m.other_roles.length
                                ? ` <span class="chorus-role-hint">(${escapeHtml(m.other_roles.join(", "))})</span>`
                                : "";
                            html += `<li>${escapeHtml(m.name)}${hint}</li>`;
                        });
                    html += `</ul>`;
                });
                html += `</div>`;
            }
        }
        html += `</div>`; // cast-chorus-section
        html += `</div>`; // cast-column-body

        col.innerHTML = html;
        container.appendChild(col);
    });

    // Wire up cast collapse toggles
    container.querySelectorAll(".cast-column-toggle").forEach(btn => {
        btn.addEventListener("click", () => {
            const cid = Number(btn.dataset.castId);
            if (collapsedCasts.has(cid)) collapsedCasts.delete(cid);
            else collapsedCasts.add(cid);
            renderCastColumns(expandedCastingContainer);
        });
    });

    // Wire up chorus toggle buttons
    container.querySelectorAll(".cast-chorus-toggle").forEach(btn => {
        btn.addEventListener("click", () => {
            const cid = Number(btn.dataset.castId);
            if (expandedChorusCasts.has(cid)) {
                expandedChorusCasts.delete(cid);
            } else {
                expandedChorusCasts.add(cid);
            }
            renderCastColumns(expandedCastingContainer);
        });
    });

    // Wire up remove-role ✕ buttons
    container.querySelectorAll(".remove-cast-role-btn").forEach(btn => {
        btn.addEventListener("click", onRemoveRoleClick);
    });
}

function computeChorusForCast(castId, principalCastsByStudent, castNameById) {
    const out = [];
    castingData.assigned_students.forEach(student => {
        const principals = principalCastsByStudent[student.id] || [];

        // Is this student a principal in THIS cast?
        if (principals.some(p => p.cast_id === castId)) return;

        // Chorus in this cast. Note roles in other casts.
        const otherRoles = principals
            .filter(p => p.cast_id !== castId)
            .map(p => `${p.role} in ${castNameById[p.cast_id] || "?"}`);

        out.push({
            id: student.id,
            name: student.name,
            voice_type: student.voice_type,
            other_roles: otherRoles,
        });
    });

    return out;
}

async function onRemoveRoleClick(e) {
    const btn = e.currentTarget;
    const studentId = Number(btn.dataset.studentId);
    const studentName = btn.dataset.studentName;
    const castId = Number(btn.dataset.castId);
    const roleName = btn.dataset.roleName;

    const ok = confirm(`Remove ${studentName} from ${roleName}?`);
    if (!ok) return;

    try {
        const res = await fetch(`${API}/admin/assign-principal`, {
            credentials: "include",
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                opera_id: castingData.opera.id,
                cast_id: castId,
                role_name: roleName,
                student_id: null,  // null = clear
            })
        });
        const data = await res.json();
        if (data.status !== "success") {
            alert(data.message || "Failed to remove.");
            return;
        }
        loadCastingForOpera(castingData.opera.id);
    } catch (err) {
        console.error(err);
        alert("Server error.");
    }
}


// --- Assign Roles modal (the old role × cast grid, now in a modal) ---

function openAssignRolesModal() {
    if (!castingData) return;
    document.getElementById("assign-roles-title").textContent =
        `Assign Roles — ${castingData.opera.name}`;
    renderAssignRolesGrid();
    document.getElementById("assign-roles-modal").classList.remove("hidden");
}

function closeAssignRolesModal() {
    document.getElementById("assign-roles-modal").classList.add("hidden");
}

function renderAssignRolesGrid(containerId = "assign-roles-grid") {
    const container = document.getElementById(containerId);
    container.innerHTML = "";

    if (!castingData || castingData.roles.length === 0) {
        container.innerHTML = `<em class="empty-note">No principal roles for this opera.</em>`;
        return;
    }

    // Sort roles by voice type, then alphabetically
    const sortedRoles = [...castingData.roles].sort((a, b) => {
        const ai = VOICE_SORT_ORDER.indexOf((a.voice_type || "").toLowerCase());
        const bi = VOICE_SORT_ORDER.indexOf((b.voice_type || "").toLowerCase());
        const aIdx = ai < 0 ? VOICE_SORT_ORDER.length : ai;
        const bIdx = bi < 0 ? VOICE_SORT_ORDER.length : bi;
        if (aIdx !== bIdx) return aIdx - bIdx;
        return a.name.localeCompare(b.name);
    });

    // Lookup by (cast_id, role_name)
    const byCastRole = {};
    castingData.assignments.forEach(a => {
        byCastRole[`${a.cast_id}::${a.role_name}`] = a;
    });

    // Lookup: for THIS cast, which students are already cast?
    // Allow cross-cast double-casting but block same-cast double-casting.
    function studentsInCast(castId) {
        return castingData.assignments
            .filter(a => a.cast_id === castId)
            .map(a => ({student_id: a.student_id, role: a.role_name}));
    }

    // Header row
    const header = document.createElement("div");
    header.className = "casting-header";
    header.style.gridTemplateColumns = `1fr repeat(${castingData.casts.length}, 1fr)`;

    const roleHeaderCell = document.createElement("div");
    roleHeaderCell.className = "casting-role-label";
    roleHeaderCell.textContent = "Role";
    header.appendChild(roleHeaderCell);

    castingData.casts.forEach(c => {
        const castLabel = document.createElement("div");
        castLabel.className = "casting-cast-label";

        const nameSpan = document.createElement("span");
        nameSpan.className = "cast-label-name";
        nameSpan.textContent = c.name;

        const renameBtn = document.createElement("button");
        renameBtn.type = "button";
        renameBtn.className = "cast-label-btn cast-rename-btn";
        renameBtn.title = "Rename";
        renameBtn.textContent = "✏";

        const removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "cast-label-btn cast-remove-btn";
        removeBtn.title = "Remove cast";
        removeBtn.textContent = "✕";

        castLabel.appendChild(nameSpan);
        castLabel.appendChild(renameBtn);
        castLabel.appendChild(removeBtn);

        renameBtn.addEventListener("click", () => {
            const input = document.createElement("input");
            input.type = "text";
            input.className = "cast-rename-input";
            input.value = c.name;
            castLabel.innerHTML = "";
            castLabel.appendChild(input);
            input.focus();
            input.select();

            const restore = () => {
                castLabel.innerHTML = "";
                castLabel.appendChild(nameSpan);
                castLabel.appendChild(renameBtn);
                castLabel.appendChild(removeBtn);
            };
            const save = async () => {
                const newName = input.value.trim();
                if (newName && newName !== c.name) {
                    await renameCast(castingData.opera.id, c.id, newName);
                } else {
                    restore();
                }
            };
            input.addEventListener("keydown", e => {
                if (e.key === "Enter") save();
                if (e.key === "Escape") restore();
            });
            input.addEventListener("blur", save);
        });

        removeBtn.addEventListener("click", () => {
            if (!confirm(`Remove "${c.name}"? All role assignments for this cast will be lost.`)) return;
            removeCast(castingData.opera.id, c.id);
        });

        header.appendChild(castLabel);
    });

    container.appendChild(header);

    sortedRoles.forEach(role => {
        const row = document.createElement("div");
        row.className = "casting-role-row";
        row.style.gridTemplateColumns = `1fr repeat(${castingData.casts.length}, 1fr)`;

        // Role label
        const roleLabel = document.createElement("div");
        roleLabel.className = "casting-role-cell";
        roleLabel.innerHTML = `
            <strong>${escapeHtml(role.name)}</strong>
            <div class="casting-voice-hint">${escapeHtml(role.voice_type || "")}</div>
        `;
        row.appendChild(roleLabel);

        castingData.casts.forEach(cast => {
            const cell = document.createElement("div");
            cell.className = "casting-cell";

            const key = `${cast.id}::${role.name}`;
            const currentAssignment = byCastRole[key];

            const allowedVoices = VOICE_COMPATIBILITY[(role.voice_type || "").toLowerCase()] || [];
            const compatibleStudents = castingData.all_students
                .filter(s => allowedVoices.includes((s.voice_type || "").toLowerCase()))
                .sort((a, b) => a.name.localeCompare(b.name));

            const sameCastCastings = studentsInCast(cast.id);
            const isExternalAssignment = currentAssignment && !currentAssignment.student_id;

            if (isExternalAssignment) {
                // No-account contact already in this slot — show as a removable pill
                const pill = document.createElement("span");
                pill.className = "cover-pill";
                pill.textContent = currentAssignment.name + " (no account)";
                const removeX = document.createElement("button");
                removeX.type = "button";
                removeX.className = "cover-pill-remove";
                removeX.textContent = "×";
                removeX.title = "Remove";
                removeX.addEventListener("click", async () => {
                    await doAssignPrincipal(castingData.opera.id, cast.id, role.name, null);
                });
                pill.appendChild(removeX);
                cell.appendChild(pill);
                row.appendChild(cell);
                return;
            }

            // --- Select ---
            const select = document.createElement("select");
            select.className = "casting-select";
            select.dataset.operaId = castingData.opera.id;
            select.dataset.castId = cast.id;
            select.dataset.roleName = role.name;

            const noneOpt = document.createElement("option");
            noneOpt.value = "";
            noneOpt.textContent = "— Not cast —";
            select.appendChild(noneOpt);

            compatibleStudents.forEach(student => {
                const opt = document.createElement("option");
                opt.value = student.id;
                opt.textContent = student.name;
                const conflict = sameCastCastings.find(
                    c => c.student_id === student.id && c.role !== role.name
                );
                if (conflict) {
                    opt.textContent = `${student.name} (already ${conflict.role})`;
                    opt.disabled = true;
                    opt.style.color = "var(--text-faint)";
                }
                if (currentAssignment && currentAssignment.student_id === student.id) {
                    opt.selected = true;
                    opt.disabled = false;
                    opt.textContent = student.name;
                    opt.style.color = "";
                }
                select.appendChild(opt);
            });
            select.addEventListener("change", onPrincipalAssignmentChange);

            // --- Assign row (select + search button + no-account button) ---
            const assignRow = document.createElement("div");
            assignRow.className = "casting-assign-row";
            const searchToggleBtn = document.createElement("button");
            searchToggleBtn.type = "button";
            searchToggleBtn.className = "casting-search-toggle";
            searchToggleBtn.textContent = "\u{1F50D}";
            const noAccountToggleBtn = document.createElement("button");
            noAccountToggleBtn.type = "button";
            noAccountToggleBtn.className = "casting-search-toggle";
            noAccountToggleBtn.title = "Assign someone without an account";
            noAccountToggleBtn.textContent = "+";
            assignRow.appendChild(select);
            assignRow.appendChild(searchToggleBtn);
            assignRow.appendChild(noAccountToggleBtn);

            // --- No-account panel (name + email, no login required) ---
            const externalPanel = document.createElement("div");
            externalPanel.className = "casting-search-panel hidden";
            const externalNameInput = document.createElement("input");
            externalNameInput.type = "text";
            externalNameInput.className = "casting-search-input";
            externalNameInput.placeholder = "Name";
            const externalEmailInput = document.createElement("input");
            externalEmailInput.type = "email";
            externalEmailInput.className = "casting-search-input";
            externalEmailInput.placeholder = "Email (for rehearsal notices)";
            externalEmailInput.style.marginTop = "var(--space-1)";
            const externalActions = document.createElement("div");
            externalActions.className = "casting-search-top";
            const externalSaveBtn = document.createElement("button");
            externalSaveBtn.type = "button";
            externalSaveBtn.className = "subtle-btn";
            externalSaveBtn.textContent = "Save";
            const externalCancelBtn = document.createElement("button");
            externalCancelBtn.type = "button";
            externalCancelBtn.className = "casting-search-close";
            externalCancelBtn.textContent = "✕";
            externalActions.appendChild(externalSaveBtn);
            externalActions.appendChild(externalCancelBtn);
            externalPanel.appendChild(externalNameInput);
            externalPanel.appendChild(externalEmailInput);
            externalPanel.appendChild(externalActions);

            noAccountToggleBtn.addEventListener("click", () => {
                assignRow.classList.add("hidden");
                externalPanel.classList.remove("hidden");
                externalNameInput.value = "";
                externalEmailInput.value = "";
                externalNameInput.focus();
            });
            externalCancelBtn.addEventListener("click", () => {
                externalPanel.classList.add("hidden");
                assignRow.classList.remove("hidden");
            });
            externalSaveBtn.addEventListener("click", async () => {
                const name = externalNameInput.value.trim();
                if (!name) { externalNameInput.focus(); return; }
                const ok = await doAssignPrincipalExternal(
                    castingData.opera.id, cast.id, role.name, name, externalEmailInput.value.trim()
                );
                if (ok) {
                    externalPanel.classList.add("hidden");
                    assignRow.classList.remove("hidden");
                }
            });

            // --- Search panel ---
            const searchPanel = document.createElement("div");
            searchPanel.className = "casting-search-panel hidden";
            const searchTop = document.createElement("div");
            searchTop.className = "casting-search-top";
            const searchInput = document.createElement("input");
            searchInput.type = "text";
            searchInput.className = "casting-search-input";
            searchInput.placeholder = "Search singers…";
            const searchCloseBtn = document.createElement("button");
            searchCloseBtn.type = "button";
            searchCloseBtn.className = "casting-search-close";
            searchCloseBtn.textContent = "✕";
            searchTop.appendChild(searchInput);
            searchTop.appendChild(searchCloseBtn);
            const searchResults = document.createElement("div");
            searchResults.className = "casting-search-results";
            searchPanel.appendChild(searchTop);
            searchPanel.appendChild(searchResults);

            cell.appendChild(assignRow);
            cell.appendChild(searchPanel);
            cell.appendChild(externalPanel);

            // Toggle into search mode
            searchToggleBtn.addEventListener("click", () => {
                assignRow.classList.add("hidden");
                searchPanel.classList.remove("hidden");
                searchInput.value = "";
                searchResults.innerHTML = "";
                searchInput.focus();
            });

            // Back to select mode
            searchCloseBtn.addEventListener("click", () => {
                searchPanel.classList.add("hidden");
                assignRow.classList.remove("hidden");
            });

            // Filter as user types
            searchInput.addEventListener("input", () => {
                const query = searchInput.value.toLowerCase().trim();
                searchResults.innerHTML = "";
                if (!query) return;
                const matches = compatibleStudents.filter(s => s.name.toLowerCase().includes(query));
                if (!matches.length) {
                    const noResult = document.createElement("div");
                    noResult.className = "casting-no-results";
                    noResult.textContent = "Singer not found";
                    searchResults.appendChild(noResult);
                    return;
                }
                matches.forEach(student => {
                    const item = document.createElement("div");
                    item.className = "casting-search-result-item";
                    const conflict = sameCastCastings.find(
                        c => c.student_id === student.id && c.role !== role.name
                    );
                    if (conflict) {
                        item.textContent = `${student.name} (already ${conflict.role})`;
                        item.classList.add("casting-result-conflict");
                    } else {
                        item.textContent = student.name;
                        item.addEventListener("click", async () => {
                            const ok = await doAssignPrincipal(
                                castingData.opera.id, cast.id, role.name, student.id
                            );
                            if (ok) {
                                searchPanel.classList.add("hidden");
                                assignRow.classList.remove("hidden");
                            }
                        });
                    }
                    searchResults.appendChild(item);
                });
            });

            // --- Covers section ---
            const coversSection = document.createElement("div");
            coversSection.className = "covers-section";

            const coversLabel = document.createElement("div");
            coversLabel.className = "covers-label";
            coversLabel.textContent = "Covers";
            coversSection.appendChild(coversLabel);

            const coverPills = document.createElement("div");
            coverPills.className = "cover-pills";

            const existingCovers = (castingData.covers || []).filter(
                cv => cv.cast_id === cast.id && cv.role_name === role.name
            );
            existingCovers.forEach(cv => {
                const pill = document.createElement("span");
                pill.className = "cover-pill";
                pill.textContent = cv.student_name;
                const removeX = document.createElement("button");
                removeX.type = "button";
                removeX.className = "cover-pill-remove";
                removeX.textContent = "×";
                removeX.title = "Remove cover";
                removeX.addEventListener("click", async () => {
                    await removeCover(cv.id, castingData.opera.id);
                });
                pill.appendChild(removeX);
                coverPills.appendChild(pill);
            });
            coversSection.appendChild(coverPills);

            // Add cover button + inline search
            const addCoverBtn = document.createElement("button");
            addCoverBtn.type = "button";
            addCoverBtn.className = "add-cover-btn";
            addCoverBtn.textContent = "+ Add Cover";
            coversSection.appendChild(addCoverBtn);

            const coverSearchWrap = document.createElement("div");
            coverSearchWrap.className = "cover-search-wrap hidden";
            const coverSearchInput = document.createElement("input");
            coverSearchInput.type = "text";
            coverSearchInput.className = "cover-search-input";
            coverSearchInput.placeholder = "Search singers…";
            const coverSearchResults = document.createElement("div");
            coverSearchResults.className = "cover-search-results";
            const coverSearchClose = document.createElement("button");
            coverSearchClose.type = "button";
            coverSearchClose.className = "casting-search-close";
            coverSearchClose.textContent = "✕";
            const coverSearchTop = document.createElement("div");
            coverSearchTop.className = "casting-search-top";
            coverSearchTop.appendChild(coverSearchInput);
            coverSearchTop.appendChild(coverSearchClose);
            coverSearchWrap.appendChild(coverSearchTop);
            coverSearchWrap.appendChild(coverSearchResults);
            coversSection.appendChild(coverSearchWrap);

            addCoverBtn.addEventListener("click", () => {
                addCoverBtn.classList.add("hidden");
                coverSearchWrap.classList.remove("hidden");
                coverSearchInput.value = "";
                coverSearchResults.innerHTML = "";
                coverSearchInput.focus();
            });

            coverSearchClose.addEventListener("click", () => {
                coverSearchWrap.classList.add("hidden");
                addCoverBtn.classList.remove("hidden");
            });

            coverSearchInput.addEventListener("input", () => {
                const q = coverSearchInput.value.toLowerCase().trim();
                coverSearchResults.innerHTML = "";
                if (!q) return;
                const alreadyCoverIds = new Set(existingCovers.map(cv => cv.student_id));
                const matches = castingData.all_students.filter(s =>
                    s.name.toLowerCase().includes(q) && !alreadyCoverIds.has(s.id)
                );
                if (!matches.length) {
                    const noR = document.createElement("div");
                    noR.className = "casting-no-results";
                    noR.textContent = "Singer not found";
                    coverSearchResults.appendChild(noR);
                    return;
                }
                matches.forEach(student => {
                    const item = document.createElement("div");
                    item.className = "casting-search-result-item";
                    item.textContent = student.name + (student.voice_type ? ` (${student.voice_type})` : "");
                    item.addEventListener("click", async () => {
                        await addCover(castingData.opera.id, cast.id, role.name, student.id);
                        coverSearchWrap.classList.add("hidden");
                        addCoverBtn.classList.remove("hidden");
                    });
                    coverSearchResults.appendChild(item);
                });
            });

            cell.appendChild(coversSection);

            row.appendChild(cell);
        });

        container.appendChild(row);
    });
}

async function doAssignPrincipal(operaId, castId, roleName, studentId) {
    try {
        const res = await fetch(`${API}/admin/assign-principal`, {
            credentials: "include",
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ opera_id: operaId, cast_id: castId, role_name: roleName, student_id: studentId }),
        });
        const data = await res.json();
        if (data.status !== "success") {
            alert(data.message || "Failed to save.");
            return false;
        }
        await loadCastingForOpera(operaId);
        renderAssignRolesGrid();
        renderAssignRolesGrid("edit-prod-roles-grid");
        return true;
    } catch (err) {
        console.error(err);
        alert("Server error.");
        return false;
    }
}

async function doAssignPrincipalExternal(operaId, castId, roleName, name, email) {
    try {
        const res = await fetch(`${API}/admin/assign-principal`, {
            credentials: "include",
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ opera_id: operaId, cast_id: castId, role_name: roleName, external_name: name, external_email: email }),
        });
        const data = await res.json();
        if (data.status !== "success") {
            alert(data.message || "Failed to save.");
            return false;
        }
        await loadCastingForOpera(operaId);
        renderAssignRolesGrid();
        renderAssignRolesGrid("edit-prod-roles-grid");
        return true;
    } catch (err) {
        console.error(err);
        alert("Server error.");
        return false;
    }
}

async function addCover(operaId, castId, roleName, studentId) {
    try {
        const res = await fetch(`${API}/admin/covers`, {
            credentials: "include",
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({opera_id: operaId, cast_id: castId, role_name: roleName, student_id: studentId}),
        });
        const data = await res.json();
        if (data.status !== "success") {
            alert(data.message || "Failed to add cover.");
            return;
        }
        await loadCastingForOpera(operaId);
        renderAssignRolesGrid();
        renderAssignRolesGrid("edit-prod-roles-grid");
    } catch (err) {
        console.error(err);
        alert("Server error.");
    }
}

async function removeCover(coverId, operaId) {
    if (!confirm("Remove this cover?")) return;
    try {
        const res = await fetch(`${API}/admin/covers/${coverId}`, {
            credentials: "include",
            method: "DELETE",
        });
        const data = await res.json();
        if (data.status !== "success") {
            alert(data.message || "Failed to remove cover.");
            return;
        }
        await loadCastingForOpera(operaId);
        renderAssignRolesGrid();
        renderAssignRolesGrid("edit-prod-roles-grid");
    } catch (err) {
        console.error(err);
        alert("Server error.");
    }
}

async function onPrincipalAssignmentChange(e) {
    const select = e.target;
    await doAssignPrincipal(
        Number(select.dataset.operaId),
        Number(select.dataset.castId),
        select.dataset.roleName,
        select.value ? Number(select.value) : null
    );
}
// -----------------------------------------------------------
// PRODUCTION STAFF (inside Casting tab)
// -----------------------------------------------------------

let staffData = { staff: [], teachers: [] };

const STAFF_ROLE_LABELS = {
    "director": "Director",
    "assistant_director": "Assistant Director",
    "stage_manager": "Stage Manager",
    "assistant_stage_manager": "Assistant Stage Manager",
    "conductor": "Conductor",
    "assistant_conductor": "Assistant Conductor",
    "orchestra_manager": "Orchestra Manager",
};

async function loadStaffForOpera(operaId) {
    const box = document.getElementById("staff-list");
    box.innerHTML = `<em class="empty-note">Loading…</em>`;

    try {
        const res = await fetch(`${API}/admin/opera-staff/${operaId}`, { credentials: "include" });
        const data = await res.json();
        staffData = data;
        renderStaffList();
    } catch (e) {
        console.error(e);
        box.innerHTML = `<em class="empty-note">Failed to load staff.</em>`;
    }
}

function renderStaffList() {
    const box = document.getElementById("staff-list");
    if (!staffData.staff || staffData.staff.length === 0) {
        box.innerHTML = `<em class="empty-note">No staff assigned yet.</em>`;
        return;
    }

    // Group by role
    const byRole = {};
    staffData.staff.forEach(s => {
        if (!byRole[s.staff_role]) byRole[s.staff_role] = [];
        byRole[s.staff_role].push(s);
    });

    const roleOrder = ["director", "assistant_director", "stage_manager", "assistant_stage_manager", "conductor", "assistant_conductor", "orchestra_manager"];
    let html = "";

    roleOrder.forEach(role => {
        if (!byRole[role]) return;
        html += `<div class="staff-role-group">`;
        html += `<h5>${STAFF_ROLE_LABELS[role]}</h5>`;
        html += `<ul>`;
        byRole[role].forEach(s => {
            html += `
                <li>
                    ${escapeHtml(s.teacher_name)}
                    <button class="remove-staff-btn" data-staff-id="${s.id}" data-name="${escapeHtml(s.teacher_name)}" data-role="${STAFF_ROLE_LABELS[role]}">✕</button>
                </li>
            `;
        });
        html += `</ul></div>`;
    });

    box.innerHTML = html;

    box.querySelectorAll(".remove-staff-btn").forEach(btn => {
        btn.addEventListener("click", onRemoveStaffClick);
    });
}

async function onRemoveStaffClick(e) {
    const btn = e.currentTarget;
    const staffId = Number(btn.dataset.staffId);
    const name = btn.dataset.name;
    const role = btn.dataset.role;

    const ok = confirm(`Remove ${name} as ${role}?`);
    if (!ok) return;

    try {
        const res = await fetch(`${API}/admin/remove-staff`, {
            credentials: "include",
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ staff_id: staffId })
        });
        const data = await res.json();
        if (data.status !== "success") {
            alert(data.message || "Failed.");
            return;
        }
        loadStaffForOpera(castingData.opera.id);
    } catch (err) {
        console.error(err);
        alert("Server error.");
    }
}

function openAddStaffModal() {
    if (!castingData) return;

    const teacherSelect = document.getElementById("staff-teacher");
    teacherSelect.innerHTML = "";
    if (staffData.teachers && staffData.teachers.length > 0) {
        staffData.teachers.forEach(t => {
            const opt = document.createElement("option");
            opt.value = t.id;
            opt.textContent = `${t.name} — ${STAFF_ROLE_LABELS[t.admin_role] || t.admin_role}`;
            teacherSelect.appendChild(opt);
        });
    } else {
        teacherSelect.innerHTML = `<option value="">No admins with roles found</option>`;
    }

    document.getElementById("staff-msg").textContent = "";
    document.getElementById("add-staff-modal").classList.remove("hidden");
}

function closeAddStaffModal() {
    document.getElementById("add-staff-modal").classList.add("hidden");
}

async function saveStaff() {
    const msg = document.getElementById("staff-msg");
    msg.textContent = "";

    const teacherId = Number(document.getElementById("staff-teacher").value);

    if (!teacherId) {
        msg.textContent = "Please pick an admin.";
        return;
    }

    try {
        const res = await fetch(`${API}/admin/assign-staff`, {
            credentials: "include",
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                opera_id: castingData.opera.id,
                teacher_id: teacherId,
            })
        });
        const data = await res.json();
        if (data.status === "success") {
            closeAddStaffModal();
            loadStaffForOpera(castingData.opera.id);
        } else {
            msg.textContent = data.message || "Failed.";
        }
    } catch (err) {
        console.error(err);
        msg.textContent = "Server error.";
    }
}


// --- Manage Opera Roster modal (unchanged from before) ---

function openManageStudentsModal() {
    if (!castingData) return;
    document.getElementById("manage-students-title").textContent =
        `Manage Roster — ${castingData.opera.name}`;

    const list = document.getElementById("manage-students-list");

    const assignedIds = new Set(castingData.assigned_students.map(s => s.id));

    const byVoice = {};
    castingData.all_students.forEach(s => {
        const v = (s.voice_type || "unknown").toLowerCase();
        if (!byVoice[v]) byVoice[v] = [];
        byVoice[v].push(s);
    });

    let html = "";
    VOICE_SORT_ORDER.forEach(voice => {
        if (!byVoice[voice] || byVoice[voice].length === 0) return;
        html += `<h4 class="manage-voice-header">${voice}</h4>`;
        html += `<div class="manage-student-list">`;
        byVoice[voice]
            .sort((a, b) => a.name.localeCompare(b.name))
            .forEach(s => {
                const checked = assignedIds.has(s.id) ? "checked" : "";
                html += `
                    <label class="manage-student-label">
                        <input type="checkbox" class="manage-student-check"
                               data-student-id="${s.id}" ${checked}>
                        ${escapeHtml(s.name)}
                    </label>
                `;
            });
        html += `</div>`;
    });
    list.innerHTML = html || `<em class="empty-note">No vocalists in the system yet.</em>`;

    list.querySelectorAll(".manage-student-check").forEach(cb => {
        cb.addEventListener("change", onManageStudentToggle);
    });

    document.getElementById("manage-students-modal").classList.remove("hidden");
}

async function onManageStudentToggle(e) {
    const cb = e.target;
    const studentId = Number(cb.dataset.studentId);
    const operaId = castingData.opera.id;

    try {
        if (cb.checked) {
            await fetch(`${API}/admin/add-to-opera`, {
            credentials: "include",
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({opera_id: operaId, student_ids: [studentId]})
            });
        } else {
            await fetch(`${API}/admin/remove-from-opera`, {
            credentials: "include",
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({opera_id: operaId, student_id: studentId})
            });
        }
        await loadCastingForOpera(operaId);
    } catch (err) {
        console.error(err);
        alert("Server error.");
        cb.checked = !cb.checked;
    }
}

function closeManageStudentsModal() {
    document.getElementById("manage-students-modal").classList.add("hidden");
}
// -----------------------------------------------------------
// 4. REHEARSALS
// -----------------------------------------------------------

let rehearsalOperaData = null;  // cached casts/roles/staff for the selected opera

async function loadRehearsalOperas() {
    const res = await fetch(`${API}/operas`, { credentials: "include" });
    const data = await res.json();

    rehearsalOpera.innerHTML = "";
    data.forEach(o => {
        const opt = document.createElement("option");
        opt.value = o.id;
        opt.textContent = o.name;
        rehearsalOpera.appendChild(opt);
    });

    if (rehearsalOpera.value) {
        loadRehearsalOperaData(rehearsalOpera.value);
    }
}

async function loadRehearsalOperaData(operaId) {
    // We need: casts, roles, production staff. We already have endpoints for each.
    try {
        const [castsRes, castingRes, staffRes] = await Promise.all([
            fetch(`${API}/admin/casts?opera_id=${operaId}`, { credentials: "include" }),
            fetch(`${API}/admin/opera-casting/${operaId}`, { credentials: "include" }),
            fetch(`${API}/admin/opera-leaders/${operaId}`, { credentials: "include" }),
        ]);
        const casts = await castsRes.json();
        const casting = await castingRes.json();
        const leaders = await staffRes.json();

        rehearsalOperaData = {
            operaId: Number(operaId),
            casts,
            roles: casting.roles || [],
            leaders,  // [{teacher_id, name, roles: [...]}]
        };

        renderCastCheckboxes();
        renderRoleCheckboxes();
        renderLeaderCheckboxes();
    } catch (e) {
        console.error(e);
    }
}

function renderCastCheckboxes() {
    const box = document.getElementById("rehearsal-casts-checkboxes");
    if (!rehearsalOperaData || !rehearsalOperaData.casts.length) {
        box.innerHTML = `<em class="empty-note">No casts.</em>`;
        return;
    }
    box.innerHTML = rehearsalOperaData.casts.map(c => `
        <label class="checkbox-pill">
            <input type="checkbox" class="rehearsal-cast-check" value="${c.id}" checked>
            ${escapeHtml(c.name)}
        </label>
    `).join("");
}

function renderRoleCheckboxes() {
    const box = document.getElementById("rehearsal-roles-checkboxes");
    if (!rehearsalOperaData || !rehearsalOperaData.roles.length) {
        box.innerHTML = `<em class="empty-note">No principal roles.</em>`;
        return;
    }
    // Sort by voice then name
    const sorted = [...rehearsalOperaData.roles].sort((a, b) => {
        const ai = VOICE_SORT_ORDER.indexOf((a.voice_type || "").toLowerCase());
        const bi = VOICE_SORT_ORDER.indexOf((b.voice_type || "").toLowerCase());
        const aIdx = ai < 0 ? 99 : ai;
        const bIdx = bi < 0 ? 99 : bi;
        if (aIdx !== bIdx) return aIdx - bIdx;
        return a.name.localeCompare(b.name);
    });
    box.innerHTML = sorted.map(r => `
        <label class="checkbox-pill">
            <input type="checkbox" class="rehearsal-role-check" value="${escapeHtml(r.name)}">
            ${escapeHtml(r.name)}
        </label>
    `).join("");
}

function renderLeaderCheckboxes() {
    const box = document.getElementById("rehearsal-leaders-checkboxes");
    if (!rehearsalOperaData || !rehearsalOperaData.leaders.length) {
        box.innerHTML = `<em class="empty-note">No staff assigned. Add production staff in the Casting tab first.</em>`;
        return;
    }
    box.innerHTML = rehearsalOperaData.leaders.map(l => {
        const roleLabels = (l.roles || []).map(r => STAFF_ROLE_LABELS[r] || r).join(", ");
        return `
            <label class="checkbox-pill">
                <input type="checkbox" class="rehearsal-leader-check" value="${l.teacher_id}">
                ${escapeHtml(l.name)}${roleLabels ? ` <span class="leader-role-hint">(${escapeHtml(roleLabels)})</span>` : ""}
            </label>
        `;
    }).join("");
}

function populateTimeDropdown(selectEl, startHour = 8, endHour = 22, stepMinutes = 15) {
    if (!selectEl) return;
    selectEl.innerHTML = "";

    for (let h = startHour; h <= endHour; h++) {
        for (let m = 0; m < 60; m += stepMinutes) {
            if (h === endHour && m > 0) continue;
            const hh = String(h).padStart(2, "0");
            const mm = String(m).padStart(2, "0");

            const opt = document.createElement("option");
            opt.value = `${hh}:${mm}`;
            opt.textContent = `${hh}:${mm}`;
            selectEl.appendChild(opt);
        }
    }
}

function onRehearsalKindChange() {
    const kind = document.getElementById("rehearsal-kind")?.value || "vocal";
    document.getElementById("rehearsal-vocal-fields")
        ?.classList.toggle("hidden", kind === "orchestra");
}

function onAttendanceTypeChange() {
    const attendanceType = document.getElementById("rehearsal-attendance").value;
    const rolesSection = document.getElementById("rehearsal-roles-section");
    if (attendanceType === "coaching") {
        rolesSection.classList.remove("hidden");
    } else {
        rolesSection.classList.add("hidden");
    }
}

async function createRehearsal() {
    const date = document.getElementById("rehearsal-date").value;
    const startTime = document.getElementById("rehearsal-start-time").value;
    const endTime = document.getElementById("rehearsal-end-time").value;
    const attendanceType = document.getElementById("rehearsal-attendance").value;

    const msgEl = document.getElementById("rehearsal-msg");
    msgEl.textContent = "";

    if (!date || !startTime || !endTime) {
        msgEl.textContent = "Please select date, start time, and end time.";
        return;
    }

    // Collect cast checkboxes
    const castChecks = document.querySelectorAll(".rehearsal-cast-check:checked");
    const castIds = Array.from(castChecks).map(cb => Number(cb.value));
    const allCastsChecked = castIds.length === (rehearsalOperaData?.casts.length || 0);
    // If all are checked, send an empty array (= all casts, opera-wide)
    const cast_ids = allCastsChecked ? [] : castIds;

    // Collect role checkboxes (coaching only)
    let role_names = [];
    if (attendanceType === "coaching") {
        const roleChecks = document.querySelectorAll(".rehearsal-role-check:checked");
        role_names = Array.from(roleChecks).map(cb => cb.value);
        if (role_names.length === 0) {
            msgEl.textContent = "Coaching rehearsals need at least one role.";
            return;
        }
    }

    // Collect leader checkboxes
    const leaderChecks = document.querySelectorAll(".rehearsal-leader-check:checked");
    const leader_ids = Array.from(leaderChecks).map(cb => Number(cb.value));

    const startISO = new Date(`${date}T${startTime}`).toISOString();
    const endISO = new Date(`${date}T${endTime}`).toISOString();

    const res = await fetch(`${API}/admin/create-rehearsal`, {
            credentials: "include",
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
            opera_id: Number(rehearsalOpera.value),
            attendance_type: attendanceType,
            rehearsal_type: (USER_ROLE === "orchestra_admin" || ORG_TYPE === "orchestra")
                ? "orchestra"
                : (["head_admin", "system_admin"].includes(USER_ROLE)
                    ? (document.getElementById("rehearsal-kind")?.value || "vocal")
                    : "vocal"),
            cast_ids,
            role_names,
            leader_ids,
            start_time: startISO,
            end_time: endISO,
            notes: document.getElementById("rehearsal-notes").value,
            location: (document.getElementById("rehearsal-location")?.value || "").trim(),
        })
    });

    const data = await res.json();

    if (data.status === "success") {
        const cancelled = data.lessons_cancelled || 0;
        msgEl.textContent = cancelled > 0
            ? `Rehearsal created. ${cancelled} conflicting lesson${cancelled === 1 ? "" : "s"} cancelled and student${cancelled === 1 ? "" : "s"} notified.`
            : "Rehearsal created.";
        document.getElementById("rehearsal-notes").value = "";
        // Reset role checks (keep casts + leaders as-is for convenience)
        document.querySelectorAll(".rehearsal-role-check").forEach(cb => cb.checked = false);
        loadAdminRehearsals();
        setTimeout(() => document.getElementById("rehearsal-create-modal")?.classList.add("hidden"), 1200);
    } else {
        msgEl.textContent = data.message || "Failed to create rehearsal.";
    }
}

function updateAdminBulkPreview() {
    const from = document.getElementById("reh-admin-from")?.value;
    const to = document.getElementById("reh-admin-to")?.value;
    const days = [...document.querySelectorAll("#reh-admin-days input:checked")].map(cb => cb.value);
    const preview = document.getElementById("reh-admin-bulk-preview");
    if (!preview) return;
    if (!from || !to || !days.length) { preview.textContent = ""; return; }
    const sd = new Date(from + "T00:00:00");
    const ed = new Date(to + "T00:00:00");
    if (ed < sd) { preview.textContent = "End date must be after start date."; return; }
    const DAY_JS = {monday:1,tuesday:2,wednesday:3,thursday:4,friday:5,saturday:6,sunday:0};
    const dayNums = days.map(d => DAY_JS[d]);
    let count = 0, cur = new Date(sd);
    while (cur <= ed) {
        if (dayNums.includes(cur.getDay())) count++;
        cur.setDate(cur.getDate() + 1);
    }
    preview.textContent = count > 0
        ? `This will create ${count} rehearsal${count !== 1 ? "s" : ""}.`
        : "No rehearsals match this selection.";
}

async function createBulkAdminRehearsal() {
    const msgEl = document.getElementById("rehearsal-msg");
    msgEl.textContent = "";

    const startTime = document.getElementById("rehearsal-start-time").value;
    const endTime = document.getElementById("rehearsal-end-time").value;
    const attendanceType = document.getElementById("rehearsal-attendance").value;
    const from = document.getElementById("reh-admin-from")?.value;
    const to = document.getElementById("reh-admin-to")?.value;
    const days = [...document.querySelectorAll("#reh-admin-days input:checked")].map(cb => cb.value);

    if (!from || !to) { msgEl.textContent = "Please select from and to dates."; return; }
    if (!days.length) { msgEl.textContent = "Select at least one day of the week."; return; }
    if (!startTime || !endTime) { msgEl.textContent = "Please select start and end time."; return; }

    const castChecks = document.querySelectorAll(".rehearsal-cast-check:checked");
    const castIds = Array.from(castChecks).map(cb => Number(cb.value));
    const allCastsChecked = castIds.length === (rehearsalOperaData?.casts.length || 0);
    const cast_ids = allCastsChecked ? [] : castIds;

    let role_names = [];
    if (attendanceType === "coaching") {
        const roleChecks = document.querySelectorAll(".rehearsal-role-check:checked");
        role_names = Array.from(roleChecks).map(cb => cb.value);
        if (role_names.length === 0) { msgEl.textContent = "Coaching rehearsals need at least one role."; return; }
    }

    const leaderChecks = document.querySelectorAll(".rehearsal-leader-check:checked");
    const leader_ids = Array.from(leaderChecks).map(cb => Number(cb.value));

    const rehearsal_type = (USER_ROLE === "orchestra_admin" || ORG_TYPE === "orchestra")
        ? "orchestra"
        : (["head_admin", "system_admin"].includes(USER_ROLE)
            ? (document.getElementById("rehearsal-kind")?.value || "vocal")
            : "vocal");

    const btn = document.getElementById("create-rehearsal-btn");
    btn.disabled = true;
    const origText = btn.textContent;
    btn.textContent = "Scheduling...";

    const res = await fetch(`${API}/admin/rehearsals/bulk`, {
        credentials: "include",
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
            opera_id: Number(rehearsalOpera.value),
            attendance_type: attendanceType,
            rehearsal_type,
            cast_ids,
            role_names,
            leader_ids,
            start_date: from,
            end_date: to,
            days,
            start_time: startTime,
            end_time: endTime,
            notes: document.getElementById("rehearsal-notes").value,
            location: (document.getElementById("rehearsal-location")?.value || "").trim(),
        })
    });

    btn.disabled = false;
    btn.textContent = origText;

    const data = await res.json();
    if (data.status === "success") {
        msgEl.textContent = `${data.created} rehearsal${data.created !== 1 ? "s" : ""} scheduled.`;
        document.getElementById("rehearsal-notes").value = "";
        document.getElementById("reh-admin-from").value = "";
        document.getElementById("reh-admin-to").value = "";
        document.querySelectorAll("#reh-admin-days input").forEach(cb => cb.checked = false);
        const prev = document.getElementById("reh-admin-bulk-preview");
        if (prev) prev.textContent = "";
        loadAdminRehearsals();
        setTimeout(() => document.getElementById("rehearsal-create-modal")?.classList.add("hidden"), 1200);
    } else {
        msgEl.textContent = data.message || "Failed to schedule rehearsals.";
    }
}

// -----------------------------------------------------------
// SCHEDULED REHEARSALS (opera tabs + upcoming + past)
// -----------------------------------------------------------

let scheduledAllRehearsals = [];       // all rehearsals, as returned by API
let scheduledSelectedOpera = null;     // opera name currently active in sub-tab
let scheduledPastExpanded = false;     // collapsible state for past rehearsals

async function loadAdminRehearsals() {
    try {
        const res = await fetch(`${API}/admin/rehearsals`, { credentials: "include" });
        const data = await res.json();
        scheduledAllRehearsals = Array.isArray(data) ? data : [];
        renderScheduledRehearsals();
    } catch (e) {
        console.error(e);
        const upcoming = document.getElementById("scheduled-upcoming");
        if (upcoming) upcoming.innerHTML = `<em class="empty-note">Failed to load rehearsals.</em>`;
    }
}

function renderScheduledRehearsals() {
    const operaTabsBox = document.getElementById("scheduled-opera-tabs");
    const upcomingBox = document.getElementById("scheduled-upcoming");
    const pastBox = document.getElementById("scheduled-past");
    const pastToggle = document.getElementById("scheduled-past-toggle");

    if (!operaTabsBox || !upcomingBox || !pastBox) return;

    // Empty state
    if (scheduledAllRehearsals.length === 0) {
        const emptyMsg = (typeof ORG_TYPE !== "undefined" && ORG_TYPE === "orchestra")
            ? "No rehearsals scheduled yet."
            : "No operas have rehearsals yet.";
        operaTabsBox.innerHTML = `<em class="empty-note">${emptyMsg}</em>`;
        upcomingBox.innerHTML = "";
        pastBox.innerHTML = "";
        pastToggle.textContent = "▶ Show past rehearsals (0)";
        return;
    }

    // Unique opera names in insertion order (rehearsals are already sorted by start_time)
    const operaNames = [];
    scheduledAllRehearsals.forEach(r => {
        if (!operaNames.includes(r.opera)) operaNames.push(r.opera);
    });

    // Pick active opera — prefer existing selection, else first one
    if (!scheduledSelectedOpera || !operaNames.includes(scheduledSelectedOpera)) {
        scheduledSelectedOpera = operaNames[0];
    }

    // Render sub-tabs
    operaTabsBox.innerHTML = operaNames.map(name => `
        <button class="sub-tab-btn ${name === scheduledSelectedOpera ? "active" : ""}"
                data-opera-name="${escapeHtml(name)}">
            ${escapeHtml(name)}
        </button>
    `).join("");

    operaTabsBox.querySelectorAll(".sub-tab-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            scheduledSelectedOpera = btn.dataset.operaName;
            renderScheduledRehearsals();
        });
    });

    // Split rehearsals for the selected opera into upcoming vs past
    const now = new Date();
    const forOpera = scheduledAllRehearsals.filter(r => r.opera === scheduledSelectedOpera);
    const upcoming = forOpera.filter(r => new Date(r.end_time) >= now);
    const past = forOpera.filter(r => new Date(r.end_time) < now);

    // Upcoming — chronological (earliest first)
    upcoming.sort((a, b) => new Date(a.start_time) - new Date(b.start_time));
    // Past — reverse chronological (most recent first)
    past.sort((a, b) => new Date(b.start_time) - new Date(a.start_time));

    upcomingBox.innerHTML = upcoming.length === 0
        ? `<em class="empty-note">No upcoming rehearsals.</em>`
        : upcoming.map(renderRehearsalRow).join("");

    pastBox.innerHTML = past.length === 0
        ? `<em class="empty-note">No past rehearsals.</em>`
        : past.map(renderRehearsalRow).join("");

    pastToggle.textContent = `${scheduledPastExpanded ? "▼" : "▶"} ${scheduledPastExpanded ? "Hide" : "Show"} past rehearsals (${past.length})`;
    pastBox.classList.toggle("hidden", !scheduledPastExpanded);

    // Wire up action buttons
    [upcomingBox, pastBox].forEach(box => {
        box.querySelectorAll(".call-singers-btn").forEach(btn => {
            btn.addEventListener("click", () => openCallSingersModal(Number(btn.dataset.id)));
        });
        box.querySelectorAll(".cancel-rehearsal-btn").forEach(btn => {
            btn.addEventListener("click", () => cancelRehearsal(Number(btn.dataset.id)));
        });
        box.querySelectorAll(".add-reh-notes-btn").forEach(btn => {
            btn.addEventListener("click", () => openAddRehearsalNotesModal(Number(btn.dataset.id)));
        });
        box.querySelectorAll(".view-reh-notes-btn").forEach(btn => {
            btn.addEventListener("click", () => openViewRehearsalNotesModal(Number(btn.dataset.id)));
        });
        box.querySelectorAll(".edit-rehearsal-btn").forEach(btn => {
            btn.addEventListener("click", () => openEditRehearsalModal(Number(btn.dataset.id)));
        });
        box.querySelectorAll(".absence-count-btn").forEach(btn => {
            btn.addEventListener("click", () => openAbsencesModal(Number(btn.dataset.id)));
        });
    });
}

function renderRehearsalRow(r) {
    const attendanceLabel = {
        principals: "Principals only",
        chorus: "Chorus only",
        full: "Full cast",
        coaching: "Coaching",
    };

    const start = new Date(r.start_time);
    const end = new Date(r.end_time);

    let castDisplay;
    if (r.attendance_type === "chorus") {
        castDisplay = "Chorus";
    } else if (r.casts && r.casts.length > 0) {
        castDisplay = r.casts.join(", ");
    } else if (r.cast) {
        castDisplay = r.cast;
    } else {
        castDisplay = "All casts";
    }

    const rolesLine = (r.roles && r.roles.length > 0)
        ? `<br><span class="rehearsal-roles">Roles: ${escapeHtml(r.roles.join(", "))}</span>`
        : "";

    const leadersLine = (r.leaders && r.leaders.length > 0)
        ? `<br><span class="rehearsal-leaders">Led by: ${escapeHtml(r.leaders.join(", "))}</span>`
        : "";

    const locationLine = r.location
        ? `<br><span class="rehearsal-location">📍 ${escapeHtml(r.location)}</span>`
        : "";

    const callSingersBtn = r.rehearsal_type === "orchestra"
        ? `<button class="subtle-btn call-singers-btn" data-id="${r.id}">Call Singers</button>`
        : "";

    const absenceBtn = r.absence_count > 0
        ? `<button class="subtle-btn absence-count-btn" data-id="${r.id}">${r.absence_count} Absence${r.absence_count !== 1 ? "s" : ""}</button>`
        : "";

    return `
        <div class="rehearsal-row">
            <div class="rehearsal-row-header">
                <div>
                    <strong>${escapeHtml(r.opera)}</strong>
                    <span class="rehearsal-cast">(${escapeHtml(castDisplay)})</span>
                    <span class="rehearsal-attendance">${attendanceLabel[r.attendance_type] || ""}</span>
                </div>
                <div class="rehearsal-row-actions">
                    ${absenceBtn}
                    ${callSingersBtn}
                </div>
            </div>
            <br>
            ${start.toLocaleDateString()}
            ${start.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}
            –
            ${end.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}
            ${locationLine}
            ${rolesLine}
            ${leadersLine}
            ${r.notes ? `<br><em class="rehearsal-notes-preview">${renderNotes(r.notes)}</em>` : ""}
            <div class="rehearsal-row-footer">
                <div class="rehearsal-row-footer-left">
                    <button class="subtle-btn add-reh-notes-btn" data-id="${r.id}">Create Rehearsal Notes</button>
                    <button class="subtle-btn view-reh-notes-btn" data-id="${r.id}">View Rehearsal Notes</button>
                    <button class="subtle-btn edit-rehearsal-btn" data-id="${r.id}">Edit</button>
                </div>
                <button class="subtle-btn danger-btn cancel-rehearsal-btn" data-id="${r.id}">Cancel</button>
            </div>
        </div>
    `;
}


// -----------------------------------------------------------
// REHEARSAL ABSENCES
// -----------------------------------------------------------

async function openAbsencesModal(rehearsalId) {
    const r = scheduledAllRehearsals.find(x => x.id === rehearsalId);
    const title = document.getElementById("reh-absences-title");
    const list = document.getElementById("reh-absences-list");
    if (!title || !list) return;

    const dateStr = r ? new Date(r.start_time).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) : "";
    title.textContent = `Absences${dateStr ? " — " + dateStr : ""}`;
    list.innerHTML = `<em class="empty-note">Loading…</em>`;
    document.getElementById("reh-absences-modal").classList.remove("hidden");

    try {
        const res = await fetch(`${API}/admin/rehearsals/${rehearsalId}/absences`, { credentials: "include" });
        const data = await res.json();
        if (!data.length) {
            list.innerHTML = `<em class="empty-note">No absences recorded.</em>`;
            return;
        }
        list.innerHTML = "";
        data.forEach(a => {
            const div = document.createElement("div");
            div.className = "absence-entry";
            div.innerHTML = `
                <strong>${escapeHtml(a.name)}</strong>
                ${a.reason ? `<span class="absence-reason-label">${escapeHtml(a.reason)}</span>` : ""}
                ${a.note ? `<p class="hint" style="margin:4px 0 0;">${escapeHtml(a.note)}</p>` : ""}
            `;
            list.appendChild(div);
        });
    } catch (e) {
        list.innerHTML = `<em class="empty-note">Failed to load absences.</em>`;
    }
}


// -----------------------------------------------------------
// REHEARSAL NOTES
// -----------------------------------------------------------

let activeNotesRehearsalId = null;

function openViewRehearsalNotesModal(rehearsalId) {
    const r = scheduledAllRehearsals.find(x => x.id === rehearsalId);
    if (!r) return;
    document.getElementById("view-notes-title").textContent = `Rehearsal Notes — ${r.opera}`;
    const body = document.getElementById("view-notes-body");
    body.textContent = r.notes || "";
    body.innerHTML = r.notes
        ? r.notes.split("\n").map(l => `<p style="margin:0 0 6px;">${renderNotes(l)}</p>`).join("")
        : `<em class="empty-note">No notes for this rehearsal yet.</em>`;
    document.getElementById("reh-view-notes-modal").classList.remove("hidden");
}

function openAddRehearsalNotesModal(rehearsalId) {
    const r = scheduledAllRehearsals.find(x => x.id === rehearsalId);
    if (!r) return;
    activeNotesRehearsalId = rehearsalId;
    document.getElementById("add-notes-title").textContent = `Create Rehearsal Notes — ${r.opera}`;
    document.getElementById("add-notes-textarea").value = r.notes || "";
    document.getElementById("add-notes-msg").textContent = "";
    document.getElementById("reh-add-notes-modal").classList.remove("hidden");
}

async function sendRehearsalNotes() {
    if (!activeNotesRehearsalId) return;
    const notes = document.getElementById("add-notes-textarea").value.trim();
    const msgEl = document.getElementById("add-notes-msg");
    if (!notes) { msgEl.textContent = "Please enter some notes before sending."; return; }

    const btn = document.getElementById("send-reh-notes-btn");
    btn.disabled = true;
    btn.textContent = "Sending…";

    const res = await fetch(`${API}/admin/rehearsals/${activeNotesRehearsalId}/notes`, {
        method: "POST",
        credentials: "include",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({notes}),
    });

    btn.disabled = false;
    btn.textContent = "Send Notes";

    const data = await res.json();
    if (data.status === "success") {
        msgEl.className = "msg success-msg";
        msgEl.textContent = `Notes sent to ${data.emailed} recipient${data.emailed !== 1 ? "s" : ""}.`;
        // Update in-memory rehearsal so View Notes reflects the new content immediately
        const r = scheduledAllRehearsals.find(x => x.id === activeNotesRehearsalId);
        if (r) r.notes = notes;
        renderScheduledRehearsals();
        setTimeout(() => document.getElementById("reh-add-notes-modal")?.classList.add("hidden"), 1500);
    } else {
        msgEl.className = "msg";
        msgEl.textContent = data.message || "Failed to send notes.";
    }
}

// -----------------------------------------------------------
// EDIT REHEARSAL
// -----------------------------------------------------------

let activeEditRehearsalId = null;

function openEditRehearsalModal(rehearsalId) {
    const r = scheduledAllRehearsals.find(x => x.id === rehearsalId);
    if (!r) return;
    activeEditRehearsalId = rehearsalId;

    const start = new Date(r.start_time);
    const end = r.end_time ? new Date(r.end_time) : null;
    const pad = n => String(n).padStart(2, "0");
    document.getElementById("edit-reh-start").value = `${pad(start.getHours())}:${pad(start.getMinutes())}`;
    document.getElementById("edit-reh-end").value = end ? `${pad(end.getHours())}:${pad(end.getMinutes())}` : "";
    document.getElementById("edit-reh-location").value = r.location || "";
    document.getElementById("edit-reh-attendance").value = r.attendance_type || "full";
    document.getElementById("edit-reh-notes").value = r.notes || "";
    document.getElementById("reh-edit-title").textContent = `Edit Rehearsal — ${r.opera}`;
    document.getElementById("reh-edit-msg").textContent = "";
    document.getElementById("reh-edit-modal").classList.remove("hidden");
}

async function saveRehearsalEdit() {
    const msg = document.getElementById("reh-edit-msg");
    msg.textContent = "";
    const r = scheduledAllRehearsals.find(x => x.id === activeEditRehearsalId);
    if (!r) return;

    const startTime = document.getElementById("edit-reh-start").value;
    const endTime = document.getElementById("edit-reh-end").value;
    const location = document.getElementById("edit-reh-location").value.trim();
    const attendance_type = document.getElementById("edit-reh-attendance").value;
    const notes = document.getElementById("edit-reh-notes").value.trim();

    if (!startTime) { msg.textContent = "Start time is required."; return; }

    // Reconstruct ISO datetimes using the original date
    const origStart = new Date(r.start_time);
    const [sh, sm] = startTime.split(":").map(Number);
    origStart.setHours(sh, sm, 0, 0);
    let endISO = null;
    if (endTime) {
        const origEnd = new Date(r.start_time);
        const [eh, em] = endTime.split(":").map(Number);
        origEnd.setHours(eh, em, 0, 0);
        endISO = origEnd.toISOString();
    }

    const btn = document.getElementById("save-reh-edit-btn");
    btn.disabled = true;
    btn.textContent = "Saving…";
    try {
        const res = await fetch(`${API}/admin/rehearsals/${activeEditRehearsalId}`, {
            method: "PUT", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                start_time: origStart.toISOString(),
                end_time: endISO,
                location,
                attendance_type,
                notes,
            }),
        });
        const data = await res.json();
        if (data.status === "success") {
            // Update in-memory
            r.start_time = origStart.toISOString();
            r.end_time = endISO;
            r.location = location;
            r.attendance_type = attendance_type;
            r.notes = notes;
            renderScheduledRehearsals();
            document.getElementById("reh-edit-modal").classList.add("hidden");
        } else {
            msg.className = "msg"; msg.textContent = data.message || "Failed.";
        }
    } catch (e) { msg.textContent = "Server error."; }
    finally { btn.disabled = false; btn.textContent = "Save Changes"; }
}


// -----------------------------------------------------------
// CANCEL REHEARSAL
// -----------------------------------------------------------

async function cancelRehearsal(rehearsalId) {
    if (!confirm("Cancel this rehearsal? This cannot be undone.")) return;
    try {
        const res = await fetch(`${API}/admin/rehearsal/${rehearsalId}`, {
            method: "DELETE",
            credentials: "include",
        });
        const data = await res.json();
        if (data.status === "success") {
            scheduledAllRehearsals = scheduledAllRehearsals.filter(r => r.id !== rehearsalId);
            renderScheduledRehearsals();
        } else {
            alert(data.message || "Failed to cancel rehearsal.");
        }
    } catch (e) {
        console.error(e);
        alert("Server error.");
    }
}


// -----------------------------------------------------------
// CALL SINGERS
// -----------------------------------------------------------

let callSingersRehearsalId = null;
let callSingersData = null;

async function openCallSingersModal(rehearsalId) {
    callSingersRehearsalId = rehearsalId;
    callSingersData = null;

    const modal = document.getElementById("call-singers-modal");
    const msgEl = document.getElementById("call-singers-msg");
    const operaNameEl = document.getElementById("call-singers-opera-name");
    const scopeEl = document.getElementById("call-singers-scope");

    msgEl.textContent = "Loading…";
    msgEl.classList.remove("error-msg", "success-msg");
    operaNameEl.textContent = "";
    scopeEl.value = "tutti";
    document.getElementById("call-singers-cast-row").classList.add("hidden");
    document.getElementById("call-singers-role-row").classList.add("hidden");
    document.getElementById("call-singers-optional").checked = false;
    modal.classList.remove("hidden");

    try {
        const res = await fetch(`${API}/admin/rehearsal/${rehearsalId}/call-singers-data`, { credentials: "include" });
        callSingersData = await res.json();
        if (callSingersData.error) {
            msgEl.textContent = callSingersData.error;
            msgEl.classList.add("error-msg");
            return;
        }
        operaNameEl.textContent = callSingersData.opera_name;
        msgEl.textContent = "";

        // Populate cast dropdown
        const castSelect = document.getElementById("call-singers-cast");
        castSelect.innerHTML = "";
        callSingersData.casts.forEach(c => {
            const opt = document.createElement("option");
            opt.value = c.id;
            opt.textContent = c.name;
            castSelect.appendChild(opt);
        });

        // Populate role dropdown
        const roleSelect = document.getElementById("call-singers-role");
        roleSelect.innerHTML = "";
        callSingersData.roles.forEach(r => {
            const opt = document.createElement("option");
            opt.value = r;
            opt.textContent = r;
            roleSelect.appendChild(opt);
        });
    } catch (e) {
        console.error(e);
        msgEl.textContent = "Failed to load rehearsal data.";
        msgEl.classList.add("error-msg");
    }
}

function closeCallSingersModal() {
    document.getElementById("call-singers-modal")?.classList.add("hidden");
}

function onCallSingersScopeChange() {
    const scope = document.getElementById("call-singers-scope").value;
    document.getElementById("call-singers-cast-row").classList.toggle("hidden", scope !== "cast");
    document.getElementById("call-singers-role-row").classList.toggle("hidden", scope !== "role");
}

async function submitCallSingers() {
    const scope = document.getElementById("call-singers-scope").value;
    const castId = scope === "cast" ? Number(document.getElementById("call-singers-cast").value) : null;
    const roleName = scope === "role" ? document.getElementById("call-singers-role").value : null;
    const isOptional = document.getElementById("call-singers-optional").checked;
    const msgEl = document.getElementById("call-singers-msg");
    const btn = document.getElementById("submit-call-singers-btn");

    msgEl.textContent = "";
    msgEl.classList.remove("error-msg", "success-msg");
    btn.disabled = true;
    btn.textContent = "Sending…";

    try {
        const res = await fetch(`${API}/admin/rehearsal/${callSingersRehearsalId}/call-singers`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scope, cast_id: castId, role_name: roleName, is_optional: isOptional }),
        });
        const data = await res.json();
        if (data.status === "success") {
            msgEl.textContent = `Called ${data.count} of ${data.total} singer${data.total !== 1 ? "s" : ""}.`;
            msgEl.classList.add("success-msg");
            btn.textContent = "Done";
        } else {
            msgEl.textContent = data.message || "Failed to send calls.";
            msgEl.classList.add("error-msg");
            btn.disabled = false;
            btn.textContent = "Send Call";
        }
    } catch (e) {
        console.error(e);
        msgEl.textContent = "Server error.";
        msgEl.classList.add("error-msg");
        btn.disabled = false;
        btn.textContent = "Send Call";
    }
}


// -----------------------------------------------------------
// TEACHER MANAGEMENT
// -----------------------------------------------------------

let editTeacherId = null;

async function loadTeachersList() {
    const box = document.getElementById("teachers-admin-list");
    if (!box) return;
    try {
        const res = await fetch(`${API}/admin/teachers`, { credentials: "include" });
        const data = await res.json();
        if (!Array.isArray(data) || data.length === 0) {
            box.innerHTML = `<em class="empty-note">No teachers yet.</em>`;
            return;
        }
        box.innerHTML = data.map(t => {
            const typeLabel = t.teacher_type === "instrumental" ? "Instrumental" : "Vocal";
            const instrNote = t.teacher_type === "instrumental" && t.teacher_instruments
                ? ` &middot; ${escapeHtml(t.teacher_instruments)}`
                : "";
            const warn = (t.teacher_type === "instrumental" && !t.teacher_instruments)
                ? ` <span style="color:var(--error,#c0392b);">⚠ no instruments set</span>`
                : "";
            return `
                <div class="invite-row">
                    <div>
                        <strong>${escapeHtml(t.name)}</strong>
                        <span class="invite-meta">${typeLabel}${instrNote}${warn}</span>
                    </div>
                    <button class="subtle-btn edit-teacher-btn"
                            data-id="${t.id}"
                            data-name="${escapeHtml(t.name)}"
                            data-type="${escapeHtml(t.teacher_type || "vocal")}"
                            data-instruments="${escapeHtml(t.teacher_instruments || "")}">
                        Edit
                    </button>
                </div>
            `;
        }).join("");
        box.querySelectorAll(".edit-teacher-btn").forEach(btn => {
            btn.addEventListener("click", () => openEditTeacherModal(
                Number(btn.dataset.id), btn.dataset.name,
                btn.dataset.type, btn.dataset.instruments
            ));
        });
    } catch (e) {
        box.innerHTML = `<em class="empty-note">Failed to load teachers.</em>`;
    }
}

function openEditTeacherModal(id, name, type, instruments) {
    editTeacherId = id;
    document.getElementById("edit-teacher-title").textContent = `Edit: ${name}`;
    document.querySelectorAll("input[name='edit-teacher-type']").forEach(r => {
        r.checked = r.value === type;
    });
    document.getElementById("edit-instruments").value = instruments;
    document.getElementById("edit-instruments-row").classList.toggle("hidden", type !== "instrumental");
    document.getElementById("edit-teacher-msg").textContent = "";
    document.getElementById("edit-teacher-modal").classList.remove("hidden");
}

async function saveTeacherEdit() {
    if (!editTeacherId) return;
    const type = document.querySelector("input[name='edit-teacher-type']:checked")?.value || "vocal";
    const instruments = document.getElementById("edit-instruments").value.trim().toLowerCase();
    const msgEl = document.getElementById("edit-teacher-msg");

    if (type === "instrumental" && !instruments) {
        msgEl.textContent = "Please enter at least one instrument.";
        return;
    }

    const btn = document.getElementById("save-teacher-edit-btn");
    btn.disabled = true;
    btn.textContent = "Saving…";

    const res = await fetch(`${API}/admin/teachers/${editTeacherId}`, {
        method: "POST",
        credentials: "include",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ teacher_type: type, teacher_instruments: instruments }),
    });

    btn.disabled = false;
    btn.textContent = "Save";

    const data = await res.json();
    if (data.status === "success") {
        msgEl.className = "msg success-msg";
        msgEl.textContent = "Saved.";
        loadTeachersList();
        setTimeout(() => document.getElementById("edit-teacher-modal")?.classList.add("hidden"), 900);
    } else {
        msgEl.className = "msg";
        msgEl.textContent = data.message || "Failed to save.";
    }
}

// -----------------------------------------------------------
// ORGANIZATIONS (system_admin only)
// -----------------------------------------------------------

const ORG_TYPE_LABELS = { opera: "Opera", choir: "Choir", studio: "Studio", orchestra: "Orchestra" };

async function loadOrgs() {
    const box = document.getElementById("orgs-list");
    if (!box) return;
    box.innerHTML = "<em class='empty-note'>Loading…</em>";
    try {
        const res = await fetch(`${API}/admin/orgs`, { credentials: "include" });
        const data = await res.json();
        if (!Array.isArray(data) || data.length === 0) {
            box.innerHTML = "<em class='empty-note'>No organizations found.</em>";
            return;
        }
        box.innerHTML = data.map(o => `
            <div class="list-row" style="display:flex;justify-content:space-between;align-items:center;gap:var(--space-2);flex-wrap:wrap;">
                <div>
                    <strong>${o.name}</strong>
                    <span class="hint" style="margin-left:var(--space-1);">${ORG_TYPE_LABELS[o.org_type] || o.org_type}</span>
                </div>
                <div class="hint" style="white-space:nowrap;">${o.member_count} member${o.member_count !== 1 ? "s" : ""} &middot; /${o.slug}</div>
            </div>
        `).join("");
    } catch {
        box.innerHTML = "<em class='empty-note'>Failed to load organizations.</em>";
    }
}

// -----------------------------------------------------------
// INVITATIONS
// -----------------------------------------------------------

async function loadInvitations() {
    const box = document.getElementById("invitations-list");
    if (!box) return;

    try {
        const res = await fetch(`${API}/admin/invitations`, { credentials: "include" });
        const data = await res.json();

        if (!Array.isArray(data) || data.length === 0) {
            box.innerHTML = `<em class="empty-note">No invitations yet.</em>`;
            return;
        }

        // Group by status
        const pending = data.filter(i => i.status === "pending");
        const accepted = data.filter(i => i.status === "accepted");
        const expired = data.filter(i => i.status === "expired");

        const makeGroup = (label, items, open = false) => {
            if (!items.length) return "";
            return `<details class="invite-group" ${open ? "open" : ""}>
                <summary class="invite-group-summary">${label} <span class="invite-group-count">${items.length}</span></summary>
                <div class="invite-group-body">${items.map(renderInviteRow).join("")}</div>
            </details>`;
        };

        let html = "";
        html += makeGroup("Pending", pending, true);
        html += makeGroup("Accepted", accepted, false);
        html += makeGroup("Expired", expired, false);

        box.innerHTML = html;

        // Wire up cancel and resend buttons
        box.querySelectorAll(".cancel-invite-btn").forEach(btn => {
            btn.addEventListener("click", () => cancelInvite(btn.dataset.email));
        });
        box.querySelectorAll(".resend-invite-btn").forEach(btn => {
            btn.addEventListener("click", () => resendInvite(btn, btn.dataset.email));
        });
    } catch (e) {
        console.error(e);
        box.innerHTML = `<em class="empty-note">Failed to load invitations.</em>`;
    }
}

function renderInviteRow(i) {
    const ROLE_LABELS = { teacher: "Teacher", admin: "Admin", head_admin: "Opera Admin", system_admin: "System Admin" };
    const roleLabel = ROLE_LABELS[i.role] || i.role;
    const created = new Date(i.created_at).toLocaleDateString();
    const expires = new Date(i.expires_at).toLocaleDateString();
    const accepted = i.accepted_at ? new Date(i.accepted_at).toLocaleDateString() : null;

    let metaLine = `${roleLabel}`;
    if (i.fullname_hint) metaLine += ` · ${escapeHtml(i.fullname_hint)}`;
    metaLine += ` · sent ${created}`;
    if (i.invited_by) metaLine += ` by ${escapeHtml(i.invited_by)}`;

    let statusBadge;
    if (i.status === "pending") {
        statusBadge = `<span class="invite-status pending">Pending — expires ${expires}</span>`;
    } else if (i.status === "accepted") {
        statusBadge = `<span class="invite-status accepted">Accepted ${accepted}</span>`;
    } else {
        statusBadge = `<span class="invite-status expired">Expired ${expires}</span>`;
    }

    const cancelBtn = i.status === "pending"
        ? `<button class="cancel-invite-btn subtle-btn" data-email="${escapeHtml(i.email)}">Cancel</button>`
        : "";

    const resendBtn = (i.status === "pending" || i.status === "expired")
        ? `<button class="resend-invite-btn subtle-btn" data-email="${escapeHtml(i.email)}">Resend</button>`
        : "";

    return `
        <div class="invite-row">
            <div class="invite-main">
                <strong>${escapeHtml(i.email)}</strong>
                <div class="invite-meta">${metaLine}</div>
                ${statusBadge}
            </div>
            <div class="invite-actions">${resendBtn}${cancelBtn}</div>
        </div>
    `;
}

async function sendInvite() {
    const email = document.getElementById("invite-email").value.trim().toLowerCase();
    const fullnameHint = document.getElementById("invite-fullname-hint").value.trim();
    const selectedRole = document.getElementById("invite-role").value;
    // Conductor/Assistant Conductor are top-level picks but are orchestra_admin
    // accounts under the hood, with admin_role set to the specific sub-role.
    const isConductorPick = selectedRole === "conductor" || selectedRole === "assistant_conductor";
    const role = isConductorPick ? "orchestra_admin" : selectedRole;


    const msg = document.getElementById("invite-msg");
    msg.textContent = "";
    msg.classList.remove("error-msg", "success-msg");

    if (!email) {
        msg.textContent = "Email is required.";
        msg.classList.add("error-msg");
        return;
    }

    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        msg.textContent = "Please enter a valid email address.";
        msg.classList.add("error-msg");
        return;
    }

    const btn = document.getElementById("send-invite-btn");
    btn.disabled = true;
    btn.textContent = "Sending…";

    // Teacher type fields (only relevant when role === "teacher")
    const teacherType = document.querySelector('input[name="invite-teacher-type"]:checked')?.value || "vocal";
    const teacherInstruments = document.getElementById("invite-instruments")?.value.trim() || "";

    // Admin sub-role (relevant when role === "admin", or a conductor pick was made)
    const adminRole = isConductorPick ? selectedRole : (document.getElementById("invite-admin-role")?.value || "");

    // Org fields (relevant when system_admin invites head_admin / admin / student)
    const orgName = document.getElementById("invite-org-name")?.value.trim() || "";
    const orgSlug = document.getElementById("invite-org-slug")?.value.trim() || "";
    const orgType = document.getElementById("invite-org-type")?.value || "opera";
    const orgLogoUrl = document.getElementById("invite-org-logo")?.value.trim() || null;

    // Lesson config fields
    const lessonsEnabled = document.getElementById("invite-lessons-enabled")?.checked || false;
    const checkedDurations = [...document.querySelectorAll('input[name="invite-lesson-duration"]:checked')]
        .map(cb => parseInt(cb.value)).filter(Boolean);
    const lessonDurations = checkedDurations.length ? checkedDurations.join(",") : "30";
    const lessonMaxPerDay = parseInt(document.getElementById("invite-lesson-max-per-day")?.value || "1");
    const lessonMaxPerTeacher = parseInt(document.getElementById("invite-lesson-max-per-teacher")?.value || "5");
    const lessonOpenHour = parseInt(document.getElementById("invite-lesson-open-hour")?.value || "21");
    const lessonCloseHour = parseInt(document.getElementById("invite-lesson-close-hour")?.value || "18");
    const lessonCancelNotice = parseInt(document.getElementById("invite-lesson-cancel-notice")?.value || "60");
    const lessonLunchBreak = document.getElementById("invite-lesson-lunch-break")?.checked ?? true;

    if (USER_ROLE === "system_admin") {
        if (!orgSlug) {
            msg.textContent = "Organization ID is required.";
            msg.classList.add("error-msg");
            btn.disabled = false;
            btn.textContent = "Send Invitation";
            return;
        }
    }

    try {
        const res = await fetch(`${API}/admin/invite`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                email,
                role,
                fullname_hint: fullnameHint || null,
                teacher_type: role === "teacher" ? teacherType : "vocal",
                teacher_instruments: role === "teacher" ? teacherInstruments : "",
                admin_role: (role === "admin" || role === "orchestra_admin") ? adminRole : null,
                org_name: orgName || null,
                org_slug: orgSlug || null,
                org_type: orgType || null,
                org_logo_url: orgLogoUrl,
                lessons_enabled: lessonsEnabled,
                lesson_durations: lessonDurations,
                lesson_max_per_day: lessonMaxPerDay,
                lesson_max_per_teacher: lessonMaxPerTeacher,
                lesson_booking_open_hour: lessonOpenHour,
                lesson_booking_close_hour: lessonCloseHour,
                lesson_cancellation_notice_min: lessonCancelNotice,
                lesson_has_lunch_break: lessonLunchBreak,
            })
        });
        const data = await res.json();

        if (data.status === "success") {
            msg.textContent = data.email_sent
                ? "Invitation sent!"
                : "Invitation created, but email may not have been delivered.";
            msg.classList.add("success-msg");

            // Clear form, close modal, refresh list
            ["invite-email", "invite-fullname-hint", "invite-org-name", "invite-org-logo"].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.value = "";
            });
            const orgSlugEl = document.getElementById("invite-org-slug");
            if (orgSlugEl) { orgSlugEl.value = ""; delete orgSlugEl.dataset.manuallyEdited; }
            document.getElementById("invite-modal")?.classList.add("hidden");
            loadInvitations();
        } else {
            msg.textContent = data.message || "Failed to send invitation.";
            msg.classList.add("error-msg");
        }
    } catch (e) {
        console.error(e);
        msg.textContent = "Server error.";
        msg.classList.add("error-msg");
    } finally {
        btn.disabled = false;
        btn.textContent = "Send Invitation";
    }
}

async function resendInvite(btn, email) {
    btn.disabled = true;
    btn.textContent = "Sending…";
    try {
        const res = await fetch(`${API}/admin/resend-invitation`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email }),
        });
        const data = await res.json();
        if (data.status === "success") {
            btn.textContent = "Sent!";
            setTimeout(() => loadInvitations(), 1200);
        } else {
            btn.textContent = "Failed";
            btn.disabled = false;
        }
    } catch (e) {
        btn.textContent = "Error";
        btn.disabled = false;
    }
}

async function cancelInvite(email) {
    if (!confirm(`Cancel the pending invitation to ${email}?`)) return;

    try {
        const res = await fetch(`${API}/admin/cancel-invitation`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email })
        });
        const data = await res.json();
        if (data.status === "success") {
            loadInvitations();
        } else {
            alert(data.message || "Failed to cancel.");
        }
    } catch (e) {
        console.error(e);
        alert("Server error.");
    }
}

function setInviteRolePill(pill) {
    document.querySelectorAll("#invite-role-pills .chip").forEach(c => c.classList.remove("active"));
    pill.classList.add("active");
    const role = pill.dataset.role;
    const orgType = pill.dataset.orgType || "opera";
    const select = document.getElementById("invite-role");
    if (select) select.value = role;
    const orgTypeEl = document.getElementById("invite-org-type");
    if (orgTypeEl) orgTypeEl.value = orgType;
    onInviteRoleChange();
}

function initInviteRolePills() {
    if (USER_ROLE !== "system_admin") {
        // Non-system-admin users pick from the plain dropdown, not pills
        document.getElementById("invite-role")?.classList.remove("hidden");
        onInviteRoleChange();
        return;
    }
    document.getElementById("invite-role-pills")?.classList.remove("hidden");
    document.querySelectorAll("#invite-role-pills .chip").forEach(pill => {
        pill.addEventListener("click", () => setInviteRolePill(pill));
    });
    // Set initial state to first pill
    const firstPill = document.querySelector("#invite-role-pills .chip");
    if (firstPill) setInviteRolePill(firstPill);
}

function onInviteRoleChange() {
    const select = document.getElementById("invite-role");
    if (!select) return;

    if (USER_ROLE === "system_admin") {
        const role = select.value;
        const orgSection = document.getElementById("invite-org-section");
        const orgTypeRow = document.getElementById("invite-org-type-row");
        const orgHint = document.getElementById("invite-org-hint");
        const orgTypeEl = document.getElementById("invite-org-type");
        const lessonsRow = document.getElementById("invite-lessons-toggle-row");
        const lessonsHint = document.getElementById("invite-lessons-toggle-hint");

        orgSection?.classList.remove("hidden");
        if (orgTypeRow) orgTypeRow.classList.add("hidden"); // always hidden — org type comes from the pill
        document.getElementById("invite-teacher-type-section")?.classList.add("hidden");

        // Lesson booking toggle only for Choir Admin
        const isChoirAdmin = role === "admin";
        lessonsRow?.classList.toggle("hidden", !isChoirAdmin);
        lessonsHint?.classList.toggle("hidden", !isChoirAdmin);
        if (!isChoirAdmin) {
            const cb = document.getElementById("invite-lessons-enabled");
            if (cb) cb.checked = false;
            document.getElementById("invite-lesson-config")?.classList.add("hidden");
        }

        const orgType = orgTypeEl?.value || "opera";
        const orgHints = {
            opera:      "Enter a name and ID for their opera organization. If the ID already exists the invite will join that org; otherwise a new one is created automatically.",
            orchestra:  "Enter a name and ID for their orchestra. If the ID already exists the invite will join that org; otherwise a new one is created automatically.",
            choir:      "Enter a name and ID for the choir they'll administer. A new org is created if the ID doesn't exist yet.",
            studio:     "Enter a name and ID for their private studio. A new studio org is created if the ID doesn't exist yet.",
        };
        if (orgHint) orgHint.textContent = orgHints[orgType] || orgHints.opera;
        return;
    }

    const isOrchestraOrg = ORG_TYPE === "orchestra";
    const canInviteOrchAdmin = USER_ROLE === "head_admin";

    if (isOrchestraOrg) {
        // Orchestra org: only show conductor / assistant conductor / instrumentalist
        ["head_admin", "admin", "teacher", "studio_teacher", "student"].forEach(val => {
            const opt = select.querySelector(`option[value="${val}"]`);
            if (opt) opt.style.display = "none";
        });
        ["conductor", "assistant_conductor"].forEach(val => {
            const opt = select.querySelector(`option[value="${val}"]`);
            if (opt) opt.style.display = canInviteOrchAdmin ? "" : "none";
        });
        const orchMemberOpt = select.querySelector('option[value="orchestra_member"]');
        if (orchMemberOpt) orchMemberOpt.style.display = "";
        // Default to orchestra_member if nothing valid selected
        const validOrchRoles = new Set(["conductor", "assistant_conductor", "orchestra_member"]);
        if (!validOrchRoles.has(select.value)) select.value = "orchestra_member";
    } else {
        // head_admin and below: hide head_admin and student options, restore admin label
        ["head_admin", "student"].forEach(val => {
            const opt = select.querySelector(`option[value="${val}"]`);
            if (opt) opt.style.display = "none";
        });
        const adminOptReset = select.querySelector('option[value="admin"]');
        if (adminOptReset) adminOptReset.textContent = "Admin";

        // Show admin and conductor/assistant conductor for head_admin only
        const adminOpt = select.querySelector('option[value="admin"]');
        if (adminOpt) adminOpt.style.display = canInviteOrchAdmin ? "" : "none";
        ["conductor", "assistant_conductor"].forEach(val => {
            const opt = select.querySelector(`option[value="${val}"]`);
            if (opt) opt.style.display = canInviteOrchAdmin ? "" : "none";
        });
        // Hide orchestra_member in non-orchestra orgs
        const orchMemberOpt = select.querySelector('option[value="orchestra_member"]');
        if (orchMemberOpt) orchMemberOpt.style.display = "none";
    }

    const role = select.value;

    // Org section: system_admin only (handled above)
    document.getElementById("invite-org-section")?.classList.add("hidden");

    // Hide studio_teacher option for non-system-admin and non-head_admin
    const studioOpt = select.querySelector('option[value="studio_teacher"]');
    if (studioOpt) studioOpt.style.display = (USER_ROLE === "head_admin" && !isOrchestraOrg) ? "" : "none";

    // Teacher type section: only shown when inviting a teacher in non-orchestra org
    const teacherTypeSection = document.getElementById("invite-teacher-type-section");
    if (teacherTypeSection) {
        teacherTypeSection.classList.toggle("hidden", role !== "teacher");
    }

    // Admin sub-role section: only relevant for the generic opera/choir "admin" role
    // (conductor/assistant conductor are now their own top-level role options)
    const adminRoleSection = document.getElementById("invite-admin-role-section");
    const adminRoleSelect = document.getElementById("invite-admin-role");
    if (adminRoleSection && adminRoleSelect) {
        const isAdminInvite = role === "admin";
        adminRoleSection.classList.toggle("hidden", !isAdminInvite);
        if (isAdminInvite) adminRoleSelect.value = "director";
    }
}

function onTeacherTypeChange() {
    const isInstrumental = document.querySelector('input[name="invite-teacher-type"]:checked')?.value === "instrumental";
    const instrumentsSection = document.getElementById("invite-instruments-section");
    if (instrumentsSection) {
        instrumentsSection.classList.toggle("hidden", !isInstrumental);
    }
}


// -----------------------------------------------------------
// ORCHESTRA (sections + seating)
// -----------------------------------------------------------

let orchestraSections = [];
let orchestraMembers = [];
let orchestraSelectedOperaId = null;

async function loadOrchestra() {
    await Promise.all([loadOrchestraSections(), loadOrchestraOperas(), loadOrchestraMembers()]);
    renderOrchestraProductionList();
}

async function loadOrchestraSections() {
    try {
        const res = await fetch(`${API}/admin/orchestra-sections`, { credentials: "include" });
        orchestraSections = await res.json();

        // Auto-init standard sections on first load if none exist
        if (!orchestraSections.length) {
            const defaultSections = (typeof ORCHESTRA_DEFAULT_SECTIONS !== "undefined" ? ORCHESTRA_DEFAULT_SECTIONS : [])
                .map((s, i) => ({ ...s, sort_order: i }));
            await fetch(`${API}/admin/orchestra-sections/init-defaults`, {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ sections: defaultSections }),
            });
            const res2 = await fetch(`${API}/admin/orchestra-sections`, { credentials: "include" });
            orchestraSections = await res2.json();
        }
    } catch (e) {
        console.error(e);
    }
}

async function adjustChairCount(sectionId, delta) {
    try {
        const res = await fetch(`${API}/admin/orchestra-sections/${sectionId}/chair-count`, {
            method: "PATCH",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ delta }),
        });
        const data = await res.json();
        if (data.status === "success") {
            const sec = orchestraSections.find(s => s.id === sectionId);
            if (sec) sec.chair_count = data.chair_count;
            if (orchestraExpandedOperaId && orchestraExpandedContainer) {
                await reloadOrchInline(orchestraExpandedOperaId, orchestraExpandedContainer);
            }
        }
    } catch (e) {
        console.error(e);
    }
}

async function loadOrchestraMembers() {
    try {
        const res = await fetch(`${API}/admin/orchestra-members`, { credentials: "include" });
        orchestraMembers = await res.json();
    } catch (e) {
        console.error(e);
    }
}

async function loadOrchestraOperas() {
    try {
        const res = await fetch(`${API}/operas`, { credentials: "include" });
        orchestraOperas = await res.json();
    } catch (e) {
        console.error(e);
    }
}

let orchestraExpandedOperaId = null;
let orchestraExpandedContainer = null;

function renderOrchestraProductionList() {
    const list = document.getElementById("orchestra-productions-list");
    if (!list) return;
    const isOrchestraOrg = typeof ORG_TYPE !== "undefined" && ORG_TYPE === "orchestra";
    if (!orchestraOperas.length) {
        list.innerHTML = `<em class="empty-note">${isOrchestraOrg ? "No concerts yet." : "No productions yet."}</em>`;
        return;
    }
    list.innerHTML = "";
    orchestraOperas.forEach(op => {
        const row = document.createElement("div");
        row.className = "production-row orch-prod-row";
        row.dataset.id = op.id;
        row.innerHTML = `
            <div class="prod-header">
                <div class="prod-toggle" data-id="${op.id}">
                    <span class="prod-chevron">&#9658;</span>
                    <div class="prod-info">
                        <strong>${escapeHtml(op.name)}</strong>
                    </div>
                </div>
            </div>
            <div class="orch-prod-inline hidden" data-opera-id="${op.id}"></div>
        `;
        row.querySelector(".prod-toggle").addEventListener("click", () => toggleOrchProd(op.id, row));
        list.appendChild(row);
    });
}

async function toggleOrchProd(operaId, row) {
    const inlineEl = row.querySelector(".orch-prod-inline");
    const isExpanded = !inlineEl.classList.contains("hidden");

    document.querySelectorAll(".orch-prod-row").forEach(r => {
        r.querySelector(".orch-prod-inline")?.classList.add("hidden");
        r.querySelector(".prod-chevron")?.classList.remove("prod-chevron--open");
        r.classList.remove("prod-expanded");
    });
    orchestraExpandedOperaId = null;
    orchestraExpandedContainer = null;

    if (isExpanded) return;

    row.classList.add("prod-expanded");
    inlineEl.classList.remove("hidden");
    row.querySelector(".prod-chevron").classList.add("prod-chevron--open");
    orchestraExpandedOperaId = operaId;
    orchestraExpandedContainer = inlineEl;
    await reloadOrchInline(operaId, inlineEl);
}

async function reloadOrchInline(operaId, container) {
    if (!container) return;
    container.innerHTML = `<em class="empty-note" style="padding:var(--space-3);display:block;">Loading...</em>`;
    try {
        const [seatsRes, staffRes] = await Promise.all([
            fetch(`${API}/admin/orchestra-seats/${operaId}`, { credentials: "include" }),
            fetch(`${API}/admin/opera-staff/${operaId}`, { credentials: "include" }),
        ]);
        const seats = await seatsRes.json();
        const staffPayload = await staffRes.json();
        renderOrchInlineContent(operaId, container, seats, staffPayload);
    } catch (e) {
        console.error(e);
        container.innerHTML = `<em class="empty-note">Failed to load.</em>`;
    }
}

function renderOrchInlineContent(operaId, container, seats, staffPayload) {
    container.innerHTML = "";

    // ── Sections grid ─────────────────────────────────────────
    const grid = document.createElement("div");
    grid.className = "prod-inline-casting";

    if (!orchestraSections.length) {
        const empty = document.createElement("em");
        empty.className = "empty-note";
        empty.textContent = "No sections yet. Use \"+ Add Section\" above.";
        grid.appendChild(empty);
    } else {
        const seatsBySec = {};
        (Array.isArray(seats) ? seats : []).forEach(s => {
            if (!seatsBySec[s.section_id]) seatsBySec[s.section_id] = {};
            seatsBySec[s.section_id][s.chair_number] = s;
        });

        orchestraSections.forEach(sec => {
            const col = document.createElement("div");
            col.className = "cast-column";

            const chairCount = sec.chair_count || 5;
            const sectionSeats = seatsBySec[sec.id] || {};

            const colHeader = document.createElement("div");
            colHeader.className = "orch-section-col-header";
            colHeader.innerHTML = `
                <div class="orch-section-col-name">
                    <span class="orch-section-col-title">${escapeHtml(sec.name)}</span>
                    <em class="hint">${escapeHtml(sec.instrument)}</em>
                </div>
                <div class="orch-section-col-actions">
                    <span class="hint">${chairCount} chair${chairCount !== 1 ? "s" : ""}</span>
                    <button type="button" class="subtle-btn add-chair-btn" data-id="${sec.id}">+</button>
                    <button type="button" class="subtle-btn remove-chair-btn" data-id="${sec.id}">&#8722;</button>
                    <button type="button" class="subtle-btn delete-section-btn" data-id="${sec.id}">Remove</button>
                </div>
            `;
            col.appendChild(colHeader);

            for (let chair = 1; chair <= chairCount; chair++) {
                const seat = sectionSeats[chair];
                const chairRow = document.createElement("div");
                chairRow.className = "orch-chair-row";
                chairRow.innerHTML = `
                    <span class="orch-chair-label">Chair ${chair}</span>
                    <span class="orch-chair-member ${seat?.member_id ? "" : "orch-chair-empty"}">${escapeHtml(seat?.member_name || "Unassigned")}</span>
                    <button type="button" class="subtle-btn assign-seat-btn"
                        data-opera-id="${operaId}"
                        data-section-id="${sec.id}"
                        data-chair="${chair}"
                        data-current-member="${seat?.member_id || ""}"
                        data-current-ext-name="${escapeHtml(seat?.external_name || "")}"
                        data-current-ext-email="${escapeHtml(seat?.external_email || "")}">${(seat?.member_id || seat?.external_name) ? "Change" : "Assign"}</button>
                `;
                col.appendChild(chairRow);
            }
            grid.appendChild(col);
        });

        grid.querySelectorAll(".add-chair-btn").forEach(btn =>
            btn.addEventListener("click", () => adjustChairCount(Number(btn.dataset.id), 1)));
        grid.querySelectorAll(".remove-chair-btn").forEach(btn =>
            btn.addEventListener("click", () => adjustChairCount(Number(btn.dataset.id), -1)));
        grid.querySelectorAll(".delete-section-btn").forEach(btn =>
            btn.addEventListener("click", () => deleteOrchestraSection(Number(btn.dataset.id))));
        grid.querySelectorAll(".assign-seat-btn").forEach(btn =>
            btn.addEventListener("click", () => openAssignSeatModal(
                Number(btn.dataset.operaId),
                Number(btn.dataset.sectionId),
                Number(btn.dataset.chair),
                btn.dataset.currentMember || null,
                btn.dataset.currentExtName || null,
                btn.dataset.currentExtEmail || null
            )));
    }
    container.appendChild(grid);

    // ── Production Staff ──────────────────────────────────────
    const staffPanel = document.createElement("div");
    staffPanel.className = "orch-staff-panel";

    const staffHeaderDiv = document.createElement("div");
    staffHeaderDiv.className = "orch-staff-header";
    staffHeaderDiv.innerHTML = `<h4>Production Staff</h4>`;
    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "subtle-btn";
    copyBtn.textContent = "Copy Seating";
    copyBtn.addEventListener("click", () => {
        orchestraExpandedOperaId = operaId;
        orchestraExpandedContainer = container;
        openCopySeatingModal();
    });
    staffHeaderDiv.appendChild(copyBtn);
    staffPanel.appendChild(staffHeaderDiv);

    const staffListDiv = document.createElement("div");
    staffListDiv.className = "orch-staff-list";
    const currentStaff = Array.isArray(staffPayload?.staff) ? staffPayload.staff : [];
    if (!currentStaff.length) {
        staffListDiv.innerHTML = `<em class="empty-note">No staff assigned yet.</em>`;
    } else {
        currentStaff.forEach(s => {
            const row = document.createElement("div");
            row.className = "staff-row";
            row.innerHTML = `
                <span class="staff-row-name">${escapeHtml(s.name)}</span>
                <span class="staff-row-role">${escapeHtml(STAFF_ROLE_LABELS[s.staff_role] || s.staff_role || "")}</span>
                <button type="button" class="subtle-btn">Remove</button>
            `;
            row.querySelector("button").addEventListener("click", async () => {
                await fetch(`${API}/admin/opera/${operaId}/staff/${s.id}`, { method: "DELETE", credentials: "include" });
                await reloadOrchInline(operaId, container);
            });
            staffListDiv.appendChild(row);
        });
    }
    staffPanel.appendChild(staffListDiv);

    const availableAdmins = Array.isArray(staffPayload?.teachers) ? staffPayload.teachers : [];
    if (availableAdmins.length) {
        const addRow = document.createElement("div");
        addRow.className = "orch-staff-add-row";
        const sel = document.createElement("select");
        sel.innerHTML = `<option value="">-- select admin --</option>`;
        availableAdmins.forEach(t => {
            const opt = document.createElement("option");
            opt.value = t.id;
            opt.textContent = `${escapeHtml(t.name)} -- ${escapeHtml(STAFF_ROLE_LABELS[t.admin_role] || t.admin_role || "")}`;
            sel.appendChild(opt);
        });
        const addBtn = document.createElement("button");
        addBtn.type = "button";
        addBtn.className = "subtle-btn";
        addBtn.textContent = "Add";
        const addMsg = document.createElement("p");
        addMsg.className = "hint";
        addBtn.addEventListener("click", async () => {
            const teacherId = Number(sel.value);
            if (!teacherId) { addMsg.textContent = "Select a staff member first."; return; }
            addMsg.textContent = "";
            try {
                const res = await fetch(`${API}/admin/assign-staff`, {
                    method: "POST", credentials: "include",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ opera_id: operaId, teacher_id: teacherId }),
                });
                const data = await res.json();
                if (data.status === "success") {
                    await reloadOrchInline(operaId, container);
                } else {
                    addMsg.textContent = data.message || "Failed.";
                }
            } catch (e) { addMsg.textContent = "Server error."; }
        });
        addRow.appendChild(sel);
        addRow.appendChild(addBtn);
        staffPanel.appendChild(addRow);
        staffPanel.appendChild(addMsg);
    }
    container.appendChild(staffPanel);
}

// ── Production Staff ──────────────────────────────────────────────────────────

async function loadProductionStaff(operaId) {
    const list = document.getElementById("production-staff-list");
    const msg  = document.getElementById("staff-add-msg");
    if (!list) return;
    list.innerHTML = `<em class="empty-note">Loading…</em>`;
    if (msg) msg.textContent = "";

    try {
        const res = await fetch(`${API}/admin/opera/${operaId}/staff`, { credentials: "include" });
        const staff = await res.json();

        if (!Array.isArray(staff) || staff.length === 0) {
            list.innerHTML = `<em class="empty-note">No staff assigned yet.</em>`;
        } else {
            list.innerHTML = "";
            const div = document.createElement("div");
            div.className = "staff-list";
            staff.forEach(s => {
                const row = document.createElement("div");
                row.className = "staff-row";
                row.innerHTML = `
                    <span class="staff-row-name">${escapeHtml(s.name)}</span>
                    <span class="staff-row-role">${escapeHtml(s.role || "")}</span>
                    <button type="button" class="subtle-btn remove-staff-btn" data-teacher-id="${s.id}">Remove</button>
                `;
                row.querySelector(".remove-staff-btn").addEventListener("click", async () => {
                    await removeProductionStaff(operaId, s.id);
                    loadProductionStaff(operaId);
                });
                div.appendChild(row);
            });
            list.appendChild(div);
        }

        // Populate teacher dropdown (all org teachers)
        await populateStaffTeacherSelect();
    } catch (e) {
        console.error(e);
        list.innerHTML = `<em class="empty-note">Failed to load staff.</em>`;
    }
}

async function populateStaffTeacherSelect() {
    const sel = document.getElementById("staff-teacher-select");
    if (!sel || sel.dataset.loaded) return;
    try {
        const res = await fetch(`${API}/admin/teachers`, { credentials: "include" });
        const teachers = await res.json();
        sel.innerHTML = `<option value="">— select teacher —</option>`;
        (Array.isArray(teachers) ? teachers : []).forEach(t => {
            const opt = document.createElement("option");
            opt.value = t.id;
            opt.textContent = t.fullname;
            sel.appendChild(opt);
        });
        sel.dataset.loaded = "1";
    } catch (e) {
        console.error(e);
    }
}

async function removeProductionStaff(operaId, teacherId) {
    try {
        await fetch(`${API}/admin/opera/${operaId}/staff/${teacherId}`, {
            method: "DELETE",
            credentials: "include",
        });
    } catch (e) {
        console.error(e);
    }
}

async function addProductionStaff(operaId) {
    const sel  = document.getElementById("staff-teacher-select");
    const role = document.getElementById("staff-role-label");
    const msg  = document.getElementById("staff-add-msg");

    const teacherId = sel?.value;
    if (!teacherId) {
        if (msg) msg.textContent = "Select a teacher first.";
        return;
    }
    if (msg) msg.textContent = "";

    try {
        const res = await fetch(`${API}/admin/opera/${operaId}/staff`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ teacher_id: Number(teacherId), role_label: role?.value.trim() || "" }),
        });
        const data = await res.json();
        if (data.status === "success") {
            if (role) role.value = "";
            sel.value = "";
            loadProductionStaff(operaId);
        } else {
            if (msg) msg.textContent = data.message || "Failed to add.";
        }
    } catch (e) {
        console.error(e);
        if (msg) msg.textContent = "Server error.";
    }
}

async function loadSeatingForOpera(operaId) {
    const panel = document.getElementById("orchestra-seating-panel");
    if (!panel) return;

    const savedScroll = window.scrollY;
    panel.innerHTML = `<em class="empty-note">Loading…</em>`;

    try {
        const res = await fetch(`${API}/admin/orchestra-seats/${operaId}`, { credentials: "include" });
        const seats = await res.json();
        renderSeatingPanel(operaId, orchestraSections, seats);
    } catch (e) {
        console.error(e);
        panel.innerHTML = `<em class="empty-note">Failed to load seating.</em>`;
    }

    window.scrollTo({ top: savedScroll, behavior: "instant" });
}

function renderSeatingPanel(operaId, sections, seats) {
    const panel = document.getElementById("orchestra-seating-panel");
    if (!panel) return;

    if (!sections.length) {
        panel.innerHTML = `<em class="empty-note">No sections defined. Use "+ Add Section" to add one.</em>`;
        return;
    }

    const seatsBySectionAndChair = {};
    seats.forEach(s => {
        if (!seatsBySectionAndChair[s.section_id]) seatsBySectionAndChair[s.section_id] = {};
        seatsBySectionAndChair[s.section_id][s.chair_number] = s;
    });

    panel.innerHTML = "";
    sections.forEach(sec => {
        const sectionSeats = seatsBySectionAndChair[sec.id] || {};
        const chairCount = sec.chair_count || 5;

        const card = document.createElement("div");
        card.className = "orchestra-section-card";

        let chairRows = "";
        for (let chair = 1; chair <= chairCount; chair++) {
            const seat = sectionSeats[chair];
            const memberName = seat?.member_name || "— Unassigned —";
            chairRows += `
                <div class="staff-list-row">
                    <span>Chair ${chair}: <em>${escapeHtml(memberName)}</em></span>
                    <button type="button" class="subtle-btn assign-seat-btn"
                        data-opera-id="${operaId}"
                        data-section-id="${sec.id}"
                        data-chair="${chair}"
                        data-current-member="${seat?.member_id || ""}"
                        data-current-ext-name="${escapeHtml(seat?.external_name || "")}"
                        data-current-ext-email="${escapeHtml(seat?.external_email || "")}">
                        ${(seat?.member_id || seat?.external_name) ? "Change" : "Assign"}
                    </button>
                </div>
            `;
        }

        card.innerHTML = `
            <div class="orchestra-section-header">
                <h3>${escapeHtml(sec.name)} <em class="hint">(${escapeHtml(sec.instrument)})</em></h3>
                <div class="orchestra-section-actions">
                    <span class="hint">${chairCount} chair${chairCount !== 1 ? "s" : ""}</span>
                    <button type="button" class="subtle-btn add-chair-btn" data-id="${sec.id}">+ Chair</button>
                    <button type="button" class="subtle-btn remove-chair-btn" data-id="${sec.id}">− Chair</button>
                    <button type="button" class="subtle-btn delete-section-btn" data-id="${sec.id}">Remove</button>
                </div>
            </div>
            ${chairRows}
        `;
        panel.appendChild(card);
    });

    panel.querySelectorAll(".assign-seat-btn").forEach(btn => {
        btn.addEventListener("click", () => openAssignSeatModal(
            Number(btn.dataset.operaId),
            Number(btn.dataset.sectionId),
            Number(btn.dataset.chair),
            btn.dataset.currentMember || null,
            btn.dataset.currentExtName || null,
            btn.dataset.currentExtEmail || null
        ));
    });
    panel.querySelectorAll(".add-chair-btn").forEach(btn =>
        btn.addEventListener("click", () => adjustChairCount(Number(btn.dataset.id), 1)));
    panel.querySelectorAll(".remove-chair-btn").forEach(btn =>
        btn.addEventListener("click", () => adjustChairCount(Number(btn.dataset.id), -1)));
    panel.querySelectorAll(".delete-section-btn").forEach(btn =>
        btn.addEventListener("click", () => deleteOrchestraSection(Number(btn.dataset.id))));
}

// ── Copy seating from another production ─────────────────────────
let orchestraOperas = [];

function openCopySeatingModal() {
    const modal = document.getElementById("copy-seating-modal");
    if (!modal) return;
    const select = document.getElementById("copy-from-opera");
    select.innerHTML = `<option value="" disabled selected>Select a production…</option>`;
    orchestraOperas.forEach(op => {
        if (op.id === orchestraExpandedOperaId) return;
        const opt = document.createElement("option");
        opt.value = op.id;
        opt.textContent = op.name;
        select.appendChild(opt);
    });
    document.getElementById("copy-seating-msg").textContent = "";
    modal.classList.remove("hidden");
}

function closeCopySeatingModal() {
    document.getElementById("copy-seating-modal")?.classList.add("hidden");
}

async function confirmCopySeating() {
    const fromOperaId = Number(document.getElementById("copy-from-opera").value);
    const msg = document.getElementById("copy-seating-msg");
    if (!fromOperaId) { msg.textContent = "Please select a production."; return; }
    if (!confirm("This will replace all current seat assignments for this production. Continue?")) return;
    try {
        const res = await fetch(`${API}/admin/orchestra-seats/copy`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ from_opera_id: fromOperaId, to_opera_id: orchestraExpandedOperaId }),
        });
        const data = await res.json();
        if (data.status === "success") {
            closeCopySeatingModal();
            if (orchestraExpandedContainer) await reloadOrchInline(orchestraExpandedOperaId, orchestraExpandedContainer);
        } else {
            msg.textContent = data.message || "Failed to copy.";
        }
    } catch (e) {
        console.error(e);
        msg.textContent = "Server error.";
    }
}

function openAssignSeatModal(operaId, sectionId, chairNumber, currentMemberId, currentExternalName, currentExternalEmail) {
    const modal = document.getElementById("assign-seat-modal");
    if (!modal) return;

    const sec = orchestraSections.find(s => s.id === sectionId);
    document.getElementById("assign-seat-title").textContent =
        `Assign — ${sec ? sec.name : "Section"}, Chair ${chairNumber}`;
    document.getElementById("assign-seat-opera-id").value = operaId;
    document.getElementById("assign-seat-section-id").value = sectionId;
    document.getElementById("assign-seat-chair").value = chairNumber;
    document.getElementById("seat-msg").textContent = "";

    const extNameInput = document.getElementById("assign-seat-external-name");
    const extEmailInput = document.getElementById("assign-seat-external-email");
    const extFields = document.getElementById("seat-no-account-fields");
    if (extNameInput) extNameInput.value = currentExternalName || "";
    if (extEmailInput) extEmailInput.value = currentExternalEmail || "";
    if (extFields) extFields.classList.toggle("hidden", !currentExternalName);

    // Populate member dropdown — filter to members matching section's instrument (including doublings)
    const memberSelect = document.getElementById("assign-seat-member");
    memberSelect.innerHTML = `<option value="">— Unassigned —</option>`;
    const sectionInstrument = sec?.instrument?.toLowerCase() || "";
    const filtered = sectionInstrument
        ? orchestraMembers.filter(m => {
            const mi = (m.instrument || "").toLowerCase();
            return mi === sectionInstrument ||
                   (typeof INSTRUMENT_FAMILY !== "undefined" && INSTRUMENT_FAMILY[mi] === sectionInstrument);
        })
        : orchestraMembers;

    filtered.forEach(m => {
        const opt = document.createElement("option");
        opt.value = m.id;
        const mi = (m.instrument || "").toLowerCase();
        // Only show instrument label when it's a doubling (differs from section instrument)
        const label = (mi && mi !== sectionInstrument) ? `${m.name} (${m.instrument})` : m.name;
        opt.textContent = label;
        if (String(m.id) === String(currentMemberId)) opt.selected = true;
        memberSelect.appendChild(opt);
    });

    modal.classList.remove("hidden");
}

function closeAssignSeatModal() {
    document.getElementById("assign-seat-modal")?.classList.add("hidden");
}

async function saveSeatAssignment() {
    const operaId = Number(document.getElementById("assign-seat-opera-id").value);
    const sectionId = Number(document.getElementById("assign-seat-section-id").value);
    const chairNumber = Number(document.getElementById("assign-seat-chair").value);
    const memberIdRaw = document.getElementById("assign-seat-member").value;
    const memberId = memberIdRaw ? Number(memberIdRaw) : null;
    const externalName = !memberId ? (document.getElementById("assign-seat-external-name")?.value || "").trim() : "";
    const externalEmail = !memberId ? (document.getElementById("assign-seat-external-email")?.value || "").trim() : "";
    const msg = document.getElementById("seat-msg");
    msg.textContent = "";

    try {
        const res = await fetch(`${API}/admin/orchestra-seats`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                opera_id: operaId, section_id: sectionId, chair_number: chairNumber, member_id: memberId,
                external_name: externalName || null, external_email: externalEmail || null,
            })
        });
        const data = await res.json();
        if (data.status === "success") {
            closeAssignSeatModal();
            if (orchestraExpandedContainer) await reloadOrchInline(operaId, orchestraExpandedContainer);
        } else {
            msg.textContent = data.message || "Failed to save.";
        }
    } catch (e) {
        console.error(e);
        msg.textContent = "Server error.";
    }
}

function openAddSectionModal() {
    const modal = document.getElementById("add-section-modal");
    if (!modal) return;
    document.getElementById("section-name").value = "";
    document.getElementById("section-msg").textContent = "";
    populateInstrumentSelect("section-instrument", "section-instrument-other-row");
    const sel = document.getElementById("section-instrument");
    if (sel) sel.value = "";
    const otherRow = document.getElementById("section-instrument-other-row");
    if (otherRow) otherRow.classList.add("hidden");
    const otherInput = document.getElementById("section-instrument-other");
    if (otherInput) otherInput.value = "";
    modal.classList.remove("hidden");
}

function closeAddSectionModal() {
    document.getElementById("add-section-modal")?.classList.add("hidden");
}

async function addOrchestraSection() {
    const name = document.getElementById("section-name").value.trim();
    const instrument = getInstrumentValue("section-instrument", "section-instrument-other");
    const msg = document.getElementById("section-msg");
    msg.textContent = "";

    if (!name || !instrument) {
        msg.textContent = "Name and instrument are required.";
        return;
    }

    try {
        const res = await fetch(`${API}/admin/orchestra-sections`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, instrument, sort_order: orchestraSections.length })
        });
        const data = await res.json();
        if (data.status === "success") {
            closeAddSectionModal();
            await loadOrchestraSections();
            if (orchestraExpandedOperaId && orchestraExpandedContainer) {
                await reloadOrchInline(orchestraExpandedOperaId, orchestraExpandedContainer);
            }
        } else {
            msg.textContent = data.message || "Failed to add section.";
        }
    } catch (e) {
        console.error(e);
        msg.textContent = "Server error.";
    }
}

async function deleteOrchestraSection(sectionId) {
    if (!confirm("Remove this section? Any seat assignments for it will also be deleted.")) return;
    try {
        await fetch(`${API}/admin/orchestra-sections/${sectionId}`, {
            method: "DELETE",
            credentials: "include",
        });
        await loadOrchestraSections();
        if (orchestraExpandedOperaId && orchestraExpandedContainer) {
            await reloadOrchInline(orchestraExpandedOperaId, orchestraExpandedContainer);
        }
    } catch (e) {
        console.error(e);
        alert("Server error.");
    }
}

// -----------------------------------------------------------
// PRODUCTIONS (head_admin / system_admin only)
// -----------------------------------------------------------

let productionsList = [];

async function loadProductions() {
    const box = document.getElementById("productions-list");
    if (!box) return;

    try {
        const res = await fetch(`${API}/admin/productions`, { credentials: "include" });
        const data = await res.json();
        productionsList = Array.isArray(data) ? data : [];

        if (productionsList.length === 0) {
            box.innerHTML = `<em class="empty-note">No productions yet.</em>`;
            return;
        }

        box.innerHTML = productionsList.map(p => {
            const dates = (p.start_date || p.end_date)
                ? `<span class="prod-dates">${p.start_date || "?"} – ${p.end_date || "?"}</span>`
                : "";
            return `
                <div class="production-row" data-id="${p.id}">
                    <div class="prod-header">
                        <div class="prod-toggle" data-id="${p.id}">
                            <span class="prod-chevron">▶</span>
                            <div class="prod-info">
                                <strong>${escapeHtml(p.title)}</strong>
                                ${dates}
                                <span class="prod-casts">${p.num_casts} cast${p.num_casts !== 1 ? "s" : ""}</span>
                            </div>
                        </div>
                        <div class="prod-actions">
                            <button class="subtle-btn edit-prod-btn" data-id="${p.id}">Edit</button>
                        </div>
                    </div>
                    <div class="prod-inline-casting hidden"></div>
                </div>
            `;
        }).join("");

        box.querySelectorAll(".prod-toggle").forEach(toggle => {
            const row = toggle.closest(".production-row");
            toggle.addEventListener("click", () => toggleProductionCasting(Number(toggle.dataset.id), row));
        });
        box.querySelectorAll(".edit-prod-btn").forEach(btn => {
            btn.addEventListener("click", () => openEditProductionModal(Number(btn.dataset.id)));
        });
    } catch (e) {
        console.error(e);
        box.innerHTML = `<em class="empty-note">Failed to load productions.</em>`;
    }
}

const VOICE_PARTS = ["Any", "soprano", "mezzo-soprano", "tenor", "baritone", "bass-baritone", "bass", "spoken"];

function addProdRole() {
    const list = document.getElementById("prod-roles-list");
    if (!list) return;

    const row = document.createElement("div");
    row.className = "prod-role-row";

    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.className = "prod-role-name";
    nameInput.placeholder = "Role name (e.g. Violetta)";

    const voiceSelect = document.createElement("select");
    voiceSelect.className = "prod-role-voice";
    VOICE_PARTS.forEach(vp => {
        const opt = document.createElement("option");
        opt.value = vp;
        opt.textContent = vp === "Any" ? "Any voice type" : vp.charAt(0).toUpperCase() + vp.slice(1);
        voiceSelect.appendChild(opt);
    });

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "subtle-btn";
    removeBtn.textContent = "✕";
    removeBtn.addEventListener("click", () => row.remove());

    row.appendChild(nameInput);
    row.appendChild(voiceSelect);
    row.appendChild(removeBtn);
    list.appendChild(row);
    nameInput.focus();
}

async function createProduction() {
    const title = (document.getElementById("prod-title")?.value || "").trim();
    const startDate = document.getElementById("prod-start-date")?.value || null;
    const endDate = document.getElementById("prod-end-date")?.value || null;
    const numCasts = Number(document.getElementById("prod-num-casts")?.value || 1);
    const msg = document.getElementById("prod-msg");
    msg.textContent = "";

    if (!title) { msg.textContent = "Title is required."; return; }

    // Collect roles from dynamic row builder
    const roles = [];
    document.querySelectorAll("#prod-roles-list .prod-role-row").forEach(row => {
        const name = row.querySelector(".prod-role-name")?.value.trim();
        const voice = row.querySelector(".prod-role-voice")?.value || "Any";
        if (name) roles.push({ role_name: name, voice_type: voice });
    });

    try {
        const res = await fetch(`${API}/admin/productions`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title, start_date: startDate, end_date: endDate, num_casts: numCasts, roles }),
        });
        const data = await res.json();
        if (data.status === "success") {
            document.getElementById("prod-title").value = "";
            document.getElementById("prod-start-date").value = "";
            document.getElementById("prod-end-date").value = "";
            document.getElementById("prod-num-casts").value = "1";
            document.getElementById("prod-roles-list").innerHTML = "";
            document.getElementById("prod-create-modal")?.classList.add("hidden");
            await loadProductions();
            if (data.opera_id) openEditProductionModal(data.opera_id);
        } else {
            msg.textContent = data.message || "Failed to create production.";
        }
    } catch (e) {
        console.error(e);
        msg.textContent = "Server error.";
    }
}

async function createConcert() {
    const title = (document.getElementById("concert-title")?.value || "").trim();
    const startDate = document.getElementById("concert-start-date")?.value || null;
    const endDate = document.getElementById("concert-end-date")?.value || null;
    const msg = document.getElementById("concert-msg");
    msg.textContent = "";

    if (!title) { msg.textContent = "Title is required."; return; }

    try {
        const res = await fetch(`${API}/admin/productions`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title, start_date: startDate, end_date: endDate, num_casts: 1, roles: [] }),
        });
        const data = await res.json();
        if (data.status === "success") {
            document.getElementById("concert-title").value = "";
            document.getElementById("concert-start-date").value = "";
            document.getElementById("concert-end-date").value = "";
            document.getElementById("concert-create-modal")?.classList.add("hidden");
            await loadOrchestraOperas();
            renderOrchestraProductionList();
        } else {
            msg.textContent = data.message || "Failed to create concert.";
        }
    } catch (e) {
        console.error(e);
        msg.textContent = "Server error.";
    }
}

async function openEditProductionModal(prodId) {
    const p = productionsList.find(x => x.id === prodId);
    if (!p) return;
    document.getElementById("edit-prod-id").value = p.id;
    document.getElementById("edit-prod-modal-title").textContent = p.title;
    document.getElementById("edit-prod-title").value = p.title;
    document.getElementById("edit-prod-start-date").value = p.start_date || "";
    document.getElementById("edit-prod-end-date").value = p.end_date || "";
    document.getElementById("edit-prod-msg").textContent = "";
    document.getElementById("edit-prod-staff-msg").textContent = "";
    document.getElementById("edit-prod-staff-list").innerHTML = `<em class="empty-note">Loading…</em>`;
    document.getElementById("edit-prod-roles-grid").innerHTML = `<em class="empty-note">Loading…</em>`;
    document.getElementById("edit-production-modal").classList.remove("hidden");

    try {
        const [castingRes, staffRes] = await Promise.all([
            fetch(`${API}/admin/opera-casting/${prodId}`, { credentials: "include" }),
            fetch(`${API}/admin/opera-staff/${prodId}`, { credentials: "include" }),
        ]);
        const casting = await castingRes.json();
        const staffPayload = await staffRes.json();
        if (!casting.error) {
            castingData = casting;
            castingSelectedOperaId = prodId;
        }
        staffData = staffPayload;
    } catch (e) {
        console.error(e);
    }

    renderEditModalStaff();
    renderAssignRolesGrid("edit-prod-roles-grid");
}

function closeEditProductionModal() {
    document.getElementById("edit-production-modal").classList.add("hidden");
}

async function saveEditProduction() {
    const id = document.getElementById("edit-prod-id").value;
    const title = (document.getElementById("edit-prod-title").value || "").trim();
    const startDate = document.getElementById("edit-prod-start-date").value || null;
    const endDate = document.getElementById("edit-prod-end-date").value || null;
    const msg = document.getElementById("edit-prod-msg");
    msg.textContent = "";

    if (!title) { msg.textContent = "Title is required."; return; }

    try {
        const res = await fetch(`${API}/admin/productions/${id}`, {
            method: "PUT",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title, start_date: startDate, end_date: endDate }),
        });
        const data = await res.json();
        if (data.status === "success") {
            msg.textContent = "Saved.";
            document.getElementById("edit-prod-modal-title").textContent = title;
            loadProductions();
        } else {
            msg.textContent = data.message || "Failed to save.";
        }
    } catch (e) {
        console.error(e);
        msg.textContent = "Server error.";
    }
}

function renderEditModalStaff() {
    const box = document.getElementById("edit-prod-staff-list");
    const teacherSelect = document.getElementById("edit-prod-staff-teacher");

    teacherSelect.innerHTML = "";
    if (staffData.teachers && staffData.teachers.length > 0) {
        staffData.teachers.forEach(t => {
            const opt = document.createElement("option");
            opt.value = t.id;
            opt.textContent = `${t.name} — ${STAFF_ROLE_LABELS[t.admin_role] || t.admin_role}`;
            teacherSelect.appendChild(opt);
        });
    } else {
        teacherSelect.innerHTML = `<option value="">No admins with roles found</option>`;
    }

    if (!staffData.staff || staffData.staff.length === 0) {
        box.innerHTML = `<em class="empty-note">No staff assigned yet.</em>`;
        return;
    }

    const roleOrder = ["director", "assistant_director", "stage_manager", "assistant_stage_manager", "conductor", "assistant_conductor", "orchestra_manager"];
    const byRole = {};
    staffData.staff.forEach(s => {
        if (!byRole[s.staff_role]) byRole[s.staff_role] = [];
        byRole[s.staff_role].push(s);
    });

    let html = "";
    roleOrder.forEach(role => {
        if (!byRole[role]) return;
        html += `<div class="staff-role-group"><h5>${STAFF_ROLE_LABELS[role]}</h5><ul>`;
        byRole[role].forEach(s => {
            html += `<li>${escapeHtml(s.teacher_name)}
                <button class="remove-staff-btn" data-staff-id="${s.id}"
                        data-name="${escapeHtml(s.teacher_name)}"
                        data-role="${STAFF_ROLE_LABELS[role]}">✕</button></li>`;
        });
        html += `</ul></div>`;
    });
    box.innerHTML = html;

    box.querySelectorAll(".remove-staff-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
            if (!confirm(`Remove ${btn.dataset.name} as ${btn.dataset.role}?`)) return;
            await fetch(`${API}/admin/remove-staff`, {
                credentials: "include", method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ staff_id: Number(btn.dataset.staffId) }),
            });
            const res = await fetch(`${API}/admin/opera-staff/${castingSelectedOperaId}`, { credentials: "include" });
            staffData = await res.json();
            renderEditModalStaff();
            renderStaffList();
        });
    });
}


async function renameCast(operaId, castId, newName) {
    try {
        const res = await fetch(`${API}/admin/casts/${castId}`, {
            method: "PATCH",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: newName }),
        });
        const data = await res.json();
        if (data.status === "success") {
            const cast = castingData.casts.find(c => c.id === castId);
            if (cast) cast.name = newName;
            renderAssignRolesGrid("edit-prod-roles-grid");
            await loadProductions();
        }
    } catch (e) {
        console.error(e);
    }
}

async function removeCast(operaId, castId) {
    try {
        const res = await fetch(`${API}/admin/casts/${castId}`, {
            method: "DELETE",
            credentials: "include",
        });
        const data = await res.json();
        if (data.status === "success") {
            const castingRes = await fetch(`${API}/admin/opera-casting/${operaId}`, { credentials: "include" });
            castingData = await castingRes.json();
            renderAssignRolesGrid("edit-prod-roles-grid");
            await loadProductions();
        } else {
            alert(data.message || "Failed to remove cast.");
        }
    } catch (e) {
        console.error(e);
        alert("Server error.");
    }
}

async function addCastToProduction() {
    const prodId = castingSelectedOperaId;
    if (!prodId) return;
    const btn = document.getElementById("add-cast-btn");
    if (btn) { btn.disabled = true; btn.textContent = "Adding…"; }
    try {
        const res = await fetch(`${API}/admin/productions/${prodId}/casts`, {
            method: "POST",
            credentials: "include",
        });
        const data = await res.json();
        if (data.status === "success") {
            const castingRes = await fetch(`${API}/admin/opera-casting/${prodId}`, { credentials: "include" });
            castingData = await castingRes.json();
            renderAssignRolesGrid("edit-prod-roles-grid");
            await loadProductions();
        } else {
            alert(data.message || "Failed to add cast.");
        }
    } catch (e) {
        console.error(e);
        alert("Server error.");
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "+ Add Cast"; }
    }
}

// -----------------------------------------------------------
// ORG TRANSFER REQUESTS (head_admin / system_admin)
// -----------------------------------------------------------

async function loadOrgTransferRequests() {
    const box = document.getElementById("requests-list");
    if (!box) return;

    try {
        const res = await fetch(`${API}/admin/org-transfer-requests`, { credentials: "include" });
        const data = await res.json();

        if (!Array.isArray(data) || data.length === 0) {
            box.innerHTML = `<em class="empty-note">No pending transfer requests.</em>`;
            return;
        }

        box.innerHTML = data.map(r => `
            <div class="transfer-row" data-id="${r.id}">
                <div class="transfer-info">
                    <strong>${escapeHtml(r.student_name)}</strong>
                    <span class="transfer-email">${escapeHtml(r.student_email)}</span>
                    <span class="transfer-from">From: ${escapeHtml(r.from_org)}</span>
                    ${r.message ? `<em class="transfer-msg">${escapeHtml(r.message)}</em>` : ""}
                </div>
                <div class="transfer-actions">
                    <button class="approve-transfer-btn" data-id="${r.id}">Approve</button>
                    <button class="deny-transfer-btn subtle-btn" data-id="${r.id}">Deny</button>
                </div>
            </div>
        `).join("");
    } catch (e) {
        console.error(e);
        box.innerHTML = `<em class="empty-note">Failed to load requests.</em>`;
    }
}

async function onTransferReviewClick(e) {
    const btn = e.target.closest(".approve-transfer-btn, .deny-transfer-btn");
    if (!btn) return;
    const id = btn.dataset.id;
    const decision = btn.classList.contains("approve-transfer-btn") ? "approved" : "denied";
    const label = decision === "approved" ? "approve" : "deny";
    if (!confirm(`Are you sure you want to ${label} this transfer request?`)) return;

    try {
        const res = await fetch(`${API}/admin/org-transfer-requests/${id}/review`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ decision }),
        });
        const data = await res.json();
        if (data.status === "success") {
            loadOrgTransferRequests();
        } else {
            alert(data.message || "Failed.");
        }
    } catch (e) {
        console.error(e);
        alert("Server error.");
    }
}


// -----------------------------------------------------------
// 5. SCHEDULES (teacher availability)
// -----------------------------------------------------------

let scheduleAllTeachers = [];

async function loadScheduleSummary() {
    const box = document.getElementById("schedule-summary");
    if (!box) return;

    try {
        const res = await fetch(`${API}/admin/all-schedules`, { credentials: "include" });
        const data = await res.json();
        scheduleAllTeachers = Array.isArray(data) ? data : [];

        if (scheduleAllTeachers.length === 0) {
            box.innerHTML = `<em class="empty-note">No teachers.</em>`;
            return;
        }

        box.innerHTML = "";
        scheduleAllTeachers.forEach(t => {
            const card = document.createElement("div");
            card.className = "schedule-summary-card";

            if (t.schedule.length === 0) {
                card.innerHTML = `
                    <div class="schedule-teacher-name">${escapeHtml(t.name)}</div>
                    <em class="empty-note">No availability set.</em>
                `;
            } else {
                const lines = t.schedule
                    .map(w => `<div>${ADMIN_DAY_NAMES[w.weekday]}: ${adminFormatTime(w.start)}–${adminFormatTime(w.end)}</div>`)
                    .join("");
                card.innerHTML = `
                    <div class="schedule-teacher-name">${escapeHtml(t.name)}</div>
                    <div class="schedule-lines">${lines}</div>
                `;
            }
            box.appendChild(card);
        });
    } catch (e) {
        console.error(e);
        box.innerHTML = `<em class="empty-note">Failed to load schedules.</em>`;
    }
}
// -----------------------------------------------------------
// 6b. STAFF MESSAGING
// -----------------------------------------------------------

let msgCurrentScope = "org";
let msgStaffList = [];        // all admins for recipient picker
let msgProductionList = [];   // productions for scope nav

async function loadMessagesTab() {
    await Promise.all([loadMsgScopeNav(), loadMsgStaff()]);
    loadMsgBoard(msgCurrentScope);
    refreshMsgBadge();
    loadDmTab();
}

async function loadMsgScopeNav() {
    try {
        const res = await fetch(`${API}/admin/productions`, { credentials: "include" });
        const prods = await res.json();
        msgProductionList = Array.isArray(prods) ? prods : [];
    } catch (e) {
        msgProductionList = [];
    }
    renderMsgScopeNav();
}

function renderMsgScopeNav() {
    const nav = document.getElementById("msg-scope-nav");
    if (!nav) return;
    nav.innerHTML = "";

    const addBtn = (label, scope) => {
        const btn = document.createElement("button");
        btn.className = "sub-tab-btn" + (scope === msgCurrentScope ? " active" : "");
        btn.dataset.scope = scope;
        btn.textContent = label;
        btn.addEventListener("click", () => {
            msgCurrentScope = scope;
            nav.querySelectorAll(".sub-tab-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            loadMsgBoard(scope);
        });
        nav.appendChild(btn);
    };

    addBtn("Org-wide", "org");
    msgProductionList.forEach(p => addBtn(p.title, `opera_${p.id}`));
}

async function loadMsgStaff() {
    try {
        const res = await fetch(`${API}/admin/messages/staff`, { credentials: "include" });
        msgStaffList = await res.json();
    } catch (e) {
        msgStaffList = [];
    }
    renderMsgRecipientSelect();
}

function renderMsgRecipientSelect() {
    const sel = document.getElementById("msg-recipient-select");
    if (!sel) return;
    sel.innerHTML = "";
    msgStaffList.forEach(s => {
        const opt = document.createElement("option");
        opt.value = s.id;
        const roleLabel = STAFF_ROLE_LABELS[s.admin_role] || s.role;
        opt.textContent = `${s.fullname} — ${roleLabel}`;
        sel.appendChild(opt);
    });
}

async function loadMsgBoard(scope) {
    const list = document.getElementById("msg-list");
    if (!list) return;
    list.innerHTML = `<em class="empty-note">Loading…</em>`;
    try {
        const res = await fetch(`${API}/admin/messages?scope=${encodeURIComponent(scope)}`, { credentials: "include" });
        const msgs = await res.json();
        renderMsgList(msgs);
        refreshMsgBadge();
    } catch (e) {
        list.innerHTML = `<em class="empty-note">Failed to load messages.</em>`;
    }
}

function renderMsgList(msgs) {
    const list = document.getElementById("msg-list");
    if (!list) return;
    if (!msgs.length) {
        list.innerHTML = `<em class="empty-note">No messages yet. Be the first to post.</em>`;
        return;
    }
    list.innerHTML = "";
    // Show newest-first (already sorted desc from backend)
    msgs.forEach(m => {
        const card = document.createElement("div");
        card.className = "msg-card" + (m.recipients.length ? " msg-card--directed" : "");

        const ts = m.created_at ? new Date(m.created_at).toLocaleString(undefined, {
            month: "short", day: "numeric", hour: "numeric", minute: "2-digit"
        }) : "";

        const recipientLine = m.recipients.length
            ? `<div class="msg-recipients">To: ${m.recipients.map(escapeHtml).join(", ")}</div>`
            : "";

        card.innerHTML = `
            <div class="msg-meta">
                <span class="msg-sender">${escapeHtml(m.sender_name)}</span>
                <span class="msg-time">${ts}</span>
                ${m.recipients.length ? `<span class="msg-directed-tag">Direct</span>` : ""}
            </div>
            ${recipientLine}
            <div class="msg-body">${escapeHtml(m.body)}</div>
        `;
        list.appendChild(card);
    });
}

async function sendMsg() {
    const body = (document.getElementById("msg-body")?.value || "").trim();
    const status = document.getElementById("msg-send-status");
    if (!body) { status.textContent = "Message cannot be empty."; return; }

    const directedToggle = document.getElementById("msg-directed-toggle");
    let recipientIds = [];
    if (directedToggle?.checked) {
        const sel = document.getElementById("msg-recipient-select");
        recipientIds = sel ? [...sel.selectedOptions].map(o => Number(o.value)) : [];
        if (!recipientIds.length) { status.textContent = "Select at least one recipient."; return; }
    }

    const btn = document.getElementById("msg-send-btn");
    btn.disabled = true;
    status.textContent = "Sending…";

    try {
        const res = await fetch(`${API}/admin/messages`, {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scope: msgCurrentScope, body, recipient_ids: recipientIds }),
        });
        const data = await res.json();
        if (data.status === "success") {
            document.getElementById("msg-body").value = "";
            status.textContent = recipientIds.length
                ? "Message sent and email delivered to recipients."
                : "Posted to board.";
            loadMsgBoard(msgCurrentScope);
        } else {
            status.textContent = data.message || "Failed to send.";
        }
    } catch (e) {
        status.textContent = "Server error.";
    } finally {
        btn.disabled = false;
    }
}

async function refreshMsgBadge() {
    try {
        const res = await fetch(`${API}/admin/messages/unread`, { credentials: "include" });
        const data = await res.json();
        const badge = document.getElementById("msg-badge");
        if (!badge) return;
        if (data.total > 0) {
            badge.textContent = data.total;
            badge.classList.remove("hidden");
        } else {
            badge.classList.add("hidden");
        }
    } catch (e) { /* silent */ }
}

// -----------------------------------------------------------
// 7. INIT
// -----------------------------------------------------------

document.addEventListener("DOMContentLoaded", async () => {
    await ME_READY;

    // --- Invitations (set up first so a later error can't block role filtering) ---
    document.getElementById("open-invite-modal-btn")?.addEventListener("click", () => {
        document.getElementById("invite-modal")?.classList.remove("hidden");
    });
    document.getElementById("close-invite-modal-btn")?.addEventListener("click", () => {
        document.getElementById("invite-modal")?.classList.add("hidden");
    });
    document.getElementById("send-invite-btn")?.addEventListener("click", sendInvite);
    document.getElementById("invite-role")?.addEventListener("change", onInviteRoleChange);
    document.querySelectorAll('input[name="invite-teacher-type"]').forEach(radio => {
        radio.addEventListener("change", onTeacherTypeChange);
    });
    // Auto-generate org slug from org name as user types
    document.getElementById("invite-org-name")?.addEventListener("input", (e) => {
        const slugEl = document.getElementById("invite-org-slug");
        if (slugEl && !slugEl.dataset.manuallyEdited) {
            slugEl.value = e.target.value
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, "-")
                .replace(/^-|-$/g, "");
        }
    });
    document.getElementById("invite-org-slug")?.addEventListener("input", (e) => {
        e.target.dataset.manuallyEdited = e.target.value ? "1" : "";
    });
    document.getElementById("invite-lessons-enabled")?.addEventListener("change", (e) => {
        document.getElementById("invite-lesson-config")?.classList.toggle("hidden", !e.target.checked);
    });
    try {
        initInviteRolePills();
    } catch (e) {
        console.error("initInviteRolePills failed:", e);
    }

    // --- Tabs ---
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.addEventListener("click", () => setActiveTab(btn.dataset.tab));
    });
    setActiveTab(getTabFromURL());
    window.addEventListener("hashchange", () => setActiveTab(getTabFromURL()));

    // --- Productions / Casting ---
    loadCastingOperas();
    loadProductions();


    // Edit Production modal — inline add staff
    document.getElementById("edit-prod-add-staff-btn")?.addEventListener("click", async () => {
        const msg = document.getElementById("edit-prod-staff-msg");
        msg.textContent = "";
        const teacherId = Number(document.getElementById("edit-prod-staff-teacher").value);
        if (!teacherId) { msg.textContent = "No admin selected."; return; }
        try {
            const res = await fetch(`${API}/admin/assign-staff`, {
                credentials: "include", method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ opera_id: castingSelectedOperaId, teacher_id: teacherId }),
            });
            const data = await res.json();
            if (data.status === "success") {
                const staffRes = await fetch(`${API}/admin/opera-staff/${castingSelectedOperaId}`, { credentials: "include" });
                staffData = await staffRes.json();
                renderEditModalStaff();
                renderStaffList();
            } else {
                msg.textContent = data.message || "Failed.";
            }
        } catch (e) { msg.textContent = "Server error."; }
    });

    document.getElementById("edit-prod-staff-no-account-toggle")?.addEventListener("click", () => {
        document.getElementById("edit-prod-staff-no-account-fields")?.classList.toggle("hidden");
    });

    document.getElementById("edit-prod-add-staff-external-btn")?.addEventListener("click", async () => {
        const msg = document.getElementById("edit-prod-staff-msg");
        msg.textContent = "";
        const externalName = (document.getElementById("edit-prod-staff-external-name")?.value || "").trim();
        const externalEmail = (document.getElementById("edit-prod-staff-external-email")?.value || "").trim();
        const externalRole = document.getElementById("edit-prod-staff-external-role")?.value;
        if (!externalName) { msg.textContent = "Name is required."; return; }
        try {
            const res = await fetch(`${API}/admin/assign-staff`, {
                credentials: "include", method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    opera_id: castingSelectedOperaId,
                    external_name: externalName,
                    external_email: externalEmail || null,
                    external_role: externalRole,
                }),
            });
            const data = await res.json();
            if (data.status === "success") {
                document.getElementById("edit-prod-staff-external-name").value = "";
                document.getElementById("edit-prod-staff-external-email").value = "";
                document.getElementById("edit-prod-staff-no-account-fields")?.classList.add("hidden");
                const staffRes = await fetch(`${API}/admin/opera-staff/${castingSelectedOperaId}`, { credentials: "include" });
                staffData = await staffRes.json();
                renderEditModalStaff();
                renderStaffList();
            } else {
                msg.textContent = data.message || "Failed.";
            }
        } catch (e) { msg.textContent = "Server error."; }
    });

// --- Rehearsals ---
    populateTimeDropdown(document.getElementById("rehearsal-start-time"));
    populateTimeDropdown(document.getElementById("rehearsal-end-time"));
    loadRehearsalOperas();
    loadAdminRehearsals();
    rehearsalOpera?.addEventListener("change", e => {
        loadRehearsalOperaData(e.target.value);
    });
    document.getElementById("create-rehearsal-btn")
        ?.addEventListener("click", () => {
            const scope = document.querySelector("input[name='reh-scope']:checked")?.value;
            if (scope === "range") createBulkAdminRehearsal(); else createRehearsal();
        });
    document.getElementById("rehearsal-attendance")
        ?.addEventListener("change", onAttendanceTypeChange);
    onAttendanceTypeChange();

    // Head admins can create both vocal and orchestra rehearsals
    if (["head_admin", "system_admin"].includes(USER_ROLE)) {
        document.getElementById("rehearsal-kind-row")?.classList.remove("hidden");
        document.getElementById("rehearsal-kind")
            ?.addEventListener("change", onRehearsalKindChange);
    }

    // --- Scheduled Rehearsals: past rehearsals toggle ---
    document.getElementById("scheduled-past-toggle")?.addEventListener("click", () => {
        scheduledPastExpanded = !scheduledPastExpanded;
        renderScheduledRehearsals();
    });

    // --- New Rehearsal modal ---
    document.getElementById("new-rehearsal-btn")
        ?.addEventListener("click", () =>
            document.getElementById("rehearsal-create-modal")?.classList.remove("hidden"));
    document.getElementById("close-rehearsal-create-btn")
        ?.addEventListener("click", () =>
            document.getElementById("rehearsal-create-modal")?.classList.add("hidden"));
    document.getElementById("rehearsal-create-modal")?.addEventListener("click", e => {
        if (e.target.id === "rehearsal-create-modal")
            e.target.classList.add("hidden");
    });

    // Scope toggle (single / range)
    document.querySelectorAll("input[name='reh-scope']").forEach(radio => {
        radio.addEventListener("change", () => {
            const isRange = radio.value === "range";
            document.getElementById("reh-admin-single-fields")?.classList.toggle("hidden", isRange);
            document.getElementById("reh-admin-bulk-fields")?.classList.toggle("hidden", !isRange);
        });
    });
    ["reh-admin-from", "reh-admin-to"].forEach(id =>
        document.getElementById(id)?.addEventListener("change", updateAdminBulkPreview));
    document.querySelectorAll("#reh-admin-days input").forEach(cb =>
        cb.addEventListener("change", updateAdminBulkPreview));

    // --- Edit Teacher modal ---
    document.getElementById("save-teacher-edit-btn")?.addEventListener("click", saveTeacherEdit);
    document.getElementById("close-edit-teacher-btn")?.addEventListener("click", () =>
        document.getElementById("edit-teacher-modal")?.classList.add("hidden"));
    document.getElementById("edit-teacher-modal")?.addEventListener("click", e => {
        if (e.target.id === "edit-teacher-modal") e.target.classList.add("hidden");
    });
    document.querySelectorAll("input[name='edit-teacher-type']").forEach(radio => {
        radio.addEventListener("change", () => {
            document.getElementById("edit-instruments-row")
                ?.classList.toggle("hidden", radio.value !== "instrumental");
        });
    });

    // --- Edit Rehearsal modal ---
    document.getElementById("save-reh-edit-btn")?.addEventListener("click", saveRehearsalEdit);
    document.getElementById("close-reh-edit-btn")?.addEventListener("click", () =>
        document.getElementById("reh-edit-modal")?.classList.add("hidden"));
    document.getElementById("reh-edit-modal")?.addEventListener("click", e => {
        if (e.target.id === "reh-edit-modal") e.target.classList.add("hidden");
    });

    // --- Absences modal ---
    document.getElementById("close-reh-absences-btn")?.addEventListener("click", () =>
        document.getElementById("reh-absences-modal")?.classList.add("hidden"));
    document.getElementById("reh-absences-modal")?.addEventListener("click", e => {
        if (e.target.id === "reh-absences-modal") e.target.classList.add("hidden");
    });

    // --- View / Add Rehearsal Notes modals ---
    document.getElementById("close-view-notes-btn")
        ?.addEventListener("click", () => document.getElementById("reh-view-notes-modal")?.classList.add("hidden"));
    document.getElementById("reh-view-notes-modal")?.addEventListener("click", e => {
        if (e.target.id === "reh-view-notes-modal") e.target.classList.add("hidden");
    });
    document.getElementById("send-reh-notes-btn")?.addEventListener("click", sendRehearsalNotes);
    document.getElementById("close-add-notes-btn")
        ?.addEventListener("click", () => document.getElementById("reh-add-notes-modal")?.classList.add("hidden"));
    document.getElementById("reh-add-notes-modal")?.addEventListener("click", e => {
        if (e.target.id === "reh-add-notes-modal") e.target.classList.add("hidden");
    });

    // --- New Production modal ---
    document.getElementById("new-production-btn")
        ?.addEventListener("click", () =>
            document.getElementById("prod-create-modal")?.classList.remove("hidden"));
    document.getElementById("close-prod-create-btn")
        ?.addEventListener("click", () =>
            document.getElementById("prod-create-modal")?.classList.add("hidden"));
    document.getElementById("prod-create-modal")?.addEventListener("click", e => {
        if (e.target.id === "prod-create-modal")
            e.target.classList.add("hidden");
    });

    // --- New Concert modal (standalone orchestra orgs) ---
    document.getElementById("new-concert-btn")
        ?.addEventListener("click", () =>
            document.getElementById("concert-create-modal")?.classList.remove("hidden"));
    document.getElementById("close-concert-create-btn")
        ?.addEventListener("click", () =>
            document.getElementById("concert-create-modal")?.classList.add("hidden"));
    document.getElementById("concert-create-modal")?.addEventListener("click", e => {
        if (e.target.id === "concert-create-modal")
            e.target.classList.add("hidden");
    });
    document.getElementById("create-concert-btn")?.addEventListener("click", createConcert);

    // --- Productions ---
    document.getElementById("add-prod-role-btn")?.addEventListener("click", addProdRole);
    document.getElementById("create-production-btn")?.addEventListener("click", createProduction);
    document.getElementById("save-edit-prod-btn")?.addEventListener("click", saveEditProduction);
    document.getElementById("cancel-edit-prod-btn")?.addEventListener("click", closeEditProductionModal);
    document.getElementById("edit-production-modal")?.addEventListener("click", e => {
        if (e.target.id === "edit-production-modal") closeEditProductionModal();
    });
    document.getElementById("add-cast-btn")?.addEventListener("click", addCastToProduction);

    // --- Requests ---
    // (event delegation handles approve/deny buttons rendered dynamically)
    document.getElementById("requests-list")?.addEventListener("click", onTransferReviewClick);

    // --- Messaging ---
    document.getElementById("msg-send-btn")?.addEventListener("click", sendMsg);
    document.getElementById("msg-directed-toggle")?.addEventListener("change", e => {
        document.getElementById("msg-recipient-section")?.classList.toggle("hidden", !e.target.checked);
    });

    // --- Direct Messages ---
    document.querySelectorAll(".dm-view-btn").forEach(btn =>
        btn.addEventListener("click", () => {
            dmView = btn.dataset.dmView;
            document.querySelectorAll(".dm-view-btn").forEach(b => b.classList.toggle("active", b === btn));
            renderDmView();
        })
    );
    document.getElementById("dm-send-btn")?.addEventListener("click", sendDm);

    // Load unread badges on init
    refreshMsgBadge();
    refreshDmBadge();

    const isOrchestraOrg = ORG_TYPE === "orchestra";

    // system_admin sees Invitations, Orgs, Messages — hide everything else
    if (USER_ROLE === "system_admin") {
        ["rehearsals", "casting", "orchestra"].forEach(tab => {
            document.querySelector(`.tab-btn[data-tab="${tab}"]`)?.classList.add("tab-btn--hidden");
        });
        document.querySelector('.tab-btn[data-tab="orgs"]')?.classList.remove("tab-btn--hidden");
        setActiveTab("invitations");
    }

    // Standalone orchestra org: hide Productions, force orchestra rehearsal type
    if (isOrchestraOrg) {
        document.querySelector('.tab-btn[data-tab="casting"]')?.classList.add("tab-btn--hidden");
        // Always orchestra rehearsal type — hide the kind toggle and vocal fields
        document.getElementById("rehearsal-kind-row")?.classList.add("hidden");
        document.getElementById("rehearsal-vocal-fields")?.classList.add("hidden");
        // Relabel "Opera" → "Program" in the new rehearsal modal
        const rehearsalOperaLabel = document.querySelector('label[for="rehearsal-opera"]');
        if (rehearsalOperaLabel) rehearsalOperaLabel.textContent = "Program";
        // Within the Orchestra tab, "Productions" become "Concerts" with their own create button
        const orchProdHeading = document.getElementById("orchestra-prod-list-heading");
        if (orchProdHeading) orchProdHeading.textContent = "Concerts";
        document.getElementById("new-concert-btn")?.classList.remove("hidden");
    }

    // orchestra_admin sees Rehearsals and Orchestra only (no Invitations)
    if (USER_ROLE === "orchestra_admin") {
        ["casting", "invitations"].forEach(tab => {
            document.querySelector(`.tab-btn[data-tab="${tab}"]`)?.classList.add("tab-btn--hidden");
        });
        document.getElementById("rehearsal-vocal-fields")?.classList.add("hidden");
        setActiveTab(isOrchestraOrg ? "orchestra" : "rehearsals");
    }

    // head_admin in orchestra org lands on Orchestra tab
    if (USER_ROLE === "head_admin" && isOrchestraOrg) {
        setActiveTab("orchestra");
    }

    // Invitations tab: system_admin and head_admin only
    const canSeeInvitations = ["system_admin", "head_admin"].includes(USER_ROLE);
    document.querySelector('.tab-btn[data-tab="invitations"]')
        ?.classList.toggle("tab-btn--hidden", !canSeeInvitations);

    // Orchestra tab: always visible in orchestra org; otherwise head_admin and orchestra_admin only
    const canSeeOrchestra = isOrchestraOrg || ["head_admin", "orchestra_admin"].includes(USER_ROLE);
    document.querySelector('.tab-btn[data-tab="orchestra"]')
        ?.classList.toggle("tab-btn--hidden", !canSeeOrchestra);

    // --- Orchestra modals ---
    document.getElementById("confirm-copy-seating-btn")?.addEventListener("click", confirmCopySeating);
    document.getElementById("cancel-copy-seating-btn")?.addEventListener("click", closeCopySeatingModal);
    document.getElementById("copy-seating-modal")?.addEventListener("click", e => {
        if (e.target.id === "copy-seating-modal") closeCopySeatingModal();
    });
    document.getElementById("add-section-btn")?.addEventListener("click", openAddSectionModal);
    document.getElementById("save-section-btn")?.addEventListener("click", addOrchestraSection);
    document.getElementById("cancel-section-btn")?.addEventListener("click", closeAddSectionModal);
    document.getElementById("add-section-modal")?.addEventListener("click", e => {
        if (e.target.id === "add-section-modal") closeAddSectionModal();
    });
    document.getElementById("save-seat-btn")?.addEventListener("click", saveSeatAssignment);
    document.getElementById("cancel-seat-btn")?.addEventListener("click", closeAssignSeatModal);
    document.getElementById("assign-seat-modal")?.addEventListener("click", e => {
        if (e.target.id === "assign-seat-modal") closeAssignSeatModal();
    });
    document.getElementById("seat-no-account-toggle")?.addEventListener("click", () => {
        document.getElementById("seat-no-account-fields")?.classList.toggle("hidden");
    });

    // --- Call Singers modal ---
    document.getElementById("submit-call-singers-btn")?.addEventListener("click", submitCallSingers);
    document.getElementById("cancel-call-singers-btn")?.addEventListener("click", closeCallSingersModal);
    document.getElementById("call-singers-scope")?.addEventListener("change", onCallSingersScopeChange);
    document.getElementById("call-singers-modal")?.addEventListener("click", e => {
        if (e.target.id === "call-singers-modal") closeCallSingersModal();
    });

    // --- Auto-refresh on tab focus (invitations excluded — loaded on tab switch) ---
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState !== "visible") return;
        loadAdminRehearsals();
        loadScheduleSummary();
        if (castingData && castingData.opera) {
            loadCastingForOpera(castingData.opera.id);
        }
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
    if (!msgs.length) { list.innerHTML = `<em class="empty-note">${dmView === "inbox" ? "All caught up!" : "No direct messages yet."}</em>`; return; }
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
        const g = c.group || "Members";
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