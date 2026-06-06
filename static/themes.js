// =====================================================
// CHARACTER THEMES — master list + picker UI helper
// =====================================================

const CHARACTER_THEMES = [
    {
        id: "queen-of-the-night",
        name: "Queen of the Night",
        tagline: "Black, gold, and cream — the original.",
        // Swatches shown in the picker (light variant)
        swatches: ["#15120e", "#c9a227", "#faf6ef", "#f5ecd7"],
        isDefault: true,
    },
    {
        id: "mimi",
        name: "Mimì",
        tagline: "Gentle sage and warm slate.",
        swatches: ["#fafaf9", "#7a9b76", "#3d4541", "#f0efed"],
    },
    {
        id: "don-giovanni",
        name: "Don Giovanni",
        tagline: "Stately indigo on cream.",
        swatches: ["#faf6f0", "#4a3d8c", "#2a2520", "#f0eadc"],
    },
    {
        id: "tosca",
        name: "Tosca",
        tagline: "Warm Italian terracotta.",
        swatches: ["#f8f5f0", "#c25f3d", "#3d3833", "#ede8de"],
    },
    {
        id: "carmen",
        name: "Carmen",
        tagline: "Modern teal on graphite.",
        swatches: ["#f7f7f8", "#0f7c7c", "#2a2c30", "#ececee"],
    },
    {
        id: "violetta",
        name: "Violetta",
        tagline: "Velvet curtains and parchment.",
        swatches: ["#f8f4ec", "#7a2230", "#3a2f28", "#ede5d2"],
    },
];

const DEFAULT_THEME_ID = "queen-of-the-night";

function isValidThemeId(id) {
    return CHARACTER_THEMES.some(t => t.id === id);
}

/**
 * Render the theme picker into a container element.
 * @param {HTMLElement} container - target element to fill
 * @param {string} selectedId - id of theme to mark selected
 * @param {Function} onChange - called with new theme id when user picks one
 */
function renderThemePicker(container, selectedId, onChange) {
    if (!container) return;

    const active = isValidThemeId(selectedId) ? selectedId : DEFAULT_THEME_ID;

    container.innerHTML = "";
    container.classList.add("theme-picker");

    CHARACTER_THEMES.forEach(theme => {
        const card = document.createElement("label");
        card.className = "theme-option";
        if (theme.id === active) card.classList.add("selected");
        card.dataset.themeId = theme.id;

        const swatchRow = theme.swatches.map(c =>
            `<div class="theme-swatch" style="background:${c}"></div>`
        ).join("");

        const defaultTag = theme.isDefault
            ? `<span class="default-tag">(default)</span>`
            : "";

        card.innerHTML = `
            <input type="radio" name="theme-radio" value="${theme.id}" ${theme.id === active ? "checked" : ""}>
            <div class="theme-swatch-row">${swatchRow}</div>
            <div class="theme-name">${theme.name}${defaultTag}</div>
        `;

        card.addEventListener("click", (e) => {
            // Update selection state visually
            container.querySelectorAll(".theme-option").forEach(el => el.classList.remove("selected"));
            card.classList.add("selected");
            const radio = card.querySelector("input[type='radio']");
            if (radio) radio.checked = true;
            if (typeof onChange === "function") onChange(theme.id);
        });

        container.appendChild(card);
    });
}

/**
 * Get the currently selected theme ID from a container that has a rendered picker.
 */
function getSelectedThemeFromPicker(container) {
    if (!container) return DEFAULT_THEME_ID;
    const selected = container.querySelector(".theme-option.selected");
    return selected?.dataset.themeId || DEFAULT_THEME_ID;
}