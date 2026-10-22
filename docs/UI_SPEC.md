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
| ▦ | **Workbench** (beta) | One screen for every task (below) | Group by, column catalog, resizable/reorderable headers with actions, edit columns, preview | ✓ experimental |
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

### Workbench (experimental, `workbench.html`, `workbench.py`, `static/js/workbench.js`)

On screen it is called **Filomena** (title, nav item, page title, settings section); the code, URL (`/workbench`)
and docs keep the name Workbench.

One table for every task; the four step screens stay as the guided path. No description text under the title.
**Job stripe** (every page, while a scan or other job runs): fixed to the top of the window, edge to edge, 40 px,
in inverted colours (dark blue with light text in the light theme, light blue with dark text in the dark theme):
job name, stage, a bar using all the free width, `done / total`, and **Cancel**. Cancel stops the job at its next
progress step (*Cancelling…* meanwhile); what is done is kept (stages are idempotent) and the page reloads with the
partial results; the job is recorded as `cancelled`. The page gets 40 px of top padding while the stripe shows.
Header: two rows. Row 1 holds the title with undo / redo right of it, Data input, Grouping by, Sort by, Search and ⚙ (⚙ is part of the row, so a
job progress stripe pushes the whole row down); row 2 the filters
with Selection and Actions. When row 1 gets tight (under 1240 px) Search becomes a 32 px magnifier button that opens
the field in place (it stays open while it has text); the Data input field stays on one line (folder count truncates with …, *Found N files* hides below 208 px); under 1040 px the labels hide visually (still read by screen
readers) and the pickers narrow to 128 px.
- The Workbench is the **start screen** (`/` opens it; first item in the nav). The four steps stay in the nav.
- Every picker in the header is a **dropdown sheet** (a select-like button and a menu sheet), with ( ) / (•) radio
  marks for choices and a short grey hint under items that need one.
- **Workbench** title and **Data input**: a field with *N folders* (or *N of M folders*) and *Found N files*. Its
  sheet, as wide as the field, never leaves the page:
  - *Remove all* (red, bold, right end of the *Add new…* row, after a confirm) removes every folder from the library;
    nothing on disk is touched, and folders used by a plan stay so it can still be undone.
  - *+ Add new…* (accent colour, bold) opens the macOS folder dialog right away (the local server runs `osascript` *choose folder*;
    in the Tauri app this becomes the native dialog). Picking a folder adds it; Cancel does nothing. Where no
    dialog is available, the page asks for a typed path instead;
  - one row per folder: a checkbox (include it in the view; unticked folders stay in the library), the path,
    file count and access, and *Remove* (forgets the folder and what the library knows about its files; nothing on
    disk is touched; refused while a plan refers to its files, so undo keeps working);
  - *Use demo folder* adds `~/meta-duplo-demo` (created only if it does not exist yet).
  - at the bottom, *Add new folders as writable* (off by default) applies to the next *Add new…*.
  Adding or removing starts a scan; the job bar shows progress and the page reloads when it is done.
- **Grouping by**: None, Duplicates, Photo (all copies), Path / Folders, Day, Device, Place, Author.
- **Duplicates grouping** starts with the chip **Dup.: ≥ 2** (switching to it adds the chip, switching away removes
  it if it was added that way). Remove the chip and files without any copy come back as one **No duplicates (n)**
  group, always last and collapsed. Its files are unrelated, so it has no red / green colours (path and
  filename in the normal text colour), no meta checks and no match values.
- **Applied filters** as grey chips (*Type: JPG ×*) and a **+** button; on the right **Selection** (None, All (n),
  Custom (n), Duplicates – newer / older / less metadata, each with its hint) and **Actions** (*Delete…* = removal
  plan, *Rename…* = rename plan, *Change*: *Date… / Place… / Device…* open the edit rule dialog for a purple
  column, *Save edits*; the ones that need a selection are disabled without one).
The page has no padding: the table runs from the left edge of the screen to the right (or to the preview panel);
only the title, toolbar, filters and count above it are inset slightly.

