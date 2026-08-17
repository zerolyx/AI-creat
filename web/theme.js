"use strict";

(function initializeThemeSystem() {
  const STORAGE_KEY = "industrial-voice-theme";
  const ALLOWED_THEMES = new Set(["system", "light", "dark"]);
  const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");

  function getPreference() {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      return ALLOWED_THEMES.has(stored) ? stored : "system";
    } catch (_error) {
      return "system";
    }
  }

  function resolvedTheme(preference) {
    return preference === "system" ? (systemTheme.matches ? "dark" : "light") : preference;
  }

  function syncControls(preference) {
    document.querySelectorAll("[data-theme-option]").forEach((button) => {
      const selected = button.dataset.themeOption === preference;
      button.classList.toggle("is-active", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
  }

  function applyTheme(preference, persist = true) {
    const selected = ALLOWED_THEMES.has(preference) ? preference : "system";
    const resolved = resolvedTheme(selected);
    document.documentElement.dataset.theme = resolved;
    document.documentElement.dataset.themePreference = selected;
    document.documentElement.style.colorScheme = resolved;
    const themeMeta = document.querySelector('meta[name="theme-color"]');
    if (themeMeta) themeMeta.content = resolved === "dark" ? "#0b1116" : "#17212b";
    if (persist) {
      try {
        window.localStorage.setItem(STORAGE_KEY, selected);
      } catch (_error) {
        // Theme selection still applies for this session when storage is unavailable.
      }
    }
    syncControls(selected);
    window.dispatchEvent(new CustomEvent("industrialthemechange", { detail: { preference: selected, resolved } }));
  }

  function bindControls() {
    document.querySelectorAll("[data-theme-option]").forEach((button) => {
      button.addEventListener("click", () => applyTheme(button.dataset.themeOption));
    });
    syncControls(getPreference());
  }

  const handleSystemChange = () => {
    if (getPreference() === "system") applyTheme("system", false);
  };
  if (typeof systemTheme.addEventListener === "function") {
    systemTheme.addEventListener("change", handleSystemChange);
  } else if (typeof systemTheme.addListener === "function") {
    systemTheme.addListener(handleSystemChange);
  }

  window.IndustrialTheme = { apply: applyTheme, getPreference, resolvedTheme };
  applyTheme(getPreference(), false);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindControls, { once: true });
  } else {
    bindControls();
  }
})();
