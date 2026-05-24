// ======================================================
// THEME (light/dark) + CHARACTER THEME
// Load this as the FIRST script on every page so we apply
// before paint and avoid flashing the wrong theme.
// ======================================================

(function () {
    const THEME_KEY = 'coaching-scheduler-theme';
    const CHARACTER_THEME_KEY = 'coaching-scheduler-character-theme';
    const VALID_CHARACTER_THEMES = [
        'queen-of-the-night', 'mimi', 'don-giovanni',
        'tosca', 'carmen', 'violetta'
    ];

    function getInitialLightDark() {
        try {
            const saved = localStorage.getItem(THEME_KEY);
            if (saved === 'light' || saved === 'dark') return saved;
        } catch (e) {}
        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
            return 'dark';
        }
        return 'light';
    }

    function applyLightDark(theme) {
        document.documentElement.setAttribute('data-theme', theme);
    }

    function getInitialCharacterTheme() {
        try {
            const saved = localStorage.getItem(CHARACTER_THEME_KEY);
            if (VALID_CHARACTER_THEMES.includes(saved)) return saved;
        } catch (e) {}
        return 'queen-of-the-night';
    }

    function applyCharacterTheme(themeId) {
        if (!VALID_CHARACTER_THEMES.includes(themeId)) {
            themeId = 'queen-of-the-night';
        }
        document.documentElement.setAttribute('data-character-theme', themeId);
    }

    applyLightDark(getInitialLightDark());
    applyCharacterTheme(getInitialCharacterTheme());

    window.setCharacterTheme = function(themeId) {
        applyCharacterTheme(themeId);
        try { localStorage.setItem(CHARACTER_THEME_KEY, themeId); } catch (e) {}
    };

    window.setLightDark = function(theme) {
        applyLightDark(theme);
        try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
    };

    function setupToggle() {
        if (document.querySelector('.theme-toggle')) return;

        const btn = document.createElement('button');
        btn.className = 'theme-toggle';
        btn.type = 'button';
        btn.setAttribute('aria-label', 'Toggle light/dark theme');
        btn.title = 'Toggle theme';

        function updateIcon() {
            const current = document.documentElement.getAttribute('data-theme');
            btn.textContent = current === 'dark' ? 'â' : 'â¾';
        }
        updateIcon();

        btn.addEventListener('click', () => {
            const current = document.documentElement.getAttribute('data-theme');
            const next = current === 'dark' ? 'light' : 'dark';
            window.setLightDark(next);
            updateIcon();
        });

        document.body.appendChild(btn);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', setupToggle);
    } else {
        setupToggle();
    }
})();