```
[☰] Workbench  Data input [3 folders · Found 32 235 files]  Grouping by [Duplicates ▾]  Sort by [Dup. ↓ ▾]  [🔍 Search…  ×]  [⚙]
Applied filters: [Type: JPG ×] [Dup. confidence: ≥ 100 ×] [+]          Selection [None ▾]  Actions [Choose… ▾]
┌─┬─────┬─────────────────────────⋯┬────────────────────⋯┬──New filename───────────⋯┬Dup.┬Dup. confidence┬Meta checks─────┬Date taken──┬New date─┐
│☐│[▣]  │ Diff                      │ Diff                 │ → 2019-05-18 11-47-10 (…  │3pcs│100 %          │ –              │ Same       │ → …     │
│☐│└ ▫  │ disk / Camera / DCIM /    │ IMG_0003.JPG (Keep)  │ → 2019-05-18 11-47-10 (…  │3pcs│100 %          │ date loc device│ 2019-05-18 │ → …     │
│☐│└ ▫  │ disk / Backup 2019 /      │ Copy of IMG_0003.JPG │ → …                       │3pcs│100 %          │ date loc device│ 2019-05-18 │ → …     │
└─┴─────┴───────────────────────────┴──────────────────────┴───────────────────────────┴────┴───────────────┴────────────────┴────────────┴─────────┘
```

- **Expander column:** when grouped, the second column is a 40 px expander column (as wide as the checkbox column):
  › on group rows (click the row to fold it), └ on file rows; its header › (shown on hover) collapses or expands all
  groups, and groups loaded later follow it. The group's name and file count (e.g. “Apple iPhone 12 (95)”) show in
  the group row's cell of the matching data column: Filename for Duplicates and Photo, Path for Folder, Date taken
  for Day, Device, Place, Author; the tooltip on › names it too.
- **Pinned columns:** checkbox, expander and, when it is the first column, the thumbnail stay on the left when the
  table scrolls sideways.
- **Group by** sets the task: *Duplicates* (only files with copies; the kept copy is the reference row),
  *Photo (all copies)*, *Folder*, *Day*, *Device*, *Place*, *No grouping*. The group row shows the reference
  thumbnail and, per column, **Same** (green) or **Diff** (red). The chevron and the group name fold and unfold the
  group; clicking any other group-row cell (or moving the keyboard cursor onto it) marks that column's cells in all
  the group's files and shows the group's first file in the preview.
- **Differences:** values that differ from the group's reference row are red (with *No grouping* there is no
  reference, so nothing is red or green and Meta checks shows –). **Green means every file of the group
  agrees:** a file name is green only when all files in the group have that name (the group's name in the group row
  is then green too, red when the names differ); a path segment is green when every file has it at that place, red
  on a copy where it differs from the reference row, and neutral otherwise (e.g. the reference's own folder when a
  copy sits elsewhere). *Text colours* in the settings sheet switches the colours off.
