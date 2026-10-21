# UI specification (merged: medorg + duplicate finder)

Server-rendered HTML (Jinja2), progressively enhanced with **htmx** and **Alpine.js**, styled
with **plain CSS** you own (ADR-002, ADR-010). There is no build step: edit a template or stylesheet, then refresh.
The navigation follows the four-step workflow in `WORKFLOW.md`.

Previous versions: `research/medorg-UI_SPEC-v1.md` (12 screens) and
`research/duplicate-finder-UI-DESIGN.md` (setup, results views, ignore model, basket).

## Principles

1. **Follow the workflow.** The left navigation is the four steps, with live counts of open items.
   Each page says what the step does and what happens on disk.
2. **Review, don't browse.** Every screen helps you decide, fix or approve something.
3. **Explain everything.** Every recommendation and match shows its evidence: where a value came from,
   why, its confidence, and which copies disagree.
4. **Bulk first.** Bulk actions (*Review plan for all groups*, *Accept all ≥ 90 %*) sit beside per-item ones.
5. **Safe by design.** Nothing touches the disk outside the Plan page, behind a dry-run list and a confirm.
   Every plan can be undone.
6. **Fast at scale.** Keyset pagination (`id > ? LIMIT 100`), infinite scroll with `hx-trigger="revealed"`,
   and thumbnails from the cache.

## Screens

| Nav | Screen | Purpose | Key elements | POC |
|---|---|---|---|---|
| ◎ | **Sources** | Add folders, scan, overview | Source table (files, size, role Scan/Reference, Read-only/Writable toggles); add-folder form; *Scan all sources*; four step tiles with counts and reclaimable bytes | ✓ |
| 1 | **Identical files** | Remove 100 % copies | Group cards; status tabs Open / Resolved / Not duplicates / Ignored; *Review plan for all groups* | ✓ |
| 2 | **Near duplicates** | Resolve almost-identical files | Tabs *Duplicates* / *Similar shots, review only*; cards with the **differing values highlighted**; evidence line (picture %, Δt, device, pixel ratio) | ✓ |
| – | **Compare** | Look before deciding | Swipe compare (Alpine range slider over two images); metadata table per member | ✓ |
| 3 | **Harmonize & rename** | One date/place/device per capture; new names | File name rule editor with **live preview**; recommendation table (current → recommended, confidence badge, explanation, Accept / Keep current / Confirm / Edit / Undo); bulk accept; *Needs a decision* / *All*; rename plan options | ✓ |
| 4 | **Re-check** | New candidates after harmonizing | Sortable, filterable table: thumbnail, name, folder, size, date, place, device, confidence, **bindings**, group; per-group actions | ✓ |
| ↺ | **Plans & undo** | Approve, apply, undo | Plan list; plan detail = dry-run diff (action, before → after, status, error); Apply / Discard / Undo | ✓ |
| ⚙ | **Settings** | Config | Home time zone, near-duplicate threshold, re-check minimum | ✓ |
| – | Folders overlap | Whole-folder decisions | `A ≡ B`, `A ⊂ B`, `A ∩ B %` tree and heatmap | later |
| – | Timeline, Map, Devices | Sanity views from medorg v1 | Histogram, clustered map, device units | later |

### Step 1 and 2: group card (`partials/group_card.html`)

```
┌ Same photo, different file      [100 %] [2 files] [21 KB to reclaim] [#6] ┐
│ ┌─────────────┐  ┌─────────────┐                                            │
│ │ thumbnail   │  │ thumbnail   │   kept copy has a green outline            │
│ │ IMG_0002.JPG│  │ IMG-…WA0003 │                                            │
│ │ Camera/DCIM │  │ WhatsApp    │                                            │
│ │ Date  …     │  │ Date  —     │   values that differ are highlighted       │
│ │ Device …    │  │ Device —    │                                            │
│ │ ✓ Keep      │  │ visual 100 % · 41 % pixels                               │
│ └─────────────┘  │ [Keep this one instead]                                  │
│                  └─────────────┘                                            │
├─────────────────────────────────────────────────────────────────────────────┤
│ [Move others to holding folder…] [Compare] [Not duplicates] [Ignore]        │
└─────────────────────────────────────────────────────────────────────────────┘
```

- *Keep this one instead* swaps the card in place (`hx-post`, `hx-swap="outerHTML"`).
- *Not duplicates* records labelled pairs; *Ignore* hides the group by its signature; both survive rescans.
- *Move others…* builds a cleanup plan and opens it; nothing moves until *Apply plan*.

### Step 3: recommendation row (`partials/capture_rows.html`)

One row per capture. Columns: thumbnail · copies (removed copies listed as *removed, used as evidence*)
· Date & time · Place · Device · New name of the kept copy.

Each field cell:

```
now: 2019-06-02 10:15:30            ← current value of the kept copy
keep as is?   /   2019-05-18 11:47:10   /   ✓ already correct   /   nothing found
[70 %] [accepted]                   ← band colour + text, status chip
From metadata of IMG_0004.JPG. Copies disagree by 81870 s; the earliest is used.
[Accept] [Keep current] [Edit]      ← or [Confirm] [Edit], or [Undo]
```

- A left bar shows the status: green accepted, blue edited, grey rejected.
- *Edit* opens an inline form (Alpine `x-show`); a date must match `YYYY-MM-DD HH:MM:SS`.
- Every action swaps only that row (`hx-target="#cap-<id>"`).

### Step 4: sortable list (`partials/step4_table.html`)

- Header links carry `?sort=<col>&dir=<asc|desc>&min_conf=<x>`; htmx swaps only `#recheck-table`
  and pushes the URL (`hx-push-url`), so views are bookmarkable and the back button works.
- Columns: thumbnail, Name (✓ = kept), Folder, Size, Date, Place, Device, Confidence (badge; the
  tooltip lists the evidence vector), Bindings, Group (link to Compare).
- Rows of one group share a background shade. Group actions sit on the kept row.

## Interaction patterns (htmx)

| Pattern | How |
|---|---|
| Boosted navigation | `<body hx-boost="true">` turns links and forms into AJAX page swaps |
| Token on every POST | `<body hx-headers='{"X-Medorg-Token": "…"}'>`; the server rejects POSTs without it |
| Job progress | `#job` polls `GET /job?watch=1` every second (`hx-trigger="every 1s"`); when the job ends, `HX-Refresh` reloads the page with fresh results |
| Live preview | Pattern input: `hx-post="/settings/pattern" hx-trigger="input changed delay:300ms"` |
| In-place update | Card / row actions return the re-rendered card or row (`hx-swap="outerHTML"`) |
| Server-driven redirect | Plan builders answer with `HX-Redirect: /plans/<id>` |
| Infinite scroll | Last row: `hx-get="/step/3/more?after=<id>" hx-trigger="revealed"` |
| Sorting | Header links `hx-get` + `hx-target="#recheck-table"` + `hx-push-url="true"` |
| Confirm | `hx-confirm` on Apply and Undo |
| Toasts | `HX-Trigger: {"toast": "…"}` → Alpine listener on `window` |

SSE (medorg ARCHITECTURE §6) replaces polling once jobs are long enough for it to matter.

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
