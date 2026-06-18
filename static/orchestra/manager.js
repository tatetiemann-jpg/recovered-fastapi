// Orchestra Manager JS

let SECTIONS = [];
let MEMBERS = [];
let CONCERTS = [];
let PIECES = [];
let CURRENT_CONCERT_ID = null;
let CURRENT_PIECE_ID = null;
let CURRENT_REHEARSAL_ID = null;
let EDITING_MEMBER_ID = null;
let EDITING_CONCERT_ID = null;
let EDITING_PIECE_ID = null;
let SEAT_CONTEXT = null; // {piece_id, section_id, chair_number, part_number}

// ── Utilities ────────────────────────────────────────────────────────────────

function fmtDT(iso) {
  if (!iso) return "";
  const d = new Date(iso.replace(" ", "T"));
  return d.toLocaleString(undefined, {weekday:"short", month:"short", day:"numeric",
    hour:"2-digit", minute:"2-digit"});
}
function fmtDate(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString(undefined, {month:"short", day:"numeric", year:"numeric"});
}
async function api(method, path, body) {
  const opts = {method, headers:{"Content-Type":"application/json"}};
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  return r.json().catch(() => ({}));
}

// ── Tabs ─────────────────────────────────────────────────────────────────────

function initTabs() {
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      const panel = document.querySelector(`[data-tab-panel="${btn.dataset.tab}"]`);
      if (panel) panel.classList.add("active");
    });
  });
}

// ── Modals ───────────────────────────────────────────────────────────────────

function openModal(id) { document.getElementById(id)?.classList.remove("hidden"); }
function closeModal(id) { document.getElementById(id)?.classList.add("hidden"); }

document.addEventListener("click", e => {
  if (e.target.classList.contains("modal-close-btn")) {
    const id = e.target.dataset.modal;
    if (id) closeModal(id);
  }
  if (e.target.classList.contains("modal-overlay")) {
    e.target.classList.add("hidden");
  }
});

// ── Sections ─────────────────────────────────────────────────────────────────

async function loadSections() {
  SECTIONS = await api("GET", "/orchestra/sections").catch(() => []);
  if (!Array.isArray(SECTIONS)) SECTIONS = [];
  populateSectionSelects();
}

function populateSectionSelects() {
  const filterSel = document.getElementById("sub-section-filter");
  const newSubSel = document.getElementById("new-sub-section");
  const memberSel = document.getElementById("member-section-select");
  const subReqSel = document.getElementById("sub-req-section");
  const rehSections = document.getElementById("reh-sections-checks");

  [filterSel, newSubSel, memberSel, subReqSel].forEach(sel => {
    if (!sel) return;
    const first = sel.querySelector("option");
    sel.innerHTML = "";
    if (first) sel.appendChild(first.cloneNode(true));
    SECTIONS.forEach(s => {
      const o = document.createElement("option");
      o.value = s.id; o.textContent = s.name;
      sel.appendChild(o);
    });
  });

  if (rehSections) {
    rehSections.innerHTML = "";
    SECTIONS.forEach(s => {
      const lbl = document.createElement("label");
      lbl.style.cssText = "display:flex;align-items:center;gap:4px;";
      lbl.innerHTML = `<input type="checkbox" value="${s.id}"> ${s.name}`;
      rehSections.appendChild(lbl);
    });
  }
}

// ── Rehearsals ────────────────────────────────────────────────────────────────

async function loadRehearsals() {
  const rehearsals = await api("GET", "/orchestra/rehearsals");
  const list = document.getElementById("rehearsals-list");
  if (!Array.isArray(rehearsals) || !rehearsals.length) {
    list.innerHTML = "<em class='empty-note'>No rehearsals yet.</em>";
    return;
  }
  list.innerHTML = rehearsals.map(r => {
    const sectionTags = (r.section_ids || []).length
      ? `<span class="tag">${r.section_ids.length} section(s)</span>` : "";
    return `
      <div class="card" style="cursor:pointer;" data-reh-id="${r.id}">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <div>
            <strong>${fmtDT(r.start_time)}</strong>
            ${r.end_time ? `<span class="hint"> — ${fmtDT(r.end_time)}</span>` : ""}
            ${r.concert_title ? `<span class="tag" style="margin-left:8px;">${r.concert_title}</span>` : ""}
            ${r.attendance_type === "sectional" ? `<span class="tag" style="margin-left:4px;">Sectional</span>` : ""}
            ${sectionTags}
          </div>
          <div style="display:flex;gap:8px;">
            <button class="subtle-btn" onclick="openAttendanceModal(${r.id},'${encodeURIComponent(fmtDT(r.start_time))}')">Attendance</button>
            <button class="subtle-btn" onclick="deleteRehearsal(${r.id})">Delete</button>
          </div>
        </div>
        ${r.location ? `<div class="hint">${r.location}</div>` : ""}
        ${r.notes ? `<div class="hint" style="margin-top:4px;">${r.notes}</div>` : ""}
      </div>`;
  }).join("");
}

async function deleteRehearsal(id) {
  if (!confirm("Delete this rehearsal?")) return;
  await api("DELETE", `/orchestra/rehearsals/${id}`);
  loadRehearsals();
}

async function openAttendanceModal(rehearsalId, titleEncoded) {
  CURRENT_REHEARSAL_ID = rehearsalId;
  const title = decodeURIComponent(titleEncoded);
  document.getElementById("attendance-modal-title").textContent = `Attendance — ${title}`;
  openModal("attendance-modal");
  await Promise.all([
    loadAbsenceRequests(rehearsalId),
    loadAttendance(rehearsalId),
    loadSubRequests(rehearsalId),
  ]);
}

// ── Absence Requests ──────────────────────────────────────────────────────────

