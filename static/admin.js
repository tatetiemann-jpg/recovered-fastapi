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
const VALID_TABS = ["rehearsals", "casting", "invitations", "orchestra"];

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
    if (tabName === "invitations") { loadInvitations(); loadOrgTransferRequests(); }
    if (tabName === "orchestra") loadOrchestra();
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
    renderCastingOperaTabs();

    if (castingOperas.length > 0) {
        // Keep prior selection if still valid, else pick first
        const stillValid = castingOperas.some(o => o.id === castingSelectedOperaId);
        if (!stillValid) {
            castingSelectedOperaId = castingOperas[0].id;
        }
        loadCastingForOpera(castingSelectedOperaId);
    }
}

function renderCastingOperaTabs() {
    const nav = document.getElementById("casting-opera-tabs");
    if (!nav) return;

    if (castingOperas.length === 0) {
        nav.innerHTML = `<em class="empty-note">No operas yet.</em>`;
        return;
    }

    nav.innerHTML = castingOperas.map(o => `
        <button class="sub-tab-btn ${o.id === castingSelectedOperaId ? "active" : ""}"
                data-opera-id="${o.id}">
            ${escapeHtml(o.name)}
        </button>
    `).join("");

    nav.querySelectorAll(".sub-tab-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            castingSelectedOperaId = Number(btn.dataset.operaId);
            renderCastingOperaTabs();  // update active state
            loadCastingForOpera(castingSelectedOperaId);
        });
    });
}

async function loadCastingForOpera(operaId) {
    castingSelectedOperaId = Number(operaId);
    const colsContainer = document.getElementById("casting-cast-columns");
    colsContainer.innerHTML = `<em class="empty-note">Loading…</em>`;

    try {
        const res = await fetch(`${API}/admin/opera-casting/${operaId}`, { credentials: "include" });
        const data = await res.json();

        if (data.error) {
            colsContainer.innerHTML = `<em class="empty-note">${data.error}</em>`;
            return;
        }

        castingData = data;
        renderCastColumns();
        loadStaffForOpera(operaId);   // ← ADD THIS LINE
    } catch (e) {
        console.error(e);
        colsContainer.innerHTML = `<em class="empty-note">Failed to load casting data.</em>`;
    }
}
function renderCastColumns() {
    const container = document.getElementById("casting-cast-columns");
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
            renderCastColumns();
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
            renderCastColumns();
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

function renderAssignRolesGrid() {
    const container = document.getElementById("assign-roles-grid");
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
    let headerHtml = `<div class="casting-role-label">Role</div>`;
    castingData.casts.forEach(c => {
        headerHtml += `<div class="casting-cast-label">${escapeHtml(c.name)}</div>`;
    });
    header.innerHTML = headerHtml;
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

            const select = document.createElement("select");
            select.className = "casting-select";
            select.dataset.operaId = castingData.opera.id;
            select.dataset.castId = cast.id;
            select.dataset.roleName = role.name;

            // "Not cast" default
            const noneOpt = document.createElement("option");
            noneOpt.value = "";
            noneOpt.textContent = "— Not cast —";
            select.appendChild(noneOpt);

            // Compatible students
            const allowedVoices = VOICE_COMPATIBILITY[(role.voice_type || "").toLowerCase()] || [];
            const compatibleStudents = castingData.all_students
                .filter(s => allowedVoices.includes((s.voice_type || "").toLowerCase()))
                .sort((a, b) => a.name.localeCompare(b.name));

            // Who's already principal IN THIS CAST (excluding this role)?
            const sameCastCastings = studentsInCast(cast.id);

            compatibleStudents.forEach(student => {
                const opt = document.createElement("option");
                opt.value = student.id;
                opt.textContent = student.name;

                // Check: is this student already principal in THIS cast in a different role?
                const conflictInThisCast = sameCastCastings.find(
                    c => c.student_id === student.id && c.role !== role.name
                );

                if (conflictInThisCast) {
                    opt.textContent = `${student.name} (already ${conflictInThisCast.role} in ${cast.name})`;
                    opt.disabled = true;
                    opt.style.color = "var(--text-faint)";
                }

                // If this student is currently in this exact slot, select it
                if (currentAssignment && currentAssignment.student_id === student.id) {
                    opt.selected = true;
                    opt.disabled = false;
                    opt.textContent = student.name;
                    opt.style.color = "";
                }

                select.appendChild(opt);
            });

            select.addEventListener("change", onPrincipalAssignmentChange);

            cell.appendChild(select);
            row.appendChild(cell);
        });

        container.appendChild(row);
    });
}

