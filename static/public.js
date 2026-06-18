// ===============================
// GLOBAL CONTEXT
// ===============================
const API = "https://countrpnt.com";

// Orchestral instruments — used for signup dropdown and Add Section modal
// Includes specific doublings so members can identify their exact instrument
const ORCHESTRA_INSTRUMENTS = [
    { group: "Strings",          items: ["Violin", "Viola", "Cello", "Double Bass"] },
    { group: "Woodwinds",        items: ["Flute", "Piccolo", "Oboe", "English Horn", "Clarinet", "Bass Clarinet", "Bassoon", "Contrabassoon"] },
    { group: "Brass",            items: ["French Horn", "Trumpet", "Bass Trumpet", "Trombone", "Bass Trombone", "Tuba"] },
    { group: "Percussion",       items: ["Timpani", "Snare Drum", "Bass Drum", "Cymbals", "Marimba", "Xylophone", "Vibraphone", "Glockenspiel", "Chimes", "Tambourine"] },
    { group: "Keyboard & Other", items: ["Harp", "Piano", "Celesta", "Organ"] },
];

// Default seating sections for new orchestras — doublings fold into their parent section
const ORCHESTRA_DEFAULT_SECTIONS = [
    { name: "Violin I",    instrument: "violin",      chair_count: 16 },
    { name: "Violin II",   instrument: "violin",      chair_count: 14 },
    { name: "Viola",       instrument: "viola",       chair_count: 12 },
    { name: "Cello",       instrument: "cello",       chair_count: 10 },
    { name: "Double Bass", instrument: "double bass", chair_count: 8  },
    { name: "Flute",       instrument: "flute",       chair_count: 3  },
    { name: "Oboe",        instrument: "oboe",        chair_count: 3  },
    { name: "Clarinet",    instrument: "clarinet",    chair_count: 3  },
    { name: "Bassoon",     instrument: "bassoon",     chair_count: 3  },
    { name: "French Horn", instrument: "french horn", chair_count: 6  },
    { name: "Trumpet",     instrument: "trumpet",     chair_count: 4  },
    { name: "Trombone",    instrument: "trombone",    chair_count: 3  },
    { name: "Tuba",        instrument: "tuba",        chair_count: 1  },
    { name: "Percussion",  instrument: "percussion",  chair_count: 3  },
    { name: "Harp",        instrument: "harp",        chair_count: 2  },
    { name: "Piano",       instrument: "piano",       chair_count: 1  },
];

// Maps specific doublings to their parent section instrument key
// Used to show doublers in the correct section during seat assignment
const INSTRUMENT_FAMILY = {
    "piccolo":      "flute",
    "english horn": "oboe",
    "bass clarinet":"clarinet",
    "contrabassoon":"bassoon",
    "bass trumpet": "trumpet",
    "bass trombone":"trombone",
    "snare drum":   "percussion",
    "bass drum":    "percussion",
    "cymbals":      "percussion",
    "marimba":      "percussion",
    "xylophone":    "percussion",
    "vibraphone":   "percussion",
    "glockenspiel": "percussion",
    "chimes":       "percussion",
    "tambourine":   "percussion",
    "timpani":      "percussion",
    "celesta":      "piano",
    "organ":        "piano",
};

function populateInstrumentSelect(selectId, otherRowId) {
    const select = document.getElementById(selectId);
    if (!select) return;
    if (select.dataset.populated) return;
    select.dataset.populated = "1";

    select.innerHTML = '<option value="" disabled selected>Select instrument…</option>';
    ORCHESTRA_INSTRUMENTS.forEach(({ group, items }) => {
        const grp = document.createElement("optgroup");
        grp.label = group;
        items.forEach(name => {
            const opt = document.createElement("option");
            opt.value = name.toLowerCase();
            opt.textContent = name;
            grp.appendChild(opt);
        });
        select.appendChild(grp);
    });
    const otherOpt = document.createElement("option");
    otherOpt.value = "other";
    otherOpt.textContent = "Other…";
    select.appendChild(otherOpt);

    if (otherRowId) {
        select.addEventListener("change", () => {
            const row = document.getElementById(otherRowId);
            if (row) row.classList.toggle("hidden", select.value !== "other");
        });
    }
}