async function loadAbsenceRequests(rehearsalId) {
  const reqs = await api("GET", `/orchestra/rehearsals/${rehearsalId}/absence-requests`);
  const section = document.getElementById("absence-requests-section");
  const list = document.getElementById("absence-requests-list");
  const pending = Array.isArray(reqs) ? reqs.filter(r => r.status === "pending") : [];
  if (!pending.length) { section.style.display = "none"; return; }
  section.style.display = "";
  list.innerHTML = pending.map(r => `
    <div class="card" style="padding:8px 12px;display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
      <div>
        <strong>${r.fullname}</strong>
        ${r.section_name ? `<span class="hint"> — ${r.section_name}</span>` : ""}
        ${r.reason ? `<div class="hint" style="margin-top:2px;">Reason: ${r.reason}</div>` : ""}
        ${r.note ? `<div class="hint">Note: ${r.note}</div>` : ""}
      </div>
      <div style="display:flex;gap:6px;">
        <button class="subtle-btn" onclick="approveAbsence(${r.id}, ${rehearsalId})">✓ Approve</button>
        <button class="subtle-btn" onclick="denyAbsence(${r.id}, ${rehearsalId})">✗ Deny</button>
      </div>
    </div>`).join("");
}

async function approveAbsence(absenceId, rehearsalId) {
  const r = await api("POST", `/orchestra/absence-request/${absenceId}/approve`);
  if (r.status === "success") {
    await loadAbsenceRequests(rehearsalId);
    await loadSubRequests(rehearsalId);
    await loadAttendance(rehearsalId);
  } else { alert(r.message || "Failed."); }
}

async function denyAbsence(absenceId, rehearsalId) {
  if (!confirm("Deny this absence request?")) return;
  await api("POST", `/orchestra/absence-request/${absenceId}/deny`);
  loadAbsenceRequests(rehearsalId);
}

async function loadAttendance(rehearsalId) {
  const rows = await api("GET", `/orchestra/rehearsals/${rehearsalId}/attendance`);
  const list = document.getElementById("attendance-list");
  if (!Array.isArray(rows) || !rows.length) {
    list.innerHTML = "<em class='empty-note'>No members.</em>";
    return;
  }
  list.innerHTML = "";

  // Group: family → section_id → members
  const byFamily = {};
  rows.forEach(m => {
    const f = m.section_family || "other";
    const sKey = m.section_id || "__none__";
    if (!byFamily[f]) byFamily[f] = {};
    if (!byFamily[f][sKey]) byFamily[f][sKey] = { name: m.section_name || "Other", members: [] };
    byFamily[f][sKey].members.push(m);
  });

  FAMILY_ORDER_KEYS.filter(f => byFamily[f]).forEach(f => {
    const { group: famGroup, inner: famInner } = makeOrchAccordion(
      FAMILY_LABELS[f], true, true, "orch-family-group"
    );

    Object.values(byFamily[f]).forEach(secGroup => {
      const { group: secGrp, inner: secInner } = makeOrchAccordion(
        `${secGroup.name} <span class="section-count">(${secGroup.members.length})</span>`,
        true, true
      );

      secGroup.members.forEach(m => {
        const row = document.createElement("div");
        row.className = "card";
        row.style.cssText = "display:flex;justify-content:space-between;align-items:center;padding:8px 12px;margin-bottom:6px;";
        row.innerHTML = `
          <div>
            <strong>${m.fullname}</strong>
            ${m.instrument ? `<span class="hint"> — ${m.instrument}</span>` : ""}
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;">
            <button class="subtle-btn ${m.status === 'attended' ? 'active' : ''}"
              onclick="setAttendance(${rehearsalId}, ${m.member_id}, 'attended')">✓ Attended</button>
            <button class="subtle-btn ${m.status === 'excused' ? 'active' : ''}"
              onclick="setAttendance(${rehearsalId}, ${m.member_id}, 'excused')">~ Excused</button>
            <button class="subtle-btn ${m.status === 'absent' ? 'active' : ''}"
              onclick="adminMarkAbsent(${rehearsalId}, ${m.member_id}, '${m.fullname.replace(/'/g,"\\'")}')">✗ Absent</button>
          </div>`;
        secInner.appendChild(row);
      });

      famInner.appendChild(secGrp);
    });

    list.appendChild(famGroup);
  });
}

async function setAttendance(rehearsalId, memberId, status) {
  await api("POST", `/orchestra/rehearsals/${rehearsalId}/attendance`, {member_id: memberId, status});
  loadAttendance(rehearsalId);
}

async function adminMarkAbsent(rehearsalId, memberId, memberName) {
  const reason = prompt(`Reason for ${memberName}'s absence (optional):`);
  if (reason === null) return; // cancelled
  await api("POST", `/orchestra/rehearsals/${rehearsalId}/attendance`,
            {member_id: memberId, status: "absent"});
  const r = await api("POST", "/orchestra/admin-mark-absent", {
    rehearsal_id: rehearsalId,
    member_id: memberId,
    reason: reason || "Admin marked absent",
  });
  if (r.status === "success") {
    await loadAttendance(rehearsalId);
    await loadSubRequests(rehearsalId);
  } else { alert(r.message || "Failed."); }
}

document.getElementById("att-mark-all-attended")?.addEventListener("click", async () => {
  if (!CURRENT_REHEARSAL_ID) return;
  const rows = await api("GET", `/orchestra/rehearsals/${CURRENT_REHEARSAL_ID}/attendance`);
  if (!Array.isArray(rows)) return;
  await Promise.all(rows.map(m =>
    api("POST", `/orchestra/rehearsals/${CURRENT_REHEARSAL_ID}/attendance`, {member_id: m.member_id, status: "attended"})
  ));
  loadAttendance(CURRENT_REHEARSAL_ID);
});

document.getElementById("att-mark-all-absent")?.addEventListener("click", async () => {
  if (!CURRENT_REHEARSAL_ID) return;
  const rows = await api("GET", `/orchestra/rehearsals/${CURRENT_REHEARSAL_ID}/attendance`);
  if (!Array.isArray(rows)) return;
  await Promise.all(rows.map(m =>
    api("POST", `/orchestra/rehearsals/${CURRENT_REHEARSAL_ID}/attendance`, {member_id: m.member_id, status: "absent"})
  ));
  loadAttendance(CURRENT_REHEARSAL_ID);
});

