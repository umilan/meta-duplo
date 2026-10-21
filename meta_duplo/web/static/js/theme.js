// Appearance preferences, per viewer (localStorage), applied as attributes on <html>:
//   data-theme   light (default) | dark
//   data-borders on (default) | off       cell borders in tables
//   data-zebra   off (default) | on       light background on every odd table row
//   data-density normal (default) | compact
// Loaded synchronously in <head> so the saved choices apply before the first paint (no flash).
(() => {
  const DEFAULTS = { theme: "light", borders: "on", zebra: "off", density: "normal" };
  const KEY = (name) => (name === "theme" ? "meta-duplo-theme" : `meta-duplo-${name}`);
  const read = (name) => {
    let v = null;
    try { v = localStorage.getItem(KEY(name)); } catch { /* storage blocked: defaults */ }
    if (name === "theme" && v !== "dark") v = "light";  // the old "auto" setting becomes light
    return v || DEFAULTS[name];
  };
  const prefs = {};
  for (const name of Object.keys(DEFAULTS)) {
    prefs[name] = read(name);
    document.documentElement.dataset[name] = prefs[name];
  }
  window.mdPrefs = {
    get: (name) => prefs[name],
    set(name, value) {
      prefs[name] = value;
      document.documentElement.dataset[name] = value;
      try {
        value === DEFAULTS[name] ? localStorage.removeItem(KEY(name)) : localStorage.setItem(KEY(name), value);
      } catch { /* ignore */ }
    },
  };
})();