function getInstrumentValue(selectId, otherInputId) {
    const select = document.getElementById(selectId);
    if (!select) return "";
    if (select.value === "other") {
        return (document.getElementById(otherInputId)?.value || "").trim().toLowerCase();
    }
    return select.value;
}
const params = new URLSearchParams(window.location.search);
const path = window.location.pathname;

// -------------------------------
// ORG CONTEXT (single authority)
// -------------------------------
function getOrg() {
    return localStorage.getItem("org") || "default";
}

(function initOrg() {
    let org = localStorage.getItem("org");
    if (!org) {
        org = params.get("org") || "default";
        localStorage.setItem("org", org);
    }
})();

// ===============================
// LOGIN PAGE UI
// ===============================
document.addEventListener("DOMContentLoaded", () => {
    const title = document.getElementById("loginTitle");
    if (title) {
        title.textContent = "Log into CountrPnt";
    }
});

// ===============================
// LOGIN
// ===============================
async function login() {
    const username = document.getElementById("username")?.value.trim().toLowerCase();
    const password = document.getElementById("password")?.value.trim();
    const msg = document.getElementById("msg");

    if (msg) msg.textContent = "";

    if (!username || !password) {
        if (msg) msg.textContent = "Please enter username and password.";
        return;
    }

    try {
        // Login can be slow under load (bcrypt + mobile networks).
        // Retry once on network failure before giving up.
        let res;
        try {
            res = await fetch(`${API}/login`, {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username, password, org: getOrg() })
            });
        } catch (firstErr) {
            console.warn("Login first attempt failed, retrying:", firstErr);
            await new Promise(r => setTimeout(r, 600));
            res = await fetch(`${API}/login`, {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username, password, org: getOrg() })
            });
        }

        const data = await res.json();

        if (!data.success) {
            if (msg) msg.textContent = data.message || "Invalid login.";
            return;
        }

        // Server sets an HttpOnly session cookie. Redirect based on org_type then role.
        if (data.org_type === "choir") {
            if (data.role === "admin") location.href = "/choir/admin";
            else if (data.role === "ensemble_member") location.href = "/ensemble/member";
            else location.href = "/choir/member";
        } else if (data.org_type === "orchestra") {
            if (["admin", "head_admin", "orchestra_admin"].includes(data.role)) location.href = "/orchestra/manager";
            else location.href = "/orchestra-member";
        } else if (["admin", "head_admin", "system_admin", "orchestra_admin"].includes(data.role)) {
            location.href = "/admin";
        } else if (data.role === "teacher") {
            location.href = "/teacher";
        } else if (data.role === "studio_teacher") {
            location.href = "/studio-teacher";
        } else if (data.role === "studio_member") {
            location.href = "/student";
        } else if (data.role === "student") {
            location.href = "/student";
        } else if (data.role === "orchestra_member") {
            location.href = "/orchestra-member";
        }

    } catch (err) {
        console.error(err);
        if (msg) msg.textContent = "Server error. Try again.";
    }
}

