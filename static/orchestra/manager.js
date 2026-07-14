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
  const opts = {method, credentials: "include", headers:{"Content-Type":"application/json"}};
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
      // Reset concerts sub-panels so the list is always visible when entering the tab
      if (btn.dataset.tab === "concerts") {
        document.getElementById("pieces-panel")?.classList.add("hidden");
        document.getElementById("seating-panel")?.classList.add("hidden");
        document.getElementById("concerts-list")?.classList.remove("hidden");
      }
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
  const list = document.getElementById("rehearsals-list");
  try {
    const rehearsals = await api("GET", "/orchestra/rehearsals");
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
  } catch (e) {
    console.error(e);
    list.innerHTML = "<em class='empty-note'>Failed to load.</em>";
  }
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
  const section = document.getElementById("absence-requests-section");
  const list = document.getElementById("absence-requests-list");
  try {
    const reqs = await api("GET", `/orchestra/rehearsals/${rehearsalId}/absence-requests`);
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
  } catch (e) {
    console.error(e);
    section.style.display = "";
    list.innerHTML = "<em class='empty-note'>Failed to load.</em>";
  }
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
  const list = document.getElementById("attendance-list");
  let rows;
  try {
    rows = await api("GET", `/orchestra/rehearsals/${rehearsalId}/attendance`);
  } catch (e) {
    console.error(e);
    list.innerHTML = "<em class='empty-note'>Failed to load.</em>";
    return;
  }
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
    if (!byFamily[f][sKey]) byFamily[f][sKey] = { name: m.section_name || "Other", section_id: m.section_id, members: [] };
    byFamily[f][sKey].members.push(m);
  });

  FAMILY_ORDER_KEYS.filter(f => byFamily[f]).forEach(f => {
    const { group: famGroup, inner: famInner } = makeOrchAccordion(
      FAMILY_LABELS[f], true, true, "orch-family-group"
    );
    const sortedSections = Object.values(byFamily[f]).sort((a, b) => {
      const sA = SECTIONS.find(s => s.id === a.section_id);
      const sB = SECTIONS.find(s => s.id === b.section_id);
      return sectionScorePos(sA?.instrument || "", a.name) - sectionScorePos(sB?.instrument || "", b.name);
    });

    sortedSections.forEach(secGroup => {
      const { group: secGrp, inner: secInner } = makeOrchAccordion(
        `${secGroup.name} <span class="section-count">(${secGroup.members.length})</span>`,
        true, true
      );

      secGroup.members.forEach(m => {
        const S = "width:30px;height:30px;padding:0;border-radius:6px;border:2px solid;font-size:15px;font-weight:700;cursor:pointer;flex-shrink:0;";
        const attOn  = m.status === "attended" ? "background:#22c55e;border-color:#22c55e;color:#fff;" : "background:transparent;border-color:#22c55e;color:#22c55e;";
        const excOn  = m.status === "excused"  ? "background:#eab308;border-color:#eab308;color:#1f1b15;" : "background:transparent;border-color:#eab308;color:#eab308;";
        const absOn  = m.status === "absent"   ? "background:#ef4444;border-color:#ef4444;color:#fff;" : "background:transparent;border-color:#ef4444;color:#ef4444;";
        const row = document.createElement("div");
        row.className = "card";
        row.style.cssText = "display:flex;align-items:center;gap:10px;padding:8px 12px;margin-bottom:6px;";
        row.innerHTML = `
          <div style="flex:1;min-width:0;">
            <div style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${m.fullname}</div>
            ${m.instrument ? `<div class="hint" style="font-size:0.8em;white-space:nowrap;">${m.instrument}</div>` : ""}
          </div>
          <div style="display:flex;gap:5px;flex-shrink:0;">
            <button style="${S}${attOn}" title="Attended"
              onclick="setAttendance(${rehearsalId}, ${m.member_id}, 'attended')">✓</button>
            <button style="${S}${excOn}" title="Excused"
              onclick="setAttendance(${rehearsalId}, ${m.member_id}, 'excused')">~</button>
            <button style="${S}${absOn}" title="Absent"
              onclick="adminMarkAbsent(${rehearsalId}, ${m.member_id})">✗</button>
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

async function adminMarkAbsent(rehearsalId, memberId) {
  const r = await api("POST", "/orchestra/admin-mark-absent", {
    rehearsal_id: rehearsalId,
    member_id: memberId,
    reason: "Admin marked absent",
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
  let reqs;
  try {
    reqs = await api("GET", `/orchestra/sub-requests/${rehearsalId}`);
  } catch (e) {
    console.error(e);
    wrap.innerHTML = "<em class='empty-note'>Failed to load sub requests.</em>";
    return;
  }
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
function populateTimeSelect(el) {
  const current = el.value;
  el.innerHTML = '<option value="">-- time --</option>';
  for (let h = 6; h <= 23; h++) {
    for (let m = 0; m < 60; m += 30) {
      const hhmm = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
      const suffix = h >= 12 ? "PM" : "AM";
      const h12 = ((h + 11) % 12) + 1;
      const opt = document.createElement("option");
      opt.value = hhmm;
      opt.textContent = `${h12}:${String(m).padStart(2, "0")} ${suffix}`;
      el.appendChild(opt);
    }
  }
  if (current) el.value = current;
}

document.getElementById("open-schedule-rehearsal-btn")?.addEventListener("click", () => {
  document.getElementById("reh-save-msg").textContent = "";
  document.querySelector("input[name='reh-mode'][value='single']").checked = true;
  document.getElementById("reh-single-fields").classList.remove("hidden");
  document.getElementById("reh-bulk-fields").classList.add("hidden");
  document.getElementById("reh-date").value = "";
  ["bulk-start-date", "bulk-end-date"].forEach(id => document.getElementById(id).value = "");
  document.querySelectorAll("#bulk-days input").forEach(cb => cb.checked = false);
  document.getElementById("bulk-preview").textContent = "";
  populateTimeSelect(document.getElementById("reh-start"));
  populateTimeSelect(document.getElementById("reh-end"));
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

document.querySelectorAll("input[name='reh-mode']").forEach(radio => {
  radio.addEventListener("change", () => {
    const isRange = radio.value === "range";
    document.getElementById("reh-single-fields").classList.toggle("hidden", isRange);
    document.getElementById("reh-bulk-fields").classList.toggle("hidden", !isRange);
  });
});

const ORCH_DAY_JS = { monday: 1, tuesday: 2, wednesday: 3, thursday: 4, friday: 5, saturday: 6, sunday: 0 };

function updateOrchBulkPreview() {
  const startDate = document.getElementById("bulk-start-date").value;
  const endDate = document.getElementById("bulk-end-date").value;
  const days = [...document.querySelectorAll("#bulk-days input:checked")].map(cb => cb.value);
  const preview = document.getElementById("bulk-preview");

  if (!startDate || !endDate || !days.length) { preview.textContent = ""; return; }

  const sd = new Date(startDate + "T00:00:00");
  const ed = new Date(endDate + "T00:00:00");
  if (ed < sd) { preview.textContent = "End date must be after start date."; return; }

  const dayNums = days.map(d => ORCH_DAY_JS[d]);
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

["bulk-start-date", "bulk-end-date"].forEach(id =>
  document.getElementById(id)?.addEventListener("change", updateOrchBulkPreview));
document.querySelectorAll("#bulk-days input").forEach(cb =>
  cb.addEventListener("change", updateOrchBulkPreview));

document.getElementById("reh-type")?.addEventListener("change", function() {
  const wrap = document.getElementById("reh-sections-wrap");
  wrap?.classList.toggle("hidden", this.value !== "sectional");
});

async function createSingleRehearsal() {
  const date = document.getElementById("reh-date").value;
  const start = document.getElementById("reh-start").value;
  const msg = document.getElementById("reh-save-msg");
  if (!date || !start) { msg.textContent = "Date and start time are required."; return; }
  const end = document.getElementById("reh-end").value;
  const type = document.getElementById("reh-type").value;
  const sectionIds = type === "sectional"
    ? [...document.querySelectorAll("#reh-sections-checks input:checked")].map(el => parseInt(el.value))
    : [];
  const r = await api("POST", "/orchestra/rehearsals", {
    start_time: `${date}T${start}`,
    end_time: end ? `${date}T${end}` : null,
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
}

async function bulkScheduleOrchestra() {
  const msg = document.getElementById("reh-save-msg");
  msg.textContent = "";

  const start_date = document.getElementById("bulk-start-date").value;
  const end_date = document.getElementById("bulk-end-date").value;
  const days = [...document.querySelectorAll("#bulk-days input:checked")].map(cb => cb.value);
  const start_time = document.getElementById("reh-start").value;
  const end_time = document.getElementById("reh-end").value;
  const type = document.getElementById("reh-type").value;
  const sectionIds = type === "sectional"
    ? [...document.querySelectorAll("#reh-sections-checks input:checked")].map(el => parseInt(el.value))
    : [];

  if (!start_date || !end_date) { msg.textContent = "Start and end dates are required."; return; }
  if (!days.length) { msg.textContent = "Select at least one day of the week."; return; }
  if (!start_time) { msg.textContent = "Start time is required."; return; }

  const btn = document.getElementById("save-rehearsal-btn");
  btn.disabled = true;
  btn.textContent = "Scheduling...";

  try {
    const r = await api("POST", "/orchestra/rehearsals/bulk", {
      start_date, end_date, days, start_time, end_time: end_time || null,
      concert_id: parseInt(document.getElementById("reh-concert").value) || null,
      attendance_type: type,
      section_ids: sectionIds,
      location: document.getElementById("reh-location").value,
      notes: document.getElementById("reh-notes").value,
    });
    if (r.status === "success") {
      msg.className = "msg success-msg";
      msg.textContent = `Done! ${r.created} rehearsal${r.created !== 1 ? "s" : ""} scheduled.`;
      setTimeout(() => {
        closeModal("schedule-rehearsal-modal");
        loadRehearsals();
      }, 800);
    } else {
      msg.className = "msg";
      msg.textContent = r.message || "Failed to schedule.";
    }
  } catch (e) {
    msg.className = "msg";
    msg.textContent = "Server error.";
  } finally {
    btn.disabled = false;
    btn.textContent = "Schedule Rehearsal";
  }
}

document.getElementById("save-rehearsal-btn")?.addEventListener("click", () => {
  const mode = document.querySelector("input[name='reh-mode']:checked")?.value;
  if (mode === "range") bulkScheduleOrchestra(); else createSingleRehearsal();
});

// ── Concerts ──────────────────────────────────────────────────────────────────

async function loadConcerts() {
  try {
    CONCERTS = await api("GET", "/orchestra/concerts");
    if (!Array.isArray(CONCERTS)) CONCERTS = [];
    renderConcerts();
  } catch (e) {
    console.error(e);
    CONCERTS = [];
    document.getElementById("concerts-list").innerHTML = "<em class='empty-note'>Failed to load.</em>";
  }
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
  renderConcerts();
});

document.getElementById("back-to-pieces-btn")?.addEventListener("click", () => {
  document.getElementById("seating-panel").classList.add("hidden");
  document.getElementById("pieces-panel").classList.remove("hidden");
  if (CURRENT_CONCERT_ID) loadPieces(CURRENT_CONCERT_ID);
});

async function loadPieces(concertId) {
  const list = document.getElementById("pieces-list");
  try {
    PIECES = await api("GET", `/orchestra/concerts/${concertId}/pieces`);
  } catch (e) {
    console.error(e);
    PIECES = [];
    list.innerHTML = "<em class='empty-note'>Failed to load.</em>";
    return;
  }
  if (!Array.isArray(PIECES)) PIECES = [];
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
  const grid = document.getElementById("seating-grid");
  let seats;
  try {
    seats = await api("GET", `/orchestra/pieces/${pieceId}/seats`);
  } catch (e) {
    console.error(e);
    grid.innerHTML = "<em class='empty-note'>Failed to load.</em>";
    return;
  }
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
    byFamily[fam].sort((a, b) => sectionScorePos(a.instrument, a.name) - sectionScorePos(b.instrument, b.name));
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

      // Chair count edit button
      {
        const secHdr = secGrp.querySelector(".unified-section-header");
        const editBtn = document.createElement("button");
        editBtn.type = "button";
        editBtn.className = "chair-edit-btn";
        editBtn.title = "Edit chair count";
        editBtn.innerHTML = "&#9998;";
        editBtn.addEventListener("click", e => {
          e.stopPropagation();
          const existing = secHdr.querySelector(".chair-count-input-wrap");
          if (existing) { existing.remove(); return; }
          const wrap = document.createElement("span");
          wrap.className = "chair-count-input-wrap";
          const inp = document.createElement("input");
          inp.type = "number";
          inp.min = "1";
          inp.max = "200";
          inp.value = chairCount;
          inp.className = "chair-count-input";
          const okBtn = document.createElement("button");
          okBtn.type = "button";
          okBtn.textContent = "Save";
          okBtn.className = "btn-xs";
          okBtn.addEventListener("click", async ev => {
            ev.stopPropagation();
            const val = parseInt(inp.value, 10);
            if (!val || val < 1) return;
            try {
              const r = await api("PATCH", "/orchestra/sections/" + sec.id + "/chair-count", { chair_count: val });
              const s = SECTIONS.find(x => x.id === sec.id);
              if (s) s.chair_count = r.chair_count;
              loadSeating(CURRENT_PIECE_ID);
            } catch (_) { alert("Failed to update chair count"); }
          });
          wrap.appendChild(inp);
          wrap.appendChild(okBtn);
          secHdr.appendChild(wrap);
          inp.focus();
          inp.select();
        });
        secHdr.appendChild(editBtn);
      }

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
          const name = seat?.member_name || "";
          const assigned = !!name;

          const cell = document.createElement("div");
          cell.className = "chair-cell" + (assigned ? " assigned" : "");
          cell.title = `Chair ${chair}${part > 1 ? ` / ${partLabels[part-1]}` : ""}${assigned ? " — " + name : ""}`;
          cell.innerHTML = `<span class="chair-num">${chair}</span>${assigned ? `<span class="chair-name">${name}</span>` : ""}`;
          cell.addEventListener("click", () => {
            if (assigned) openPlayerInfo(seat, sec, chair, part);
            else openAssignSeat(sec.id, chair, part);
          });
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
  const sec = SECTIONS.find(s => s.id === sectionId);
  const secInstrument = (sec?.instrument || "").toLowerCase().trim();
  SEAT_CONTEXT = {piece_id: CURRENT_PIECE_ID, section_id: sectionId,
                  chair_number: chairNumber, part_number: partNumber,
                  section_instrument: secInstrument};
  document.getElementById("assign-seat-title").textContent =
    `Assign Seat — Chair ${chairNumber}${partNumber > 1 ? ` / Part ${partNumber}` : ""}`;
  document.getElementById("seat-save-msg").textContent = "";
  document.getElementById("seat-no-account-fields").classList.add("hidden");
  document.getElementById("seat-ext-name").value = "";
  document.getElementById("seat-ext-email").value = "";
  document.getElementById("doubling-wrap").classList.add("hidden");
  document.getElementById("doubling-auto-row").classList.add("hidden");
  document.getElementById("doubling-manual-row").classList.add("hidden");

  // Populate member select — section members first, then anyone who already doubles this instrument
  const sel = document.getElementById("seat-member-select");
  sel.innerHTML = '<option value="">— Unassigned —</option>';
  const sectionMembers = MEMBERS.filter(m => m.section_id === sectionId);
  sectionMembers.forEach(m => {
    const o = document.createElement("option");
    o.value = m.id;
    o.textContent = m.fullname + (m.part_label ? ` (${m.part_label})` : "");
    sel.appendChild(o);
  });
  const sectionIds = new Set(sectionMembers.map(m => m.id));
  const doublers = MEMBERS.filter(m => {
    if (sectionIds.has(m.id)) return false;
    return (m.doublings || "").toLowerCase().split(",").map(d => d.trim()).includes(secInstrument);
  });
  if (doublers.length) {
    const grp = document.createElement("optgroup");
    grp.label = "Doublers";
    doublers.forEach(m => {
      const o = document.createElement("option");
      o.value = m.id;
      o.textContent = m.fullname + ` (doubles)`;
      grp.appendChild(o);
    });
    sel.appendChild(grp);
  }
  openModal("assign-seat-modal");
}

document.getElementById("seat-member-select")?.addEventListener("change", function() {
  const memberId = parseInt(this.value);
  const wrap = document.getElementById("doubling-wrap");
  if (!memberId || !SEAT_CONTEXT?.section_instrument) { wrap.classList.add("hidden"); return; }

  const member = MEMBERS.find(m => m.id === memberId);
  const secInstrument = SEAT_CONTEXT.section_instrument;
  const memberInstrument = (member?.instrument || "").toLowerCase().trim();
  const existingDoublings = (member?.doublings || "").toLowerCase().split(",").map(d => d.trim()).filter(Boolean);

  // Already recorded — nothing to offer
  if (existingDoublings.includes(secInstrument)) { wrap.classList.add("hidden"); return; }

  const autoRow = document.getElementById("doubling-auto-row");
  const manualRow = document.getElementById("doubling-manual-row");
  const check = document.getElementById("doubling-check");
  check.checked = false;

  const isKnownPair = (DOUBLING_PAIRS[secInstrument] || []).includes(memberInstrument);
  wrap.classList.remove("hidden");

  if (isKnownPair) {
    document.getElementById("doubling-auto-label").textContent =
      `Add "${secInstrument}" to ${member.fullname.split(" ")[0]}'s doublings`;
    autoRow.classList.remove("hidden");
    manualRow.classList.add("hidden");
  } else {
    autoRow.classList.add("hidden");
    const candidates = (DOUBLING_PAIRS[secInstrument] || [])
      .filter(i => i !== memberInstrument && !existingDoublings.includes(i));
    if (!candidates.length) { wrap.classList.add("hidden"); return; }
    const instrSel = document.getElementById("doubling-instr-select");
    instrSel.innerHTML = '<option value="">— none —</option>';
    candidates.forEach(i => {
      const o = document.createElement("option");
      o.value = i; o.textContent = i.charAt(0).toUpperCase() + i.slice(1);
      instrSel.appendChild(o);
    });
    manualRow.classList.remove("hidden");
  }
});

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
  if (r.status !== "success") { msg.textContent = r.message || "Failed."; return; }

  // Persist doubling if requested
  if (memberId) {
    const secInstrument = SEAT_CONTEXT.section_instrument;
    const autoChecked = document.getElementById("doubling-check")?.checked && secInstrument;
    const manualInstr = document.getElementById("doubling-instr-select")?.value || "";
    const doublingToAdd = autoChecked ? secInstrument : manualInstr;
    if (doublingToAdd) {
      const member = MEMBERS.find(m => m.id === memberId);
      const existing = (member?.doublings || "").split(",").map(d => d.trim()).filter(Boolean);
      if (!existing.includes(doublingToAdd)) {
        existing.push(doublingToAdd);
        const updated = existing.join(",");
        await api("PATCH", `/orchestra/members/${memberId}`, {doublings: updated});
        if (member) member.doublings = updated;
      }
    }
  }

  closeModal("assign-seat-modal");
  loadSeating(SEAT_CONTEXT.piece_id);
});

// ── Player info (filled seat click) ──────────────────────────────────────────

let PLAYER_INFO_CONTEXT = null;

function openPlayerInfo(seat, sec, chairNumber, partNumber) {
  const PART_LABELS = ["1st Part", "2nd Part", "3rd Part", "4th Part"];
  const partSuffix = partNumber > 1 ? ` / ${PART_LABELS[partNumber - 1]}` : "";
  PLAYER_INFO_CONTEXT = {seat, sec, chairNumber, partNumber};

  document.getElementById("player-info-chair").textContent =
    `${sec.name} — Chair ${chairNumber}${partSuffix}`;
  document.getElementById("player-info-name").textContent = seat.member_name || "—";
  document.getElementById("player-info-msg").textContent = "";

  const member = seat.member_id ? MEMBERS.find(m => m.id === seat.member_id) : null;

  const instrParts = [member?.instrument, member?.part_label].filter(Boolean);
  document.getElementById("player-info-instrument").textContent = instrParts.join(" · ");

  const email = member?.email || seat.external_email || "";
  const emailEl = document.getElementById("player-info-email");
  emailEl.textContent = email ? `✉  ${email}` : "";
  emailEl.hidden = !email;

  const doublings = (member?.doublings || "").split(",").map(d => d.trim()).filter(Boolean);
  const doublingsEl = document.getElementById("player-info-doublings");
  doublingsEl.textContent = doublings.length ? `Doubles: ${doublings.join(", ")}` : "";
  doublingsEl.hidden = !doublings.length;

  openModal("player-info-modal");
}

document.getElementById("player-info-edit-btn")?.addEventListener("click", () => {
  if (!PLAYER_INFO_CONTEXT) return;
  const {sec, chairNumber, partNumber} = PLAYER_INFO_CONTEXT;
  closeModal("player-info-modal");
  openAssignSeat(sec.id, chairNumber, partNumber);
});

document.getElementById("player-info-clear-btn")?.addEventListener("click", async () => {
  if (!PLAYER_INFO_CONTEXT) return;
  const {sec, chairNumber, partNumber} = PLAYER_INFO_CONTEXT;
  const msg = document.getElementById("player-info-msg");
  const r = await api("POST", `/orchestra/pieces/${CURRENT_PIECE_ID}/seats`, {
    section_id: sec.id,
    chair_number: chairNumber,
    part_number: partNumber,
  });
  if (r.status === "success") {
    closeModal("player-info-modal");
    loadSeating(CURRENT_PIECE_ID);
  } else { msg.textContent = r.message || "Failed."; }
});

// ── Members ───────────────────────────────────────────────────────────────────

async function loadMembers() {
  try {
    MEMBERS = await api("GET", "/orchestra/members");
    if (!Array.isArray(MEMBERS)) MEMBERS = [];
    renderMembers();
  } catch (e) {
    console.error(e);
    MEMBERS = [];
    document.getElementById("members-list").innerHTML = "<em class='empty-note'>Failed to load.</em>";
  }
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
    if (!byFamily[f][sKey]) byFamily[f][sKey] = { name: m.section_name || "Other", section_id: m.section_id, members: [] };
    byFamily[f][sKey].members.push(m);
  });

  FAMILY_ORDER_KEYS.filter(f => byFamily[f]).forEach(f => {
    const { group: famGroup, inner: famInner } = makeOrchAccordion(
      FAMILY_LABELS[f], true, true, "orch-family-group"
    );
    const sortedSections = Object.values(byFamily[f]).sort((a, b) => {
      const sA = SECTIONS.find(s => s.id === a.section_id);
      const sB = SECTIONS.find(s => s.id === b.section_id);
      return sectionScorePos(sA?.instrument || "", a.name) - sectionScorePos(sB?.instrument || "", b.name);
    });
    // Update count label
    const total = sortedSections.reduce((s, g) => s + g.members.length, 0);
    famGroup.querySelector(".section-name").insertAdjacentHTML("afterend",
      `<span class="section-count" style="margin-left:6px;">(${total})</span>`);

    sortedSections.forEach(secGroup => {
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
  const list = document.getElementById("sub-roster-list");
  let subs;
  try {
    subs = await api("GET", url);
  } catch (e) {
    console.error(e);
    list.innerHTML = "<em class='empty-note'>Failed to load.</em>";
    return;
  }
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

let dmInbox = [];
let dmSent = [];
let dmContacts = [];
let dmSelectedRecipients = new Set();
let dmView = "inbox";

function initMessages() {
  document.querySelectorAll(".dm-view-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      dmView = btn.dataset.dmView;
      document.querySelectorAll(".dm-view-btn").forEach(b => b.classList.toggle("active", b === btn));
      renderDmView();
    });
  });
  document.getElementById("dm-scope")?.addEventListener("change", e => {
    document.getElementById("dm-recipient-row")?.classList.toggle("hidden", e.target.value !== "direct");
  });
  document.getElementById("dm-send-btn")?.addEventListener("click", sendDm);
  loadDmTab();
}

async function loadDmTab() {
  await Promise.all([loadDmMessages(), loadDmContactList()]);
  renderDmView();
  renderDmContactPicker();
  refreshDmBadge();
}

async function loadDmMessages() {
  try {
    const res = await fetch("/dm", { credentials: "include" });
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
    const res = await fetch("/dm/contacts", { credentials: "include" });
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
  if (!msgs.length) {
    list.innerHTML = `<em class="empty-note">${dmView === "inbox" ? "All caught up!" : "No messages yet."}</em>`;
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
  await fetch(`/dm/${msgId}/read`, { method: "POST", credentials: "include" });
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
    const res = await fetch("/dm/unread", { credentials: "include" });
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
    const res = await fetch("/dm", {
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

// ── Invitations ───────────────────────────────────────────────────────────────

async function loadInvitations() {
  const list = document.getElementById("invitations-list");
  let invites;
  try {
    invites = await api("GET", "/orchestra/invitations");
  } catch (e) {
    console.error(e);
    list.innerHTML = "<em class='empty-note'>Failed to load.</em>";
    return;
  }
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

// Returns a numeric position matching standard full-score order within each family.
function sectionScorePos(instrument, name) {
  const i = (instrument || "").toLowerCase();
  const n = (name || "").toLowerCase();
  // Strings
  if (/violin/.test(i) || /violin/.test(n)) {
    if (/\b(1|i|first)\b/.test(n)) return 10;
    if (/\b(2|ii|second)\b/.test(n)) return 11;
    return 12;
  }
  if (/viola/.test(i)) return 13;
  if (/\bcello/.test(i)) return 14;
  if (/double.?bass|contrabass/.test(i)) return 15;
  if (/harp/.test(i)) return 16;
  // Woodwinds — piccolo before flute, oboe before English horn, Eb before standard before bass clarinet, bassoon before contrabassoon
  if (/piccolo/.test(i)) return 20;
  if (/\bflute\b/.test(i)) return 21;
  if (/alto.flute/.test(i)) return 22;
  if (/\boboe\b/.test(i)) return 23;
  if (/english.?horn|cor.?anglais/.test(i)) return 24;
  if (/clarinet/.test(i) && /\beb\b|e[- ]flat/.test(i)) return 25;
  if (/clarinet/.test(i) && /bass/.test(i)) return 27;
  if (/clarinet/.test(i)) return 26;
  if (/contrabassoon/.test(i)) return 29;
  if (/bassoon/.test(i)) return 28;
  if (/saxophone|sax/.test(i)) return 30;
  // Brass — horn, trumpet, trombone, tuba
  if (/french.?horn|\bhorn\b/.test(i)) return 40;
  if (/trumpet/.test(i)) return 41;
  if (/cornet/.test(i)) return 42;
  if (/bass.trombone/.test(i)) return 44;
  if (/trombone/.test(i)) return 43;
  if (/euphonium/.test(i)) return 45;
  if (/tuba/.test(i)) return 46;
  // Percussion — timpani always first
  if (/timpani/.test(i)) return 50;
  return 51;
}

const ORCH_FAMILY_ORDER = ["Strings", "Woodwinds", "Brass", "Percussion", "Other"];

// DOUBLING_PAIRS is defined in public.js (shared with opera/admin.js).

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
  .chair-cell:hover { background: var(--hover); }
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

  await loadSections();
  await Promise.all([loadRehearsals(), loadConcerts(), loadMembers()]);
  loadSubs(null);
  loadInvitations();
}

init();
