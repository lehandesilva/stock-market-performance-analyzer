// Theme: Auto (follows the OS) → Light → Dark. Loaded in <head>, before first paint, so a saved
// choice never flashes the wrong colours. The choice is kept in localStorage; app.js draws the button.
(() => {
  const KEY = "smpa-theme", MODES = ["auto", "light", "dark"], root = document.documentElement;
  const mode = () => { try { const m = localStorage.getItem(KEY); return MODES.includes(m) ? m : "auto"; } catch { return "auto"; } };
  const apply = (m) => { if (m === "auto") delete root.dataset.theme; else root.dataset.theme = m; };
  apply(mode());
  window.smpaTheme = {
    mode,
    cycle() {
      const next = MODES[(MODES.indexOf(mode()) + 1) % MODES.length];
      try { if (next === "auto") localStorage.removeItem(KEY); else localStorage.setItem(KEY, next); } catch { /* storage blocked */ }
      apply(next);
      return next;
    },
  };
})();
