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

  // Hover: tint the row (CSS), the column (classes set here) and the cell, in the body and the header;
  // one ⋯ button follows the pointer over data cells (not the checkbox, grouping or thumbnail columns).
  const hover = { table: null, idx: -1 };
  const headerKey = (table, idx) => table.tHead?.rows[0]?.cells[idx]?.dataset.key || "";
  const clearColumn = () => {
    document.querySelectorAll(".wbt .is-hcol").forEach((el) => el.classList.remove("is-hcol"));
    hover.table = null; hover.idx = -1;
  };
  const cellButton = () => {
    let btn = document.getElementById("wb-cellbtn");
    if (!btn) {
      btn = document.createElement("button");
      btn.id = "wb-cellbtn"; btn.type = "button"; btn.className = "cellbtn"; btn.textContent = "⋯";
      btn.title = "Cell menu"; btn.setAttribute("aria-label", "Cell menu");
      btn.addEventListener("click", (ev) => {  // never let it reach the row (that opens the preview)
        ev.stopPropagation();
        window.dispatchEvent(new CustomEvent("wb-cellmenu", { detail: { btn } }));
      });
    }
    return btn;
  };
  document.addEventListener("mouseover", (e) => {
    const td = e.target.closest && e.target.closest(".wbt tbody td, .wbt thead th");
    if (!td) return;
    const table = td.closest("table"), idx = td.cellIndex;
    if (table !== hover.table || idx !== hover.idx) {
      clearColumn();
      hover.table = table; hover.idx = idx;
      if (idx > 0 && idx < td.parentElement.cells.length - 1) {
        table.querySelectorAll(`tr > :nth-child(${idx + 1})`).forEach((c) => c.classList.add("is-hcol"));
      }
    }
    const key = headerKey(table, idx);
    const inCanvas = td.tagName === "TD" && key && key !== "thumb" && !td.closest("tbody.more");
    if (inCanvas) {
      const btn = cellButton();
      if (btn.parentElement !== td) td.appendChild(btn);
    } else {
      document.getElementById("wb-cellbtn")?.remove();
    }
  });
  document.addEventListener("mouseout", (e) => {
    const table = e.target.closest && e.target.closest(".wbt");
    if (table && !(e.relatedTarget && table.contains(e.relatedTarget))) {
      clearColumn();
      document.getElementById("wb-cellbtn")?.remove();
    }
  });

  // Column drag: a vertical line shows where the dragged header will land.
  const dropLine = () => {
    let el = document.getElementById("wb-dropline");
    if (!el) {
      el = document.createElement("div");
      el.id = "wb-dropline"; el.className = "dropline"; el.hidden = true;
      document.body.appendChild(el);
    }
    return el;
  };
  const showLine = (th, side) => {
    const r = th.getBoundingClientRect();
    const box = (th.closest(".wb-scroll") || th).getBoundingClientRect();
    const x = Math.min(Math.max(side === "after" ? r.right : r.left, box.left + 1), box.right - 1);
    const el = dropLine();
    el.style.left = `${Math.round(x) - 1}px`;
    el.style.top = `${Math.round(r.top)}px`;
    el.style.height = `${Math.round(box.bottom - r.top)}px`;
    el.hidden = false;
  };
  const hideLine = () => { const el = document.getElementById("wb-dropline"); if (el) el.hidden = true; };
  document.addEventListener("dragover", (e) => { if (!e.target.closest || !e.target.closest(".wbh-cell[data-key]")) hideLine(); });
  document.addEventListener("dragend", () => {
    hideLine();
    document.querySelectorAll(".wbh-cell.is-dragging").forEach((th) => th.classList.remove("is-dragging"));
  });
  document.addEventListener("drop", hideLine);

  document.addEventListener("alpine:init", () => {
    // Selection as a plain object (id → true): reliably reactive in every browser, Safari included.
    Alpine.store("wb", {
      sel: {},
      count: 0,
      total: 0,              // rows in the current view (all groups, filters applied), set by the table
      cols: [],              // keys of the columns shown, set by the table (drives the column picker)
      active: null,          // row shown in the preview (clicked, or just ticked)
      panel: false,          // preview panel open (mirrors wbPage.panel for the settings sheet)
      expanded: true,        // groups open; new pages of groups follow it
      expandAll(on) { this.expanded = on; window.dispatchEvent(new CustomEvent("wb-fold", { detail: on })); },
      preset: "none",        // selection picker: none | all | custom | dup_newer | dup_older | dup_lessmeta
      has(id) { return this.sel[id] === true; },
      add(id) { if (!this.sel[id]) { this.sel[id] = true; this.count++; } },
      remove(id) { if (this.sel[id]) { delete this.sel[id]; this.count--; } },
      edited() { this.preset = this.count ? "custom" : "none"; },  // a hand change leaves the preset
      toggle(id) { this.has(id) ? this.remove(id) : this.add(id); this.edited(); },
      setMany(ids, on) { ids.forEach((id) => (on ? this.add(id) : this.remove(id))); this.edited(); },
      clear() { this.sel = {}; this.count = 0; this.preset = "none"; },
      csv() { return Object.keys(this.sel).join(","); },
      get all() { return this.total > 0 && this.count >= this.total; },
      // Presets are computed by the server over the whole view, including groups not loaded yet.
      load(which) {
        return fetch(`/wb/ids?which=${which}`).then((r) => r.json()).then((d) => {
          this.clear(); d.ids.forEach((id) => this.add(id)); this.preset = this.count ? which : "none";
        });
      },
      applyPreset(p) { return p === "none" ? this.clear() : p === "custom" ? null : this.load(p); },
      selectAll(on) { return on ? this.load("all") : this.clear(); },  // header checkbox
    });

    Alpine.data("wbPage", () => ({
      panel: false,
      init() { this.$watch("panel", (on) => { Alpine.store("wb").panel = on; }); },
      dialog: null,          // "filter" | "edit" | "addcol" | null
      f: { key: "", op: "contains", value: "", label: "" },
      e: { target: "", kind: "recommended" },
      colQuery: "",
      menu: null,            // the open column menu: { key, label, target, groupable, x, y }
      openCellMenu(btn) {
        if (this.menu && this.menu.cell && this.menu.btn === btn && this.menu.td === btn.parentElement) { this.menu = null; return; }
        const r = btn.getBoundingClientRect();
        this.menu = { cell: true, btn, td: btn.parentElement, key: "__cell", label: "Cell menu",
                      x: Math.max(8, Math.min(r.right - 256, window.innerWidth - 264)), y: r.bottom + 4 };
      },
      openMenu(d, btn) {
        if (this.menu && this.menu.key === d.key) { this.menu = null; return; }
        const r = btn.getBoundingClientRect();
        this.menu = { ...d, x: Math.max(8, Math.min(r.right - 256, window.innerWidth - 264)), y: r.bottom + 4 };
      },
      openFilter(d, op = "contains") { this.f = { key: d.key, label: d.label, op, value: "" }; this.dialog = "filter"; },
      openEdit(d) { this.e = { target: d.target, kind: d.kind || "recommended" }; this.dialog = "edit"; },
      close() { this.dialog = null; },
      post: postView,
    }));

    Alpine.data("wbHead", (key, dir, first = "asc") => ({
      post: postView,
      // first click: `first` (numbers start high, so 100 % confidence is on top), then toggle
      sort(d) { postView({ action: "sort", key, dir: d || (dir ? (dir === "asc" ? "desc" : "asc") : first) }); },
      resize(ev) {
        const col = document.querySelector(`col[data-key="${CSS.escape(key)}"]`);
        const table = ev.target.closest("table");
        if (!col || !table) return;
        const x0 = ev.clientX, w0 = col.getBoundingClientRect().width || parseInt(col.style.width, 10), t0 = table.offsetWidth;
        let w = w0;
        const move = (e) => {
          w = Math.max(40, Math.round((w0 + e.clientX - x0) / 8) * 8);  // 8px grid (ADR-017)
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
      side: "before",
      dragStart(e) {
        e.dataTransfer.setData("text/plain", key);
        e.dataTransfer.effectAllowed = "move";
        e.target.closest("th")?.classList.add("is-dragging");
      },
      over(e) {
        const th = e.currentTarget, r = th.getBoundingClientRect();
        this.side = e.clientX > r.left + r.width / 2 ? "after" : "before";  // right half: drop after this column
        e.dataTransfer.dropEffect = "move";
        showLine(th, this.side);
      },
      drop(e) {
        hideLine();
        const from = e.dataTransfer.getData("text/plain");
        let before = key;
        if (this.side === "after") {
          const next = e.currentTarget.nextElementSibling;
          before = next && next.dataset.key ? next.dataset.key : "";  // "" = move to the end
        }
        if (from && from !== key && from !== before) postView({ action: "col_move", key: from, before });
      },
    }));
  });
})();
