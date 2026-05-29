// ======================================================
// APP-LEVEL SCRIPT (shared across all dashboards)
// ======================================================

// USERNAME + USER_ROLE are populated by /me. The server derives identity
// from the HttpOnly session cookie — the frontend doesn't track it anymore.
let USERNAME = null;
let USER_ROLE = null;
let FULLNAME = null;
let EMAIL_VERIFIED = true;
let CURRENT_THEME = "queen-of-the-night";

async function loadMe() {
    try {
        const res = await fetch(`${API}/me`, { credentials: "include" });
        const data = await res.json();

        if (!data.logged_in) {
            // No valid session → punt to login
            window.location.href = "/";
            return;
        }

        USERNAME = data.username;
        USER_ROLE = data.role;
        FULLNAME = data.fullname;
        EMAIL_VERIFIED = data.email_verified !== false;
        CURRENT_THEME = data.theme || "queen-of-the-night";

        // Apply server-side theme preference
        if (typeof window.setCharacterTheme === "function") {
            window.setCharacterTheme(CURRENT_THEME);
        }

        const el = document.getElementById("welcome");
        if (el && FULLNAME) {
            el.textContent = data.org_name
                ? `Welcome, ${FULLNAME} — ${data.org_name}`
                : `Welcome, ${FULLNAME}`;
        }

        // Show "please verify your email" banner if needed
        if (!EMAIL_VERIFIED) {
            showEmailVerificationBanner();
        }
    } catch (e) {
        console.error("Failed to load user session:", e);
        window.location.href = "/";
    }
}

function showEmailVerificationBanner() {
    // Don't double-inject
    if (document.getElementById("email-verify-banner")) return;

    const banner = document.createElement("div");
    banner.id = "email-verify-banner";
    banner.className = "email-verify-banner";
    banner.innerHTML = `
        <span>Please verify your email address. Some features may not work until you do.</span>
        <button id="resend-verify-btn" class="subtle-btn">Resend email</button>
    `;
    document.body.insertBefore(banner, document.body.firstChild);

    document.getElementById("resend-verify-btn").addEventListener("click", async () => {
        const btn = document.getElementById("resend-verify-btn");
        btn.disabled = true;
        btn.textContent = "Sending...";
        try {
            await fetch(`${API}/auth/resend-verification`, {
                method: "POST",
                credentials: "include",
            });
            btn.textContent = "Sent! Check your inbox.";
        } catch (e) {
            console.error(e);
            btn.disabled = false;
            btn.textContent = "Failed — try again";
        }
    });
}

// Kick off immediately; expose promise so dashboards can await it
const ME_READY = loadMe();


// -------------------- SHARED UTILITIES ----------------------

function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

// Escape HTML then wrap bare URLs in clickable <a> tags.
function renderNotes(text) {
    if (!text) return "";
    const escaped = escapeHtml(text);
    return escaped.replace(
        /(https?:\/\/[^\s<>"]+)/g,
        '<a href="$1" target="_blank" rel="noopener" class="notes-url">$1</a>'
    );
}


// -------------------- EDIT ACCOUNT MODAL --------------------

function openEditAccount() {
    const modal = document.getElementById("edit-account-modal");
    if (!modal) return;
    modal.classList.remove("hidden");
    document.getElementById("edit-msg").textContent = "";
    ["new-username", "new-password", "current-password"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = "";
    });
    const logoutBox = document.getElementById("logout-other-devices");
    if (logoutBox) logoutBox.checked = true;

    // Render the theme picker if the container exists in this dashboard's modal
    const themeBox = document.getElementById("edit-account-theme-picker");
    if (themeBox && typeof renderThemePicker === "function") {
        renderThemePicker(themeBox, CURRENT_THEME, (newId) => {
            // Apply preview immediately so user sees the change live
            if (typeof window.setCharacterTheme === "function") {
                window.setCharacterTheme(newId);
            }
        });
    }

    // Show org transfer section only for students
    const transferSection = document.getElementById("org-transfer-section");
    if (transferSection) {
        if (USER_ROLE === "student") {
            transferSection.classList.remove("hidden");
            loadOrgTransferStatus();
            populateTransferOrgDropdown();
        } else {
            transferSection.classList.add("hidden");
        }
    }
}


// -------------------- ORG TRANSFER (student) --------------------

async function populateTransferOrgDropdown() {
    const select = document.getElementById("transfer-org");
    if (!select || select.options.length > 1) return; // already populated
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
    }
}

async function loadOrgTransferStatus() {
    const statusEl = document.getElementById("org-transfer-status");
    const formEl = document.getElementById("org-transfer-form");
    if (!statusEl) return;

    try {
        const res = await fetch(`${API}/student/org-transfer-request`, { credentials: "include" });
        const data = await res.json();
        if (data.request && data.request.status === "pending") {
            statusEl.innerHTML = `<p class="hint">Transfer to <strong>${data.request.to_org}</strong> is <strong>pending</strong> — awaiting head admin approval.</p>`;
            if (formEl) formEl.classList.add("hidden");
        } else if (data.request && data.request.status === "approved") {
            statusEl.innerHTML = `<p class="hint">Your transfer to <strong>${data.request.to_org}</strong> was approved.</p>`;
            if (formEl) formEl.classList.add("hidden");
        } else {
            statusEl.innerHTML = "";
            if (formEl) formEl.classList.remove("hidden");
        }
    } catch (e) {
        console.error("Failed to load transfer status:", e);
    }
}