async function onPrincipalAssignmentChange(e) {
    const select = e.target;
    const operaId = Number(select.dataset.operaId);
    const castId = Number(select.dataset.castId);
    const roleName = select.dataset.roleName;
    const studentId = select.value ? Number(select.value) : null;

    try {
        const res = await fetch(`${API}/admin/assign-principal`, {
            credentials: "include",
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                opera_id: operaId,
                cast_id: castId,
                role_name: roleName,
                student_id: studentId,
            })
        });
        const data = await res.json();
        if (data.status !== "success") {
            alert(data.message || "Failed to save.");
        }
        // Refresh both the modal grid AND the columns behind it
        await loadCastingForOpera(operaId);
        renderAssignRolesGrid();
    } catch (err) {
        console.error(err);
        alert("Server error.");
    }
}
// -----------------------------------------------------------
// PRODUCTION STAFF (inside Casting tab)
// -----------------------------------------------------------

let staffData = { staff: [], teachers: [] };

const STAFF_ROLE_LABELS = {
    "director": "Director",
    "assistant_director": "Assistant Director",
    "conductor": "Conductor",
    "assistant_conductor": "Assistant Conductor",
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

    const roleOrder = ["director", "assistant_director", "conductor", "assistant_conductor"];
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
    staffData.teachers.forEach(t => {
        const opt = document.createElement("option");
        opt.value = t.id;
        opt.textContent = t.name;
        teacherSelect.appendChild(opt);
    });

    document.getElementById("staff-role").value = "director";
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
    const staffRole = document.getElementById("staff-role").value;

    if (!teacherId) {
        msg.textContent = "Please pick a teacher.";
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
                staff_role: staffRole,
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
    list.innerHTML = html || `<em class="empty-note">No students in the system yet.</em>`;

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
            rehearsal_type: USER_ROLE === "orchestra_admin"
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
        operaTabsBox.innerHTML = `<em class="empty-note">No operas have rehearsals yet.</em>`;
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

    return `
        <div class="rehearsal-row">
            <div class="rehearsal-row-header">
                <div>
                    <strong>${escapeHtml(r.opera)}</strong>
                    <span class="rehearsal-cast">(${escapeHtml(castDisplay)})</span>
                    <span class="rehearsal-attendance">${attendanceLabel[r.attendance_type] || ""}</span>
                </div>
                <div class="rehearsal-row-actions">
                    ${callSingersBtn}
                    <button class="subtle-btn cancel-rehearsal-btn" data-id="${r.id}">Cancel</button>
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
            ${r.notes ? `<br><em>${escapeHtml(r.notes)}</em>` : ""}
        </div>
    `;
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

        let html = "";

        if (pending.length > 0) {
            html += `<h3 class="invite-section-heading">Pending</h3>`;
            html += pending.map(renderInviteRow).join("");
        }

        if (accepted.length > 0) {
            html += `<h3 class="invite-section-heading">Accepted</h3>`;
            html += accepted.map(renderInviteRow).join("");
        }

        if (expired.length > 0) {
            html += `<h3 class="invite-section-heading">Expired</h3>`;
            html += expired.map(renderInviteRow).join("");
        }

        box.innerHTML = html;

        // Wire up cancel buttons
        box.querySelectorAll(".cancel-invite-btn").forEach(btn => {
            btn.addEventListener("click", () => cancelInvite(btn.dataset.email));
        });
    } catch (e) {
        console.error(e);
        box.innerHTML = `<em class="empty-note">Failed to load invitations.</em>`;
    }
}

