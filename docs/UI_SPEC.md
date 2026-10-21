# UI specification (merged: meta-duplo + duplicate finder)

Server-rendered HTML (Jinja2), progressively enhanced with **htmx** and **Alpine.js**, styled
with **plain CSS** you own (ADR-002, ADR-010). There is no build step: edit a template or stylesheet, then refresh.
The navigation follows the four-step workflow in `WORKFLOW.md`.

Previous versions: `research/meta-duplo-UI_SPEC-v1.md` (12 screens) and
`research/duplicate-finder-UI-DESIGN.md` (setup, results views, ignore model, basket).

## Principles

1. **Follow the workflow.** The left navigation is the four steps, with live counts of open items.
   Each page says what the step does and what happens on disk.
2. **Review, don't browse.** Every screen helps you decide, fix or approve something.
3. **Explain everything.** Every recommendation and match shows its evidence: where a value came from,
   why, its confidence, and which copies disagree.
4. **Bulk first.** Bulk actions (*Smart select*, *Accept all ≥ 90 %*) sit beside per-item ones. Lists must stay usable with thousands of groups.
5. **Safe by design.** Nothing touches the disk outside the Plan page, behind a dry-run list and a confirm.
   Every plan can be undone.
6. **Fast at scale.** Keyset pagination (`id > ? LIMIT 100`), infinite scroll with `hx-trigger="revealed"`,
   and thumbnails from the cache.

## Screens

| Nav | Screen | Purpose | Key elements | POC |
|---|---|---|---|---|
| ◎ | **Sources** | Add folders, scan, overview | Source table (files, size, role Scan/Reference, Read-only/Writable toggles); add-folder form; *Scan all sources*; four step tiles with counts and reclaimable bytes | ✓ |
| 1 | **Identical files** | Remove 100 % copies | Results table (below) | ✓ |
| 2 | **Near duplicates** | Resolve almost-identical files | Results table with tabs *Duplicates* / *Similar shots, review only*; verdict per group; values that differ from the kept copy highlighted | ✓ |
| – | **Compare** | Look before deciding | Swipe compare (Alpine range slider over two images); metadata table per member | ✓ |
| 3 | **Harmonize & rename** | One date/place/device per photo; new names | File name rule bar; **rename grid** (below): one row per file, old → new name, metadata columns, inline decisions | ✓ |
| 4 | **Re-check** | New candidates after harmonizing | Results table on harmonized date/place/device, with a minimum-confidence filter | ✓ |
| ↺ | **Plans & undo** | Approve, apply, undo | Plan list; plan detail = dry-run diff (action, before → after, status, error); Apply / Discard / Undo | ✓ |
| ⚙ | **Settings** | Config | Home time zone, near-duplicate threshold, re-check minimum | ✓ |
| – | Folders overlap | Whole-folder decisions | `A ≡ B`, `A ⊂ B`, `A ∩ B %` tree and heatmap | later |
| – | Timeline, Map, Devices | Sanity views from meta_duplo v1 | Histogram, clustered map, device units | later |

### Steps 1, 2 and 4: results table (`results.html`, `partials/results_group.html`)

Modelled on the Disk Drill duplicate finder: one compact table, built for hundreds or thousands of groups.

