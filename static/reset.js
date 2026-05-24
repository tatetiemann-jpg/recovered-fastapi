// =====================================================
// PASSWORD RESET PAGE
// =====================================================

(function() {
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get("token");

    const intro = document.getElementById("reset-intro");
    const formWrapper = document.getElementById("reset-form-wrapper");
    const successWrapper = document.getElementById("reset-success");
    const msg = document.getElementById("reset-msg");
    const submitBtn = document.getElementById("submit-reset-btn");

    // No token in URL â can't reset
    if (!token) {
        formWrapper.classList.add("hidden");
        intro.textContent = "This link is invalid. Request a new password reset from the login page.";
        return;
    }

    async function submitReset() {
        const newPw = document.getElementById("new-password").value;
        const confirmPw = document.getElementById("new-password-confirm").value;

        msg.textContent = "";
        msg.classList.remove("error-msg", "success-msg");

        if (!newPw || !confirmPw) {
            msg.textContent = "Please fill in both password fields.";
            msg.classList.add("error-msg");
            return;
        }

        if (newPw.length < 8) {
            msg.textContent = "Password must be at least 8 characters.";
            msg.classList.add("error-msg");
            return;
        }

        if (newPw !== confirmPw) {
            msg.textContent = "Passwords do not match.";
            msg.classList.add("error-msg");
            return;
        }

        submitBtn.disabled = true;
        submitBtn.textContent = "Updating...";

        try {
            const res = await fetch(`${API}/auth/reset-password`, {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ token, new_password: newPw })
            });
            const data = await res.json();

            if (data.success) {
                formWrapper.classList.add("hidden");
                intro.classList.add("hidden");
                successWrapper.classList.remove("hidden");
            } else {
                msg.textContent = data.message || "Reset failed.";
                msg.classList.add("error-msg");
                submitBtn.disabled = false;
                submitBtn.textContent = "Set New Password";
            }
        } catch (e) {
            console.error(e);
            msg.textContent = "Server error. Please try again.";
            msg.classList.add("error-msg");
            submitBtn.disabled = false;
            submitBtn.textContent = "Set New Password";
        }
    }

    submitBtn.addEventListener("click", submitReset);

    // Enter to submit
    document.addEventListener("keydown", (e) => {
        if (e.key === "Enter") submitReset();
    });
})();