// Sub Requests in Attendance Modal
async function loadSubRequests(rehearsalId) {
  const wrap = document.getElementById("attendance-sub-requests");
  const reqs = await api("GET", `/orchestra/sub-requests/${rehearsalId}`);
  if (!Array.isArray(reqs) || !reqs.length) { wrap.innerHTML = ""; return; }
  wrap.innerHTML = `<h4 style="margin-bottom:var(--space-2);">Sub Requests</h4>` +
    reqs.map(req => {
      const isSectionSent = req.status === "section_sent";
      const statusLabel = req.status === "section_sent" ? "Section notified (8hr window)"
        : req.status.replace(/_/g, " ");
      const actionable = req.status !== "filled" && req.status !== "cancelled";
      return `
        <div class="card" style="padding:8px 12px;margin-bottom:8px;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div>
              <strong>${req.section_name}</strong>
              <span class="tag" style="margin-left:8px;">${statusLabel}</span>
              ${req.filled_by_name ? `<span class="hint"> — filled by ${req.filled_by_name}</span>` : ""}
              ${isSectionSent ? `<div class="hint" style="margin-top:4px;">Section members have been emailed. If no one covers within 8 hrs, the sub list will be contacted automatically.</div>` : ""}
            </div>
            ${actionable ? `<div style="display:flex;gap:6px;flex-shrink:0;margin-left:8px;">
              ${!isSectionSent ? `<button class="subtle-btn" onclick="contactPreferred(${req.id})">Contact Preferred</button>` : ""}
              <button class="subtle-btn" onclick="contactAll(${req.id})">Skip to All Subs</button>
              <button class="subtle-btn" onclick="cancelSubReq(${req.id})">Cancel</button>
            </div>` : ""}
          </div>
        </div>`;
    }).join("");
}

async function contactPreferred(reqId) {
  const r = await api("POST", `/orchestra/sub-request/${reqId}/contact-preferred`);
  alert(r.status === "success" ? "Preferred sub contacted." : r.message || "Failed.");
  loadSubRequests(CURRENT_REHEARSAL_ID);
}
async function contactAll(reqId) {
  if (!confirm("This will email all subs on the list immediately, bypassing any remaining section wait time. Continue?")) return;
  const r = await api("POST", `/orchestra/sub-request/${reqId}/contact-all`);
  alert(r.status === "success" ? `Contacted ${r.sent} sub(s).` : r.message || "Failed.");
  loadSubRequests(CURRENT_REHEARSAL_ID);
}
async function cancelSubReq(reqId) {
  if (!confirm("Cancel this sub request?")) return;
  await api("POST", `/orchestra/sub-request/${reqId}/cancel`);
  loadSubRequests(CURRENT_REHEARSAL_ID);
}

document.getElementById("open-sub-request-btn")?.addEventListener("click", () => {
  openModal("sub-request-modal");
});

document.getElementById("create-sub-request-btn")?.addEventListener("click", async () => {
  const sectionId = parseInt(document.getElementById("sub-req-section").value);
  const msg = document.getElementById("sub-req-msg");
  if (!sectionId) { msg.textContent = "Select a section."; return; }
  const r = await api("POST", "/orchestra/sub-request", {
    rehearsal_id: CURRENT_REHEARSAL_ID, section_id: sectionId
  });
  if (r.status === "success" || r.sub_request_id) {
    msg.textContent = r.existing ? "Request already exists." : "Sub request created.";
    closeModal("sub-request-modal");
    loadSubRequests(CURRENT_REHEARSAL_ID);
  } else {
    msg.textContent = r.message || "Failed.";
  }
});

// Schedule Rehearsal Modal
document.getElementById("open-schedule-rehearsal-btn")?.addEventListener("click", () => {
  document.getElementById("reh-save-msg").textContent = "";
  openModal("schedule-rehearsal-modal");
  // Populate concert select
  const sel = document.getElementById("reh-concert");
  sel.innerHTML = '<option value="">— None —</option>';
  CONCERTS.forEach(c => {
    const o = document.createElement("option");
    o.value = c.id; o.textContent = c.title;
    sel.appendChild(o);
  });
});

document.getElementById("reh-type")?.addEventListener("change", function() {
  const wrap = document.getElementById("reh-sections-wrap");
  wrap?.classList.toggle("hidden", this.value !== "sectional");
});

document.getElementById("save-rehearsal-btn")?.addEventListener("click", async () => {
  const start = document.getElementById("reh-start").value;
  const msg = document.getElementById("reh-save-msg");
  if (!start) { msg.textContent = "Start time required."; return; }
  const type = document.getElementById("reh-type").value;
  const sectionIds = type === "sectional"
    ? [...document.querySelectorAll("#reh-sections-checks input:checked")].map(el => parseInt(el.value))
    : [];
  const r = await api("POST", "/orchestra/rehearsals", {
    start_time: start,
    end_time: document.getElementById("reh-end").value || null,
    concert_id: parseInt(document.getElementById("reh-concert").value) || null,
    attendance_type: type,
    section_ids: sectionIds,
    location: document.getElementById("reh-location").value,
    notes: document.getElementById("reh-notes").value,
  });
  if (r.status === "success") {
    closeModal("schedule-rehearsal-modal");
    loadRehearsals();
  } else {
    msg.textContent = r.message || "Failed.";
  }
});

// ── Concerts ──────────────────────────────────────────────────────────────────

async function loadConcerts() {
  CONCERTS = await api("GET", "/orchestra/concerts");
  if (!Array.isArray(CONCERTS)) CONCERTS = [];
  renderConcerts();
}

function renderConcerts() {
  const list = document.getElementById("concerts-list");
  if (!CONCERTS.length) { list.innerHTML = "<em class='empty-note'>No concerts yet.</em>"; return; }
  list.innerHTML = CONCERTS.map(c => `
    <div class="card" style="display:flex;justify-content:space-between;align-items:center;">
      <div>
        <strong>${c.title}</strong>
        ${c.start_date ? `<span class="hint"> — ${fmtDate(c.start_date)}</span>` : ""}
      </div>
      <div style="display:flex;gap:8px;">
        <button class="subtle-btn" onclick="openPiecesPanel(${c.id},'${encodeURIComponent(c.title)}')">Works &amp; Seating →</button>
        <button class="subtle-btn" onclick="openEditConcert(${c.id})">Edit</button>
        <button class="subtle-btn" onclick="deleteConcert(${c.id})">Delete</button>
      </div>
    </div>`).join("");
}