// ===============================
// SIGNUP
// ===============================
async function signup() {
    const msg = document.getElementById("msg");
    msg.textContent = "";
    msg.classList.remove("error-msg", "success-msg");

    const role = document.getElementById("role")?.value;
    const fullname = document.getElementById("fullname")?.value.trim();
    const email = document.getElementById("email")?.value.trim();
    const username = document.getElementById("username")?.value.trim().toLowerCase();
    const password = document.getElementById("password")?.value;
    const passwordConfirm = document.getElementById("password-confirm")?.value;
    const voiceType = document.getElementById("voice_type")?.value || null;
    const instrument = getInstrumentValue("instrument", "instrument-other") || null;
    const specialty = document.getElementById("specialty")?.value.trim() || null;
    const code = document.getElementById("code")?.value.trim() || null;

    if (!role) {
        msg.textContent = "Please select your role.";
        msg.classList.add("error-msg");
        return;
    }
    if (!fullname || !email || !username || !password) {
        msg.textContent = "All required fields must be filled in.";
        msg.classList.add("error-msg");
        return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        msg.textContent = "Please enter a valid email address.";
        msg.classList.add("error-msg");
        return;
    }
    if (password.length < 8) {
        msg.textContent = "Password must be at least 8 characters long.";
        msg.classList.add("error-msg");
        return;
    }
    if (password !== passwordConfirm) {
        msg.textContent = "Passwords do not match.";
        msg.classList.add("error-msg");
        return;
    }
    if (role === "student" && !voiceType) {
        msg.textContent = "Please select your voice type.";
        msg.classList.add("error-msg");
        return;
    }
    if (role === "orchestra_member" && !instrument) {
        msg.textContent = "Please enter your instrument.";
        msg.classList.add("error-msg");
        return;
    }
    // Org selection — read from select (old flow) or pre-selected slug (new wizard flow)
    const orgSlugFromSelect = role === "orchestra_member"
        ? (document.getElementById("org-orch")?.value || "")
        : (document.getElementById("org")?.value || "");
    const orgSlug = orgSlugFromSelect || (window.selectedOrgSlug || "");
    if ((role === "student" || role === "orchestra_member") && !orgSlug) {
        msg.textContent = "Please select your organization.";
        msg.classList.add("error-msg");
        return;
    }
    if ((role === "teacher" || role === "admin") && !code) {
        msg.textContent = "Access code is required.";
        msg.classList.add("error-msg");
        return;
    }

    // Theme — read from the picker if it exists on the page
    const themeBox = document.getElementById("signup-theme-picker");
    const theme = themeBox && typeof getSelectedThemeFromPicker === "function"
        ? getSelectedThemeFromPicker(themeBox)
        : "queen-of-the-night";

    try {
        const res = await fetch(`${API}/signup`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                role, username, email, password, fullname,
                voice_type: voiceType, instrument, specialty, code,
                org: orgSlug || getOrg(),
                theme,
            })
        });

        const data = await res.json();

        if (data.message === "Account created") {
            msg.textContent = "Account created! Check your email to verify your address. Redirecting…";
            msg.classList.add("success-msg");
            setTimeout(() => { location.href = "/login"; }, 2500);
            return;
        }

        msg.textContent = data.message || "Signup failed. Please try again.";
        msg.classList.add("error-msg");
    } catch (err) {
        console.error(err);
        msg.textContent = "Server error. Please try again.";
        msg.classList.add("error-msg");
    }
}


// ===============================
// ORG DROPDOWN (signup page)
// ===============================
async function populateOrgDropdown(selectId = "org") {
    const select = document.getElementById(selectId);
    if (!select) return;
    if (select.dataset.populated) return;
    select.dataset.populated = "1";
    try {
        const res = await fetch(`${API}/orgs`);
        const orgs = await res.json();
        orgs.forEach(o => {
            const opt = document.createElement("option");
            opt.value = o.slug;
            opt.textContent = o.name;
            select.appendChild(opt);
        });
    } catch (e) {
        console.error("Failed to load orgs:", e);
        delete select.dataset.populated;
    }
}


// ===============================
// UI HELPERS
// ===============================
const SVG_EYE = `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>`;
const SVG_EYE_OFF = `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-10-7-10-7a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 10 7 10 7a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>`;

document.addEventListener("DOMContentLoaded", () => {
    // Password show/hide toggle — auto-wires any .eye-btn inside a .field-row
    document.querySelectorAll(".field-row").forEach(row => {
        const input = row.querySelector("input[type='password']");
        const btn = row.querySelector(".eye-btn");
        if (!input || !btn) return;
        btn.innerHTML = SVG_EYE;
        btn.addEventListener("click", () => {
            const show = input.type === "password";
            input.type = show ? "text" : "password";
            btn.innerHTML = show ? SVG_EYE_OFF : SVG_EYE;
        });
    });

    // Calendar subscribe collapsible — shared across all views
    const calToggle = document.getElementById("calendar-subscribe-toggle");
    if (calToggle) {
        calToggle.addEventListener("click", () => {
            const body = document.getElementById("calendar-subscribe-body");
            const chevron = document.getElementById("calendar-subscribe-chevron");
            if (!body) return;
            const collapsed = body.classList.toggle("hidden");
            if (chevron) chevron.innerHTML = collapsed ? "&#9654;" : "&#9660;";
        });
    }
});

