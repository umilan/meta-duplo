---
name: workbench-ui
description: The Workbench screen (shown as "Filomena") as implemented now, covering layout, header pickers, filter row, the grouped table and its menus, edit columns with source links, dialogs, preview panel, lightbox, keyboard model, selection store, undo history client, job stripe and toasts. Use it when rebuilding or changing anything on /workbench, so classes, data attributes, Alpine components, events and keys match the current app.
---

# Workbench UI (Filomena)

Sources this describes (the code is the source of truth):
`meta_duplo/web/templates/workbench.html`, `partials/wb_table.html` (filter row + table head),
`partials/wb_groups.html` (group and file rows, lazy pages), `partials/wb_preview.html`, `partials/job.html`,
`partials/link_icon.svg`, `base.html`, `meta_duplo/web/static/js/workbench.js`, `static/css/components.css`
(section "workbench"), with server state in `meta_duplo/web/app.py` (`/wb/*`) and `meta_duplo/web/workbench.py`.
Visual tokens and generic components are in `rebuild/12-design-system/SKILL.md`.

On screen the page is **Filomena** (h1, nav item with a `chip chip--exp` "beta", `<title>Filomena · meta-duplo</title>`,
settings section). Code, URL (`/workbench`, also `/`) and docs say Workbench.

## 1. Architecture in one paragraph

All view state (columns + widths, order, pinned, slim, sort, group, filters, search `q`, edit rules, reference rows,
sources off, colours) lives on the server in the library setting `workbench_view`. Every change is a
`POST /wb/view` with `action=…` (form-encoded, header `X-Meta-Duplo-Token`). The server answers `204` with
`HX-Trigger: wb-reload` (unless only a width changed) and `X-Wb-History: <action>` when the view changed. `#wb-table`
listens with `hx-get="/wb/table" hx-trigger="wb-reload from:body" hx-swap="innerHTML" hx-disinherit="*"` and
re-renders `partials/wb_table.html`. Grouping, source toggles and folder changes call `location.reload()` because
they render outside the table. The selection (ticked rows) exists only in the page (`Alpine.store("wb")`).
Scripts: `theme.js` (sync, in head), `htmx.min.js` (defer), `workbench.js` (defer, registers on `alpine:init`),
`alpine.min.js` (defer, last). `<body hx-boost="true" hx-headers='{"X-Meta-Duplo-Token": "…"}'>`.

## 2. Page layout

```
.app.app--nonav            (nav hidden by default on /workbench; ☰ toggles `toggle-nav`)
└ main.main.main--flush    (no padding, no max-width)
  ├ #job                   (job stripe partial while a job runs)
  └ .wb[x-data=wbPage]     (grid; .wb--panel adds a column var(--panel-w, 336px))
    ├ .wb-main
    │ ├ .wbbar.wb-pad      header row 1
    │ └ #wb-table[data-pattern]
    │   ├ .filters.wb-pad  header row 2 (filter chips, undo/redo, Selection, Actions)
    │   └ .wb-scroll > table.wbt   (or .empty)
    ├ .lightbox[x-data=lightbox]
    ├ aside.wb-panel.results-panel   (preview, x-show="panel")
    ├ .wbsheet[role=menu]  page-level column / cell menu (x-show="menu")
    └ .modal               all dialogs (x-show="dialog")
```

`.wb-pad` = `padding-inline: 16px`. In `.main--flush` the `.wb` gap is 0, `.wb-scroll` loses inline borders and radius,
the panel is `border-radius: 0; border-top: 0; border-right: 0; top: var(--job-h, 0px)`. Under 1088px `.wb--panel`
collapses to one column.

### 2.1 Header row 1 — `.wbbar`

`display:flex; gap:16px; padding-top:16px; margin-bottom:16px; container: wbbar / inline-size; position:relative; z-index:35`
(containment makes a stacking context; z 35 keeps its sheets above the sticky table head).

| Order | Element | Details |
|---|---|---|
| 1 | `button.navtoggle` | inline SVG sidebar icon; `$dispatch('toggle-nav')`; base `.app` toggles `.app--nonav` |
| 2 | `h1.wb-title` | "Filomena", `white-space:nowrap`, `flex:none` |
| 3 | `span.wbbar__label#datain-label` "Data input" + `div.pick.datain[x-data=dataInput]` | see 2.2 |
| 4 | "Grouping by" `.pick` > `button.selectbtn` | text = `group_names[view.group]`; class `is-grouping` when group ≠ `none` (background `--group-bg`, the beige of group rows). Sheet items `role=menuitemradio`: None, Duplicates, Photo (all copies), Path / Folders, Day, Device, Place, Author. Click: `post({action:'group', group})` then `location.reload()` |
| 5 | "Sort by" `.pick` > `button.selectbtn` | `x-text="sortText()"`: header label of the sorted column + ↑/↓; **"Path ↑"** when `sort[0]` is empty (title "Path, then file name; in a duplicate set the kept copy comes first"). Class `is-sorting` (2px `--sort-c` border) when a column is sorted. Sheet: section "Column" (a sorted column that is not shown is listed first, checked, by `sortLabel`), one radio per `$store.wb.colLabels` (`[key,label,kind]`, kind≠special); section "Order": Ascending ↑ / Descending ↓. `sortBy(key, dir)`: first direction is `desc` for `kind==='num'`, else `asc`; keeps current direction when re-picking the same column |
| 6 | `div.searchfield` | `x-data='{ q, open }'`, `:class="(open||q) && 'is-open'"`. `button.searchfield__btn` (magnifier SVG), `input[type=search][x-ref=q]` placeholder "Search…" (path, filename, new filename), `@input.debounce.400ms="post({action:'q', q})"`, Esc closes when empty; `button.searchfield__clear` × (x-show q) posts `q=''` |
| 7 | `{{ self.topbar() }}` | the ⚙ `.topbar` > `button.setbtn` + `.setsheet` (Section 9) as last item |

Responsive (container queries on `wbbar`):
- `≤1240px`: `.searchfield` collapses to the 32px magnifier button (bordered, clickable) unless `.is-open` (then 240px wide); `.selectbtn` min-width 128px (normally 176px).
- `≤1040px`: `.wbbar__label` visually hidden (1px clip, still read by screen readers).
- `.pick.datain` is its own container: `≤208px` hides `.datain__count`.

### 2.2 Data input picker (`x-data="dataInput"`, state `open`, `writable`)

- Field `button.datain__field` (full width, 32px, border `--line-strong`, accent on hover/expanded):
  `span.datain__folders` "N folders" or "N of M folders" (when some are unticked), `span.muted.datain__count` "Found N files" (counts only ticked sources). Both ellipsize.