async function submitOrgTransfer() {
    const orgSlug = document.getElementById("transfer-org")?.value;
    const message = document.getElementById("transfer-message")?.value.trim();
    const msg = document.getElementById("transfer-msg");
    if (msg) msg.textContent = "";

    if (!orgSlug) {
        if (msg) msg.textContent = "Please select a destination organization.";
        return;
    }

    try {
        const res = await fetch(`${API}/student/org-transfer-request`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ to_org: orgSlug, message: message || null }),
        });
        const data = await res.json();
        if (data.status === "success") {
            if (msg) msg.textContent = "Transfer request submitted!";
            loadOrgTransferStatus();
        } else {
            if (msg) msg.textContent = data.message || "Failed to submit request.";
        }
    } catch (e) {
        console.error(e);
        if (msg) msg.textContent = "Server error.";
    }
}

function closeEditAccount() {
    const modal = document.getElementById("edit-account-modal");
    if (!modal) return;
    modal.classList.add("hidden");
    document.getElementById("edit-msg").textContent = "";
    // If user previewed a theme but didn't save, revert
    if (typeof window.setCharacterTheme === "function") {
        window.setCharacterTheme(CURRENT_THEME);
    }
}

async function submitAccountUpdate() {
    const newUsername = document.getElementById("new-username").value.trim().toLowerCase();
    const newPassword = document.getElementById("new-password").value.trim();
    const currentPassword = document.getElementById("current-password").value.trim();
    const logoutOthers = document.getElementById("logout-other-devices")?.checked ?? true;

    // Theme — read from the picker if present
    const themeBox = document.getElementById("edit-account-theme-picker");
    const newTheme = themeBox && typeof getSelectedThemeFromPicker === "function"
        ? getSelectedThemeFromPicker(themeBox)
        : CURRENT_THEME;

    // Save theme if it changed (independent of password — works even without current password)
    if (newTheme !== CURRENT_THEME) {
        try {
            await fetch(`${API}/user/theme`, {
                method: "POST",
                credentials: "include",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({ theme: newTheme }),
            });
            CURRENT_THEME = newTheme;
            if (typeof window.setCharacterTheme === "function") {
                window.setCharacterTheme(newTheme);
            }
        } catch (e) {
            console.error("Failed to save theme:", e);
        }
    }

    // If only theme was changed, no need to require current-password
    if (!newUsername && !newPassword) {
        if (newTheme !== CURRENT_THEME || newTheme === CURRENT_THEME) {
            document.getElementById("edit-msg").textContent = "Theme saved.";
            document.getElementById("edit-msg").classList.add("success-msg");
            setTimeout(() => {
                document.getElementById("edit-account-modal")?.classList.add("hidden");
                document.getElementById("edit-msg").textContent = "";
                document.getElementById("edit-msg").classList.remove("success-msg");
            }, 800);
            return;
        }
    }

    if (!currentPassword) {
        document.getElementById("edit-msg").textContent = "Current password is required to change username or password.";
        return;
    }

    const res = await fetch(`${API}/user/update-account`, {
        method: "POST",
        credentials: "include",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
            new_username: newUsername || null,
            new_password: newPassword || null,
            current_password: currentPassword,
            logout_other_devices: logoutOthers,
        })
    });

    const data = await res.json();

    if (data.status !== "success") {
        document.getElementById("edit-msg").textContent = data.message;
        return;
    }

    alert("Account updated successfully. Please log in again.");
    // If they changed password, their current session may have been invalidated.
    // Force a full logout regardless for cleanliness.
    await fetch(`${API}/logout`, { method: "POST", credentials: "include" });
    location.href = "/";
}


// -------------------- LOGOUT --------------------

async function logout() {
    try {
        await fetch(`${API}/logout`, { method: "POST", credentials: "include" });
    } catch (e) {
        console.error("Logout request failed:", e);
    }
    // Clear any legacy localStorage entries from before the switch
    localStorage.removeItem("username");
    localStorage.removeItem("role");
    window.location.href = "/";
}


// -------------------- WIRE UP BUTTONS --------------------

document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("open-edit-account-btn")?.addEventListener("click", openEditAccount);
    document.getElementById("cancel-edit-account-btn")?.addEventListener("click", closeEditAccount);
    document.getElementById("save-account-btn")?.addEventListener("click", submitAccountUpdate);
    document.getElementById("logout-btn")?.addEventListener("click", logout);
    document.getElementById("submit-transfer-btn")?.addEventListener("click", submitOrgTransfer);

    // Cancel-lesson handler (student page only)
    const lessonsBox = document.getElementById("my-lessons");
    lessonsBox?.addEventListener("click", (e) => {
        if (!e.target.classList.contains("cancel-lesson-btn")) return;
        const lessonId = e.target.dataset.lessonId;
        if (!confirm("Are you sure you want to cancel this lesson?")) return;

        fetch(`${API}/student/cancel-lesson`, {
            method: "POST",
            credentials: "include",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ lesson_id: lessonId })
        })
        .then(res => res.json())
        .then(data => {
            if (data.status === "success" && typeof loadMyLessons === "function") {
                loadMyLessons();
            } else if (data.status !== "success") {
                alert(data.message || "Failed to cancel lesson.");
            }
        });
    });
});