document.getElementById("open-add-concert-btn")?.addEventListener("click", () => {
  EDITING_CONCERT_ID = null;
  document.getElementById("concert-modal-title").textContent = "New Concert";
  document.getElementById("concert-title-input").value = "";
  document.getElementById("concert-start").value = "";
  document.getElementById("concert-end").value = "";
  document.getElementById("concert-save-msg").textContent = "";
  openModal("add-concert-modal");
});

function openEditConcert(id) {
  const c = CONCERTS.find(x => x.id === id);
  if (!c) return;
  EDITING_CONCERT_ID = id;
  document.getElementById("concert-modal-title").textContent = "Edit Concert";
  document.getElementById("concert-title-input").value = c.title;
  document.getElementById("concert-start").value = c.start_date || "";
  document.getElementById("concert-end").value = c.end_date || "";
  document.getElementById("concert-save-msg").textContent = "";
  openModal("add-concert-modal");
}

document.getElementById("save-concert-btn")?.addEventListener("click", async () => {
  const title = document.getElementById("concert-title-input").value.trim();
  const msg = document.getElementById("concert-save-msg");
  if (!title) { msg.textContent = "Title required."; return; }
  const body = {
    title,
    start_date: document.getElementById("concert-start").value || null,
    end_date: document.getElementById("concert-end").value || null,
  };
  const r = EDITING_CONCERT_ID
    ? await api("PATCH", `/orchestra/concerts/${EDITING_CONCERT_ID}`, body)
    : await api("POST", "/orchestra/concerts", body);
  if (r.status === "success" || r.id) {
    closeModal("add-concert-modal");
    await loadConcerts();
  } else { msg.textContent = r.message || "Failed."; }
});

async function deleteConcert(id) {
  if (!confirm("Delete this concert?")) return;
  await api("DELETE", `/orchestra/concerts/${id}`);
  loadConcerts();
}

// ── Pieces ────────────────────────────────────────────────────────────────────

async function openPiecesPanel(concertId, titleEncoded) {
  CURRENT_CONCERT_ID = concertId;
  CURRENT_PIECE_ID = null;
  const title = decodeURIComponent(titleEncoded);
  document.getElementById("pieces-concert-title").textContent = `Works — ${title}`;
  document.getElementById("pieces-panel").classList.remove("hidden");
  document.getElementById("seating-panel").classList.add("hidden");
  document.getElementById("concerts-list").classList.add("hidden");
  await loadPieces(concertId);
}

document.getElementById("back-to-concerts-btn")?.addEventListener("click", () => {
  document.getElementById("pieces-panel").classList.add("hidden");
  document.getElementById("seating-panel").classList.add("hidden");
  document.getElementById("concerts-list").classList.remove("hidden");
});

document.getElementById("back-to-pieces-btn")?.addEventListener("click", () => {
  document.getElementById("seating-panel").classList.add("hidden");
  if (CURRENT_CONCERT_ID) loadPieces(CURRENT_CONCERT_ID);
});

async function loadPieces(concertId) {
  PIECES = await api("GET", `/orchestra/concerts/${concertId}/pieces`);
  if (!Array.isArray(PIECES)) PIECES = [];
  const list = document.getElementById("pieces-list");
  if (!PIECES.length) { list.innerHTML = "<em class='empty-note'>No works added yet.</em>"; return; }
  list.innerHTML = PIECES.map(p => `
    <div class="card" style="display:flex;justify-content:space-between;align-items:center;">
      <div>
        <strong>${p.title}</strong>
        ${p.composer ? `<span class="hint"> — ${p.composer}</span>` : ""}
        ${p.opus ? `<span class="hint"> ${p.opus}</span>` : ""}
        ${p.duration_min ? `<span class="hint"> (${p.duration_min} min)</span>` : ""}
      </div>
      <div style="display:flex;gap:8px;">
        <button class="subtle-btn" onclick="openSeatingPanel(${p.id},'${encodeURIComponent(p.title)}')">Seating →</button>
        <button class="subtle-btn" onclick="openEditPiece(${p.id})">Edit</button>
        <button class="subtle-btn" onclick="deletePiece(${p.id})">Delete</button>
      </div>
    </div>`).join("");
}