- Sheet `div.wbsheet.pick__sheet.datain__sheet` (`width: max(100%, 560px); max-width: calc(100vw - 32px)`):
  - `.datain__top`: `button.datain__addbtn` "+ Add new…" (`span.datain__plus`; bold, uppercase, accent) → `addFolder()` = `htmx.ajax POST /wb/sources/pick {writable}`. On macOS the server runs `osascript choose folder`; if no dialog is available it answers `HX-Trigger: {"wb-pickpath": {...}}` and `@wb-pickpath.window="typePath()"` shows `window.prompt("Folder to add (full path):", "/Volumes/")` → `POST /wb/sources`.
    `button.datain__removeall` "Remove all" (bold uppercase, `--conflict`; only when sources exist) `hx-post=/wb/sources/remove-all hx-confirm=…`.
  - One `div.datain__src` per source (grid `minmax(0,1fr) auto auto`, title = root): label with checkbox (checked when not in `sources_off`; change → `post({action:'source_toggle', id}).then(location.reload)`), `span.datain__path` (mono), `small muted` "n files · reference|writable|read-only", `button.linkbtn.datain__remove` "Remove" (uppercase, red on hover; `hx-post=/wb/sources/{id}/remove`, `hx-confirm`). Empty: "No folders yet."
  - `button.datain__demo` "Use demo folder" (`hx-post=/wb/sources/demo`).
  - `label.datain__writable` checkbox `x-model="writable"` "Add new folders as writable" + `pick__hint`.
- Server refusals arrive as toasts (folder used by a plan, folder not found).

### 2.3 Header row 2 — `.filters.wb-pad` (inside `#wb-table`, re-rendered with the table)

Its `x-init` seeds the store: `total = nrows`, `cols` (shown keys), `colLabels`, `sort`, `sortLabel`.

| Element | Details |
|---|---|
| `span.filters__label` | "Applied filters:" |
| `span.fchip` per filter | `button.fchip__edit` text `Label: value` (op `equals` → just value; no-value ops → op label; else `op value`), click → `openFilter({key, value, index}, op)` (edit in place); `button` × → `post({action:'filter_remove', index})`. Chip: 32px, `border: 2px solid var(--filter-c)`, `--surface-2`, weight 600 |
| `button.btn.btn--icon` "+" | `openFilter({key: cols[1].key or 'path'})` |
| `span.results-toolbar__spacer` | flex:1 |
| `span.histbtns[role=group]` | two `btn btn--icon` with inline SVG arrows: Undo (`:disabled="!$store.wb.undoN"`, title "Undo (⌘Z): selection, sorting, filters, columns, edits, reference row") and Redo (`!$store.wb.redoN`, "Redo (⇧⌘Z)"); call `$store.wb.step('undo'|'redo')`. Disabled opacity .4 |
| "Selection" `.pick.pick--right` | `button.selectbtn` `:class="$store.wb.count && 'is-active'"` (accent border/text, 600), `x-text="selText()"`: "None" or "<Preset name> (count)". Sheet `.wbsheet.pick__sheet.pick__sheet--wide` (400px), `menuitemradio` + `pick__hint`: None; All `(total)`; Custom `(count)` (disabled unless preset is custom); Duplicates – newer; Duplicates – older; Duplicates – less metadata. Click → `$store.wb.applyPreset(key)` |
| "Actions" `.pick.pick--right` | `button.selectbtn` "Choose…". Sheet: `wbsheet__section` "N selected" / "Select rows first"; Delete… (`runAction('remove')` → `/wb/remove-plan`), Rename… (`runAction('rename')` → `/wb/rename-plan` with `write_xmp=1`) — both disabled without selection; section "Change": File name pattern… (`openEdit({target:'filename'})`), Date… / Place… / Device… (`openEdit({target, kind:'recommended'})`), Save edits (`runAction('commit')` → `/wb/commit`, needs selection). Plans answer `HX-Redirect: /plans/{id}`; empty results answer a toast |

All `.pick` sheets: `x-data="{open:false}"`, `@click.outside` and `@keydown.escape.window` close, `x-show="open" x-cloak x-transition.opacity`, `aria-haspopup="menu"`, `:aria-expanded`, `aria-labelledby` pointing at the `.wbbar__label`.

## 3. The table — `.wb-scroll > table.wbt`

`.wb-scroll`: `overflow:auto; min-height:256px; border; radius; background --surface`; its `max-height` is set by
`fitTable()` (Section 13). `@scroll.passive="menu = null"` closes the column/cell menu.

`<table class="wbt [wbt--colors] [wbt--tree]" style="width: {table_width}px">` — `table-layout:fixed;
border-collapse:separate; border-spacing:0; font 12/16`. `table_width = 40 + 40 + Σ widths (+ 40 tree)`.

```html
<colgroup>
  <col class="wbt__c-check">                      <!-- 40px -->
  <col data-key="__group" style="width:40px">     <!-- only when grouped -->
  <col data-key="{key}" style="width:{w}px">      <!-- per column; widths are user data -->
  <col class="wbt__c-add">                        <!-- 40px "+" column -->
</colgroup>
```

### 3.1 Header cells

- `th.wbt__check`: checkbox "Select all rows in the table", `:checked="$store.wb.all"`, `x-effect` sets `indeterminate` when `0 < count` and not all; change → `$store.wb.selectAll(checked)`.
- `th.wbh-cell.wbh-cell--tree.wbt__tree` (grouped only, title "Grouped by …"): `button.wbh__act.wbh__fold` with `span.chev` (`chev--open` rotates 90°) → `$store.wb.expandAll(!expanded)`.
- Data columns: `th.wbh-cell[.wbh-cell--edit][.is-slim]` with attributes
  `data-key`, `data-label`, `data-target` (edit target of this field; empty for filename), `data-tip` ("Label (empty) — double-click to expand|collapse"), `data-pin` (pinned), `data-filtered`, `data-catstart` (first column of a new picker category), `aria-sort` (sorted), and `x-data="wbHead(key, currentDir, firstDir, slim)"` (`firstDir` = `desc` for num). `@dragover.prevent="over($event)" @drop.prevent="drop($event)"`.
  Inside `div.wbh[.wbh--sortable]` (height `control-h + 8px` = 40px; `@click="clickSort()"` except thumb; `@dblclick="toggleSlim()"`):

