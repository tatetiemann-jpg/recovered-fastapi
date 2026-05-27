// =====================================================
// ACCEPT INVITATION PAGE
// =====================================================

(async function() {
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get("token");

    const intro = document.getElementById("invite-intro");
    const formWrapper = document.getElementById("invite-form-wrapper");
    const successWrapper = document.getElementById("invite-success");
    const failWrapper = document.getElementById("invite-fail");
    const failMsg = document.getElementById("invite-fail-msg");

    if (!token) {
        intro.classList.add("hidden");
        failMsg.textContent = "This link is missing required information.";
        failWrapper.classList.remove("hidden");
        return;
    }

    // Fetch invite info to validate + pre-fill
    let inviteInfo;
    try {
        const res = await fetch(`${API}/auth/invite-info?token=${encodeURIComponent(token)}`);
        inviteInfo = await res.json();
    } catch (e) {
        console.error(e);
        intro.classList.add("hidden");
        failMsg.textContent = "Server error. Please try again.";
        failWrapper.classList.remove("hidden");
        return;
    }

    if (!inviteInfo.valid) {
        intro.classList.add("hidden");
        failMsg.textContent = inviteInfo.message || "This invitation is no longer valid.";
        failWrapper.classList.remove("hidden");
        return;
    }

    // Show the form, pre-fill what we can
    intro.classList.add("hidden");
    formWrapper.classList.remove("hidden");

    const roleLabelMap = { admin: "Admin", head_admin: "Head Admin", teacher: "Teacher", orchestra_admin: "Orchestra Admin", student: "Singer", choir_member: "Choir Member", ensemble_member: "Ensemble Member" };
    const roleLabel = roleLabelMap[inviteInfo.role] || inviteInfo.role;
    const orgPart = inviteInfo.org_name ? ` of ${inviteInfo.org_name}` : "";
    let summaryText = `You've been invited as ${roleLabel}${orgPart}. Email: ${inviteInfo.email}`;
    if (inviteInfo.role === "ensemble_member" && inviteInfo.instrument) {
        summaryText += ` — ${inviteInfo.instrument}`;
    } else if (inviteInfo.role === "teacher" && inviteInfo.teacher_type === "instrumental") {
        const instruments = inviteInfo.teacher_instruments
            ? ` — Instruments: ${inviteInfo.teacher_instruments}`
            : "";
        summaryText += `. Type: Instrumental${instruments}`;
    } else if (inviteInfo.role === "teacher") {
        summaryText += ". Type: Vocal";
    }
    document.getElementById("invite-summary").textContent = summaryText;

    if (inviteInfo.fullname_hint) {
        document.getElementById("invite-fullname").value = inviteInfo.fullname_hint;
    }

    // Show voice part dropdown for choir singers only
    if (inviteInfo.role === "student" && inviteInfo.org_type === "choir") {
        document.getElementById("invite-voice-row")?.classList.remove("hidden");
    }

    // Render theme picker with live preview
    const themeBox = document.getElementById("invite-theme-picker");
    if (themeBox && typeof renderThemePicker === "function") {
        renderThemePicker(themeBox, "queen-of-the-night", (newId) => {
            if (typeof window.setCharacterTheme === "function") {
                window.setCharacterTheme(newId);
            }
        });
    }

    const submitBtn = document.getElementById("submit-invite-btn");
    const msg = document.getElementById("invite-msg");

    async function submit() {
        const fullname = document.getElementById("invite-fullname").value.trim();
        const username = document.getElementById("invite-username").value.trim().toLowerCase();
        const password = document.getElementById("invite-password").value;
        const confirmPw = document.getElementById("invite-password-confirm").value;
        const voiceRow = document.getElementById("invite-voice-row");
        const voiceType = voiceRow && !voiceRow.classList.contains("hidden")
            ? (document.getElementById("invite-voice-type")?.value || "")
            : null;

        const theme = themeBox && typeof getSelectedThemeFromPicker === "function"
            ? getSelectedThemeFromPicker(themeBox)
            : "queen-of-the-night";

        msg.textContent = "";
        msg.classList.remove("error-msg");

        if (!fullname || !username || !password) {
            msg.textContent = "Please fill in all required fields.";
            msg.classList.add("error-msg");
            return;
        }
        if (password.length < 8) {
            msg.textContent = "Password must be at least 8 characters.";
            msg.classList.add("error-msg");
            return;
        }
        if (password !== confirmPw) {
            msg.textContent = "Passwords do not match.";
            msg.classList.add("error-msg");
            return;
        }
        if (voiceType !== null && !voiceType) {
            msg.textContent = "Please select your voice part.";
            msg.classList.add("error-msg");
            return;
        }

        submitBtn.disabled = true;
        submitBtn.textContent = "Creating account…";

        try {
            const res = await fetch(`${API}/auth/accept-invite`, {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    token,
                    fullname,
                    username,
                    password,
                    theme,
                    voice_type: voiceType || null,
                })
            });
            const data = await res.json();

            if (data.status === "success") {
                formWrapper.classList.add("hidden");
                successWrapper.classList.remove("hidden");
            } else {
                msg.textContent = data.message || "Failed to create account.";
                msg.classList.add("error-msg");
                submitBtn.disabled = false;
                submitBtn.textContent = "Create Account";
            }
        } catch (e) {
            console.error(e);
            msg.textContent = "Server error. Please try again.";
            msg.classList.add("error-msg");
            submitBtn.disabled = false;
            submitBtn.textContent = "Create Account";
        }
    }

    submitBtn.addEventListener("click", submit);
    document.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !formWrapper.classList.contains("hidden")) submit();
    });
})();