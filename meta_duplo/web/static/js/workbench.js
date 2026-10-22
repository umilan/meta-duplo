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
      if (r.headers.get("X-Wb-History")) undoHistory.view(r.headers.get("X-Wb-History"));
      if ((r.headers.get("HX-Trigger") || "").includes("wb-reload")) htmx.trigger(document.body, "wb-reload");
    });

  // Undo / redo: one ordered history of selection steps (kept here) and view steps (sort, filters, columns, edit
  // rules, reference row…; the views themselves are kept by the server, POST /wb/history). The list survives page
  // reloads (sessionStorage); deleting files is never in it: that goes through plans, which have their own undo.
  const toast = (value) => window.dispatchEvent(new CustomEvent("toast", { detail: { value } }));
  const undoHistory = {
    KEY: "meta-duplo-history", MAX: 100, undo: [], redo: [], snap: "", busy: false,
    store() { return Alpine.store("wb"); },
    selState() { const w = this.store(); return JSON.stringify({ ids: Object.keys(w.sel).sort(), preset: w.preset }); },
    load() {
      try { const h = JSON.parse(sessionStorage.getItem(this.KEY) || "null"); if (h) { this.undo = h.undo || []; this.redo = h.redo || []; } } catch { /* storage blocked */ }
      this.snap = this.selState(); this.save();
    },
    save() {
      try { sessionStorage.setItem(this.KEY, JSON.stringify({ undo: this.undo, redo: this.redo })); } catch { /* storage blocked */ }
      this.store().undoN = this.undo.length; this.store().redoN = this.redo.length;
    },
    push(entry) { this.undo.push(entry); if (this.undo.length > this.MAX) this.undo.shift(); this.redo = []; this.save(); },
    view(act) { this.push({ t: "view", act }); },
    // called (debounced) whenever the selection changes: one step per burst (Shift+↓ held, a group checkbox…)
    selChanged(now) { if (now !== this.snap) { this.push({ t: "sel", before: this.snap, after: now }); this.snap = now; } },
    applySel(state) {
      const s = JSON.parse(state), w = this.store();
      this.snap = state;  // the watcher sees no change, so this is not recorded again
      w.clear(); s.ids.forEach((id) => w.add(id)); w.preset = s.preset;
    },
    async step(dir) {
      if (this.busy) return;
      const from = dir === "undo" ? this.undo : this.redo, to = dir === "undo" ? this.redo : this.undo;
      const done = dir === "undo" ? "Undone" : "Redone", e = from.pop();
      if (!e) { toast(`Nothing to ${dir}`); return; }
      if (e.t === "sel") {
        this.applySel(dir === "undo" ? e.before : e.after); to.push(e); this.save(); toast(`${done}: selection`); return;
      }
      this.busy = true;
      try {
        const r = await fetch("/wb/history", { method: "POST", headers: { "X-Meta-Duplo-Token": token() },
                                               body: new URLSearchParams({ direction: dir }) }).then((x) => x.json());
        if (!r.ok) { this.save(); toast(`Nothing to ${dir}`); return; }  // the server's list was shorter (another tab)
        to.push(e); this.save(); toast(`${done}: ${r.label}`);
        if (r.reload) location.reload(); else htmx.trigger(document.body, "wb-reload");
      } finally { this.busy = false; }
    },
  };
  document.addEventListener("htmx:afterRequest", (e) => {  // view changes sent by htmx forms (filter, edit rule…)
    const act = e.detail.xhr && e.detail.xhr.getResponseHeader("X-Wb-History");
    if (act) undoHistory.view(act);
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
  // right-click on a data cell opens the same cell menu as its ⋯
  document.addEventListener("contextmenu", (e) => {
    const td = e.target.closest && e.target.closest(".wbt tbody tr.wbr > td");
    if (!td) return;
    const key = headerKey(td.closest("table"), td.cellIndex);
    if (!key || key === "thumb") return;
    e.preventDefault();
    const btn = cellButton();
    if (btn.parentElement !== td) td.appendChild(btn);
    window.dispatchEvent(new CustomEvent("wb-cellmenu", { detail: { btn } }));
  });
  // double-click on a purple edit cell: type a new value for this file (the filename column opens the naming dialog)
  document.addEventListener("dblclick", (e) => {
    const td = e.target.closest && e.target.closest(".wbt tbody tr.wbr > td");
    if (!td || e.target.closest("button, input")) return;
    const th = td.closest("table").tHead.rows[0].cells[td.cellIndex];
    if (th && th.dataset.key === "filename") {  // a file name: open the naming pattern
      window.getSelection()?.removeAllRanges();
      window.dispatchEvent(new CustomEvent("wb-edit", { detail: { target: "filename" } }));
      return;
    }
    if (!th || !th.classList.contains("wbh-cell--edit")) return;
    window.getSelection()?.removeAllRanges();  // the double-click selected a word
    const btn = cellButton();
    if (btn.parentElement !== td) td.appendChild(btn);
    window.dispatchEvent(new CustomEvent("wb-celledit", { detail: { btn } }));
  });
  document.addEventListener("mouseout", (e) => {
    const table = e.target.closest && e.target.closest(".wbt");
    if (table && !(e.relatedTarget && table.contains(e.relatedTarget))) {
      clearColumn();
      document.getElementById("wb-cellbtn")?.remove();
    }
  });

  // Keyboard: a cell cursor in the table. ↑ ↓ rows, ← → columns; Enter ticks the row, Space shows it in the
  // preview (on a group row: folds it), ⌥/Ctrl/⌘+Enter opens the cell's ⋯ menu, Esc leaves the cursor.
  // Shift+↑/↓ ticks while moving, X ticks or unticks and moves down, ⌘/Ctrl+A ticks every row of the view.
  const kb = { rowid: null, row: null, col: -1 };
  const visibleRows = () => [...document.querySelectorAll("#wb-table .wbt tbody tr")]
    .filter((tr) => tr.offsetParent !== null && (tr.classList.contains("wbr") || tr.classList.contains("wbg__head")));
  const cursorCell = () => (kb.row && kb.row.isConnected ? kb.row.cells[kb.col] : null);
  const paint = () => {
    document.querySelectorAll(".wbt .is-cursor, .wbt .is-cursor-row").forEach((el) => el.classList.remove("is-cursor", "is-cursor-row"));
    const td = cursorCell();
    if (!td) return;
    kb.row.classList.add("is-cursor-row");
    td.classList.add("is-cursor");
    td.scrollIntoView({ block: "nearest", inline: "nearest" });
  };
  const showPreview = (tr) => {
    Alpine.store("wb").active = Number(tr.dataset.rowid);
    htmx.trigger(tr, "wb-show");
  };
  const setCursor = (tr, col) => {
    if (!tr) return;
    const moved = tr !== kb.row;
    kb.row = tr; kb.rowid = tr.dataset.rowid || null;
    kb.col = Math.max(0, Math.min(col, tr.cells.length - 2));  // the last cell is the empty "+" column
    paint();
    // with the preview or the large viewer open, they follow the cursor (debounced: holding an arrow key stays smooth)
    if (moved && tr.classList.contains("wbr") && (Alpine.store("wb").panel || Alpine.store("wb").lightbox)) {
      clearTimeout(kb.pv); kb.pv = setTimeout(() => showPreview(tr), 120);
    }
  };
  const typing = (el) => el && (el.closest("input, select, textarea, [contenteditable], .modal") !== null);
  document.addEventListener("keydown", (e) => {
    if (!document.querySelector("#wb-table .wbt") || typing(e.target)) return;
    const k = e.key.toLowerCase();
    if ((e.metaKey || e.ctrlKey) && !e.altKey && (k === "z" || (k === "y" && e.ctrlKey))) {  // ⌘Z undo, ⇧⌘Z / Ctrl+Y redo
      e.preventDefault(); undoHistory.step(e.shiftKey || k === "y" ? "redo" : "undo"); return;
    }
    if (e.key === "Escape" && Alpine.store("wb").lightbox) { Alpine.store("wb").lightbox = false; return; }
    if (Alpine.store("wb").lightbox) {  // zoom keys of the large viewer (+ − 0 1 W H)
      const lb = document.querySelector(".lightbox");
      if (lb && Alpine.$data(lb).key(e)) { e.preventDefault(); return; }
    }
    const menuOpen = [...document.querySelectorAll(".wbsheet, .setsheet")].some((m) => m.getClientRects().length);
    if (menuOpen) return;  // a menu or sheet is open: its own keys (Esc) apply
    const rows = visibleRows();
    if (!rows.length) return;
    let i = rows.indexOf(kb.row);
    const has = i >= 0 && cursorCell();
    const first = () => setCursor(rows[0], 3);
    const store = Alpine.store("wb");
    const tick = (tr) => { if (tr && tr.classList.contains("wbr")) store.setMany([Number(tr.dataset.rowid)], true); };
    if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "a") {  // ⌘/Ctrl+A: every row of the view
      e.preventDefault(); store.selectAll(true); return;
    }
    if (e.shiftKey && (e.key === "ArrowDown" || e.key === "ArrowUp")) {  // extend the ticks while moving
      e.preventDefault();
      if (!has) { first(); tick(rows[0]); return; }
      tick(kb.row);
      const next = rows[e.key === "ArrowDown" ? Math.min(i + 1, rows.length - 1) : Math.max(i - 1, 0)];
      setCursor(next, kb.col); tick(next); return;
    }
    if (e.key.toLowerCase() === "x" && !e.metaKey && !e.ctrlKey && !e.altKey) {  // tick / untick, then next row
      e.preventDefault();
      if (!has) { first(); return; }
      kb.row.querySelector("td.wbt__check input")?.click();
      setCursor(rows[Math.min(i + 1, rows.length - 1)], kb.col); return;
    }
    switch (e.key) {
      case "ArrowDown": e.preventDefault(); has ? setCursor(rows[Math.min(i + 1, rows.length - 1)], kb.col) : first(); return;
      case "ArrowUp": e.preventDefault(); has ? setCursor(rows[Math.max(i - 1, 0)], kb.col) : first(); return;
      case "ArrowRight": e.preventDefault(); has ? setCursor(kb.row, kb.col + 1) : first(); return;
      case "ArrowLeft": e.preventDefault(); has ? setCursor(kb.row, kb.col - 1) : first(); return;
      case "Escape": if (has) { kb.row = null; paint(); } return;
    }
    if (!has) return;
    const tr = kb.row, td = cursorCell();
    if (e.key === "Enter" && (e.altKey || e.ctrlKey || e.metaKey)) {  // the cell's ⋯ menu
      e.preventDefault();
      const head = tr.closest("table").tHead.rows[0].cells[kb.col];
      if (tr.classList.contains("wbr") && head && head.dataset.key && head.dataset.key !== "thumb") {
        const btn = cellButton();
        td.appendChild(btn);
        window.dispatchEvent(new CustomEvent("wb-cellmenu", { detail: { btn } }));
      }
    } else if (e.key === "Enter") {  // tick / untick the row (or the whole group)
      e.preventDefault();
      tr.querySelector("td.wbt__check input")?.click();
    } else if (e.key === " ") {
      e.preventDefault();
      if (tr.classList.contains("wbg__head")) tr.click();  // fold / unfold the group
      else if (Alpine.store("wb").panel) window.dispatchEvent(new CustomEvent("wb-panel", { detail: { on: false } }));  // Space toggles
      else { showPreview(tr); window.dispatchEvent(new CustomEvent("wb-preview")); }
    }
  });
  // a click puts the cursor on that cell; after a reload the cursor finds its row again
  document.addEventListener("click", (e) => {
    const td = e.target.closest && e.target.closest(".wbt tbody td");
    if (td && td.parentElement.matches(".wbr, .wbg__head")) setCursor(td.parentElement, td.cellIndex);
  });
  document.addEventListener("htmx:afterSwap", (e) => {
    if (!e.detail.target || e.detail.target.id !== "wb-table" || !kb.rowid) return;
    const tr = document.querySelector(`#wb-table tr[data-rowid="${kb.rowid}"]`);
    if (tr) setCursor(tr, kb.col); else kb.row = null;
  });

  // The table fills the window down to its bottom edge (no empty band below it).
  const fitTable = () => {
    const box = document.querySelector(".main--flush #wb-table .wb-scroll");
    if (box) box.style.maxHeight = `${Math.max(256, window.innerHeight - box.getBoundingClientRect().top)}px`;
  };
  window.addEventListener("resize", fitTable);

  // Pinned columns (header data-pin, kept first by the server) stick to the left after the checkbox and group
  // columns; offsets come from the rendered widths, so they follow resizing.
  const applyPins = () => {
    const table = document.querySelector("#wb-table .wbt");
    if (!table || !table.tHead) return;
    const head = [...table.tHead.rows[0].cells];
    let left = 0;
    for (const th of head) {
      if (!th.matches(".wbt__check, .wbt__tree")) break;
      left += th.getBoundingClientRect().width;
    }
    const pins = head.filter((th) => th.hasAttribute("data-pin"));
    pins.forEach((th, n) => {
      const last = n === pins.length - 1;
      table.querySelectorAll(`tr > :nth-child(${th.cellIndex + 1})`).forEach((c) => {
        if (c.colSpan > 1) return;
        c.classList.add("wbt__pin"); c.classList.toggle("wbt__pin--last", last); c.style.left = `${Math.round(left)}px`;
      });
      left += th.getBoundingClientRect().width;
    });
  };
  window.addEventListener("resize", applyPins);
  document.addEventListener("htmx:afterSettle", applyPins);   // table reloads and new pages of groups
  document.addEventListener("DOMContentLoaded", () => setTimeout(applyPins, 0));
  document.addEventListener("mouseup", () => requestAnimationFrame(applyPins));  // after a column resize
  document.addEventListener("htmx:afterSettle", fitTable);
  document.addEventListener("DOMContentLoaded", () => setTimeout(fitTable, 0));

  // Header tooltips (slim columns): shown at once, above the header, outside the scrolling box so nothing clips them.
  const tip = () => {
    let el = document.getElementById("wb-tip");
    if (!el) { el = document.createElement("div"); el.id = "wb-tip"; el.className = "wbtip"; el.hidden = true; document.body.appendChild(el); }
    return el;
  };
  // Source links ([🔗A] in edit columns) use the same tooltip and light up the column / row they point to.
  let srcOn = null;
  const clearSrc = () => {
    if (!srcOn) return;
    srcOn = null;
    document.querySelectorAll(".wbt .is-srccol, .wbt .is-srcrow").forEach((x) => x.classList.remove("is-srccol", "is-srcrow"));
  };
  const showSrc = (link) => {
    if (srcOn === link) return;
    clearSrc(); srcOn = link;
    const table = link.closest("table"), key = link.dataset.srckey, row = link.dataset.srcrow;
    const th = key && table.querySelector(`thead th[data-key="${CSS.escape(key)}"]`);
    if (th) table.querySelectorAll(`tr > :nth-child(${th.cellIndex + 1})`).forEach((c) => c.classList.add("is-srccol"));
    if (row) table.querySelector(`tr.wbr[data-rowid="${CSS.escape(row)}"]`)?.classList.add("is-srcrow");
  };
  document.addEventListener("click", (e) => {
    const link = e.target.closest && e.target.closest(".wbt td .srclink");
    if (!link) return;
    e.stopPropagation();  // not the row click (preview)
    const td = link.closest("td"), th = td.closest("table").tHead.rows[0].cells[td.cellIndex];
    const target = link.dataset.target || (th?.dataset.key || "").replace(/^edit:/, "");  // a source cell names its edit column
    if (link.dataset.typed && td.closest("tr.wbr")) {  // a value typed for this file: edit it again
      const btn = cellButton();
      if (btn.parentElement !== td) td.appendChild(btn);
      window.dispatchEvent(new CustomEvent("wb-celledit", { detail: { btn } }));
    } else {
      window.dispatchEvent(new CustomEvent("wb-edit", { detail: { target, kind: "recommended" } }));
    }
  }, true);
  document.addEventListener("mouseover", (e) => {
    const link = e.target.closest && e.target.closest(".wbt .srclink");
    const th = !link && e.target.closest && e.target.closest(".wbt th[data-tip].is-slim");
    const el = tip(), anchor = link || th;
    if (link) showSrc(link); else clearSrc();
    if (!anchor) { el.hidden = true; return; }
    el.textContent = link ? link.dataset.srctip : th.dataset.tip; el.hidden = false;
    const r = anchor.getBoundingClientRect(), w = el.offsetWidth;
    el.style.left = `${Math.round(Math.max(8, Math.min(r.left, window.innerWidth - w - 8)))}px`;
    el.style.top = `${Math.round(r.top - el.offsetHeight - 4)}px`;
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
    el.style.left = `${Math.round(x) - 2}px`;  // centre the 4px line on the border
    el.style.top = `${Math.round(r.top)}px`;
    el.style.height = `${Math.round(box.bottom - r.top)}px`;
    el.hidden = false;
    document.querySelectorAll(".wbh-cell.is-droptarget").forEach((c) => c !== th && c.classList.remove("is-droptarget"));
    th.classList.add("is-droptarget");
  };
  const hideLine = () => {
    const el = document.getElementById("wb-dropline"); if (el) el.hidden = true;
    document.querySelectorAll(".wbh-cell.is-droptarget").forEach((c) => c.classList.remove("is-droptarget"));
  };
  document.addEventListener("dragover", (e) => { if (!e.target.closest || !e.target.closest(".wbh-cell[data-key]")) hideLine(); });
  document.addEventListener("dragend", () => {
    hideLine();
    document.querySelectorAll(".wbh-cell.is-dragging").forEach((th) => th.classList.remove("is-dragging"));
  });
  document.addEventListener("drop", hideLine);

  // Table reloads (sort, filter, columns…) replace the scrolling box: keep its sideways position.
  let keepLeft = null;
  document.addEventListener("htmx:beforeSwap", (e) => {
    if (e.detail.target && e.detail.target.id === "wb-table") keepLeft = document.querySelector("#wb-table .wb-scroll")?.scrollLeft ?? null;
  });
  document.addEventListener("htmx:afterSwap", (e) => {
    if (!e.detail.target || e.detail.target.id !== "wb-table" || keepLeft === null) return;
    const box = document.querySelector("#wb-table .wb-scroll");
    if (box) box.scrollLeft = keepLeft;
    keepLeft = null;
  });

  document.addEventListener("alpine:init", () => {
    // Selection as a plain object (id → true): reliably reactive in every browser, Safari included.
    Alpine.store("wb", {
      sel: {},
      count: 0,
      total: 0,              // rows in the current view (all groups, filters applied), set by the table
      cols: [],              // keys of the columns shown, set by the table (drives the column picker)
      colLabels: [],         // [key, label, kind] of the visible columns (Sort by choices)
      sort: ["", "asc"],     // current sort, set by the table
      sortLabel: "",         // its header label (also when the column is not shown)
      active: null,          // row shown in the preview (clicked, or just ticked)
      lightbox: false,       // the large viewer (click the preview image); follows the table cursor
      panel: false,          // preview panel open (mirrors wbPage.panel for the settings sheet)
      expanded: true,        // groups open; new pages of groups follow it
      expandAll(on) { this.expanded = on; window.dispatchEvent(new CustomEvent("wb-fold", { detail: on })); },
      preset: "none",        // selection picker: none | all | custom | dup_newer | dup_older | dup_lessmeta
      undoN: 0, redoN: 0,    // steps in the undo / redo history (buttons)
      step(dir) { undoHistory.step(dir); },
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
    undoHistory.load();
    let selTimer = null;
    Alpine.effect(() => {  // re-runs on every selection change; reading the keys tracks them
      const now = undoHistory.selState();
      clearTimeout(selTimer); selTimer = setTimeout(() => undoHistory.selChanged(now), 250);
    });

    Alpine.data("wbPage", () => ({
      panel: false,
      init() {
        this.$watch("panel", (on) => { Alpine.store("wb").panel = on; });
        let w = 0;
        try { w = Number(localStorage.getItem("meta-duplo-panel-w")) || 0; } catch { /* storage blocked */ }
        if (w) this.$el.style.setProperty("--panel-w", `${w}px`);
      },
      // preview panel width: drag its left edge; 256–720px on the 8px grid, kept per browser
      resizePanel(ev) {
        const panel = ev.target.closest(".wb-panel"), root = this.$root;
        const x0 = ev.clientX, w0 = panel.getBoundingClientRect().width;
        let w = w0;
        const move = (e) => {
          w = Math.max(256, Math.min(720, Math.round((w0 + x0 - e.clientX) / 8) * 8));
          root.style.setProperty("--panel-w", `${w}px`);
        };
        const up = () => {
          window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up);
          document.body.classList.remove("is-resizing");
          try { localStorage.setItem("meta-duplo-panel-w", String(w)); } catch { /* ignore */ }
        };
        document.body.classList.add("is-resizing");
        window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
      },
      dialog: null,          // "filter" | "edit" | "addcol" | null
      f: { key: "", op: "contains", value: "", label: "", index: "" },  // index: the filter being edited, "" = a new one
      e: { target: "", kind: "recommended" },
      colQuery: "",
      menu: null,            // the open column menu: { key, label, target, groupable, x, y }
      openCellMenu(btn) {
        if (this.menu && this.menu.cell && this.menu.btn === btn && this.menu.td === btn.parentElement) { this.menu = null; return; }
        const r = btn.getBoundingClientRect(), td = btn.parentElement, tr = td.parentElement;
        const th = tr.closest("table").tHead.rows[0].cells[td.cellIndex];
        const value = [...td.childNodes].filter((n) => n !== btn && !n.classList?.contains("srclink")).map((n) => n.textContent).join("").trim();
        this.sub = false;
        this.menu = { cell: true, btn, td, key: th.dataset.key, label: th.dataset.label || th.dataset.key, target: th.dataset.target,
                      isName: ["filename", "edit:filename"].includes(th.dataset.key),  // edited through the naming pattern
                      fid: tr.dataset.rowid, value: value === "—" ? "" : value, isRef: tr.classList.contains("wbr--userref"),
                      x: Math.max(8, Math.min(r.right - 304, window.innerWidth - 312)), y: r.bottom + 4 };
      },
      // cell actions (⋯ or right-click): filter by the value, apply it to group / selection / all, reference row
      cellAct(action, extra = {}) {
        const m = this.menu; this.menu = null;
        postView({ action, fid: m.fid, key: m.key, ...extra });
      },
      // "Apply this value › To column…": the user picks the field it feeds (a date: the capture date; else place / device)
      sub: false,
      ac: { fid: "", key: "", label: "", value: "", targets: [], target: "", scope: "group" },
      targetName(t) { return { date: "New date (capture date)", place: "New place", device: "New device" }[t] || t; },
      openApplyCol() {
        const m = this.menu; this.menu = null;
        const targets = m.target === "date" ? ["date"] : ["place", "device"];
        this.ac = { fid: m.fid, key: m.key, label: m.label, value: m.value.replace(/^[→=]\s*/, ""), targets,
                    target: targets.includes(m.target) ? m.target : targets[0], scope: "group" };
        this.dialog = "applycol";
      },
      saveApplyCol() {
        const a = this.ac;
        postView({ action: "cell_apply", fid: a.fid, key: a.key, target: a.target, scope: a.scope,
                   ids: a.scope === "selection" ? Alpine.store("wb").csv() : "" });
        this.close();
      },
      cellEdit: { label: "", value: "", fid: "", key: "" },
      openCellEdit() {
        const m = this.menu; this.menu = null;
        if (!m) return;
        if (!m.target) { this.openEdit({ target: "filename" }); return; }  // New filename: one pattern for all files
        this.cellEdit = { label: m.label, value: m.value.replace(/^[→=]\s*/, ""), fid: m.fid, key: m.key };  // "→ value" / "= value"
        this.dialog = "celledit"; this.$nextTick(() => this.$refs.celledit && this.$refs.celledit.select());
      },
      saveCellEdit() {
        postView({ action: "cell_set", fid: this.cellEdit.fid, key: this.cellEdit.key, value: this.cellEdit.value });
        this.close();
      },
      // header: Filter by value — the column's distinct values, most frequent first
      vals: { key: "", label: "", q: "", items: [] },
      openValues(m) {
        this.vals = { key: m.key, label: m.label, q: "", items: [] };
        this.dialog = "values";
        fetch(`/wb/values?key=${encodeURIComponent(m.key)}`).then((r) => r.json()).then((d) => { this.vals.items = d.values; });
      },
      pickValue(v) {
        postView(v === "" ? { action: "filter_add", key: this.vals.key, op: "empty" } : { action: "filter_add", key: this.vals.key, op: "equals", value: v });
        this.close();
      },
      openMenu(d, btn) {
        if (this.menu && this.menu.key === d.key) { this.menu = null; return; }
        const r = btn.getBoundingClientRect();
        this.menu = { ...d, x: Math.max(8, Math.min(r.right - 304, window.innerWidth - 312)), y: r.bottom + 4 };
      },
      openFilter(d, op = "contains") {
        this.f = { key: d.key, label: d.label, op, value: d.value ?? "", index: d.index ?? "" }; this.dialog = "filter";
      },
      openEdit(d) {
        if (d.target === "filename") { this.dialog = "naming"; window.dispatchEvent(new CustomEvent("wb-naming")); return; }
        this.e = { target: d.target, kind: d.kind || "recommended" }; this.dialog = "edit";
      },
      close() { this.dialog = null; },
      // Column picker: [key, label] pairs matching the search, their shown state, and bulk show / hide
      colKeys(pairs) {
        const q = (this.colQuery || "").toLowerCase();
        return pairs.filter(([, label]) => !q || label.toLowerCase().includes(q)).map(([key]) => key);
      },
      colsState(keys) {
        const shown = keys.filter((k) => Alpine.store("wb").cols.includes(k)).length;
        return shown === 0 ? "none" : shown === keys.length ? "all" : "some";
      },
      colsSet(keys, on) { if (keys.length) postView({ action: "cols_set", keys: keys.join(","), on: on ? "1" : "" }); },
      selText() {
        const st = Alpine.store("wb"), names = { none: "None", all: "All", custom: "Custom", dup_newer: "Duplicates – newer",
                                                 dup_older: "Duplicates – older", dup_lessmeta: "Duplicates – less metadata" };
        return st.preset === "none" ? "None" : `${names[st.preset] || "Custom"} (${st.count})`;
      },
      // with no column chosen the table runs by path (then file name, the kept copy first in a duplicate set)
      sortKey() { return Alpine.store("wb").sort[0] || "path"; },
      sortDir() { return Alpine.store("wb").sort[0] ? Alpine.store("wb").sort[1] : "asc"; },
      sortText() {
        const w = Alpine.store("wb"), key = w.sort[0];
        if (!key) return "Path ↑";
        const col = w.colLabels.find((c) => c[0] === key);
        return `${col ? col[1] : w.sortLabel || key} ${w.sort[1] === "asc" ? "↑" : "↓"}`;
      },
      // Sort by picker: a column's first direction is high-first for numbers (as a header click)
      sortBy(key, dir) {
        const col = Alpine.store("wb").colLabels.find((c) => c[0] === key);
        postView({ action: "sort", key, dir: dir || (col && col[2] === "num" ? "desc" : "asc") });
      },
      // Action picker: the same requests the old buttons sent; plans answer with HX-Redirect
      runAction(what) {
        const ids = Alpine.store("wb").csv();
        const url = { commit: "/wb/commit", remove: "/wb/remove-plan", rename: "/wb/rename-plan" }[what];
        if (!url || !ids) return;
        htmx.ajax("POST", url, { source: document.body, swap: "none",
                                 values: what === "rename" ? { ids, write_xmp: "1" } : { ids } });
      },
      // edit column header: Apply saves its values as decisions, for the selection or else every row in the view
      applyEdit(target) {
        const ids = Alpine.store("wb").csv();
        htmx.ajax("POST", "/wb/commit", { source: document.body, swap: "none", values: ids ? { ids, target } : { all: "1", target } });
      },
      post: postView,
    }));

    // Data input: "Add new…" opens the folder dialog of the Mac running the server (POST /wb/sources/pick);
    // where there is none, the server answers with wb-pickpath and the page asks for a typed path.
    Alpine.data("dataInput", () => ({
      open: false,
      writable: false,
      addFolder() {
        htmx.ajax("POST", "/wb/sources/pick", { source: document.body, swap: "none", values: { writable: this.writable ? "1" : "" } });
      },
      typePath() {
        const path = window.prompt("Folder to add (full path):", "/Volumes/");
        if (path) htmx.ajax("POST", "/wb/sources", { source: document.body, swap: "none", values: { path, writable: this.writable ? "1" : "" } });
      },
    }));

    // Naming-pattern dialog: tokens as chips (click: select, then replace / remove), text between them editable,
    // a tag from the list inserts at the text cursor; the preview line shows the values; presets in the library.
    Alpine.data("namer", (labels = {}) => ({
      parts: [], picking: -1, q: "", at: { i: 0, pos: 0 }, vals: {}, optVals: {}, name: "", file: "",  // picking: the selected parameter (-1: none)
      presets: [], preset: "", loaded: "", renaming: null, renameTo: "", adding: false, newName: "", err: "",
      init() {
        window.addEventListener("wb-naming", () => this.start());
        this.$watch("parts", () => { clearTimeout(this.t); this.t = setTimeout(() => this.refresh(), 250); }, { deep: true });
      },
      pattern() { return this.parts.map((p) => p.v).join(""); },
      parse(pattern) {
        const out = [], re = /\{[^}]+\}/g; let last = 0, m;
        while ((m = re.exec(pattern))) { out.push({ t: "text", v: pattern.slice(last, m.index) }, { t: "tok", v: m[0] }); last = re.lastIndex; }
        out.push({ t: "text", v: pattern.slice(last) });
        return out;
      },
      start() {
        const current = document.querySelector("#wb-table")?.dataset.pattern || "";
        this.parts = this.parse(current); this.picking = -1; this.q = ""; this.err = ""; this.adding = false;
        this.loadPresets().then(() => {  // mark the preset this pattern comes from, if any
          const p = this.presets.find((x) => x.pattern === current); this.preset = p ? p.name : ""; this.loaded = p ? p.pattern : "";
        });
        this.refresh(true);
      },
      load(p) { this.parts = this.parse(p.pattern); this.preset = p.name; this.loaded = p.pattern; this.err = ""; },
      activePreset() { return this.presets.find((p) => p.name === this.preset) || null; },
      modified() { return !!this.preset && this.pattern() !== this.loaded; },
      // chip text: a column tag by its key (EXIF:DateTimeOriginal, file_date…), a recommended value by its name
      techName(tok) {
        const tag = /^\{tag:(?:exif:)?([^|}]+)/.exec(tok);
        if (tag) return tag[1];
        return labels[tok] ? `<${labels[tok]}>` : tok.slice(1, -1);  // unknown tokens as typed
      },
      label(tok) { return labels[tok] || tok; },
      pretty(pattern) { return pattern.replace(/\{[^}]+\}/g, (t) => this.techName(t)); },  // a pattern as its chips read
      caret(i, ev) { this.at = { i, pos: ev.target.selectionStart ?? ev.target.value.length }; },
      focusEnd() { const ins = this.$el.querySelectorAll(".namer__text"); ins[ins.length - 1]?.focus(); },
      // the line as one row of stops: text fields (caret) and parameters (selected, no caret); ← → walk through it
      field(i) { return this.$root.querySelector(`.namer__edit [data-i="${i}"]`); },
      selectTok(i) {
        this.picking = i; this.q = "";
        this.$nextTick(() => {
          this.field(i)?.focus();
          this.$root.querySelector(".namer__opt.is-current")?.scrollIntoView({ block: "nearest" });  // its tag in the list
        });
      },
      textAt(i, pos) {  // put the caret into text part i (pos -1 = its end)
        this.picking = -1;
        this.$nextTick(() => {
          const el = this.field(i); if (!el) return;
          const at = pos < 0 ? el.value.length : Math.min(pos, el.value.length);
          el.focus(); el.setSelectionRange(at, at); this.at = { i, pos: at };
        });
      },
      leaveTok(i, dir) { if (i >= 0) this.textAt(i + dir, dir < 0 ? -1 : 0); },
      // ↑ ↓ on a selected parameter: the previous / next tag of the list (as filtered) takes its place, live
      stepTok(i, dir) {
        const opts = [...this.$root.querySelectorAll(".namer__opt")].filter((b) => b.offsetParent !== null);
        if (!opts.length) return;
        const at = opts.findIndex((b) => b.dataset.tok === this.parts[i].v);
        const next = opts[at < 0 ? (dir > 0 ? 0 : opts.length - 1) : Math.max(0, Math.min(opts.length - 1, at + dir))];
        this.parts[i].v = next.dataset.tok; this.picking = i;
        this.$nextTick(() => next.scrollIntoView({ block: "nearest" }));
      },
      textKey(i, ev) {
        const el = ev.target, atStart = el.selectionStart === 0 && el.selectionEnd === 0;
        const atEnd = el.selectionStart === el.value.length && el.selectionEnd === el.value.length;
        if (ev.key === "ArrowLeft" && atStart && i > 0) { ev.preventDefault(); this.selectTok(i - 1); }
        else if (ev.key === "ArrowRight" && atEnd && i < this.parts.length - 1) { ev.preventDefault(); this.selectTok(i + 1); }
        else if (ev.key === "Backspace" && atStart && i > 0) { ev.preventDefault(); this.selectTok(i - 1); }  // select first, then delete
        else if (ev.key === "Delete" && atEnd && i < this.parts.length - 1) { ev.preventDefault(); this.selectTok(i + 1); }
      },
      tokKey(i, ev) {
        if (ev.key === "ArrowLeft") { ev.preventDefault(); this.textAt(i - 1, -1); }
        else if (ev.key === "ArrowRight") { ev.preventDefault(); this.textAt(i + 1, 0); }
        else if (ev.key === "ArrowDown" || ev.key === "ArrowUp") { ev.preventDefault(); this.stepTok(i, ev.key === "ArrowDown" ? 1 : -1); }
        else if (ev.key === "Backspace" || ev.key === "Delete") { ev.preventDefault(); this.removeTok(); }
        else if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); this.leaveTok(i, 1); }
      },
      match(text) { const q = this.q.trim().toLowerCase().replace(/\s+/g, " "); return !q || text.includes(q); },  // the phrase as typed
      groupHas(g) { return [...this.$root.querySelectorAll(`.namer__opt[data-group="${CSS.escape(g)}"]`)].some((b) => this.match(b.dataset.q)); },
      anyMatch() { return [...this.$root.querySelectorAll(".namer__opt")].some((b) => this.match(b.dataset.q)); },
      pickFirst() { const b = [...this.$root.querySelectorAll(".namer__opt")].find((x) => this.match(x.dataset.q)); if (b) this.choose(b.dataset.tok); },
      choose(tok) {
        if (this.picking >= 0) { this.parts[this.picking].v = tok; this.selectTok(this.picking); return; }  // stays selected
        else {  // insert at the remembered text cursor
          const { i, pos } = this.at, p = this.parts[i] && this.parts[i].t === "text" ? this.parts[i] : this.parts[this.parts.length - 1];
          const idx = this.parts.indexOf(p), cut = Math.min(pos, p.v.length);
          this.parts.splice(idx, 1, { t: "text", v: p.v.slice(0, cut) }, { t: "tok", v: tok }, { t: "text", v: p.v.slice(cut) });
          this.at = { i: idx + 2, pos: 0 };  // the cursor moves after the new tag, so clicks add tags in order
        }
        this.picking = -1;
      },
      removeTok() {
        const i = this.picking;
        const join = (this.parts[i - 1]?.v || "").length;
        const merged = { t: "text", v: (this.parts[i - 1]?.v || "") + (this.parts[i + 1]?.v || "") };
        this.parts.splice(i - 1, 3, merged); this.textAt(i - 1, join);  // the caret where the parameter was
      },
      refresh(options = false) {  // options: also every tag's value for the list (once per opening)
        const body = new URLSearchParams({ pattern: this.pattern(), fid: String(Alpine.store("wb").active || ""), options: options ? "1" : "" });
        fetch("/wb/name-preview", { method: "POST", headers: { "X-Meta-Duplo-Token": token() }, body })
          .then((r) => r.json()).then((d) => {
            this.vals = d.values; this.name = d.name; this.file = d.file;
            if (d.options) this.optVals = d.options;
          });
      },
      optVal(tok) { return this.optVals[tok] ?? ""; },
      apply() {
        const t = document.querySelector("#wb-table");
        if (t) t.dataset.pattern = this.pattern();  // the table reloads inside this element, which stays
        postView({ action: "edit_set", target: "filename", pattern: this.pattern() }); this.close();
      },
      // presets (left sidebar): stored in the library (setting name_presets); "Default" is built in
      presetCall(data) {
        return fetch("/wb/presets", { method: "POST", headers: { "X-Meta-Duplo-Token": token() }, body: new URLSearchParams(data) })
          .then((r) => r.json()).then((d) => { this.presets = d.presets; });
      },
      loadPresets() { return fetch("/wb/presets").then((r) => r.json()).then((d) => { this.presets = d.presets; }); },
      nameError(name, except = "") {
        if (!name) return "Give the preset a name.";
        if (name !== except && this.presets.some((p) => p.name === name)) return `“${name}” exists already.`;
        return "";
      },
      startNew() { this.adding = true; this.newName = ""; this.err = ""; this.$nextTick(() => this.$refs.newname.focus()); },
      saveNew() {
        const name = this.newName.trim(); this.err = this.nameError(name);
        if (this.err) return;
        const pattern = this.pattern();
        this.presetCall({ action: "save", name, pattern }).then(() => { this.preset = name; this.loaded = pattern; this.adding = false; });
      },
      updatePreset() {
        const pattern = this.pattern();
        this.presetCall({ action: "save", name: this.preset, pattern }).then(() => { this.loaded = pattern; });
      },
      startRename(p) { this.renaming = p.name; this.renameTo = p.name; this.err = ""; },
      finishRename(p) {
        if (this.renaming !== p.name) return;  // Enter, then blur: only once
        const nn = this.renameTo.trim(); this.renaming = null;
        if (nn === p.name) return;
        this.err = this.nameError(nn, p.name);
        if (this.err) return;
        this.presetCall({ action: "rename", name: p.name, new: nn }).then(() => { if (this.preset === p.name) this.preset = nn; });
      },
      deletePreset(p) {
        if (!window.confirm(`Delete the preset “${p.name}”?`)) return;
        this.presetCall({ action: "delete", name: p.name }).then(() => { if (this.preset === p.name) { this.preset = ""; this.loaded = ""; } });
      },
    }));

    // Large viewer: the active file alone, or every copy of its duplicate set side by side.
    Alpine.data("lightbox", () => ({
      mode: "one",
      items: [],
      get current() { return this.items.find((i) => i.id === Alpine.store("wb").active) || this.items[0] || null; },
      init() {
        this.$watch("$store.wb.active", () => this.load());
        this.$watch("$store.wb.lightbox", (on) => { if (on) this.load(); });
      },
      load() {
        const id = Alpine.store("wb").active;
        if (!id || !Alpine.store("wb").lightbox) return;
        fetch(`/wb/mates/${id}`).then((r) => r.json()).then((d) => { this.items = d.items; });
      },
      // zoom and pan in "One": s = scale, (tx, ty) = image offset in the view; fit = the fit kept on resize (or null)
      nw: 0, nh: 0, s: 1, tx: 0, ty: 0, fit: "contain", dragging: false, p0: null,
      box() { const v = this.$refs.view; return v ? { w: v.clientWidth, h: v.clientHeight } : { w: 0, h: 0 }; },
      loaded(ev) {
        this.nw = ev.target.naturalWidth; this.nh = ev.target.naturalHeight;
        this.setFit(this.fit || "contain");
        // the view gets its size when the viewer shows (or the window resizes): re-apply the fit then
        const view = ev.target.parentNode;
        if (this.observed !== view) {
          this.ro?.disconnect(); this.observed = view;
          this.ro = new ResizeObserver(() => { if (this.fit) this.setFit(this.fit); }); this.ro.observe(view);
        }
      },
      setFit(fit) {
        const { w, h } = this.box();
        if (!this.nw || !w) { this.fit = fit; return; }  // hidden yet: the ResizeObserver applies it
        this.fit = fit;
        this.s = { contain: Math.min(w / this.nw, h / this.nh), width: w / this.nw, height: h / this.nh, actual: 1 }[fit];
        this.tx = (w - this.nw * this.s) / 2; this.ty = fit === "width" ? 0 : (h - this.nh * this.s) / 2;  // a tall image starts at its top
        this.clamp();
      },
      // keep the image on screen: centred along an axis where it is smaller than the view, edges never pulled inside
      clamp() {
        const { w, h } = this.box(), iw = this.nw * this.s, ih = this.nh * this.s;
        this.tx = iw <= w ? (w - iw) / 2 : Math.min(0, Math.max(w - iw, this.tx));
        this.ty = ih <= h ? (h - ih) / 2 : Math.min(0, Math.max(h - ih, this.ty));
      },
      zoomAt(f, cx, cy) {
        const s = Math.min(16, Math.max(0.02, this.s * f)), k = s / this.s;
        this.tx = cx - (cx - this.tx) * k; this.ty = cy - (cy - this.ty) * k; this.s = s; this.fit = null; this.clamp();
      },
      zoomBy(f) { const { w, h } = this.box(); this.zoomAt(f, w / 2, h / 2); },
      wheel(ev) {
        const r = this.$refs.view.getBoundingClientRect();
        this.zoomAt(Math.exp(-ev.deltaY * (ev.ctrlKey ? 0.01 : 0.002)), ev.clientX - r.left, ev.clientY - r.top);  // ctrl = trackpad pinch
      },
      down(ev) { if (ev.button !== 0) return; this.dragging = true; this.p0 = { x: ev.clientX - this.tx, y: ev.clientY - this.ty }; ev.currentTarget.setPointerCapture(ev.pointerId); },
      drag(ev) { if (!this.dragging) return; this.tx = ev.clientX - this.p0.x; this.ty = ev.clientY - this.p0.y; this.fit = null; this.clamp(); },
      up() { this.dragging = false; },
      // double-click: 100 % at that point, or back to the whole image
      dbl(ev) {
        if (this.fit !== "contain") { this.setFit("contain"); return; }
        const r = this.$refs.view.getBoundingClientRect(); this.zoomAt(1 / this.s, ev.clientX - r.left, ev.clientY - r.top);
      },
      key(e) {
        if (this.mode !== "one" || !this.current || this.current.kind === "video") return false;
        const act = { "+": () => this.zoomBy(1.25), "=": () => this.zoomBy(1.25), "-": () => this.zoomBy(1 / 1.25),
                      0: () => this.setFit("contain"), 1: () => this.setFit("actual"),
                      w: () => this.setFit("width"), h: () => this.setFit("height") }[e.key.toLowerCase()];
        if (!act || e.metaKey || e.ctrlKey || e.altKey) return false;
        act(); return true;
      },
    }));

    Alpine.data("wbHead", (key, dir, first = "asc", slim = false) => ({
      post: postView,
      // a click sorts, a double-click collapses / expands: the click waits a moment so a double-click can cancel it
      clickTimer: null,
      clickSort() { clearTimeout(this.clickTimer); this.clickTimer = setTimeout(() => this.sort(), 240); },
      toggleSlim() { clearTimeout(this.clickTimer); postView({ action: "col_slim", key, on: slim ? "" : "1" }); },
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