| Child | Role |
|---|---|
| `span.wbh__label[draggable=true]` (`.is-sorted`) | name with `<wbr>` before CamelCase humps and " (" (Jinja filter `wbr`); 2-line clamp; `@dragstart` |
| `span.wbh__abbr` | acronym (≤3 letters, e.g. OTD), shown only when slim |
| `span.srclink.srclink--head` | edit columns: link icon + `(n)` distinct sources; `data-srctip` legend "n sources: A = …; B = …" |
| `span.wbh__pin` | pin SVG when pinned (not thumb) |
| `span.keybadge` "!" | key capture-date fields (red circle) |
| `span.wbh__filter[role=img]` | funnel SVG in `--filter-c`; title lists the column's filters |
| `span.wbh__sort` (`.is-on`) | ↑/↓; visible on hover (direction a click gives) or always when sorted (cyan, bold) |
| `button.wbh__act` ⋯ | `visibility:hidden` until `.wbh:hover`, focus-visible or `.is-open`; `openMenu({key,label,target,groupable,edit,pinned}, $el)` |
| `span.wbh__resize` | 8px grab strip at right edge, `@mousedown.stop.prevent="resize($event)"`; 1px `--line` border, 2px accent on hover / while resizing |

- `th.wbt__add`: `btn btn--small btn--icon` "+" → `$dispatch('wb-addcol')`.
- Thead cells: `position:sticky; top:0; z-index:2; background:--surface-2; padding:0; overflow:visible`; checkbox/tree/pinned header cells z 3.

### 3.2 Body (`partials/wb_groups.html`)

One `tbody.wbg[.wbg--singles]` per group, `x-data="{ open: $store.wb.expanded }"` (`false` for the "No duplicates"
singles group), `@wb-fold.window="open = $event.detail"`.

**Group row** `tr.wbg__head` (absent with grouping None), `@click="open = !open"`:
- `td.wbt__check` (`@click.stop`) checkbox "Select group": `setMany(ids, checked)`, checked when every id is selected.
- `td.wbt__tree.wbg__exp` `span.chev` (title "label (n)").
- per column: thumb → empty; the tree's `name_col` (filename for dups/photo, path for folder, date for day, device/place/author) → `td.wbg__label` "Label <span.muted>(n)</span>"; singles with dup_count/dup_conf/checks → `td.nil` "–"; `dup_count` → "n pcs"; `dup_conf` → score only; edit columns → `td.wbt__edit` with `span.wbt__ev` "→ value" or `.nil` "—" plus a `span.srclink` of the reference row (both **dimmed to .5 opacity**); others → `td.wbg__sum.wbg__sum--same|--diff|--na` "Same / Diff / N/A".
- Group cells: `background: --group-bg` (beige), weight 400 (label 600), `border-top: 1px --line`, pointer.

**File row** `tr.wbr[.wbr--userref][data-rowid]`, `x-show="open"`, `:class="{ 'wbr--selected': has(id), 'wbr--active': active === id }"`,
`hx-get="/wb/preview/{id}" hx-target="#wb-preview" hx-trigger="click, wb-show"`, `@click="$store.wb.active = id; $dispatch('wb-preview')"`.
- `td.wbt__check` (`@click.stop`): checkbox; change → `toggle(id)`; when ticked also sets `active`, triggers `wb-show` on the row and dispatches `wb-preview` (opens panel).
- `td.wbt__tree[.is-userref]`: "└", or "★" (accent) for a user-chosen reference row.
- Cells (each may carry `data-sorted`, `data-filtered`, `data-catstart`):

| Column | Cell |
|---|---|
| `thumb` | `td.wbt__thumbcell` > `img.wbt__thumb src=/thumb/{id} loading=lazy` (4:3, height `--thumb-h`) |
| `path` | `td.wbt__path` segments `<span class="is-same|is-diff">seg</span> / …` (no class when nothing to compare) |
| `filename` | `.is-diff` if differs from reference; `.is-same` in multi-row comparable groups |
| `dup_count` | "n pcs" |
| `dup_conf` | "98 %" + `span.conf-why` "· date differs, renamed" (grey) |
| `checks` | `div.checks` of `span.check.check--ok|--no|--na` > `span.check__name` + `span.check__n` (only counts below 208px via container query); `td.nil` "–" when not comparable |
| edit (`edit:*`) | `td.wbt__edit[.wbt__edit--same]` > `span.wbt__ev` "= v" (unchanged, muted) / "→ v" / `.nil` "—" + `button.srclink` (`data-srctip`, `data-srckey`, `data-srcrow`, `data-typed`) |
| `size` | `td.col-num[.is-diff]` formatted size |
| other | `[.is-diff]` value or `span.nil` "—"; plus `button.srclink.srclink--origin` when values were copied from this cell (`data-srckey="edit:{target}"`, `data-srcrow`, `data-target`, tooltip "Source A of New date: copied to n files") |

"Nothing to compare" (`nocmp`) = singles group or grouping None: no red/green, checks "–".
Last cell of each row is an empty `td` under "+".

**Lazy pages**: 60 groups per chunk (`WB_PAGE`); `tbody.more hx-get="/wb/more?offset=N" hx-trigger="intersect once" hx-swap="outerHTML"` with "Loading more groups…". New groups follow `$store.wb.expanded`.

**Empty**: `.empty` "No rows match the filters." / "No duplicates. Run a scan from Sources, or group by photo or folder…" / "No files yet. Run a scan from Sources."

### 3.3 Colours and states in the table

| State | Rule |
|---|---|
| Differs from reference | `.wbt--colors .is-diff { color: --differ }` (red); empty differing values `color-mix(--differ 40%, --surface)` |
| Same | `.wbt--colors .is-same { color: --same }` (green); group summaries `.wbg__sum--same/--diff`, muted when colours off |
| Colours off | table loses `wbt--colors` (settings sheet "Text colours" → `wb-colors` → `post({action:'colors'})`) |
| Empty value | `.nil`, `.wbg__sum--na`: `color-mix(--muted 56%, --surface)` |
| Sorted column | `td[data-sorted]` weight 600; `th[aria-sort]` and cells: `--edge: --sort-c` (cyan) → `box-shadow: inset 2px 0 0, inset -2px 0 0` (+ top on header, + bottom on the last row of the last tbody) |
| Filtered column | `[data-filtered]` `--edge: --filter-c` (orange), same outline |
| Both | `box-shadow:none` and `::after` overlay with two 2px `repeating-linear-gradient` edges, orange/cyan alternating every 8px (header also gets a dashed top) |
| Category divider | `:root[data-borders] .wbt [data-catstart] { border-left: 2px solid --line-strong }` |
| Hover | row/column/cell tints are `background-image` overlays (`--tint`, cell `--tint-cell` + 1px inset accent 48%): work over purple and beige. Column hover: JS adds `.is-hcol` to every cell of that column index (not checkbox / last column) |
| Selected row | `.wbr--selected > td { background-color: --sel }` |
| Previewed row | `.wbr--active > td { background-color: --sel-active }` |
| Cursor | `tr.is-cursor-row > td` tint; `td.is-cursor { box-shadow: inset 0 0 0 2px --accent }` |
| Edit column | `th.wbh-cell--edit` & `.wbh` bg `--edit-bg`, label `--edit-text`; `td.wbt__edit` bg `--edit-bg`, text `--edit-text`; `--same` variant muted |
| Group rows | `--group-bg`; edit values in them `opacity:.5` |
| Zebra / borders | from `<html data-zebra|data-borders>` (design system file): zebra shades odd `.wbr` rows (`nth-child(odd of .wbr)`), borders add `td + td { border-left: 1px --line }`; borders off makes bottom borders transparent |
| Sticky left | `.wbt__check { position:sticky; left:0; z-index:1; box-shadow: inset -1px 0 0 --line }`; `.wbt__tree { left:40px }`; `.wbt__pin` left set by JS; `.wbt__pin--last` gets `inset -2px 0 0 --line-strong`; these cells get an opaque `--surface` via `:where()` so row colours still win |