document.getElementById("open-add-piece-btn")?.addEventListener("click", () => {
  EDITING_PIECE_ID = null;
  document.getElementById("piece-modal-title").textContent = "Add Work";
  ["piece-title-input","piece-composer","piece-opus","piece-duration"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  document.getElementById("piece-save-msg").textContent = "";
  openModal("add-piece-modal");
});

function openEditPiece(id) {
  const p = PIECES.find(x => x.id === id);
  if (!p) return;
  EDITING_PIECE_ID = id;
  document.getElementById("piece-modal-title").textContent = "Edit Work";
  document.getElementById("piece-title-input").value = p.title;
  document.getElementById("piece-composer").value = p.composer || "";
  document.getElementById("piece-opus").value = p.opus || "";
  document.getElementById("piece-duration").value = p.duration_min || "";
  document.getElementById("piece-save-msg").textContent = "";
  openModal("add-piece-modal");
}

document.getElementById("save-piece-btn")?.addEventListener("click", async () => {
  const title = document.getElementById("piece-title-input").value.trim();
  const msg = document.getElementById("piece-save-msg");
  if (!title) { msg.textContent = "Title required."; return; }
  const body = {
    title,
    composer: document.getElementById("piece-composer").value.trim(),
    opus: document.getElementById("piece-opus").value.trim(),
    duration_min: parseInt(document.getElementById("piece-duration").value) || null,
  };
  const r = EDITING_PIECE_ID
    ? await api("PATCH", `/orchestra/pieces/${EDITING_PIECE_ID}`, body)
    : await api("POST", `/orchestra/concerts/${CURRENT_CONCERT_ID}/pieces`, body);
  if (r.status === "success" || r.id) {
    closeModal("add-piece-modal");
    loadPieces(CURRENT_CONCERT_ID);
  } else { msg.textContent = r.message || "Failed."; }
});

async function deletePiece(id) {
  if (!confirm("Remove this work?")) return;
  await api("DELETE", `/orchestra/pieces/${id}`);
  loadPieces(CURRENT_CONCERT_ID);
}

// ── Seating ───────────────────────────────────────────────────────────────────

async function openSeatingPanel(pieceId, titleEncoded) {
  CURRENT_PIECE_ID = pieceId;
  document.getElementById("seating-piece-title").textContent =
    `Seating — ${decodeURIComponent(titleEncoded)}`;
  document.getElementById("pieces-panel").classList.add("hidden");
  document.getElementById("seating-panel").classList.remove("hidden");
  await loadSeating(pieceId);
}

async function loadSeating(pieceId) {
  const seats = await api("GET", `/orchestra/pieces/${pieceId}/seats`);
  const grid = document.getElementById("seating-grid");
  if (!Array.isArray(seats)) { grid.innerHTML = "<em class='empty-note'>No seats assigned.</em>"; return; }
  grid.innerHTML = "";

  // Index seats by section+chair+part
  const seatIndex = {};
  seats.forEach(s => {
    const key = `${s.section_id}_${s.chair_number}_${s.part_number}`;
    seatIndex[key] = s;
  });

  // Group sections by family
  const byFamily = {};
  SECTIONS.forEach(sec => {
    const fam = orchFamily(sec.instrument);
    if (!byFamily[fam]) byFamily[fam] = [];
    byFamily[fam].push(sec);
  });

  ORCH_FAMILY_ORDER.filter(fam => byFamily[fam]).forEach(fam => {
    const { group: famGroup, inner: famInner } = makeOrchAccordion(fam, true, true, "orch-family-group");

    byFamily[fam].forEach(sec => {
      const chairCount = sec.chair_count || 8;
      const isViolin = /violin/i.test(sec.name);
      const partCount = isViolin ? 4 : 1;
      const partLabels = ["1st Part", "2nd Part", "3rd Part", "4th Part"];

      // Count assigned seats for badge
      let assignedCount = 0;
      for (let part = 1; part <= partCount; part++)
        for (let chair = 1; chair <= chairCount; chair++)
          if (seatIndex[`${sec.id}_${chair}_${part}`]) assignedCount++;

      const { group: secGrp, inner: secInner } = makeOrchAccordion(
        `${sec.name} <span class="section-count">${assignedCount}/${chairCount * partCount}</span>`,
        false, true
      );
      secInner.style.paddingBottom = "var(--space-3)";

      for (let part = 1; part <= partCount; part++) {
        if (partCount > 1) {
          const partLabel = document.createElement("div");
          partLabel.className = "hint";
          partLabel.style.cssText = "margin:10px 0 6px;font-weight:500;";
          partLabel.textContent = partLabels[part - 1];
          secInner.appendChild(partLabel);
        }

        const chairRow = document.createElement("div");
        chairRow.className = "chair-grid";

        for (let chair = 1; chair <= chairCount; chair++) {
          const seat = seatIndex[`${sec.id}_${chair}_${part}`];
          const name = seat ? (seat.member_name || "—") : "";
          const assigned = !!name;

          const cell = document.createElement("div");
          cell.className = "chair-cell" + (assigned ? " assigned" : "");
          cell.title = `Chair ${chair}${part > 1 ? ` / ${partLabels[part-1]}` : ""}${assigned ? " — " + name : ""}`;
          cell.innerHTML = `<span class="chair-num">${chair}</span>${assigned ? `<span class="chair-name">${name}</span>` : ""}`;
          cell.addEventListener("click", () => openAssignSeat(sec.id, chair, part));
          chairRow.appendChild(cell);
        }

        secInner.appendChild(chairRow);
      }

      famInner.appendChild(secGrp);
    });

    grid.appendChild(famGroup);
  });
}

function openAssignSeat(sectionId, chairNumber, partNumber) {
  SEAT_CONTEXT = {piece_id: CURRENT_PIECE_ID, section_id: sectionId,
                  chair_number: chairNumber, part_number: partNumber};
  document.getElementById("assign-seat-title").textContent =
    `Assign Seat — Chair ${chairNumber}${partNumber > 1 ? ` / Part ${partNumber}` : ""}`;
  document.getElementById("seat-save-msg").textContent = "";
  document.getElementById("seat-no-account-fields").classList.add("hidden");
  document.getElementById("seat-ext-name").value = "";
  document.getElementById("seat-ext-email").value = "";

  // Populate member select
  const sel = document.getElementById("seat-member-select");
  sel.innerHTML = '<option value="">— Unassigned —</option>';
  // Filter members by section
  const sectionMembers = MEMBERS.filter(m => m.section_id === sectionId);
  const otherMembers = MEMBERS.filter(m => m.section_id !== sectionId);
  if (sectionMembers.length) {
    const grp = document.createElement("optgroup");
    grp.label = "This Section";
    sectionMembers.forEach(m => {
      const o = document.createElement("option");
      o.value = m.id; o.textContent = m.fullname + (m.part_label ? ` (${m.part_label})` : "");
      grp.appendChild(o);
    });
    sel.appendChild(grp);
  }
  if (otherMembers.length) {
    const grp = document.createElement("optgroup");
    grp.label = "Other Members";
    otherMembers.forEach(m => {
      const o = document.createElement("option");
      o.value = m.id; o.textContent = m.fullname;
      grp.appendChild(o);
    });
    sel.appendChild(grp);
  }
  openModal("assign-seat-modal");
}

document.getElementById("seat-no-account-toggle")?.addEventListener("click", () => {
  document.getElementById("seat-no-account-fields")?.classList.toggle("hidden");
});

document.getElementById("save-seat-btn")?.addEventListener("click", async () => {
  if (!SEAT_CONTEXT) return;
  const msg = document.getElementById("seat-save-msg");
  const memberId = parseInt(document.getElementById("seat-member-select").value) || null;
  const extName = document.getElementById("seat-ext-name").value.trim();
  const extEmail = document.getElementById("seat-ext-email").value.trim();
  const r = await api("POST", `/orchestra/pieces/${SEAT_CONTEXT.piece_id}/seats`, {
    section_id: SEAT_CONTEXT.section_id,
    chair_number: SEAT_CONTEXT.chair_number,
    part_number: SEAT_CONTEXT.part_number,
    member_id: memberId,
    external_name: extName || null,
    external_email: extEmail || null,
  });
  if (r.status === "success") {
    closeModal("assign-seat-modal");
    loadSeating(SEAT_CONTEXT.piece_id);
  } else { msg.textContent = r.message || "Failed."; }
});

// ── Members ───────────────────────────────────────────────────────────────────

async function loadMembers() {
  MEMBERS = await api("GET", "/orchestra/members");
  if (!Array.isArray(MEMBERS)) MEMBERS = [];
  renderMembers();
}

const FAMILY_LABELS = {
  strings: "Strings",
  woodwinds: "Woodwinds",
  brass: "Brass",
  percussion: "Percussion",
  other: "Other"
};
const FAMILY_ORDER_KEYS = ["strings","woodwinds","brass","percussion","other"];

function renderMembers() {
  const list = document.getElementById("members-list");
  if (!MEMBERS.length) { list.innerHTML = "<em class='empty-note'>No members yet.</em>"; return; }
  list.innerHTML = "";

  // Group: family → section_id → members
  const byFamily = {};
  MEMBERS.forEach(m => {
    const f = m.section_family || "other";
    const sKey = m.section_id || "__none__";
    if (!byFamily[f]) byFamily[f] = {};
    if (!byFamily[f][sKey]) byFamily[f][sKey] = { name: m.section_name || "Other", members: [] };
    byFamily[f][sKey].members.push(m);
  });

  FAMILY_ORDER_KEYS.filter(f => byFamily[f]).forEach(f => {
    const { group: famGroup, inner: famInner } = makeOrchAccordion(
      FAMILY_LABELS[f], true, true, "orch-family-group"
    );
    // Update count label
    const total = Object.values(byFamily[f]).reduce((s, g) => s + g.members.length, 0);
    famGroup.querySelector(".section-name").insertAdjacentHTML("afterend",
      `<span class="section-count" style="margin-left:6px;">(${total})</span>`);

    Object.values(byFamily[f]).forEach(secGroup => {
      const { group: secGrp, inner: secInner } = makeOrchAccordion(
        `${secGroup.name} <span class="section-count">(${secGroup.members.length})</span>`,
        false, true
      );

      secGroup.members.forEach(m => {
        const row = document.createElement("div");
        row.className = "card";
        row.style.cssText = "display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;";
        row.innerHTML = `
          <div>
            <strong>${m.fullname}</strong>
            ${m.instrument ? `<span class="hint"> — ${m.instrument}</span>` : ""}
            ${m.part_label ? `<span class="tag" style="margin-left:4px;">${m.part_label}</span>` : ""}
          </div>
          <div style="display:flex;gap:8px;">
            <button class="subtle-btn" onclick="openEditMember(${m.id})">Edit</button>
            <button class="subtle-btn" onclick="removeMember(${m.id})">Remove</button>
          </div>`;
        secInner.appendChild(row);
      });

      famInner.appendChild(secGrp);
    });

    list.appendChild(famGroup);
  });
}

document.getElementById("open-add-member-btn")?.addEventListener("click", () => {
  EDITING_MEMBER_ID = null;
  document.getElementById("member-modal-title").textContent = "Add Member";
  ["member-name-input","member-email-input","member-phone-input",
   "member-instrument-input","member-part-label","member-notes-input"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  document.getElementById("member-family-select").value = "strings";
  document.getElementById("member-section-select").value = "";
  document.getElementById("member-save-msg").textContent = "";
  openModal("add-member-modal");
});

function openEditMember(id) {
  const m = MEMBERS.find(x => x.id === id);
  if (!m) return;
  EDITING_MEMBER_ID = id;
  document.getElementById("member-modal-title").textContent = "Edit Member";
  document.getElementById("member-name-input").value = m.fullname;
  document.getElementById("member-email-input").value = m.email || "";
  document.getElementById("member-phone-input").value = m.phone || "";
  document.getElementById("member-instrument-input").value = m.instrument || "";
  document.getElementById("member-part-label").value = m.part_label || "";
  document.getElementById("member-family-select").value = m.section_family || "other";
  document.getElementById("member-section-select").value = m.section_id || "";
  document.getElementById("member-notes-input").value = m.notes || "";
  document.getElementById("member-save-msg").textContent = "";
  openModal("add-member-modal");
}

document.getElementById("save-member-btn")?.addEventListener("click", async () => {
  const fullname = document.getElementById("member-name-input").value.trim();
  const msg = document.getElementById("member-save-msg");
  if (!fullname) { msg.textContent = "Name required."; return; }
  const body = {
    fullname,
    email: document.getElementById("member-email-input").value.trim(),
    phone: document.getElementById("member-phone-input").value.trim(),
    instrument: document.getElementById("member-instrument-input").value.trim(),
    part_label: document.getElementById("member-part-label").value.trim(),
    section_family: document.getElementById("member-family-select").value,
    section_id: parseInt(document.getElementById("member-section-select").value) || null,
    notes: document.getElementById("member-notes-input").value.trim(),
  };
  const r = EDITING_MEMBER_ID
    ? await api("PATCH", `/orchestra/members/${EDITING_MEMBER_ID}`, body)
    : await api("POST", "/orchestra/members", body);
  if (r.status === "success" || r.id) {
    closeModal("add-member-modal");
    await loadMembers();
  } else { msg.textContent = r.message || "Failed."; }
});

async function removeMember(id) {
  if (!confirm("Remove this member from the roster?")) return;
  await api("DELETE", `/orchestra/members/${id}`);
  loadMembers();
}

// ── Subs ─────────────────────────────────────────────────────────────────────

async function loadSubs(sectionId) {
  const url = sectionId ? `/orchestra/subs?section_id=${sectionId}` : "/orchestra/subs";
  const subs = await api("GET", url);
  const list = document.getElementById("sub-roster-list");
  if (!Array.isArray(subs) || !subs.length) {
    list.innerHTML = "<em class='empty-note'>No subs yet.</em>";
    return;
  }
  list.innerHTML = subs.map(s => `
    <div class="card" style="display:flex;justify-content:space-between;align-items:center;">
      <div>
        <strong>${s.fullname}</strong>
        <span class="hint"> — ${s.section_name}</span>
        ${s.is_preferred ? `<span class="tag preferred" style="margin-left:6px;">Preferred${s.preferred_rank ? ` #${s.preferred_rank}` : ""}</span>` : ""}
        <div class="hint" style="margin-top:2px;">
          ${s.email}
          ${s.phone ? ` · ${s.phone}` : ""}
          · Accepted: ${s.accepted_count}, Declined: ${s.declined_count}
        </div>
        ${s.notes ? `<div class="hint">${s.notes}</div>` : ""}
      </div>
      <div style="display:flex;gap:6px;">
        ${s.is_preferred
          ? `<button class="subtle-btn" onclick="setSubPreferred(${s.id}, false, null)">Remove Preferred</button>`
          : `<button class="subtle-btn" onclick="promptSetPreferred(${s.id})">Set Preferred</button>`}
        <button class="subtle-btn" onclick="removeSub(${s.id})">Remove</button>
      </div>
    </div>`).join("");
}

document.getElementById("sub-section-filter")?.addEventListener("change", function() {
  loadSubs(parseInt(this.value) || null);
});

document.getElementById("add-sub-btn")?.addEventListener("click", async () => {
  const name = document.getElementById("new-sub-name").value.trim();
  const email = document.getElementById("new-sub-email").value.trim();
  const sectionId = parseInt(document.getElementById("new-sub-section").value) || null;
  const msg = document.getElementById("sub-add-msg");
  if (!name || !email || !sectionId) { msg.textContent = "Name, email, and section required."; return; }
  const r = await api("POST", "/orchestra/subs", {
    fullname: name, email,
    phone: document.getElementById("new-sub-phone").value.trim(),
    section_id: sectionId,
    is_preferred: document.getElementById("new-sub-preferred").checked,
    notes: document.getElementById("new-sub-notes").value.trim(),
  });
  if (r.status === "success") {
    msg.textContent = "Added.";
    ["new-sub-name","new-sub-email","new-sub-phone","new-sub-notes"].forEach(id => {
      const el = document.getElementById(id); if (el) el.value = "";
    });
    document.getElementById("new-sub-preferred").checked = false;
    const secFilter = document.getElementById("sub-section-filter").value;
    loadSubs(parseInt(secFilter) || null);
  } else { msg.textContent = r.message || "Failed."; }
});

function promptSetPreferred(subId) {
  const rank = prompt("Enter preferred rank (1 = first contacted):", "1");
  if (rank === null) return;
  setSubPreferred(subId, true, parseInt(rank) || 1);
}

async function setSubPreferred(subId, isPreferred, rank) {
  await api("PATCH", `/orchestra/subs/${subId}`, {
    is_preferred: isPreferred,
    preferred_rank: isPreferred ? rank : null,
  });
  const secFilter = document.getElementById("sub-section-filter").value;
  loadSubs(parseInt(secFilter) || null);
}

async function removeSub(subId) {
  if (!confirm("Remove sub from roster?")) return;
  await api("DELETE", `/orchestra/subs/${subId}`);
  const secFilter = document.getElementById("sub-section-filter").value;
  loadSubs(parseInt(secFilter) || null);
}

// ── Messages ──────────────────────────────────────────────────────────────────

function initMessages() {
  document.querySelectorAll(".dm-view-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".dm-view-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".dm-view").forEach(v => v.classList.add("hidden"));
      btn.classList.add("active");
      const view = document.getElementById(`dm-${btn.dataset.dmView}`);
      if (view) view.classList.remove("hidden");
      if (btn.dataset.dmView === "inbox") loadDMInbox();
      if (btn.dataset.dmView === "read") loadDMRead();
      if (btn.dataset.dmView === "compose") loadDMComposePicker();
    });
  });
  loadDMInbox();
}

