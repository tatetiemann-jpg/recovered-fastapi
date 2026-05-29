// ======================================================
// ENSEMBLE MEMBER DASHBOARD
// ======================================================

let allEnsembleRehearsals = [];
let myEnsembleAbsences = new Set();

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
    const absent = myEnsembleAbsences.has(r.id);
    const card = document.createElement("div");
    card.className = "rehearsal-card";
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
                ${absent
                    ? `<button class="cancel-lesson-btn undo-absent-btn" data-id="${r.id}">I can attend</button>`
                    : `<button class="cancel-lesson-btn cant-make-btn" data-id="${r.id}">I can't make it</button>`
                }
            </div>
        </div>
    `;
    if (absent) {
        card.querySelector(".undo-absent-btn").addEventListener("click", () => undoEnsembleAbsent(r.id));
    } else {
        card.querySelector(".cant-make-btn").addEventListener("click", () => markEnsembleAbsent(r.id));
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
    const todayStr = now.toLocaleDateString("en-CA");
    const endOfWeek = new Date(now);
    endOfWeek.setDate(now.getDate() + (6 - now.getDay()));
    const endOfWeekStr = endOfWeek.toLocaleDateString("en-CA");
    const endOfMonthStr = new Date(now.getFullYear(), now.getMonth() + 1, 0).toLocaleDateString("en-CA");
    const endOfYearStr = `${now.getFullYear()}-12-31`;

    const buckets = { today: [], week: [], month: [], year: [] };
    rehearsals.forEach(r => {
        const d = r.date;
        if (d === todayStr) buckets.today.push(r);
        else if (d > todayStr && d <= endOfWeekStr) buckets.week.push(r);
        else if (d > endOfWeekStr && d <= endOfMonthStr) buckets.month.push(r);
        else if (d > endOfMonthStr && d <= endOfYearStr) buckets.year.push(r);
    });

    const todayHeader = document.createElement("div");
    todayHeader.className = "timeline-today-header";
    todayHeader.textContent = "Today";
    container.appendChild(todayHeader);

    if (buckets.today.length) {
        buckets.today.forEach(r => container.appendChild(buildEnsembleRehearsalCard(r)));
    } else {
        const empty = document.createElement("p");
        empty.className = "hint";
        empty.textContent = "No rehearsals today.";
        container.appendChild(empty);
    }

    [
        { key: "week", label: "This Week" },
        { key: "month", label: "This Month" },
        { key: "year", label: "This Year" },
    ].forEach(({ key, label }) => {
        const group = buckets[key];
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

async function markEnsembleAbsent(rehearsalId) {
    if (!confirm("Mark yourself absent for this rehearsal? The admin will be notified.")) return;
    try {
        await fetch(`${API}/ensemble/absence`, {
            method: "POST", credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ rehearsal_id: rehearsalId }),
        });
        myEnsembleAbsences.add(rehearsalId);
        renderRehearsalTimeline(document.getElementById("rehearsal-timeline"), allEnsembleRehearsals);
    } catch (e) { console.error(e); }
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

    // Tab wiring (single tab but keeping pattern consistent)
    document.querySelectorAll(".tab-btn").forEach(btn =>
        btn.addEventListener("click", () => {
            document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
            btn.classList.add("active");
            document.querySelector(`[data-tab-panel="${btn.dataset.tab}"]`).classList.add("active");
        }));
    document.querySelector(".tab-btn")?.click();

    await loadEnsembleRehearsalTimeline();

    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) loadEnsembleRehearsalTimeline();
    });
});