### 3.4 Pinned columns (`applyPins()`)

Server puts pinned keys (plus `thumb` while it is the first column) first and marks `th[data-pin]`. JS sums the
widths of leading `.wbt__check, .wbt__tree` header cells, then for each pinned header (in order) adds
`.wbt__pin` (+ `.wbt__pin--last` on the last) and `style.left = Npx` to every cell of that column (skips
`colSpan>1`). Runs on `DOMContentLoaded` (timeout 0), `htmx:afterSettle`, `resize`, and `mouseup` (rAF, after a
column resize). Pin/unpin: header menu → `post({action:'col_pin', key, on:'1'|''})`.

### 3.5 Slim columns

A column with no value in any row is slim unless the view says otherwise (`view.slim[key]`). Slim = width 40px,
`th.is-slim`: every `.wbh` child hidden except `.wbh__abbr` and `.keybadge`, flex-column centered, no resize, no ⋯.
**Instant tooltip**: `mouseover` on `th[data-tip].is-slim` fills the single `div#wb-tip.wbtip` (position fixed,
z 70, inverted `--text`/`--bg`, 12/16, `pointer-events:none`) with `data-tip`, placed 4px above the header, clamped
8px inside the window. **Double-click** any header → `toggleSlim()` (cancels the pending sort click) →
`post({action:'col_slim', key, on})`. A single click waits 240ms (`clickTimer`) before sorting.

### 3.6 Resize, reorder, sort (`wbHead`)

- **Sort**: `sort(d)` → `post({action:'sort', key, dir})`; first click uses `first` (num → desc), then toggles.
- **Resize**: mousedown on `.wbh__resize`; width = `max(40, round((w0 + dx)/8)*8)`, live on the `<col>` and the table width; `body.is-resizing` (col-resize cursor, no text selection); on mouseup, if changed, `post({action:'col_width', key, width})` (server snaps 40–904 and does not reload).
- **Drag to reorder**: drag the label (`text/plain` = key, `effectAllowed=move`, `th.is-dragging` opacity .48). `over()` decides `side` (right half = after) and shows `div#wb-dropline.dropline` (fixed, 4px accent bar with 16px round ends, full height of `.wb-scroll`, z 45) and `th.is-droptarget` (tint). `drop()` → `post({action:'col_move', key: from, before})` (`before=""` = end). `dragend`/`drop`/dragover elsewhere hide the line.
- **Scroll keep**: `htmx:beforeSwap` on `#wb-table` stores `.wb-scroll.scrollLeft`; `htmx:afterSwap` restores it.

## 4. Header ⋯ menu (page-level `.wbsheet[role=menu]`)

One sheet for both menus, `x-show="menu"`, positioned by `:style="left: {x}px; top: {y}px"` where
`x = max(8, min(btn.right - 304, innerWidth - 312))`, `y = btn.bottom + 4`. Closed by `@mousedown.outside`,
`@keydown.escape.window`, `@resize.window`, table scroll, or clicking the same ⋯ again.

Column variant (`menu.cell` false):
1. `div.wbsheet__title.wbsheet__title--row`: label + `button.linkbtn.wbsheet__remove` "Remove" (not for edit columns) → `col_remove`.
2. Edit columns only: `div.wbsheet__acts[role=group]` with four `button.wbsheet__act`: **Apply** (`--primary`; `applyEdit(target)` → `htmx.ajax POST /wb/commit` with `{ids, target}` or `{all:'1', target}`), **Edit all** (`openEdit({target, kind:'recommended'})`), **Clear** (`edit_clear`), **Remove** (`--danger`, `edit_remove`).
3. `button.wbsheet__pinitem`: "Pin to the left" / "Unpin column".
4. Section "Sort by": "↑ Ascending (A → Z, old → new)", "↓ Descending (Z → A, new → old)".
5. Section "Find": "Filter by value…" (`openValues`), "Filter…" (`openFilter(menu)` op contains), "Search in this column…" (contains), "Show groups where it differs" (op `diff`).
6. Section "Change": "Edit these values… (new purple column)" (disabled without `menu.target`), "Group by this" when `groupable` (path→folder, date→day, device, place, dup_count/dup_conf→dups).

## 5. Cell ⋯ menu and right-click

- One floating `button#wb-cellbtn.cellbtn` "⋯" (24px, absolute right 4px, vertically centred) is appended to the hovered data cell (`mouseover`; not checkbox/tree/thumb columns, not `tbody.more`); removed on `mouseout` of the table. Cells holding it get `padding-right:32px`. Its click stops propagation and dispatches `wb-cellmenu {btn}`.
- **Right-click** on a `tr.wbr > td` (not thumb) → `preventDefault`, moves the button into the cell, dispatches `wb-cellmenu`.
- `openCellMenu(btn)` reads the cell text (minus button and srclink), `th.dataset.key/label/target`, `isName` (filename / edit:filename), `fid`, `isRef`. Same sheet, cell variant:
  1. `div.wbsheet__title` "Label: value" (— if empty).
  2. "Filter this column by this value" → `cell_filter` (adds `equals` or `empty`).
  3. Section "Change": "Edit this value…" or, for names, "Rename with a pattern…" (`openCellEdit()`; disabled when neither target nor name); "Remove this value" (always disabled: "Not yet: plans cannot clear a metadata value"); "Apply this value to its group" (`cell_apply scope=group`); "Apply this value to the selection (n)" (`scope=selection, ids=csv`; needs count); "Apply this value to all rows" (`scope=all`). Apply items need `menu.target`.
  4. Section "Compare": "Use this row as the reference of its group" / "Stop using this row as the reference" → `cell_ref`.