```
 Open ▾  [Search names and folders]  Sort [Size ▾] [↓ High to low ▾]            [◧ Preview]
 Smart select [Choose… ▾]                                         [Expand all] [Collapse all]
 305 groups
     Name                                               Size     Qty / Date           Device        Place     Match
 ⌄ [▣] IMG_0003.JPG   Same photo   2 selected        188.8 KB    3                    Apple iPhone  Vienna    100 %
 ☐  disk › Camera › DCIM › IMG_0003.JPG (Keep)         62.9 KB   2019-05-18 11:47:10  Apple iPhone  Vienna
 ☑  disk › Backup 2019 › C̶o̶p̶y̶ ̶o̶f̶ ̶I̶M̶G̶_̶0̶0̶0̶3̶.̶J̶P̶G̶        62.9 KB   2019-05-18 11:47:10  Apple iPhone  Vienna    100 %
 ⌄ [▣] IMG_4101.JPG …
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│ 306 files selected  6.5 MB  in 305 groups                     [Clear] [Review removal plan…] │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Group row:** chevron, thumbnail of the kept copy, its name, the verdict (steps 2/4), *n selected*, total size,
  number of files, the kept copy's device and place, the group confidence (steps 2/4). Clicking it expands or collapses the group.
  On hover: *Compare*, *Not duplicates*, *Ignore* (or *Reopen*).
- **File row:** checkbox = **remove this copy**; a breadcrumb `source › folder › folder › name` (long paths shorten to
  `source › … › last two folders`, full path on hover); a *Keep* pill on the kept copy, or *Keep this one* on hover; size, date,
  device, place; the match score against the kept copy. Values that differ from the kept copy are highlighted.
  Selected rows are tinted and struck through.
- **Click a file row** to open it in the **preview panel** (right, toggled by *◧ Preview*): image, path, role, date,
  device, place with coordinates, size and dimensions, software, the evidence vector, *Compare the group*.
- **Smart select:** *All copies except the kept one*, *Keep the oldest file*, *Keep the largest file*,
  *Keep the copy in folder…* (folders listed by number of copies), *Clear selection*. Rules that pick another copy
  also move the *Keep* marker. Similar shots are never selected automatically.
- **Selection is server-side** (`selection` table). It survives paging, searching and reloads, and is cleared when
  the step's groups are rebuilt by a rescan.
- **Selection bar** (sticky at the bottom): files, bytes, groups, and a warning when a group would lose every copy
  (those groups are skipped by the plan). *Review removal plan…* turns the selection into a cleanup plan and clears it.
- **Toolbar:** status (Open / Resolved / Not duplicates / Ignored), search across names and folders (300 ms debounce),
  sort by Size, Qty / bindings, Name, Date, Device, Place or Confidence, direction; minimum confidence in step 4.
  All of it lives in the URL (`hx-push-url`).
- **Paging:** 100 groups per page, the next page loads when the end of the table scrolls into view.
- The **row height** stays one line: cells truncate with an ellipsis instead of wrapping.

### Step 3: rename grid (`step3.html`, `partials/grid_row.html`)

Modelled on Advanced Renamer's file list: a spreadsheet-like grid with one row per file.

```
File name rule [{date:%Y-%m-%d %H-%M-%S} ({location_name}, {device_name}) - {original_filename}.{ext}]
               {date…} {location_name} {country} {device_name} {original_filename} {ext} {EXT}  Reset to default
All files 721 · Needs a decision 1 · Name changes 719 · Problems 0 · Excluded 1
[Search…] [Accept all ≥ 90 %] [Accept all ≥ 70 %]                                    [Columns ▾]
┌──┬──────────────┬───────────────────────────────────────┬────────────┬────────┬─────────────────────────────┬──────────────┐
│✓ │ Filename     │ New filename                          │ Path ↑     │ Status │ Date                        │ Place        │ …
│☑ │ IMG_0001.JPG │ 2019-05-14 18-22-03 (Bratislava, …)…  │ disk/DCIM  │ OK     │ 2019-05-14 18:22:03         │ Bratislava   │
│☑ │ IMG_…crop.jpg│ = unchanged                           │ disk/Edited│ Needs… │ — → 2019-05-18 11:47:10 ✓ ✗ │ — → Viedeň ↺ │
└──┴──────────────┴───────────────────────────────────────┴────────────┴────────┴─────────────────────────────┴──────────────┘
 719 files get a new name · 1 still needs a decision   ☑ Write XMP sidecars ☐ Set file date   [Review rename plan…]
