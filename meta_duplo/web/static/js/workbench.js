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
    Alpine.store("wb", {
      ids: new Set(),
      get count() { return this.ids.size; },
      has(id) { return this.ids.has(id); },
      toggle(id) { this.ids.has(id) ? this.ids.delete(id) : this.ids.add(id); },
      setMany(ids, on) { ids.forEach((id) => (on ? this.ids.add(id) : this.ids.delete(id))); },
      clear() { this.ids.clear(); },
      csv() { return Array.from(this.ids).join(","); },
      selectDuplicates() {
        fetch("/wb/ids?which=nonkeepers").then((r) => r.json()).then((d) => { this.clear(); this.setMany(d.ids, true); });
      },
      selectVisible(on) {
        const ids = Array.from(document.querySelectorAll("[data-rowid]")).map((el) => Number(el.dataset.rowid));
        this.setMany(ids, on);
      },
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