async function loadDMInbox() {
  const res = await fetch("/dm/inbox");
  const msgs = await res.json().catch(() => []);
  const list = document.getElementById("dm-inbox-list");
  const unread = msgs.filter(m => !m.read_at);
  document.getElementById("dm-badge").textContent = unread.length;
  document.getElementById("dm-badge").classList.toggle("hidden", !unread.length);
  if (!msgs.length) { list.innerHTML = "<em class='empty-note'>No messages.</em>"; return; }
  list.innerHTML = msgs.filter(m => !m.read_at).map(m => dmCard(m, false)).join("");
}

async function loadDMRead() {
  const res = await fetch("/dm/inbox");
  const msgs = await res.json().catch(() => []);
  const list = document.getElementById("dm-read-list");
  const read = msgs.filter(m => m.read_at);
  if (!read.length) { list.innerHTML = "<em class='empty-note'>No read messages.</em>"; return; }
  list.innerHTML = read.map(m => dmCard(m, true)).join("");
}

function dmCard(m, read) {
  return `<div class="card">
    <div style="display:flex;justify-content:space-between;">
      <strong>${m.subject || "(no subject)"}</strong>
      <span class="hint">${m.sent_at ? new Date(m.sent_at).toLocaleString() : ""}</span>
    </div>
    <div class="hint">From: ${m.sender_name || m.sender_email}</div>
    <div style="margin-top:8px;">${m.body_text || ""}</div>
    ${!read ? `<button class="subtle-btn" style="margin-top:8px;" onclick="markRead(${m.id})">Mark read</button>` : ""}
  </div>`;
}

