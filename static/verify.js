// =====================================================
// EMAIL VERIFICATION PAGE
// =====================================================

(async function() {
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get("token");

    const introMsg = document.getElementById("verify-msg");
    const successWrapper = document.getElementById("verify-success");
    const failWrapper = document.getElementById("verify-fail");
    const failMsg = document.getElementById("verify-fail-msg");

    if (!token) {
        introMsg.classList.add("hidden");
        failMsg.textContent = "This link is missing required information.";
        failWrapper.classList.remove("hidden");
        return;
    }

    try {
        const res = await fetch(`${API}/auth/verify-email`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token })
        });
        const data = await res.json();

        introMsg.classList.add("hidden");
        if (data.success) {
            successWrapper.classList.remove("hidden");
        } else {
            failMsg.textContent = data.message || "Verification failed.";
            failWrapper.classList.remove("hidden");
        }
    } catch (e) {
        console.error(e);
        introMsg.classList.add("hidden");
        failMsg.textContent = "Server error. Please try again.";
        failWrapper.classList.remove("hidden");
    }
})();