function renderInviteRow(i) {
    const ROLE_LABELS = { teacher: "Teacher", admin: "Admin", head_admin: "Head Admin", system_admin: "System Admin" };
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

    return `
        <div class="invite-row">
            <div class="invite-main">
                <strong>${escapeHtml(i.email)}</strong>
                <div class="invite-meta">${metaLine}</div>
                ${statusBadge}
            </div>
            <div class="invite-actions">${cancelBtn}</div>
        </div>
    `;
}

async function sendInvite() {
    const email = document.getElementById("invite-email").value.trim().toLowerCase();
    const fullnameHint = document.getElementById("invite-fullname-hint").value.trim();
    const role = document.getElementById("invite-role").value;


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

    // Org fields (relevant when system_admin invites head_admin / admin / student)
    const orgName = document.getElementById("invite-org-name")?.value.trim() || "";
    const orgSlug = document.getElementById("invite-org-slug")?.value.trim() || "";
    const orgType = document.getElementById("invite-org-type")?.value || "opera";

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
                org_name: orgName || null,
                org_slug: orgSlug || null,
                org_type: orgType || null,
            })
        });
        const data = await res.json();

        if (data.status === "success") {
            msg.textContent = data.email_sent
                ? "Invitation sent!"
                : "Invitation created, but email may not have been delivered.";
            msg.classList.add("success-msg");

            // Clear form, refresh list
            document.getElementById("invite-email").value = "";
            document.getElementById("invite-fullname-hint").value = "";
            const orgNameEl = document.getElementById("invite-org-name");
            const orgSlugEl = document.getElementById("invite-org-slug");
            if (orgNameEl) orgNameEl.value = "";
            if (orgSlugEl) { orgSlugEl.value = ""; delete orgSlugEl.dataset.manuallyEdited; }
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

function onInviteRoleChange() {
    const select = document.getElementById("invite-role");
    if (!select) return;

    if (USER_ROLE === "system_admin") {
        // system_admin invites: Head Admin (opera org) or Choir Admin (choir org)
        ["teacher", "orchestra_admin", "student"].forEach(val => {
            const opt = select.querySelector(`option[value="${val}"]`);
            if (opt) opt.style.display = "none";
        });
        const headAdminOpt = select.querySelector('option[value="head_admin"]');
        if (headAdminOpt) headAdminOpt.style.display = "";
        const adminOpt = select.querySelector('option[value="admin"]');
        if (adminOpt) { adminOpt.style.display = ""; adminOpt.textContent = "Choir Admin"; }

        // Default to head_admin if no valid selection
        if (!["head_admin", "admin"].includes(select.value)) {
            select.value = "head_admin";
        }

        const role = select.value;
        const orgSection = document.getElementById("invite-org-section");
        const orgTypeRow = document.getElementById("invite-org-type-row");
        const orgHint = document.getElementById("invite-org-hint");
        const orgTypeEl = document.getElementById("invite-org-type");

        orgSection?.classList.remove("hidden");
        document.getElementById("invite-teacher-type-section")?.classList.add("hidden");

        if (role === "head_admin") {
            if (orgTypeRow) orgTypeRow.classList.add("hidden");
            if (orgTypeEl) orgTypeEl.value = "opera";
            if (orgHint) orgHint.textContent = "Enter a name and ID for their organization. If the ID already exists the invite will join that org; otherwise a new org is created automatically.";
        } else if (role === "admin") {
            if (orgTypeRow) orgTypeRow.classList.add("hidden");
            if (orgTypeEl) orgTypeEl.value = "choir";
            if (orgHint) orgHint.textContent = "Enter a name and ID for the choir they'll administer. A new org is created if the ID doesn't exist yet.";
        }
        return;
    }

    // head_admin and below: hide head_admin and student options, restore admin label
    ["head_admin", "student"].forEach(val => {
        const opt = select.querySelector(`option[value="${val}"]`);
        if (opt) opt.style.display = "none";
    });
    const adminOptReset = select.querySelector('option[value="admin"]');
    if (adminOptReset) adminOptReset.textContent = "Admin";

    // Show admin and orchestra_admin for head_admin only
    const canInviteAdmin = USER_ROLE === "head_admin";
    const adminOpt = select.querySelector('option[value="admin"]');
    if (adminOpt) adminOpt.style.display = canInviteAdmin ? "" : "none";
    const orchAdminOpt = select.querySelector('option[value="orchestra_admin"]');
    if (orchAdminOpt) orchAdminOpt.style.display = canInviteAdmin ? "" : "none";

    const role = select.value;

    // Org section: system_admin only (handled above)
    document.getElementById("invite-org-section")?.classList.add("hidden");

    // Teacher type section: only shown when inviting a teacher
    const teacherTypeSection = document.getElementById("invite-teacher-type-section");
    if (teacherTypeSection) {
        teacherTypeSection.classList.toggle("hidden", role !== "teacher");
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
}

async function loadOrchestraSections() {
    try {
        const res = await fetch(`${API}/admin/orchestra-sections`, { credentials: "include" });
        orchestraSections = await res.json();

        // Auto-init standard sections on first load if none exist
        if (!orchestraSections.length) {
            const defaultSections = [];
            let sortOrder = 0;
            (typeof ORCHESTRA_INSTRUMENTS !== "undefined" ? ORCHESTRA_INSTRUMENTS : []).forEach(({ items }) => {
                items.forEach(name => {
                    defaultSections.push({ name, instrument: name.toLowerCase(), sort_order: sortOrder++ });
                });
            });
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
            if (orchestraSelectedOperaId) loadSeatingForOpera(orchestraSelectedOperaId);
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
    const nav = document.getElementById("orchestra-opera-tabs");
    if (!nav) return;
    try {
        const res = await fetch(`${API}/operas`, { credentials: "include" });
        const operas = await res.json();
        orchestraOperas = operas;
        if (!operas.length) {
            nav.innerHTML = `<em class="empty-note">No productions yet.</em>`;
            return;
        }
        nav.innerHTML = "";
        operas.forEach((op, i) => {
            const btn = document.createElement("button");
            btn.className = "sub-tab-btn" + (i === 0 ? " active" : "");
            btn.textContent = op.name;
            btn.dataset.operaId = op.id;
            btn.addEventListener("click", () => {
                nav.querySelectorAll(".sub-tab-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                orchestraSelectedOperaId = op.id;
                loadSeatingForOpera(op.id);
                loadProductionStaff(op.id);
            });
            nav.appendChild(btn);
        });
        // Auto-select first
        orchestraSelectedOperaId = operas[0].id;
        loadSeatingForOpera(operas[0].id);
        loadProductionStaff(operas[0].id);
    } catch (e) {
        console.error(e);
        nav.innerHTML = `<em class="empty-note">Failed to load productions.</em>`;
    }
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
                        data-current-member="${seat?.member_id || ""}">
                        ${seat?.member_id ? "Change" : "Assign"}
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
            btn.dataset.currentMember || null
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
        if (op.id === orchestraSelectedOperaId) return;
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
            body: JSON.stringify({ from_opera_id: fromOperaId, to_opera_id: orchestraSelectedOperaId }),
        });
        const data = await res.json();
        if (data.status === "success") {
            closeCopySeatingModal();
            loadSeatingForOpera(orchestraSelectedOperaId);
        } else {
            msg.textContent = data.message || "Failed to copy.";
        }
    } catch (e) {
        console.error(e);
        msg.textContent = "Server error.";
    }
}

function openAssignSeatModal(operaId, sectionId, chairNumber, currentMemberId) {
    const modal = document.getElementById("assign-seat-modal");
    if (!modal) return;

    const sec = orchestraSections.find(s => s.id === sectionId);
    document.getElementById("assign-seat-title").textContent =
        `Assign — ${sec ? sec.name : "Section"}, Chair ${chairNumber}`;
    document.getElementById("assign-seat-opera-id").value = operaId;
    document.getElementById("assign-seat-section-id").value = sectionId;
    document.getElementById("assign-seat-chair").value = chairNumber;
    document.getElementById("seat-msg").textContent = "";

    // Populate member dropdown — filter to members matching section's instrument
    const memberSelect = document.getElementById("assign-seat-member");
    memberSelect.innerHTML = `<option value="">— Unassigned —</option>`;
    const sectionInstrument = sec?.instrument?.toLowerCase() || "";
    const filtered = sectionInstrument
        ? orchestraMembers.filter(m => (m.instrument || "").toLowerCase() === sectionInstrument)
        : orchestraMembers;

    filtered.forEach(m => {
        const opt = document.createElement("option");
        opt.value = m.id;
        opt.textContent = `${m.name} (${m.instrument || "?"})`;
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
    const msg = document.getElementById("seat-msg");
    msg.textContent = "";

    try {
        const res = await fetch(`${API}/admin/orchestra-seats`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ opera_id: operaId, section_id: sectionId, chair_number: chairNumber, member_id: memberId })
        });
        const data = await res.json();
        if (data.status === "success") {
            closeAssignSeatModal();
            loadSeatingForOpera(operaId);
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
            loadOrchestraSections();
            if (orchestraSelectedOperaId) loadSeatingForOpera(orchestraSelectedOperaId);
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
        loadOrchestraSections();
        if (orchestraSelectedOperaId) loadSeatingForOpera(orchestraSelectedOperaId);
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
                    <div class="prod-main">
                        <strong>${escapeHtml(p.title)}</strong>
                        ${dates}
                        <span class="prod-casts">${p.num_casts} cast${p.num_casts !== 1 ? "s" : ""}</span>
                    </div>
                    <div class="prod-actions">
                        <button class="subtle-btn edit-prod-btn" data-id="${p.id}">Edit</button>
                    </div>
                </div>
            `;
        }).join("");

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
            msg.textContent = "Production created!";
            document.getElementById("prod-title").value = "";
            document.getElementById("prod-start-date").value = "";
            document.getElementById("prod-end-date").value = "";
            document.getElementById("prod-num-casts").value = "1";
            document.getElementById("prod-roles-list").innerHTML = "";
            loadProductions();
            loadCastingOperas();
            setTimeout(() => document.getElementById("prod-create-modal")?.classList.add("hidden"), 1200);
        } else {
            msg.textContent = data.message || "Failed to create production.";
        }
    } catch (e) {
        console.error(e);
        msg.textContent = "Server error.";
    }
}

function openEditProductionModal(prodId) {
    const p = productionsList.find(x => x.id === prodId);
    if (!p) return;
    document.getElementById("edit-prod-id").value = p.id;
    document.getElementById("edit-prod-title").value = p.title;
    document.getElementById("edit-prod-start-date").value = p.start_date || "";
    document.getElementById("edit-prod-end-date").value = p.end_date || "";
    document.getElementById("edit-prod-msg").textContent = "";
    document.getElementById("edit-production-modal").classList.remove("hidden");
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
            closeEditProductionModal();
            loadProductions();
        } else {
            msg.textContent = data.message || "Failed to save.";
        }
    } catch (e) {
        console.error(e);
        msg.textContent = "Server error.";
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
// 7. INIT
// -----------------------------------------------------------

document.addEventListener("DOMContentLoaded", async () => {
    await ME_READY;

    // --- Tabs ---
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.addEventListener("click", () => setActiveTab(btn.dataset.tab));
    });
    setActiveTab(getTabFromURL());
    window.addEventListener("hashchange", () => setActiveTab(getTabFromURL()));

    // --- Casting (new) ---
    loadCastingOperas();
    loadProductions();
    document.getElementById("open-assign-roles-btn")
        ?.addEventListener("click", openAssignRolesModal);
    document.getElementById("close-assign-roles-btn")
        ?.addEventListener("click", closeAssignRolesModal);
    document.getElementById("assign-roles-modal")?.addEventListener("click", (e) => {
        if (e.target.id === "assign-roles-modal") closeAssignRolesModal();
    });
    document.getElementById("manage-opera-students-btn")
        ?.addEventListener("click", openManageStudentsModal);
    document.getElementById("close-manage-students-btn")
        ?.addEventListener("click", closeManageStudentsModal);
    document.getElementById("manage-students-modal")?.addEventListener("click", (e) => {
        if (e.target.id === "manage-students-modal") closeManageStudentsModal();
    });

    // Production Staff
    document.getElementById("open-add-staff-btn")
        ?.addEventListener("click", openAddStaffModal);
    document.getElementById("cancel-staff-btn")
        ?.addEventListener("click", closeAddStaffModal);
    document.getElementById("save-staff-btn")
        ?.addEventListener("click", saveStaff);
    document.getElementById("add-staff-modal")?.addEventListener("click", (e) => {
        if (e.target.id === "add-staff-modal") closeAddStaffModal();
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
        ?.addEventListener("click", createRehearsal);
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

    // --- Invitations (wired up; data loads when tab is activated) ---
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
    onInviteRoleChange();

    // --- Productions ---
    document.getElementById("add-prod-role-btn")?.addEventListener("click", addProdRole);
    document.getElementById("create-production-btn")?.addEventListener("click", createProduction);
    document.getElementById("save-edit-prod-btn")?.addEventListener("click", saveEditProduction);
    document.getElementById("cancel-edit-prod-btn")?.addEventListener("click", closeEditProductionModal);
    document.getElementById("edit-production-modal")?.addEventListener("click", e => {
        if (e.target.id === "edit-production-modal") closeEditProductionModal();
    });

    // --- Requests ---
    // (event delegation handles approve/deny buttons rendered dynamically)
    document.getElementById("requests-list")?.addEventListener("click", onTransferReviewClick);

    // system_admin only needs Invitations — hide everything else
    if (USER_ROLE === "system_admin") {
        ["rehearsals", "casting", "orchestra"].forEach(tab => {
            document.querySelector(`.tab-btn[data-tab="${tab}"]`)?.classList.add("tab-btn--hidden");
        });
        setActiveTab("invitations");
    }

    // orchestra_admin sees Rehearsals and Orchestra only
    if (USER_ROLE === "orchestra_admin") {
        ["casting", "invitations"].forEach(tab => {
            document.querySelector(`.tab-btn[data-tab="${tab}"]`)?.classList.add("tab-btn--hidden");
        });
        // Hide vocal-only fields from the rehearsal form
        document.getElementById("rehearsal-vocal-fields")?.classList.add("hidden");
        setActiveTab("orchestra");
    }

    // Invitations tab: system_admin and head_admin only
    const canSeeInvitations = ["system_admin", "head_admin"].includes(USER_ROLE);
    document.querySelector('.tab-btn[data-tab="invitations"]')
        ?.classList.toggle("tab-btn--hidden", !canSeeInvitations);

    // Orchestra tab: head_admin and orchestra_admin only
    const canSeeOrchestra = ["head_admin", "orchestra_admin"].includes(USER_ROLE);
    document.querySelector('.tab-btn[data-tab="orchestra"]')
        ?.classList.toggle("tab-btn--hidden", !canSeeOrchestra);

    // --- Production staff ---
    document.getElementById("add-staff-btn")?.addEventListener("click", () => {
        if (orchestraSelectedOperaId) addProductionStaff(orchestraSelectedOperaId);
    });

    // --- Orchestra modals ---
    document.getElementById("copy-seating-btn")?.addEventListener("click", openCopySeatingModal);
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