async function markRead(id) {
  await fetch(`/dm/${id}/read`, {method:"POST"});
  loadDMInbox();
}

async function loadDMComposePicker() {
  const sel = document.getElementById("dm-to");
  sel.innerHTML = '<option value="">— select member —</option>';
  MEMBERS.filter(m => m.email).forEach(m => {
    const o = document.createElement("option");
    o.value = m.email; o.textContent = m.fullname;
    sel.appendChild(o);
  });
}

document.getElementById("dm-send-btn")?.addEventListener("click", async () => {
  const to = document.getElementById("dm-to").value;
  const subject = document.getElementById("dm-subject").value.trim();
  const body = document.getElementById("dm-body").value.trim();
  const msg = document.getElementById("dm-send-msg");
  if (!to) { msg.textContent = "Select a recipient."; return; }
  if (!body) { msg.textContent = "Message required."; return; }
  const r = await api("POST", "/dm/send", {to_email: to, subject, body_text: body});
  if (r.status === "success" || r.id) {
    msg.textContent = "Sent.";
    document.getElementById("dm-subject").value = "";
    document.getElementById("dm-body").value = "";
  } else { msg.textContent = r.message || "Failed."; }
});

// ── Invitations ───────────────────────────────────────────────────────────────

async function loadInvitations() {
  const invites = await api("GET", "/orchestra/invitations");
  const list = document.getElementById("invitations-list");
  if (!Array.isArray(invites) || !invites.length) {
    list.innerHTML = "<em class='empty-note'>No invitations sent yet.</em>";
    return;
  }
  list.innerHTML = invites.map(i => `
    <div class="card" style="display:flex;justify-content:space-between;align-items:center;">
      <div>
        ${i.fullname ? `<strong>${i.fullname}</strong> — ` : ""}<span>${i.email}</span>
        <div class="hint">Expires ${fmtDate(i.expires_at)}</div>
      </div>
      <span class="tag">${i.role.replace("_", " ")}</span>
    </div>`).join("");
}

