// ======================================================
// COOKIE / STORAGE CONSENT
// Essential storage (session cookie, org routing) always works —
// it can't be disabled without breaking login. Functional storage
// (theme preference) is optional and gated behind this consent.
// ======================================================

(function () {
    const CONSENT_KEY = "cc_consent_v1";

    function getConsent() {
        try {
            const raw = localStorage.getItem(CONSENT_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch (e) {
            return null;
        }
    }

    function saveConsent(functional) {
        try {
            localStorage.setItem(CONSENT_KEY, JSON.stringify({
                functional: !!functional,
                decidedAt: new Date().toISOString(),
            }));
        } catch (e) {}
    }

    window.hasConsent = function (category) {
        if (category === "essential") return true;
        const c = getConsent();
        return !!(c && c.functional);
    };

    function removeFunctionalStorage() {
        try {
            localStorage.removeItem("coaching-scheduler-theme");
            localStorage.removeItem("coaching-scheduler-character-theme");
        } catch (e) {}
    }

    function closeBanner() {
        document.getElementById("cookie-consent-banner")?.remove();
    }

    function renderBanner() {
        if (document.getElementById("cookie-consent-banner")) return;

        const banner = document.createElement("div");
        banner.id = "cookie-consent-banner";
        banner.setAttribute("role", "dialog");
        banner.setAttribute("aria-label", "Cookie preferences");
        banner.innerHTML = `
            <div class="cc-banner-main">
                <p class="cc-banner-text">
                    We use a required cookie to keep you signed in. With your permission, we'll also
                    remember your theme preference. See our <a href="/privacy">Privacy Policy</a> for details.
                </p>
                <div class="cc-banner-actions">
                    <button type="button" id="cc-customize-btn" class="subtle-btn">Customize</button>
                    <button type="button" id="cc-reject-btn">Reject Non-Essential</button>
                    <button type="button" id="cc-accept-btn" class="btn-primary">Accept All</button>
                </div>
            </div>
            <div id="cc-customize-panel" class="cc-customize-panel hidden">
                <div class="cc-category">
                    <label class="cc-category-row">
                        <input type="checkbox" checked disabled>
                        <span><strong>Essential</strong> — keeps you signed in. Always on; the app can't function without it.</span>
                    </label>
                </div>
                <div class="cc-category">
                    <label class="cc-category-row">
                        <input type="checkbox" id="cc-functional-toggle">
                        <span><strong>Functional</strong> — remembers your light/dark and color theme choice across visits.</span>
                    </label>
                </div>
                <div class="cc-banner-actions">
                    <button type="button" id="cc-save-btn" class="btn-primary">Save Preferences</button>
                </div>
            </div>
        `;
        document.body.appendChild(banner);

        document.getElementById("cc-accept-btn").addEventListener("click", () => {
            saveConsent(true);
            closeBanner();
        });
        document.getElementById("cc-reject-btn").addEventListener("click", () => {
            saveConsent(false);
            removeFunctionalStorage();
            closeBanner();
        });
        document.getElementById("cc-customize-btn").addEventListener("click", () => {
            document.getElementById("cc-customize-panel").classList.toggle("hidden");
        });
        document.getElementById("cc-save-btn").addEventListener("click", () => {
            const functional = document.getElementById("cc-functional-toggle").checked;
            saveConsent(functional);
            if (!functional) removeFunctionalStorage();
            closeBanner();
        });
    }

    // Small persistent control so consent can be changed anytime, not just on first visit.
    function renderReopenLink() {
        if (document.getElementById("cookie-prefs-link")) return;
        const link = document.createElement("button");
        link.id = "cookie-prefs-link";
        link.type = "button";
        link.title = "Cookie preferences";
        link.textContent = "🍪";
        link.addEventListener("click", renderBanner);
        document.body.appendChild(link);
    }

    function init() {
        renderReopenLink();
        if (!getConsent()) renderBanner();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
