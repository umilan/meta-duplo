// Workbench (experimental): column resize, drag-to-reorder, header menus and row selection.
// Loaded before Alpine; registers components on `alpine:init`. Server state lives in /wb/view.

(() => {
  const token = () => {
    try { return JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-Meta-Duplo-Token"] || ""; }
    catch { return ""; }
  };

  // POST a view change; reload the table when the server asks for it.
  const postView = (data) =>
    fetch("/wb/view", {
      method: "POST",
      headers: { "X-Meta-Duplo-Token": token(), "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(data),
    }).then((r) => {
      if ((r.headers.get("HX-Trigger") || "").includes("wb-reload")) htmx.trigger(document.body, "wb-reload");
    });

  document.addEventListener("alpine:init", () => {
    // Selection as a plain object (id → true): reliably reactive in every browser, Safari included.
    Alpine.store("wb", {
      sel: {},
      count: 0,
      total: 0,              // rows in the current view (all groups, filters applied), set by the table
      has(id) { return this.sel[id] === true; },
      add(id) { if (!this.sel[id]) { this.sel[id] = true; this.count++; } },
      remove(id) { if (this.sel[id]) { delete this.sel[id]; this.count--; } },
      toggle(id) { this.has(id) ? this.remove(id) : this.add(id); },
      setMany(ids, on) { ids.forEach((id) => (on ? this.add(id) : this.remove(id))); },
      clear() { this.sel = {}; this.count = 0; },
      csv() { return Object.keys(this.sel).join(","); },
      get all() { return this.total > 0 && this.count >= this.total; },
      load(which) { return fetch(`/wb/ids?which=${which}`).then((r) => r.json()).then((d) => { this.clear(); this.setMany(d.ids, true); }); },
      selectDuplicates() { return this.load("nonkeepers"); },
      // Header checkbox: every row of the table, including groups not loaded yet.
      selectAll(on) { return on ? this.load("all") : this.clear(); },
    });

    Alpine.data("wbPage", () => ({
      panel: false,
      dialog: null,          // "filter" | "edit" | "addcol" | null
      f: { key: "", op: "contains", value: "", label: "" },
      e: { target: "", kind: "recommended" },
      colQuery: "",
      openFilter(d, op = "contains") { this.f = { key: d.key, label: d.label, op, value: "" }; this.dialog = "filter"; },
      openEdit(d) { this.e = { target: d.target, kind: d.kind || "recommended" }; this.dialog = "edit"; },
      close() { this.dialog = null; },
      post: postView,
    }));

    Alpine.data("wbHead", (key, dir) => ({
      menu: false,
      post: postView,
      sort(d) { postView({ action: "sort", key, dir: d || (dir === "asc" ? "desc" : "asc") }); },
      resize(ev) {
        const col = document.querySelector(`col[data-key="${CSS.escape(key)}"]`);
        const table = ev.target.closest("table");
        if (!col || !table) return;
        const x0 = ev.clientX, w0 = col.getBoundingClientRect().width || parseInt(col.style.width, 10), t0 = table.offsetWidth;
        let w = w0;
        const move = (e) => {
          w = Math.max(40, Math.round(w0 + e.clientX - x0));
          col.style.width = `${w}px`;
          table.style.width = `${t0 + w - w0}px`;
        };
        const up = () => {
          window.removeEventListener("mousemove", move);
          window.removeEventListener("mouseup", up);
          document.body.classList.remove("is-resizing");
          if (w !== w0) postView({ action: "col_width", key, width: w });
        };
        document.body.classList.add("is-resizing");
        window.addEventListener("mousemove", move);
        window.addEventListener("mouseup", up);
      },
      dragStart(e) { e.dataTransfer.setData("text/plain", key); e.dataTransfer.effectAllowed = "move"; },
      drop(e) {
        const from = e.dataTransfer.getData("text/plain");
        if (from && from !== key) postView({ action: "col_move", key: from, before: key });
      },
    }));
  });
})();