```

- **Rule bar:** typing in the rule saves it after 400 ms and re-renders the grid, so the *New filename* column is the live preview.
  Token chips insert at the cursor. *Reset to default* restores the default rule.
- **Columns** (picker, saved per library). Default: Filename, New filename, Path, Status, Date, Place, Device, Date confidence.
  Optional: Date from, DateTimeOriginal, DateTime (modified), File date, Make, Model, Software, GPS, Size, Dimensions, Copies.
- **Every header sorts** (click again to reverse). Empty values always sort last. Sort, filter and search live in the URL.
- **Harmonized cells** (Date, Place, Device) show `now → new`:
  - *suggested*: italic accent, with the confidence and **✓** (accept) / **✗** (keep current);
  - *accepted*: current value struck through, new value bold, **↺** to undo;
  - *edited*: same, accent colour;
  - *rejected*: current value with **↺** to show the recommendation again.

  **Double-click** a cell to type a value (Enter saves, Esc cancels). The explanation is the cell tooltip.
  Decisions apply to the whole photo, so every copy's row updates in place (out-of-band swap, no reload, scroll kept).
- **Status:** OK · Unchanged · Needs decision · No date · Renumbered (collision) · Read-only (source not writable) · Excluded.
- **Filters:** All files, Needs a decision, Name changes, Problems (no date / read-only / renumbered), Excluded, with counts.
- **Checkbox per row** = include in renaming; unticked files keep their name and get no sidecar or file date.
- The grid scrolls in its own box with a sticky header; 200 rows load at a time as you scroll.
- **Apply bar** (sticky): how many files get a new name, how many still need a decision, the sidecar and file-date options,
  *Review rename plan…*.

## Interaction patterns (htmx)

| Pattern | How |
|---|---|
| Boosted navigation | `<body hx-boost="true">` turns links and forms into AJAX page swaps |
| Token on every POST | `<body hx-headers='{"X-Meta-Duplo-Token": "…"}'>`; the server rejects POSTs without it |
| Job progress | `#job` polls `GET /job?watch=1` every second (`hx-trigger="every 1s"`); when the job ends, `HX-Refresh` reloads the page with fresh results |
| Live preview | Rule input: `hx-post="/settings/pattern" hx-trigger="input changed delay:400ms"` → `HX-Trigger: grid-reload` |
| Row updates | Decisions and include/exclude return the affected rows wrapped in `<template>` with `hx-swap-oob="true"` |
| In-place update | Card / row actions return the re-rendered card or row (`hx-swap="outerHTML"`) |
| Server-driven redirect | Plan builders answer with `HX-Redirect: /plans/<id>` |
| Infinite scroll | Results: last `<tbody class="more">`, `hx-get="/step/<n>/rows?offset=…" hx-trigger="revealed"`. Rename grid: last `<tr class="more">`, `hx-get="/rename/rows?offset=…" hx-trigger="intersect once"` (it scrolls inside its own box) |
| Sorting, search | Toolbar form `hx-get` → `hx-target="#results" hx-select="#results"` + `hx-push-url="true"`. `#results` sets `hx-disinherit="*"` so rows do not inherit its select/swap |
| Selection | Checkbox `hx-post="/select" hx-swap="none"` → `HX-Trigger: selection-changed` → the bar re-fetches itself |
| Confirm | `hx-confirm` on Apply and Undo |
| Toasts | `HX-Trigger: {"toast": "…"}` → Alpine listener on `window` |

SSE (meta-duplo ARCHITECTURE §6) replaces polling once jobs are long enough for it to matter.

## Styling contract

- Only **CSS custom properties** for colours, spacing, radius and type, in `static/css/tokens.css`
  (light + dark via `prefers-color-scheme`).
- Files: `base.css` (elements), `layout.css` (app shell, grids), `components.css` (cards, badges, tables, fields).
- Semantic class names (`.card`, `.member--keeper`, `.badge--high`, `.field--accepted`), BEM-style.
  The only inline style is the computed `clip-path` of the swipe compare.
- Confidence bands: verified ≥ 98 %, high ≥ 90 %, medium ≥ 70 %, low ≥ 40 %, unknown. Colour is never the only
  signal: the badge also shows the number.
- Layout: left nav 232 px, main content up to 1500 px. Works at 1280 px and up; below 1000 px the nav stacks on top.
- Respect `prefers-reduced-motion`.

## Accessibility and keyboard

- All actions are buttons or links reachable by keyboard, with visible focus rings.
- Thumbnails have alt text (file name + date).
- Planned keyboard review for steps 2–4: `j/k` or `←/→` move, `a` accept, `Enter` keep, `x` mark,
  `n` not duplicates, `i` ignore, `Space` compare, `?` help (`K` was dropped: it clashed with `k`).
