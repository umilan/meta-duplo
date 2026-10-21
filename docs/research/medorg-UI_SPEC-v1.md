# UI specification

Server-rendered HTML (Jinja2), progressively enhanced with **HTMX** and **Alpine.js**, styled
with **plain CSS** you own. There is no build step: edit a template or stylesheet, then refresh.

## Principles

1. **Review, don't browse.** This is not a photo viewer. Every screen helps you decide, fix or
   approve something.
2. **Explain everything.** Any value (time, place, device) can be clicked to show its evidence,
   the heuristics used, its confidence per level, and any conflicts.
3. **Bulk first.** Fixing one anchor (a camera clock, a folder date) should visibly lift many
   files. Show that impact before you confirm.
4. **Safe by design.** Destructive steps live only in the Plan screen, behind a dry-run diff.
5. **Fast at scale.** Keyset pagination, lazy thumbnails, no screen renders more than ~200
   items at once.

## Screens

| # | Screen | Purpose | Key elements |
|---|---|---|---|
| 1 | **Libraries / Sources** | Add a source disk, see its status | Volume info, fs type warnings (NTFS read-only), read-only/writable toggle, pre-flight checks |
| 2 | **Jobs** | Run, pause and resume stages | Stage list with status; live progress (SSE); errors per stage; "re-run stale" button |
| 3 | **Overview** | Archive health at a glance | Counts per band per dimension; expected-correct counts; duplicates found; origin-type breakdown; "weakest links" list |
| 4 | **Review queue** | Work through what needs a human | Grouped by issue (conflict, low confidence, clock offset, ambiguous folder date, borderline duplicate); impact-sorted; keyboard-driven |
| 5 | **File / cluster detail** | Full explanation of one capture | Thumbnail + siblings + duplicates; for each dimension: chosen value, confidence ladder, evidence table, constraints, conflicts; override form |
| 6 | **Timeline** | Time sanity | Histogram by day/month; sessions; files with intervals shown as bars; filter by device |
| 7 | **Map** | Place sanity | Clustered markers coloured by band; radius circles; tracks as polylines; teleport flags; drag-to-correct |
| 8 | **Devices** | Units and owners | Unit list with counts, active period, owner label (editable), counter-vs-time chart; merge/split |
| 9 | **Folders** | Folder semantics | Tree with folder type, parsed tokens, coherence score; override type or date; see member bands |
| 10 | **Duplicates** | Clusters and masters | Side-by-side members, link level and score, chosen master; change master; split cluster |
| 11 | **Plan** | Build and approve actions | Choose mode (sidecar / clone tree / hardlinks / rename / in-file); layout pattern with live preview; dry-run diff; per-band rules; approve → apply; undo |
| 12 | **Settings** | Config | Home timezone, languages, thresholds, stop words, place-name overrides, layout patterns |

## Interaction patterns (HTMX)

| Pattern | How |
|---|---|
| Infinite grid | `<div hx-get="/files?after=…" hx-trigger="revealed" hx-swap="afterend">` |
| Detail panel | Click a card → `hx-get="/cluster/123" hx-target="#panel"`; URL updated via `hx-push-url` |
| Inline override | Form → `hx-post="/override"` → server returns the updated detail + an impact toast |
| Live progress | `hx-ext="sse" sse-connect="/events/jobs/7" sse-swap="progress"` |
| Filters | A `<form>` with `hx-get` on change; filters live in the URL query string |
| Keyboard review | Alpine.js: `j/k` next/prev, `a` accept, `o` override, `s` skip, `?` help |

## Component inventory (templates/partials)

- `card.html`: thumbnail, time, place, device chips, band badge, conflict marker.
- `confidence_ladder.html`: one row per level (±1 min … year) with % bars.
- `evidence_table.html`: source, origin, tier, value, valid/rejected (with reason).
- `constraint_list.html`: heuristic, kind, related files (links), reliability.
- `badge.html`: band (verified/high/medium/low/unknown); origin type.
- `diff_row.html`: before → after for plan actions.
- `impact_toast.html`: "This change lifts 312 files from low to high."
- `empty_state.html`, `error_banner.html`, `job_row.html`.

## Styling contract (for you to restyle)

- Use only **CSS custom properties** for colours, spacing, radius and typography, in
  `static/css/tokens.css`:

```css
:root {
  --font-ui: system-ui, -apple-system, "Helvetica Neue", sans-serif;
  --font-mono: ui-monospace, "SF Mono", Menlo, monospace;
  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px; --space-6: 24px;
  --radius: 8px;
  --bg: #fbfbfa; --surface: #ffffff; --text: #1d1d1f; --muted: #6e6e73; --line: #e5e5e7;
  --accent: #2f6fde;
  --band-verified: #1f8a4c; --band-high: #3aa463; --band-medium: #d29a1f;
  --band-low: #d2691f; --band-unknown: #8e8e93; --conflict: #c62f2f;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #161617; --surface: #1f1f21; --text: #f2f2f2; --muted: #a1a1a6; --line: #333336; }
}
```

- Files:
  - `base.css` for elements;
  - `layout.css` for app shell and grids;
  - `components.css` for the partials above.
- Templates use semantic class names (`.card`, `.badge--high`, `.ladder__row`), never inline
  styles.
- Layout: a left nav (screens), a main list or grid, a right detail panel (resizable). It must
  work at 1280 px and up; a mobile layout is not required.
- Respect `prefers-reduced-motion`. Colour is never the only signal: bands also carry text
  labels.

## Accessibility and keyboard

- All actions are reachable by keyboard; visible focus rings.
- Thumbnails have alt text: file name + resolved date.
- Confidence uses text and number, not only colour.