function updateFields() {
    const roleEl = document.getElementById("role");
    if (!roleEl) return;

    const role = roleEl.value;

    const studentFields = document.getElementById("student-fields");
    const orchestraFields = document.getElementById("orchestra-fields");
    const teacherFields = document.getElementById("teacher-fields");
    const codeFields = document.getElementById("code-fields");
    const codeHint = document.getElementById("code-hint");

    if (studentFields) {
        studentFields.style.display = role === "student" ? "block" : "none";
    }
    if (orchestraFields) {
        orchestraFields.style.display = role === "orchestra_member" ? "block" : "none";
        if (role === "orchestra_member") {
            populateInstrumentSelect("instrument", "instrument-other-row");
        }
    }
    if (teacherFields) {
        teacherFields.style.display = role === "teacher" ? "block" : "none";
    }
    if (codeFields) {
        codeFields.style.display = (role === "teacher" || role === "admin") ? "block" : "none";
    }
    if (codeHint) {
        codeHint.textContent = role === "admin"
            ? "Admin code (ask the system administrator)."
            : "Teacher code (provided by your admin).";
    }
}

// ===============================
// FORGOT PASSWORD
// ===============================

function openForgotPasswordModal() {
    const modal = document.getElementById("forgot-password-modal");
    if (!modal) return;
    modal.classList.remove("hidden");
    document.getElementById("forgot-email").value = "";
    const orgEl = document.getElementById("forgot-org");
    if (orgEl) orgEl.value = "";
    const msg = document.getElementById("forgot-msg");
    msg.textContent = "";
    msg.classList.remove("error-msg", "success-msg");
}

function closeForgotPasswordModal() {
    const modal = document.getElementById("forgot-password-modal");
    if (!modal) return;
    modal.classList.add("hidden");
}

async function submitForgotPassword() {
    const email = document.getElementById("forgot-email")?.value.trim().toLowerCase();
    const orgName = (document.getElementById("forgot-org")?.value || "").trim();
    const msg = document.getElementById("forgot-msg");
    const btn = document.getElementById("submit-forgot-btn");

    msg.textContent = "";
    msg.classList.remove("error-msg", "success-msg");

    if (!email) {
        msg.textContent = "Please enter your email address.";
        msg.classList.add("error-msg");
        return;
    }

    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        msg.textContent = "Please enter a valid email address.";
        msg.classList.add("error-msg");
        return;
    }

    btn.disabled = true;
    btn.textContent = "Sending...";

    const body = { email };
    if (orgName) body.org_name = orgName;

    try {
        const res = await fetch(`${API}/auth/forgot-password`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        });
        const data = await res.json();

        if (data.success) {
            // Always show success message regardless of whether email exists,
            // matching the backend's "no info leak" behavior.
            msg.textContent = "If that email is registered, a reset link is on its way. Check your inbox.";
            msg.classList.add("success-msg");
        } else {
            msg.textContent = "Something went wrong. Please try again.";
            msg.classList.add("error-msg");
        }
    } catch (e) {
        console.error(e);
        msg.textContent = "Server error. Please try again.";
        msg.classList.add("error-msg");
    } finally {
        btn.disabled = false;
        btn.textContent = "Send reset link";
    }
}

// Wire up forgot-password buttons (login page only)
document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("forgot-password-link")?.addEventListener("click", (e) => {
        e.preventDefault();
        openForgotPasswordModal();
    });
    document.getElementById("submit-forgot-btn")?.addEventListener("click", submitForgotPassword);
    document.getElementById("cancel-forgot-btn")?.addEventListener("click", closeForgotPasswordModal);
    document.getElementById("forgot-password-modal")?.addEventListener("click", (e) => {
        if (e.target.id === "forgot-password-modal") closeForgotPasswordModal();
    });
});


// ===============================
// KEYBOARD SUBMIT
// ===============================
document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;

    // If forgot-password modal is open, submit that instead
    const forgotModal = document.getElementById("forgot-password-modal");
    if (forgotModal && !forgotModal.classList.contains("hidden")) {
        submitForgotPassword();
        return;
    }

    // Only trigger if we're actually on a login/signup page (has the username field)
    if (document.getElementById("password") && typeof login === "function") {
        if (path.includes("signup")) {
            signup();
        } else {
            login();
        }
    }
});

// ===============================
// INIT
// ===============================
updateFields();