- `cellAct(action, extra)` closes the menu and posts `{action, fid, key, …extra}`.

## 6. Edit columns and source links

- Purple columns `edit:filename|date|place|device` ("New filename" etc.) appear only on demand, inserted right after their base column (or the cell's column for `cell_set` / `cell_apply`), width 304 (filename) or 176.
- Cell text: `= value` unchanged (muted), `→ value` change, `—` no data.
- **Source badges** `.srclink` (icon `partials/link_icon.svg` = `svg.srclink__icon` 16px + letter A, B, …; `*` after Z). Letters are assigned in table order per edit column. Inside cells: absolute at the right; `td:has(> .srclink)` padding-right 40px (64px with ⋯, which moves to right 40px). Header `srclink--head` sits after ⋯ (`order:2`, `cursor:help`).
- **Hover** (`mouseover` on `.wbt .srclink`): `#wb-tip` shows `data-srctip`; the column `th[data-key=srckey]` gets `.is-srccol` on all its cells (2px `--edit-text` side edges + tint) and `tr.wbr[data-rowid=srcrow]` gets `.is-srcrow` (2px top/bottom edges + tint; the intersection cell a full 2px frame on `--edit-bg`). Cleared when the pointer leaves the link.
- **Click** (capture phase, stops the row click): `data-typed` in a file row → dispatch `wb-celledit` (per-file dialog); otherwise `wb-edit {target, kind:'recommended'}` (rule dialog; a `srclink--origin` names its target in `data-target`).

## 7. Double-click behaviours

| Where | Result |
|---|---|
| Header (`.wbh`) | toggle slim (no sort) |
| `filename` data cell | clear text selection, dispatch `wb-edit {target:'filename'}` → naming dialog |
| Edit-column data cell (`th.wbh-cell--edit`) | clear selection, move ⋯ in, dispatch `wb-celledit` → cell edit dialog (New filename → naming dialog via `openCellEdit` when no target) |
| Lightbox image | 100 % at that point, or back to Fit |
| Naming preset name | rename inline (not built-ins) |

Double-clicks on buttons/inputs inside cells are ignored.

## 8. Dialogs (`.modal`, `x-show="dialog"`)

`.modal` (fixed, z 50, grid centred) > `.modal__backdrop` (click → `close()`) + one `.modal__box` per dialog
(440px max 92vw; `--wide` 1040px max 94vw; padding 24, gap 16, `--shadow-modal`). Esc anywhere closes
(`@keydown.escape.window="close()"`). `wbPage.dialog` ∈ `filter | edit | addcol | values | naming | celledit | null`.

| Dialog | Opened by | Content |
|---|---|---|
| **Filter** (`form.modal__box`, `hx-post=/wb/view hx-swap=none`) | `+`, chip click, header Filter…/Search…/differs, event `wb-filter` | h3 "Add filter" / "Edit filter"; hidden `action=filter_add`, `index`; select Column (catalog optgroups + "Edit columns"); select Condition (`OPS`: contains, is, is not, is empty, is not empty, ≥, ≤, differs in group, same in group); Value (hidden for empty/not_empty/diff/same, autofocused); hint "Grouped views keep a whole group when any of its rows matches; “No grouping” filters rows."; Cancel / "Apply filter" or "Save filter" |
| **Edit rule** (`form.modal__box--wide`, `action=edit_set`, `target`) | Actions → Date/Place/Device, header Edit…, Edit all, srclink, `wb-edit` | h3 "Edit: new date" etc.; radios `kind`: recommended, column (select of date sources / catalog fields), shift (date only: column, ±, hh, mm, ss), fixed (`YYYY-MM-DD HH:MM:SS` placeholder for date); `.rule-editor`, `.indent`, `.shift`. Actions: `btn--danger` "Remove edit column", spacer, Cancel, "Apply rule". (A filename branch with `.rule__tokens` chips exists in the template but `openEdit` routes filename to the naming dialog) |
| **Cell edit** | `openCellEdit()` | h3 "Edit {label}", hint "Only for this file…", text input prefilled without leading `=`/`→`, selected; Enter or "Preview the change" → `cell_set {fid, key, value}` |
| **Filter by value** | header "Filter by value…" | `.colpick-head` h3 "Filter by value: Label" + close ×; search input; `.valuelist` of `button.valuelist__item` (value or muted "(empty)", count right) from `GET /wb/values?key=` (most frequent first, ≤300); "Loading…"; pick → `filter_add` `equals` (or `empty`) |
| **Columns** (`--wide`) | "+" header cell, ⚙ "Columns…", `wb-addcol` | head: "n shown", `linkbtn` "Order like this list" (`cols_order`), "Select all"/"Select all found", "None"/"None of found" (`cols_set`), close ×; search "Search fields (e.g. Date, GPS, Artist, Lens)"; `.colcat` grid (auto-fill 224px) of `fieldset.colcat__group` with `legend.colcat__title` tri-state checkbox (all/some/none via `colsState`) and `label.colcat__item` checkboxes (`col_add`/`col_remove` immediately; `keybadge` on key fields); "Done" |
| **Naming pattern** (`.modal__box--wide.namer`, `x-data='namer(labels)'`) | Actions → File name pattern…, filename double-click, `wb-edit {target:'filename'}` (also dispatches `wb-naming`) | Summary: left `aside.namer__side` presets (Default built-in + saved, `GET/POST /wb/presets`), modified marker •, ✎/× on hover, "Update “name”", "+ Save as new preset"; right `.namer__main`: `.namer__preview` "Preview for file" + mono name, `.namer__edit` line of `input.namer__text` + `button.namer__tok` chips, red `.namer__warn` when not ending in `.{ext}`, hint, `.namer__picker` search + `.wbsheet.namer__list` of `button.namer__opt` grouped options with live values (`POST /wb/name-preview`). Apply → `edit_set target=filename pattern=…` and updates `#wb-table[data-pattern]`. Detailed in the naming-pattern skill |

## 9. Settings sheet on this page

`{% block topbar_place %}` is emptied and the page renders `{{ self.topbar() }}` inside `.wbbar` (`.wbbar .topbar
{ flex:none; margin:0 }`). Order inside `.setsheet`: section **Filomena** — checkbox "Preview panel"
(`:checked="$store.wb.panel"`, dispatches `wb-panel {on}`), `button.setsheet__item` "Columns…" (`wb-addcol`),
"Reset view" (`wb-reset` → `post({action:'reset'})`, keeps colours); then section **Appearance** — Light/Dark
`themeswitch`, "Text colours (same / different)" (page block `appearance_menu`, dispatches `wb-colors`), Cell borders,
Shade odd rows, Compact view, "All settings…" link. Details in the design-system skill.

## 10. Preview panel

`aside.wb-panel.results-panel[x-show=panel]` (`aria-labelledby="pv-heading"`): sticky, `max-height: calc(100vh - var(--job-h,0px))`, scrolls.
- `div.wb-panel__resize` (8px strip on the left edge, 2px accent border on hover) → `resizePanel()`: width `max(256, min(720, round(…/8)*8))` set as `--panel-w` on `.wb`, stored in `localStorage["meta-duplo-panel-w"]` (try/catch), restored in `wbPage.init`. Default 336px.
- `.pv-head` (sticky top, `--surface`): h2 "Preview" + close × (`panel=false`).
- `#wb-preview`: initially `.empty.small` "Click a row to see all of its metadata."; filled by `GET /wb/preview/{id}` (`wb_preview.html`):
  `button.preview__zoom` (title "Open large (the arrow keys keep moving in the table)", `cursor: zoom-in`) → `$store.wb.active = id; $store.wb.lightbox = true`; contains `img.preview__img` (`/media/{id}` for images, `/thumb/{id}` otherwise; max-height 256, contain) and `span.preview__zoomhint` "⤢ Open large" (pill, appears on hover/focus with image `brightness(.88)`).
  Then `h3.preview__name`, `.small.muted.path`, optional `.pv-section.pv-section--edit` "Edits (not applied yet)", sections Dates / Location / Device / Author / File as `dl.facts` (2 equal columns), "Duplicates (n)" `ul.pv-list` (`li.is-current` accent left border, ✓ on keeper), and "All metadata (n tags)" `dl.facts.facts--mono`.
- Opening: `wb-preview` (row click, tick) sets `panel = true`; `wb-panel {on}` sets it; `panel` is mirrored to `$store.wb.panel`.

## 11. Lightbox (`div.lightbox[x-data=lightbox]`, `role=dialog`, `aria-label="Large view"`)

`x-show="$store.wb.lightbox"`, fixed full window z 55; `.lightbox__backdrop` click closes; `.lightbox__box`
(`100vw/100vh − 32px`, grid rows auto / 1fr / auto).
- Head: name (strong), path (muted, ellipsis), close × "Close (Esc)".
- Items: `GET /wb/mates/{active}` → `items[]` `{id, name, path, kind, kept, conf}`; reloads when `$store.wb.active` changes (so it follows the table cursor) or when opened. `current` = active item or first.
- **One** (`.lightbox__one`): video → `<video controls>`; image → `.zoomview[x-ref=view]` (`cursor:grab`, `.is-dragging` grabbing, `touch-action:none`) with an `<img>` at natural width and `:style="width:${nw}px; transform: translate(tx,ty) scale(s)"` (`transform-origin:0 0`).
- **Duplicate set (n)** (`.lightbox__grid.lightbox__grid--1..4`, columns = min(n,4)): `figure` per copy (`.is-current` accent border), media + `figcaption` (✓ name, conf or "kept copy", path).
- Foot (grid 1fr auto 1fr): centre `.lightbox__modes.themeswitch[role=radiogroup]` One / Duplicate set (n); right `.lightbox__zoom` (One + image only): − / `span.lightbox__pct` "NN %" / + and a Fit `themeswitch`: Fit (`contain`, key 0), Width (W), Height (H), 100 % (`actual`, key 1).
- Zoom model: `s` clamped 0.02–16, `zoomAt(f, cx, cy)` keeps the point under the cursor, `fit=null` after manual zoom/pan; `clamp()` centres an axis smaller than the view and never pulls an edge inside. Wheel: factor `exp(-deltaY × (ctrlKey ? 0.01 : 0.002))` (ctrl = trackpad pinch). Pointer drag pans (pointer capture). `setFit('width')` starts a tall image at its top. A `ResizeObserver` re-applies the current fit when the view resizes.
- Keys (`key(e)`, only One + image, no modifiers): `+`/`=` zoom in ×1.25, `-` out, `0` fit, `1` 100 %, `w` width, `h` height. Esc closes. Arrow keys keep moving the table cursor (the lightbox is not a `.modal`).

## 12. Keyboard model (document `keydown` in workbench.js)

Ignored unless `#wb-table .wbt` exists; ignored when the target is inside `input, select, textarea, [contenteditable], .modal`.

| Key | Action |
|---|---|
| ⌘Z / Ctrl+Z | undo (`undoHistory.step('undo')`), works even with a menu open |
| ⇧⌘Z / Ctrl+Shift+Z / Ctrl+Y | redo |
| Esc (lightbox open) | close lightbox |
| `+ = - 0 1 W H` (lightbox open) | lightbox zoom keys |
| any key while a `.wbsheet`/`.setsheet` is visible | ignored here (the sheet's own Esc applies) |
| ⌘A / Ctrl+A | `store.selectAll(true)` (server preset `all`) |
| ↑ ↓ | move cursor between visible `tr.wbr` and `tr.wbg__head`; first press starts at row 0, column 3 |
| ← → | move between columns (clamped to `0 … cells-2`; last "+" cell excluded) |
| Shift+↑/↓ | tick current file row, move, tick the next (group rows passed, not ticked) |
| X | click the row checkbox (tick/untick; group row = whole group), move down |
| Enter | click the row checkbox |
| Alt/Ctrl/⌘+Enter | open the cell ⋯ menu (file rows, not thumb) |
| Space | group row: fold/unfold (`tr.click()`); file row: if the panel is open, close it (`wb-panel {on:false}`), else show this row's preview and open the panel |
| Esc | leave the cursor (removes `.is-cursor`) |

Cursor state `kb = {rowid, row, col}`; `paint()` sets `tr.is-cursor-row` + `td.is-cursor` and
`scrollIntoView({block:'nearest', inline:'nearest'})`. Clicking any body cell of `.wbr`/`.wbg__head` moves the cursor
there. When the panel or lightbox is open, moving to another file row calls `showPreview(tr)` (sets `active`,
triggers `wb-show`) debounced 120ms. After a `#wb-table` swap the cursor re-finds `tr[data-rowid]`.

## 13. Client state and helpers

### 13.1 `Alpine.store("wb")`

| Field / method | Meaning |
|---|---|
| `sel` (object id→true), `count` | selection (plain object for reliable reactivity in Safari) |
| `total`, `cols`, `colLabels`, `sort`, `sortLabel` | set by the table's `x-init` |
| `active` | row in preview / lightbox |
| `lightbox`, `panel`, `expanded` | viewer open, panel open (mirror), groups open |
| `preset` | `none | all | custom | dup_newer | dup_older | dup_lessmeta` |
| `undoN`, `redoN` | button enablement |
| `has/add/remove/toggle/setMany/clear/csv` | selection ops; `toggle`/`setMany` call `edited()` → preset `custom` (or `none` when empty) |
| `get all` | `total>0 && count>=total` |
| `load(which)` | `GET /wb/ids?which=` → replaces selection, preset = which (or none) |
| `applyPreset(p)`, `selectAll(on)` | none → clear; custom → no-op; others → `load` |
| `expandAll(on)` | sets `expanded`, dispatches `wb-fold` (detail = on) |
| `step(dir)` | `undoHistory.step` |

### 13.2 `undoHistory` (module-level object in workbench.js)

- `sessionStorage["meta-duplo-history"] = {undo:[…], redo:[…]}` (try/catch), MAX 100, `push` clears redo.
- Entries: `{t:'sel', before, after}` (JSON of sorted ids + preset) and `{t:'view', act}`.
- An `Alpine.effect` reads `selState()` on every selection change and, debounced 250ms, calls `selChanged(now)`: one step per burst.
- View steps are recorded from the `X-Wb-History` response header, both in `postView()` and on `htmx:afterRequest` (htmx forms).
- `step(dir)`: selection steps apply locally (`applySel` sets `snap` first so it is not re-recorded); view steps `POST /wb/history direction=…` → `{ok, label, reload}`; `!ok` → toast "Nothing to undo"; else toast "Undone: <label>" / "Redone: …" and `location.reload()` if `reload` (grouping, search, sources, colours changed) else `wb-reload`. `busy` guards double steps. Empty stack → toast "Nothing to undo|redo".
- Not in the history: saved decisions, plans, adding/removing folders.

### 13.3 `postView(data)`

`fetch("/wb/view", POST, urlencoded, X-Meta-Duplo-Token from body hx-headers)`; then records `X-Wb-History` and
triggers `wb-reload` on body if `HX-Trigger` contains it. Exposed as `post` in `wbPage` and `wbHead`.

### 13.4 `fitTable()`

`.main--flush #wb-table .wb-scroll` gets `style.maxHeight = max(256, innerHeight − box.top) px` so the table reaches the
window bottom. Runs on `resize`, `htmx:afterSettle`, `DOMContentLoaded` (timeout 0). CSS fallback
`max-height: calc(100vh - 176px)`.

### 13.5 Alpine components

| Name | Where | Purpose |
|---|---|---|
| `wbPage` | `.wb` | panel, dialogs (`dialog`, `f`, `e`, `vals`, `cellEdit`, `colQuery`), `menu`, `openMenu/openCellMenu/cellAct/openCellEdit/saveCellEdit/openValues/pickValue/openFilter/openEdit/close/colKeys/colsState/colsSet/selText/sortKey/sortDir/sortText/sortBy/runAction/applyEdit/resizePanel/post` |
| `dataInput` | `.pick.datain` | `open`, `writable`, `addFolder`, `typePath` |
| `wbHead(key, dir, first, slim)` | each data `th` | sort, slim, resize, drag/drop |
| `namer(labels)` | naming dialog | parts editor, presets, preview |
| `lightbox` | `.lightbox` | items, mode, zoom/pan |

### 13.6 Custom events

| Event | Dispatched by | Handled by |
|---|---|---|
| `wb-reload` | server `HX-Trigger`, `postView`, undo | `#wb-table` (`from:body`) |
| `wb-filter` {key,label,value,op,index} | (listener exists for external use) | `wbPage.openFilter` |
| `wb-edit` {target, kind} | filename dblclick, srclink click | `wbPage.openEdit` |
| `wb-celledit` {btn} | edit-cell dblclick, typed srclink | `openCellMenu` + `openCellEdit` |
| `wb-cellmenu` {btn} | `#wb-cellbtn` click, contextmenu, Alt/Ctrl/⌘+Enter | `openCellMenu` |
| `wb-naming` | `openEdit({target:'filename'})` | `namer.start()` |
| `wb-addcol` | "+" header, ⚙ Columns… | `dialog='addcol'` |
| `wb-preview` | row click/tick, Space | `panel = true` |
| `wb-panel` {on} | ⚙ Preview panel, Space | `panel = on` |
| `wb-reset`, `wb-colors` | ⚙ sheet | `post reset` / `post colors` |
| `wb-fold` (detail bool) | `expandAll` | every `tbody.wbg` |
| `wb-show` (htmx trigger on a row) | tick, cursor | row `hx-get` preview |
| `wb-pickpath` | server `HX-Trigger` | `dataInput.typePath` |
| `toggle-nav` | ☰ | `.app` |
| `toast` {value} | server `HX-Trigger {"toast": msg}`, `toast()` in JS | `<body x-data="{toast:''}">` |

## 14. Job stripe and toasts

- `#job` in `base.html` includes `partials/job.html` while a job runs. Running: `div.job[role=status]`
  `hx-get="/job?watch=1" hx-trigger="every 1s" hx-swap="outerHTML"`: `strong` name, `span.job__stage`, `<progress value max>` (indeterminate without total), `span.job__count` "done / total" (tabular nums), `button.job__cancel` "Cancel" (`hx-post=/job/cancel hx-target="closest .job"`) or `span.job__cancel.is-busy` "Cancelling…".
  CSS: `position:fixed; top:0; left:0; right:0; z-index:60; height:40px; padding 0 16px; gap 16px; background --job-bg; color --job-text`; the progress bar is `flex:1`, 8px, pill, track `--job-track`, value `--job-bar`. `body:has(#job > .job:not(.job--failed)) { --job-h: 40px; padding-top: var(--job-h) }`.
- Failed: `div.job.job--failed[role=alert]` static banner: 1px `--conflict` border, `--surface`, "Name failed." + mono message (≤300 chars); in `.main--flush` `#job` gets `8px 16px 0` padding only then.
- Toast: `div.toast[role=status]` at body end, `x-show="toast" x-text="toast"`, set by `@toast.window`, cleared after 3500ms; fixed bottom-right 24px, inverted (`--text` bg, `--bg` text), radius 8, `--shadow-pop`, z 60.

## 15. Accessibility and Safari notes

- Every icon-only button has `aria-label` and `title`; SVGs are `aria-hidden="true"`.
- Pickers: `aria-haspopup`, `:aria-expanded`, `aria-labelledby`; sheet items `role=menuitem` / `menuitemradio` with `aria-checked`. Segmented controls are `role=radiogroup` + `role=radio`.
- Table header has `aria-sort` on the sorted column; checkboxes have row-specific `aria-label`s; the funnel is `role=img aria-label="Filtered"`.
- Selection is a plain object (not `Set`) for Safari reactivity; storage calls are wrapped in try/catch; `CSS.escape` for attribute selectors; no build step.

## Rebuild checklist

1. Serve `/workbench` from `base.html` with `main--flush`, nav hidden by default, title block "Filomena"; load scripts in the order theme.js → htmx → workbench.js → Alpine (all but theme deferred).
2. Implement `POST /wb/view` with every action above, answering `204` + `HX-Trigger: wb-reload` + `X-Wb-History`; `/wb/table`, `/wb/more`, `/wb/values`, `/wb/ids`, `/wb/history`, `/wb/preview/{id}`, `/wb/mates/{id}`, `/wb/name-preview`, `/wb/presets`, `/wb/commit`, `/wb/rename-plan`, `/wb/remove-plan`, `/wb/sources*`, `/job`, `/job/cancel`.
3. Build header row 1 (`.wbbar`) with navtoggle, title, Data input, Grouping by, Sort by, Search, ⚙; add the container queries at 1240/1040/208px.
4. Build `#wb-table` with the filter row (chips, +, undo/redo, Selection, Actions) and the store-seeding `x-init`.
5. Render the table: colgroup widths, sticky header, `wbHead` per column, tree column, group rows, file rows, lazy `tbody.more`, empty states.
6. Add the colour/state CSS (diff/same, sorted cyan, filtered orange, dashed both, beige groups, purple edits, hover overlays, selection, cursor, category dividers).
7. Implement `applyPins`, `fitTable`, slim tooltip `#wb-tip`, drop line `#wb-dropline`, scrollLeft keep.
8. Implement the page-level menu sheet (column and cell variants), floating `#wb-cellbtn`, right-click, double-click handlers, srclink hover/click.
9. Implement the six dialogs inside one `.modal`.
10. Implement preview panel (resize + localStorage) and the lightbox (modes, zoom/pan/fit, keys).
11. Implement the store, `undoHistory` (sessionStorage), keyboard handler and toasts; job stripe partial with polling and Cancel.
12. Run the Verify section in both themes and in compact density, in Chromium and WebKit.

## Verify

Setup: `uv sync --extra geo && uv run meta-duplo demo && uv run meta-duplo serve`; open the printed link (it sets the
session cookie from `?token=`). Run Playwright against that URL with Chromium and WebKit.

Manual checks:
- Header shows ☰, "Filomena", Data input "1 folder · Found N files", Grouping by "Duplicates" on beige, Sort by "Path ↑", search, ⚙. Narrow the window below 1240px: search becomes a magnifier; below 1040px labels vanish but `aria-labelledby` still names the buttons.
- The default chip "Dup.: ≥ 2" (label from the field catalog) has a 2px orange border; its column has an orange outline and funnel.
- Click a header label: after ~240ms the column turns cyan-outlined, Sort by reads "<Label> ↑/↓" with a cyan border; sort and filter the same column → dashed orange/cyan edges.
- Double-click a header: it becomes 40px with an acronym; hover shows the tooltip immediately above it.
- Drag a header label: the 4px accent drop line with round ends spans the table; drop moves the column; widths snap to 8px when resizing.
- Pin a column from ⋯: it gets the pin icon, sticks left when scrolling horizontally, and a 2px line marks the pinned block's end.
- Right-click a date cell → "Apply this value to its group": a purple "New date" column appears right of Date with `🔗A` badges; hovering a badge outlines the source column/row; the header shows `🔗(n)`; group rows show it dimmed.
- Double-click a New date cell → cell edit dialog prefilled without `→`; double-click a file name → Naming pattern dialog.
- Arrow keys show the 2px accent cursor; Shift+↓ ticks rows; X toggles and moves; ⌘A selects all ("All (n)"); ⌘Z then ⇧⌘Z show toasts "Undone: selection" / "Redone: selection"; undoing a sort says "Undone: sorting".
- Space opens the preview; drag its left edge (256–720px); reload keeps the width. Click the image → lightbox; +, −, 0, 1, W, H work; Esc closes.
- Start a scan from Sources: the 40px inverted stripe appears at the top with Cancel; Cancel shows "Cancelling…".

Playwright (expected results in comments):
```js
await page.goto(url);                                     // url with ?token=
await expect(page.locator('h1.wb-title')).toHaveText('Filomena');
await expect(page.locator('.selectbtn.is-grouping')).toHaveCSS('background-color', 'rgb(245, 245, 220)'); // light theme
await page.locator('th.wbh-cell[data-key="date"] .wbh__label').click();
await page.waitForResponse(r => r.url().endsWith('/wb/table'));
await expect(page.locator('th.wbh-cell[data-key="date"]')).toHaveAttribute('aria-sort', /ascending|descending/);
await expect(page.locator('.selectbtn.is-sorting')).toBeVisible();
await page.locator('th.wbh-cell[data-key="date"] .wbh').hover();
await page.locator('th.wbh-cell[data-key="date"] .wbh__act').click();
await expect(page.locator('.wbsheet[role=menu]').getByText('Pin to the left')).toBeVisible();
await page.keyboard.press('Escape');
await page.keyboard.press('ArrowDown');
await expect(page.locator('td.is-cursor')).toHaveCount(1);
await page.keyboard.press('Meta+a');                      // Control+a on Linux
await expect(page.locator('.selectbtn.is-active')).toContainText('All (');
await page.keyboard.press('Meta+z');
await expect(page.locator('.toast')).toContainText('Undone');
expect(await page.evaluate(() => JSON.parse(sessionStorage.getItem('meta-duplo-history')).redo.length)).toBeGreaterThan(0);
await page.locator('tr.wbr').first().click();
await expect(page.locator('aside.wb-panel')).toBeVisible();
await expect(page.locator('#wb-preview .preview__name')).not.toBeEmpty();
await page.locator('.preview__zoom').click();
await expect(page.locator('.lightbox')).toBeVisible();
await page.keyboard.press('1');
await expect(page.locator('.lightbox__pct')).toHaveText('100 %');
await page.keyboard.press('Escape');
await expect(page.locator('.lightbox')).toBeHidden();
const fit = await page.locator('.wb-scroll').evaluate(el => [parseInt(el.style.maxHeight, 10), Math.round(el.getBoundingClientRect().top)]);
expect(Math.abs(fit[0] - Math.max(256, page.viewportSize().height - fit[1]))).toBeLessThanOrEqual(1);   // fitTable: max-height reaches the window bottom
```