document.getElementById("send-invite-btn")?.addEventListener("click", async () => {
  const email = document.getElementById("invite-email").value.trim();
  const fullname = document.getElementById("invite-name").value.trim();
  const msg = document.getElementById("invite-msg");
  if (!email) { msg.textContent = "Email required."; return; }
  const r = await api("POST", "/orchestra/invite", {email, fullname: fullname || null});
  if (r.status === "success") {
    msg.textContent = "Invitation sent.";
    document.getElementById("invite-email").value = "";
    document.getElementById("invite-name").value = "";
    loadInvitations();
  } else { msg.textContent = r.message || "Failed."; }
});

// ── Orchestra accordion helper ────────────────────────────────────────────────

function orchFamily(instrument) {
  const i = (instrument || "").toLowerCase();
  if (/violin|viola|cello|double.?bass|contrabass|harp/.test(i)) return "Strings";
  if (/flute|oboe|clarinet|bassoon|saxophone|piccolo|english.?horn|cor.?anglais/.test(i)) return "Woodwinds";
  if (/french.?horn|\bhorn\b|trumpet|trombone|tuba|cornet|euphonium/.test(i)) return "Brass";
  if (/timpani|percussion|drum|marimba|xylophone|cymbal|glockenspiel|vibraphone/.test(i)) return "Percussion";
  return "Other";
}

const ORCH_FAMILY_ORDER = ["Strings", "Woodwinds", "Brass", "Percussion", "Other"];

function makeOrchAccordion(titleHTML, startOpen = false, listMode = true, extraClass = "") {
  const group = document.createElement("div");
  group.className = "unified-section-group" + (extraClass ? " " + extraClass : "") + (startOpen ? " open" : "");

  const header = document.createElement("div");
  header.className = "unified-section-header";

  const chevron = document.createElement("button");
  chevron.type = "button";
  chevron.className = "section-chevron-btn";
  chevron.textContent = "▶";

  const nameArea = document.createElement("span");
  nameArea.className = "section-name-area";
  nameArea.innerHTML = `<span class="section-name">${titleHTML}</span>`;

  header.appendChild(chevron);
  header.appendChild(nameArea);
  group.appendChild(header);

  const inner = document.createElement("div");
  inner.className = (listMode ? "unified-section-inner--list" : "unified-section-inner") + (startOpen ? "" : " hidden");
  group.appendChild(inner);

  header.addEventListener("click", () => {
    const isOpen = group.classList.toggle("open");
    inner.classList.toggle("hidden", !isOpen);
  });

  return { group, inner };
}

// ── Chair Grid Styles (injected) ──────────────────────────────────────────────

const style = document.createElement("style");
style.textContent = `
  .chair-grid { display: flex; flex-wrap: wrap; gap: 8px; }
  .chair-cell {
    width: 64px; height: 64px;
    border: 2px solid var(--border);
    border-radius: var(--radius);
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    cursor: pointer; font-size: 0.75rem;
    transition: background 0.15s;
  }
  .chair-cell:hover { background: var(--bg-hover); }
  .chair-cell.assigned { background: var(--accent-light, #e8f5e9); border-color: var(--accent, #4caf50); }
  .chair-num { font-weight: 600; font-size: 0.85rem; }
  .chair-name { font-size: 0.65rem; text-align: center; max-width: 60px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tag.preferred { background: var(--accent-light, #e8f5e9); color: var(--accent-dark, #2e7d32); }
`;
document.head.appendChild(style);

// ── Init ──────────────────────────────────────────────────────────────────────

async function init() {
  await ME_READY;

  // Auth guard
  if (!ORG_TYPE || ORG_TYPE !== "orchestra") {
    document.body.innerHTML = "<p style='padding:2rem;'>This page is only accessible to orchestra organisations.</p>";
    return;
  }

  initTabs();
  initMessages();

  // Load welcome name
  const me = await fetch("/me").then(r => r.json()).catch(() => ({}));
  if (me.fullname) document.getElementById("welcome").textContent = `Welcome, ${me.fullname}`;

  await loadSections();
  await Promise.all([loadRehearsals(), loadConcerts(), loadMembers()]);
  loadSubs(null);
  loadInvitations();
}

init();