- **Path and Filename are separate columns.**
- **Columns:** the catalog has the core fields first (Dates, Location, Device, Author, Duplicates, File) and
  then **every EXIF tag found in the library** (`IFD0:*`, `ExifIFD:*`, `GPS:*`, QuickTime tags), sorted into the same
  categories by what they describe (date/time tags under Dates, GPS under Location, make/model/lens under Device,
  artist/copyright under Author, exposure/ISO/focal length under Capture settings, size/orientation under File, the rest under Other). *+* opens the searchable picker (up to 1040 px wide): one plain checkbox per field, grouped by category; tick to show, untick to hide (applies at once, the picker stays open), with a count of shown columns. Categories are listed File, Duplicates, Dates, Location, Device, Author, Capture settings, Other, and the table follows that order: a newly shown column lands in its place (an edit column right after the field it edits), and *Order like this list* sorts the shown columns the same way (dragging can still reorder them). Each category title has a checkbox (all / some / none) that shows or hides the whole category, and the header has *Select all*, *None* and a × to close; while searching, all of these act only on the fields found. Width (drag the right edge of a header, or the left / right edge of any cell: within 4 px the pointer turns into a resize cursor; a cell's left edge resizes the column before it), order
  (drag a header label: a strong 4 px accent line with round ends, running the full table height, shows where it lands and the target header is tinted; the left half of a header drops before it, the right half after it, including at the end) and visibility are saved per library (`workbench_view` setting).
- **Header ⋯ button** (same look as the cell ⋯ button; shown only while hovering the header, while its menu is open or when focused) opens a **menu sheet** (page-level, positioned under the button, so the scrolling table never
  clips it; Esc, a click outside or scrolling closes it): *Pin to the left* / *Unpin column* (a pinned column joins the block at the left and stays there while the table scrolls sideways, after the checkbox and group columns; a pin icon in its header, a 2 px line where the scrolling part starts; the thumbnail is pinned while it is the first column; saved in the view as `pinned`, undoable); *Sort by* ascending / descending; *Find*: Filter by value… (the column's
  values with counts, most frequent first, searchable; picking one adds an *is* filter), Filter…, Search in this
  column… (*contains*), Show groups where it differs; *Change*: Edit these values… (new purple column), Group by this (Path, Date, Device,
  Place, Dup.). *Remove* (top right, next to the column name) takes the column out of the table; section labels are a lighter grey than the items. Clicking the label toggles the sort; drag the label to move the column.
- **Filters** (bar above the table; click a chip to edit it in the filter dialog, × to remove): is / is not / contains / is empty / is not empty / ≥ / ≤ /
  differs in group / same in group. Grouped views keep a whole group when any row matches (a duplicate set needs
  its context); *No grouping* filters rows.
- **Edit columns** (light purple, always right after the field they edit: New date after Date taken, New place after Place, New device after Device, New filename after Filename, even when a value was copied from another column) exist **only on demand**: none by default,
  *Edit…* in a header's ⋯ sheet opens the rule dialog and adds one.
  - **Filename:** the **Naming pattern** dialog (also *Actions → File name pattern…* and a double-click on any
    file name). At the top, the preview: *New file name preview for '<file>'* above the new name it would get. The name is typed text with parameter chips (purple, underlined) in a resizable field. Under it a
    hint, then one bordered box holding a search field (with ×, a line under it, staying on top) and the parameter list, which scrolls under it, always open: *Recommended values* (date and time, date,
    year … sec, place, country, device, original filename, extension; they follow the New date / place / device
    columns), *Columns in this table*, then every field by category (EXIF / XMP / IPTC tags included,
    `{tag:KEY}`). Each tag shows, on the right, its value for the previewed (or first) file as it would read in
    the name (— when that file has none; the code is in the tooltip). Clicking a tag inserts it at the text cursor (the cursor moves after it, so clicks add tags in
    order; the search keeps only tags whose name, or a column tag's key, contains the typed phrase, and hides empty sections; Enter takes the first match). Clicking a chip selects it (framed, it takes the focus, so
    no text caret shows) and highlights its tag in the list, scrolled into view: the next tag replaces it (it stays
    selected), or *Remove it* / *Keep it*. **Keyboard:** ← → walk through the line, from the text over the chips
    (a chip is one step); ↑ ↓ on a selected chip swap it for the previous / next tag of the list (as filtered),
    live, with the list following; Backspace / Delete next to a chip select it, and again remove it; Esc leaves a chip. Chips show names (a column tag by its key, e.g. `EXIF:DateTimeOriginal`); the values
    show in the preview line and in the list. Each label sits 4 px above its field.
    A red warning shows while the name does not end with `.<Extension>`. Empty parts are tidied like an empty
    place; `{tag:KEY|%Y-%m-%d}` formats a date tag. Rename plans use the same values the table shows.
    **Presets** (left sidebar of the dialog, 240 px; stacked under 760 px): each shows its name and the pattern as
    its chips read; a click loads it; the active one is marked (accent), with a purple • once the name was changed
    since. Hover shows ✎ (rename inline; also double-click; Enter saves, Esc cancels) and × (delete, after a
    confirm). At the bottom: *Update “name”* (only while the active saved preset is modified) and *+ Save as new
    preset* (inline name field; names must be unique). Reopening the dialog marks the preset the current pattern
    comes from. Stored in the library setting `name_presets`; the built-in *Default* can't be changed. *Apply* sets the rule and adds
    the New filename column.
  - **Date:** recommended value; copy from another column (taken, digitized, modified, file date, GPS date,
    date in filename, recommended); **shift** a date by ± h:m:s (wrong camera clock, time zone); fixed value.
  - **Place, Device:** recommended; copy from any column; fixed value.

  `= value` means unchanged, `→ value` a change, `—` not enough data (e.g. no date). *Remove edit column* drops the rule.
  The ⋯ sheet of an edit column starts with **Apply · Edit all · Clear · Remove** (instead of a plain Remove):
  *Apply* saves its values as decisions for the ticked files, or every row in the view when none is ticked (the
  filename column saves its pattern; nothing changes on disk until a plan is applied); *Edit all* opens the rule
  dialog; *Clear* drops the rule and the per-file values but keeps the column (all `= current`); *Remove* drops
  the column and its rule.
  **Source links:** each edit cell ends with a purple badge `🔗A`, `🔗B`… naming where its value comes from: the
  rule (recommended, a copied column, a shift), a cell it was copied from (*Apply this value to …*), a value typed
  for that file, or *unchanged*. Letters follow the order the sources first show in the table. Hovering a badge
  shows an instant tooltip with the source and outlines the column (and row) it points to; clicking opens the
  rule dialog, or the per-file dialog for a typed value. The header shows `🔗(n)`, the number of different
  sources in the column at its right edge, with the legend in its tooltip. A cell values were copied from (*Apply
  this value to …*) shows the same badge at its right; its tooltip names the edit column and how many files it
  feeds, hovering outlines that column and row, and clicking opens the rule dialog. In group rows the value and badge are
  dimmed (50 %): the files below carry the real values.
  **Double-click** an edit cell to type a value for that one file (the *Edit this value…* dialog, prefilled without
  the `=` / `→` mark); in New filename it opens the naming-pattern dialog (one pattern for all files).
- **Duplicate confidence:**
  - 100 % = byte-identical;
  - 99 % = same pixels, different metadata;
  - lower = the matcher's score, capped at 98 % (e.g. "98 % · same photo").

  *Meta checks* shows date / loc / device pills: green when equal to the reference row, red when not.
- **Selection and actions** (sticky bar):
  - The **Selection** picker sits on the right of the Group by row, with the action buttons next to it (no footer bar):
    *None*, *All (n)*, *Custom (n)* (shown once you tick or untick rows by hand after a preset),
    *Duplicates – newer* (in each duplicate set keep the copy with the oldest creation date, tick the newer ones),
    *Duplicates – older* (keep the newest, tick the older ones) and *Duplicates – less metadata* (keep the copy
    with the most metadata, tick the others). Creation date = date taken, else digitized, GPS date or date in the
    filename; a copy with no date is ticked by both date presets. Presets cover the whole view (filters apply),
    including groups not loaded yet; ties keep the current kept copy;
  - *Save edits* stores the selected rows' edit values as decisions (`proposals` = edited, per photo);
  - *Remove selected copies…* builds a cleanup plan (a group never loses its last copy);
  - *Review rename plan…* saves the edits and builds a rename + XMP plan for the selected files only.

  Nothing touches the disk before a plan is applied.
- **Keyboard:** a cell cursor (2 px accent frame, row tinted). ↑ ↓ move between visible rows (group and file rows),
  ← → between columns; the first arrow starts at the top. *Enter* ticks or unticks the row (a group row: the whole
  group); *Space* toggles the preview panel for the file (a group row: folds or unfolds it); while the panel is open it
  follows the cursor as you move; *⌥/Ctrl/⌘ + Enter* opens
  the cell's ⋯ menu; *Shift + ↑ / ↓* ticks file rows while moving (group rows are passed, not ticked); *X* ticks or
  unticks the row and moves down; *⌘ / Ctrl + A* ticks every row of the view (Selection → All); *Esc* closes an open
  menu first, then leaves the cursor. Clicking a cell moves the cursor there;
  after a reload it returns to the same file. Keys typed in fields, menus and dialogs are left alone.
- **Undo / redo:** *⌘ / Ctrl + Z* undoes, *⇧⌘Z / Ctrl + Shift + Z / Ctrl + Y* redoes; the ↶ ↷ buttons right of
  the *Filomena* title do the same (greyed when there is nothing). One ordered history covers selection changes (ticking,
  presets, Shift+↑/↓; a burst within 250 ms is one step) and every view change: sorting, grouping, filters, search,
  columns (add, remove, move, width, slim), edit rules and typed or applied values, the reference row, folders
  shown, reset. A toast names what was undone. Up to 100 steps; a new change clears the redo steps; the history
  survives page reloads in the same tab. Not in it: saving edits as decisions, plans (they have their own undo on the
  plan page), adding or removing folders. In text fields ⌘Z stays the field's own undo.
- **Scroll position:** reloading the table (sort, filter, column changes) keeps its sideways scroll position.
- **Sorting:** click anywhere on a header (not ⋯ or the resize edge) to sort; numbers start high-first, a second
  click flips. An arrow next to the header text shows the sort: always on the sorted column, on hover elsewhere
  (the direction a click would give).
- **Dup. confidence:** the score, then in grey what differs from the kept copy of the set: *100 % · identical*,
  *99 % · date differs, place missing, renamed*, *98 % · date added*, *kept copy* on the kept one (date, place,
  device, author, pixel size and name are compared; three at most, then “+n”). Group rows show the score only.
- **Key capture dates:** two built-in columns, *DateTimeOriginal (EXIF)* and *DateCreated (IPTC/XMP)* (XMP
  photoshop:DateCreated, else IPTC 2:55 + 2:60), always available and shown by default. They stay with the other
  dates: the first two items of the *Dates* category in the column picker and the first two date columns in the
  table. Both carry a red **!** badge. The raw tags they come from are not listed again. Extraction reads
  DateCreated since `RAW_VERSION = 3`; saved views are brought in line once (`VIEW_VERSION = 5`). They are shown
  only, not yet weighed as capture-time candidates.
  Third in *Dates* comes *Date from file name* (a date read from the file's name, e.g. `IMG_20190518_114710`).
- **All dates shown by default:** every column of the *Dates* category (the built-in dates and every date tag found
  in the files) is ticked the first time it is offered; one that no file has a value in shows slim. The view keeps
  `dates_seen`, so a date the user unticks stays hidden and only newly found date tags are added (once).
- **Slim columns:** a shown column is never dropped, not even when no file has a value in it: an empty column is
  collapsed to 40 px (as wide as the checkbox column). Double-clicking any header collapses or expands it (the
  choice is kept per column in the view, `slim`); a single click still sorts (it waits a moment, so a double-click
  does not sort). A slim header shows an acronym (e.g. *OTD* for OffsetTimeDigitized, at most 3 letters) and keeps
  the red **!** when the column has one; hovering shows the full name at once, above the header, with “(empty)”
  when it has no values.
- **Filtered columns** show a funnel icon (accent colour) next to the header text (its tooltip lists the filters)
  and a 2 px **orange** outline down the whole column, header included; the filter chips above the table have a
  2 px orange border too. The *Grouping by* picker has the beige background of the group rows while grouping is on.
- **Header labels** wrap to two lines (breaks between CamelCase words and before “(…)”, then “…”); the full name
  is in the tooltip.
- **Group row summaries:** *Same*, *Diff*, or *–* (grey; tooltip “No value in any file of the group”) when no row of the group has a value in that column.
- **Empty values** (“—”, “–”) are dimmed, so real values stand out; an empty value that differs from the
  reference row is a faint red.
  *Meta checks* summarise the same way: *Diff* if any date / loc / dvc / aut badge differs, *–* if no row has any of them.
- **Meta check badges**: *date (n)*, *loc (n)*, *dvc (n)*, *aut (n)* — one per kind of metadata, compared with the
  group's reference row: green (same), red (differs), grey dashed (no value here or in the reference row). *n* is how
  many metadata tags of that kind the file carries (EXIF / QuickTime tags sorted like the column picker; file-system
  dates and our recommendations do not count). Below 208 px of column width only the numbers stay; the tooltip names
  each badge.
- **Sorted column:** its header label and its cells are bold; cells keep their red / green; it has a 2 px **cyan**
  outline (header arrow cyan too), and the *Sort by* picker gets a 2 px cyan border while a column is sorted. A column
  that is both filtered and sorted gets dashed edges, orange and cyan in turn (8 px dashes). The *Sort by* picker
  always names a column by its header label: with none chosen it reads *Path ↑* (the table runs by path, then file
  name, the kept copy first in a duplicate set) and Path is the ticked choice; a sorted column that is not shown is
  listed first. There is no "Default" entry. Group rows are
  regular weight except the group title and the sorted column.
- **Row colours:** ticked rows are light grey (a little darker than *Shade odd rows*); the row shown in the preview is a darker blue. Clicking a row or
  ticking its checkbox shows it in the preview panel (and opens the panel).
- **Column groups:** a 2 px divider where a column-picker category begins (File, Duplicates, Dates, Location,
  Device, Author, …), header included. The same 2 px line (`--line-strong`) runs above every group row, dividing
  the groups horizontally (also with *Cell borders* off).
- **Table height:** the table reaches down to the bottom edge of the window (no empty band below it).
- **Large viewer:** clicking the preview image (hover: zoom-in cursor, slight dim, “⤢ Open large” badge) opens it full screen (16 px margin all round). The table
  behind keeps the keyboard: ↑ ↓ move the cursor and the viewer follows. A switch at the bottom centre chooses
  *One* (the file alone) or *Duplicate set (n)* (every copy of its set side by side, up to four per row, each with
  its name, confidence and path; ✓ = kept copy, accent frame = the current file). Esc, × or a click outside closes it.
  In *One*, photos zoom and pan (originals, so 100 % is real pixels): the wheel or a trackpad pinch zooms around
  the pointer, dragging pans, − / + buttons zoom around the centre, and a fit switch at the bottom right picks *Fit*
  (whole image), *Width*, *Height* or *100 %*; the image never leaves the view. Double-click toggles 100 % at that
  point and back to Fit. Keys: + / − zoom, 0 Fit, 1 100 %, W width, H height. The chosen fit carries to the next
  file and follows window resizes; a manual zoom resets to Fit on the next file.
- **Preview panel:** resizable by dragging its left edge (256–720 px, kept per browser); a sticky header *Preview* with **×** to close it (⚙ stays in the table-side header row, left of
  the panel); then the image; the edits not yet applied; Dates, Location, Device, Author, File (labels and values
  each take half the width); the duplicate set stacked one copy per block (name, confidence, path; ✓ = kept copy,
  accent bar = this file); **all metadata tags** in a monospace list.
- **Hover:** a light tint on the row and the column under the pointer, a stronger tint and outline on the cell; the
  column header lights up too. The same works on the header row: hovering a header tints the header row, the
  column and that header cell. Tints are overlays, so they also work on purple and group rows. The arrow keys move
  the same highlight (row, column, cell) with the cursor: whichever moved last, mouse or keyboard, owns it. While a
  cell menu is open, its cell keeps the highlight and the ⋯ even when the pointer moves onto the menu.
- **Cell menu:** hovering a data cell shows **⋯** on its right (not in the checkbox, grouping └ or thumbnail
  columns); it, or a right-click on the cell, opens a sheet titled with the column and the value:
  - *Filter this column by this value* (an empty cell filters on “is empty”);
  - *Change* — only date, place and device values can be changed, because plans write only those (other columns
    show these items disabled): *Edit this value…* (a dialog; the value applies to this file only; in Filename / New filename it reads *Rename with a pattern…* and opens the naming-pattern dialog),
    *Remove this value* (disabled: plans cannot clear a metadata value yet), and *Apply this value ›*, a submenu
    (opens on hover or click, beside the menu; to its left near the window's right edge): *To its group / To the
    selection (n) / To all rows* (for date, place and device cells) and *To column…*, a small dialog to pick the field
    it feeds (a date: *New date (capture date)* only; any other value, e.g. Model or Author: *New place* or *New device*)
    and the scope (its group / the selection / all rows). Each fills the purple edit column of that field (it sits after the field it edits,
    e.g. New date after Date taken, not after the clicked column), as a preview; nothing changes until *Save edits* and a plan. A value typed for one file wins over a
    rule; a rule limited to a group or a selection leaves the other files at their current value.
  - *Compare*: *Use this row as the reference of its group* (★ in the expander column; red / green and the
    confidence notes compare against it) or *Stop using this row as the reference*.
- **Navigation:** the left navigation is hidden on the Workbench to give the table the full width; the sidebar icon
  left of the title shows or hides it.
- 60 groups load at a time as you scroll. The table scrolls in its own box with a sticky header.

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

- **Design system** (ADR-017): modelled on the Atlassian Design System. Every length is on the scale
  **2 · 4 · 8 · 16 · 24 · 32 · 40 · 48 · 64** (`--space-025` … `--space-800`); larger sizes (widths, column widths,
  panels) are multiples of 8, and 1 px hairlines are the only exception. Type: 12/16, 14/20, 20/24, 24/32
  (size/line height). Radius: 4 px for controls and chips, 8 px for panels, sheets and dialogs.
- **One look per kind of control**, on every screen:
  - Buttons (`.btn`): 32 px tall, 16 px side padding, 4 px radius; *primary* (filled accent), default (outlined),
    *danger*; `.btn--small` (24 px) only inside tables and filter rows; `.btn--icon` is square.
  - Text fields, search fields and selects: 32 px, 8 px padding, a stronger border than hairlines, accent focus ring.
    Native look is switched off (`appearance: none`, Safari search fields included); selects draw their own arrow.
  - Checkboxes and radios: 16 px, accent colour; a label wrapping one lines it up with an 8 px gap.
  - Menus and sheets (column menu, cell menu, settings): 4 px padding, 8 px radius, items 32 px tall,
    uppercase 12 px section labels.
  - Chips 24 px (filters, tokens); badges, status and meta-check pills 16 px.
  - Small icon buttons (⋯ in cells and headers, ✓/× in the grid): 24 px.
  - Tables: Workbench rows 32 px, headers 40 px; column widths snap to 8 px when resized.
  - *Compact view* steps one size down the same scale: controls and rows 24 px, ⋯ buttons 16 px, body text 12/16.
- Only **CSS custom properties** for colours, spacing, sizes, radius and type, in `static/css/tokens.css`.
  Dark values live in one block applied by `prefers-color-scheme` unless `<html data-theme="light">`, and by
  `data-theme="dark"`. The **settings button** (⚙, top right of every page) opens a dropdown sheet:
  - Page items first, under the page's name: on the Workbench (*Filomena*), the *Preview panel* checkbox,
    *Columns…* (opens the column picker) and *Reset view* (keeps Text colours).
  - *Appearance*: Light / Dark (light is the default; sets `data-theme`), *Text colours* (Workbench only: green/red
    for same/different, saved in the view), *Cell borders* (on by default), *Shade odd rows* (off by default) and
    *Compact view* (slimmer table rows and tighter spacing and type everywhere). Theme, borders, shading and density
    are per-viewer preferences in `localStorage`, applied as `data-theme`, `data-borders`, `data-zebra` and
    `data-density` on `<html>` before the first paint (`static/js/theme.js`).
  - *All settings…*. Esc or a click outside closes the sheet.
- Files: `base.css` (elements), `layout.css` (app shell, grids), `components.css` (cards, badges, tables, fields).
- Semantic class names (`.card`, `.member--keeper`, `.badge--high`, `.field--accepted`), BEM-style.
  The only inline style is the computed `clip-path` of the swipe compare.
- Confidence bands: verified ≥ 98 %, high ≥ 90 %, medium ≥ 70 %, low ≥ 40 %, unknown. Colour is never the only
  signal: the badge also shows the number.
- Layout: left nav 232 px, main content up to 1504 px. Works at 1280 px and up; below 1024 px the nav stacks on top.
- Respect `prefers-reduced-motion`.

## Accessibility and keyboard

- All actions are buttons or links reachable by keyboard, with visible focus rings.
- Thumbnails have alt text (file name + date).
- Planned keyboard review for steps 2–4: `j/k` or `←/→` move, `a` accept, `Enter` keep, `x` mark,
  `n` not duplicates, `i` ignore, `Space` compare, `?` help (`K` was dropped: it clashed with `k`).
