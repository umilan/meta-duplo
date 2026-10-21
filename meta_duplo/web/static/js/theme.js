// Colour theme: "auto" follows the system, "light"/"dark" are forced via <html data-theme>.
// Loaded synchronously in <head> so the saved choice applies before the first paint (no flash).
(() => {
  const KEY = "meta-duplo-theme";
  const apply = (mode) => {
    if (mode === "light" || mode === "dark") document.documentElement.dataset.theme = mode;
    else delete document.documentElement.dataset.theme;
  };
  let saved = "auto";
  try { saved = localStorage.getItem(KEY) || "auto"; } catch { /* storage blocked: stay on auto */ }
  apply(saved);
  window.mdTheme = {
    get: () => saved,
    set(mode) {
      saved = mode;
      apply(mode);
      try { mode === "auto" ? localStorage.removeItem(KEY) : localStorage.setItem(KEY, mode); } catch { /* ignore */ }
    },
  };
})();
