---
name: file-naming
description: The file-naming engine (pattern tokens, column tags, separator tidying, collision-free rename preview) and the two UIs that edit and show it - the step 3 rename grid and the Workbench "Naming pattern" dialog with presets. Use when rebuilding or changing how new file names are rendered, previewed, picked or stored.
---

# File naming

Code: `meta_duplo/harmonize.py` (section "file names"), `meta_duplo/web/rename_grid.py`,
`meta_duplo/web/templates/step3.html` (+ `partials/grid_rows.html`, `grid_row.html`, `grid_oob.html`),
`meta_duplo/web/templates/workbench.html` (block `.namer`), `meta_duplo/web/static/js/workbench.js`
(`Alpine.data("namer", …)`), `meta_duplo/web/app.py` (`/wb/name-preview`, `/wb/presets`, `wb_ctx`),
CSS in `meta_duplo/web/static/css/components.css` (`.namer*`). Decisions: ADR-012 (rule syntax), ADR-015
(rename grid), ADR-016 (Workbench). Nothing here writes to disk: names reach the disk only through a rename
plan (skill 08).

## 1. Where the pattern lives

| Store | Key | Meaning |
|---|---|---|
| `settings` | `filename_pattern` | The active rule. Default (`db.DEFAULT_SETTINGS`) equals `harmonize.DEFAULT_PATTERN`. Used by `rename_preview` and every rename plan. |
| `settings` | `name_presets` | JSON list `[{"name": str, "pattern": str}, …]` of user presets (built-in *Default* is not stored). |
| Workbench view (`wb.load_view`) | `view["edits"]["filename"]` | `{"kind": "pattern", "pattern": str, "overrides": {}}` – the purple *New filename* column. Becomes `filename_pattern` only on commit (`wb.commit`, see §8). |

Setters: `POST /settings/pattern` (step 3, saves as you type; empty → `DEFAULT_PATTERN`; answers 204 +
`HX-Trigger: grid-reload`) and `wb.commit(...)` (Workbench: `lib.set_setting("filename_pattern", edits.filename.pattern)`
when `only in (None, "filename")` and the rule has a pattern).

## 2. Constants (harmonize.py)

```python
TOKEN   = re.compile(r"\{(\w+)(?::([^}]*))?\}")          # {key} or {key:arg}; arg = anything but "}"
ILLEGAL = re.compile(r'[/\\:*?"<>|\x00-\x1f]')           # replaced by "-"
DEFAULT_PATTERN = "{date:%Y-%m-%d %H-%M-%S} ({location_name}, {device_name}) - {original_filename}.{ext}"
TIME_FMT = "%Y-%m-%d %H:%M:%S"                            # metadata.TIME_FMT: every stored naive local time
```

`TOKENS_HELP` (dict token → help text, passed to templates as `tokens`; step3.html does not use it, it
hard-codes its own chip list):

| Key | Help |
|---|---|
| `{date:%Y-%m-%d %H-%M-%S}` | capture date and time (strftime codes) |
| `{location_name}` | place (town) |
| `{country}` | country code |
| `{device_name}` | camera or phone |
| `{original_filename}` | first name the file was seen with, without extension |
| `{ext} / {EXT}` | extension, lower / upper case |
| `{tag:KEY} / {tag:KEY\|%Y}` | any Workbench column, e.g. `{tag:exif:EXIF:DateTimeOriginal\|%Y-%m-%d}` (optional date format) |

`NAME_TOKENS` – the dialog's "Recommended values", list of `(group, label, token)` in this order:

| group | label | token |
|---|---|---|
| Date | Date and time | `{date:%Y-%m-%d %H-%M-%S}` |
| Date | Date | `{date:%Y-%m-%d}` |
| Date | Time | `{date:%H-%M-%S}` |
| Date | Year / Month / Day | `{date:%Y}` / `{date:%m}` / `{date:%d}` |
| Date | Hour / Min / Sec | `{date:%H}` / `{date:%M}` / `{date:%S}` |
| Place | Place / Country | `{location_name}` / `{country}` |
| Device | Device | `{device_name}` |
| File | Original filename / Extension / EXTENSION | `{original_filename}` / `{ext}` / `{EXT}` |

## 3. `render_name(pattern, values, original_name, ext) -> str | None`

`values` keys: `capture_time` (TIME_FMT string or ""), `location_name`, `country`, `device_name`, optional
`tags` (dict column-key → value). `original_name` is `files.original_name` (first name ever seen; renames never
change it, so re-renaming never nests). `ext` is `files.ext` (stored lower case without dot by the scanner).

Steps:

1. `t = values.get("capture_time") or ""`, `stem = PurePosixPath(original_name).stem` (a path in
   `original_name` keeps only the last component's stem; `a.b.JPG` → `a.b`).
2. `TOKEN.sub(sub, pattern)` with, per match `key, fmt`:

| Token | Output |
|---|---|
| `{date}` / `{date:FMT}` | `datetime.strptime(t, TIME_FMT).strftime(FMT or "%Y-%m-%d %H-%M-%S")`; **no date → raise LookupError** |
| `{location_name}` | `values.get("location_name", "")` |
| `{country}` | `values.get("country", "")` |
| `{device_name}` | `values.get("device_name", "")` |
| `{original_filename}` | `stem` |
| `{ext}` / `{EXT}` | `ext.lower()` / `ext.upper()` |
| `{tag:KEY}` / `{tag:KEY\|FMT}` | if `values.get("tags") is None` → raise LookupError; else `field, _, tfmt = fmt.partition("|")`, `_tag(values["tags"].get(field), tfmt)` |
| anything else (`{foo}`) | left verbatim (`m.group(0)`) |

   `_tag(raw, fmt)`: `val = "" if raw is None else str(raw)`; when `fmt` and `val`, try
   `datetime.strptime(val[:19], f).strftime(fmt)` for `f in (TIME_FMT, "%Y:%m:%d %H:%M:%S")` (table dates, raw
   EXIF dates); if neither parses, return `val` unformatted. A missing tag is `""` (not an error).
3. `LookupError` anywhere → return **None** (caller keeps the old name, status `no_date`).
4. Tidy separators left by empty tokens, in this order:

```python
name = re.sub(r"\(\s*,\s*", "(", name)     # "( , Apple)" -> "(Apple)"
name = re.sub(r",\s*\)", ")", name)        # "(Wien, )"  -> "(Wien)"
name = re.sub(r"\(\s*\)", "", name)        # "()"        -> ""
name = re.sub(r"\s{2,}", " ", name)
name = re.sub(r"\s+\.", ".", name).strip(" -_")
```
5. `name = ILLEGAL.sub("-", name)` (after tidying: a `:` from a strftime code becomes `-`).
6. 200-char stem cap: `stem_part, dot, ext_part = name.rpartition(".")`; if there is a dot and
   `len(stem_part) > 200` → `stem_part[:200].rstrip() + "." + ext_part`. No cap without a dot.
7. Return `unicodedata.normalize("NFC", name)`.

Reference outputs (verified against the code):

| pattern | values | original, ext | result |
|---|---|---|---|
| DEFAULT | time 2020-01-02 03:04:05, Wien, Apple iPhone 12 | IMG_1.JPG, JPG | `2020-01-02 03-04-05 (Wien, Apple iPhone 12) - IMG_1.jpg` |
| DEFAULT | time only, device Canon | IMG_1.JPG, JPG | `2020-01-02 03-04-05 (Canon) - IMG_1.jpg` |
| DEFAULT | time only | IMG_1.JPG, JPG | `2020-01-02 03-04-05 - IMG_1.jpg` |
| DEFAULT | no time | IMG_1.JPG | `None` |
| `{foo} {date:%H:%M} {country}.{ext}` | time, AT | a.jpg | `{foo} 03-04 AT.jpg` |
| `{tag:make} {tag:file_date\|%Y} ({tag:lens}) - {original_filename}.{ext}` | tags make=Apple, file_date=2020-01-02 03:04:05, lens="" | IMG_1.JPG | `Apple 2020 - IMG_1.jpg` |
| `{tag:make}.{ext}` | no `tags` key | a.jpg | `None` |
| `{tag:exif:EXIF:DateTimeOriginal\|%Y}.{ext}` | tag `2019:05:18 11:47:10` | a.jpg | `2019.jpg` |
| `A`×300 + `.{ext}` | – | a.jpg | 200 × `A` + `.jpg` |

## 4. Column tags: `set_tag_source`, `tag_values`, `has_tags`

```python
_tag_source: Callable[[Library], dict[int, dict]] | None = None
def set_tag_source(fn) -> None          # registered by the web layer
def has_tags(pattern: str) -> bool      # "{tag:" in (pattern or "")
def tag_values(lib) -> dict[int, dict] | None   # file_id -> {column key: value}; None when no provider
```

The core keeps no copy of the Workbench table logic. At import time, the last line of
`meta_duplo/web/workbench.py` registers the provider:

```python
harmonize.set_tag_source(lambda lib: {r["id"]: r["v"] for r in build_rows(lib)[0]})
```

So `{tag:KEY}` resolves against the row dict `v` of the Workbench (`build_rows`): core keys such as `path`,
`filename`, `date`, `digitized`, `modify`, `file_date`, `gps_date`, `name_date`, `key_dto`, `key_created`,
`rec_date`, `place`, `rec_place`, `gps`, `country`, `device`, `rec_device`, `make`, `model`, `software`,
`serial`, `lens`, `author`, `copyright`, `owner`, `size`, `dims`, `ext` (upper case), `kind`, `dup_count`,
`dup_conf`, plus every ExifTool tag as `exif:<Group:Tag>` (e.g. `exif:EXIF:DateTimeOriginal`). These are the
file's **current** values (not the purple edit values). Without the provider registered (core used without the
web package), a pattern with tags renders `None` for every file and every file keeps its name.

## 5. `rename_preview(lib, pattern=None, limit=None) -> list[dict]`

New names for every present file, collision-free by construction.

1. `pattern = pattern or lib.setting("filename_pattern")`; `tags = tag_values(lib) if has_tags(pattern) else None`.
2. `values = effective_values(con)` (capture_id → accepted/edited proposal value, else the current value; plus
   `lat`/`lon`/`country` when the place proposal is accepted/edited and has GPS in `extra`; skill 06).
3. Rows: `files f JOIN captures c ON c.file_id = f.id LEFT JOIN rename_exclude x`, `WHERE f.present = 1 AND
   f.removed = 0 ORDER BY f.rel_path`; columns `id, source_id, rel_path, name, original_name, ext, capture_id,
   excluded`.
4. `taken[(source_id, parent_dir)]` = set of **lower-cased** current names of all those rows (case-insensitive,
   like APFS/HFS+).
5. Per row (in `rel_path` order): `vals = values[capture_id]` (+ `"tags": tags.get(id, {})` when tags are on),
   `new = render_name(pattern, vals, original_name, ext or "")`, then:

| Condition (checked in order) | `new` | `status` |
|---|---|---|
| row is in `rename_exclude` | old name | `excluded` |
| `new is None` (no date / no tag provider) | old name | `no_date` |
| `new != name` and `new.lower()` free in its folder | new | `ok` |
| `new != name` and taken | `"{base} ({n}).{ext}"` (or `"{new} ({n})"` without a dot), n = 2, 3, … until free | `renumbered` |
| `new == name` | old name | `unchanged` |

   After an `ok`/`renumbered` row: `used.discard(old.lower())`, `used.add(new.lower())` – a later file may take
   a name the earlier file freed; a later file can never take a name an earlier file now uses.
6. Output item: `{"file_id", "capture_id", "rel_path", "old", "new", "status"}`; stop after `limit` items.

Not considered for collisions: files without a `captures` row (sidecars, "other" kinds), files of other
sources. A clash with such a file on disk surfaces at apply time (`PlanError("target exists: …")`, skill 08).

## 6. Step 3 rename grid (`/step/3`)

`rename_grid.py` – read model, one row per file.

`Column(key, label, default=True, kind="text"|"num"|"field"|"status", field="")`, `COLUMNS` in this order
(default-visible marked ✓): `old` Filename ✓, `new` New filename ✓, `folder` Path ✓, `status` Status ✓
(kind status), `date` Date ✓ (field `capture_time`), `place` Place ✓ (`location_name`), `device` Device ✓
(`device_name`), `confidence` Date confidence ✓ (num), `date_source` Date from, `dto` DateTimeOriginal,
`modify` DateTime (modified), `file_date` File date, `make`, `model`, `software`, `gps` GPS, `size` (num),
`dims` Dimensions, `copies` Copies (num).

- `FILTERS`: `all` All files · `pending` Needs a decision · `changes` Name changes · `problems` Problems ·
  `excluded` Excluded.
- `STATUS_LABELS`: ok OK · unchanged Unchanged · no_date No date · renumbered Renumbered · excluded Excluded ·
  readonly Read-only · pending Needs decision.
- `visible_columns(lib)` reads setting `rename_columns` (JSON list; invalid/empty → defaults);
  `save_columns(lib, keys)` stores them in canonical `COLUMNS` order (`POST /step/3/columns`, `cols` multi-value,
  → 204 + `grid-reload`).
- `build_rows(lib)`: joins `rename_preview(lib)` with files/sources/meta/proposals. Per field column:
  `{"now", "new", "status", "changed", "confidence", "why", "capture", "field"}` with
  `changed = bool(value) and value != current`. Row status: preview status, turned into `readonly` when the
  source is not writable or `role == "reference"` and `new != old`; turned into `pending` when any field is
  `suggested` and changed (unless excluded). `folder` = source label + `/parent`.
- `query(lib, filt, q, sort, desc, offset, limit)` → `(page, total, counts)`; `counts` per filter; `problems` =
  `no_date | readonly | renumbered`; search `q` matches old, new or folder (case-insensitive); sort by any
  column (status by label), empty values last in both directions, ties by folder then old name. Page size
  `GRID_PAGE = 200` (app.py); `/rename/rows?offset=…` loads the next page via an `hx-trigger="intersect once"` row.

`step3.html`:

- Header text and **Build/Rebuild recommendations** (`POST /harmonize`, starts the `recommend` job).
- **File name rule** form: `<input id="pattern-input" name="pattern">`, `hx-post="/settings/pattern"`,
  `hx-trigger="input changed delay:400ms from:#pattern-input, submit"`. Chip buttons insert at the caret (Alpine
  `insert(t)`: splice at `selectionStart/End`, refocus, caret after, dispatch `input`): `{date:%Y-%m-%d %H-%M-%S}`,
  `{location_name}`, `{country}`, `{device_name}`, `{original_filename}`, `{ext}`, `{EXT}`; **Reset to default**
  sets `DEFAULT_PATTERN`.
- Filter tabs with counts; search (`hx-get /step/3`, 300 ms); **Accept all ≥ 90 %** / **≥ 70 %**
  (`POST /proposals/accept min_conf`); **Columns ▾** checkbox menu.
- `#grid` table (reloads on `grid-reload`): first column a checkbox per file (checked = included;
  `POST /rename/exclude` with `file_id` and `on` → inserts/deletes `rename_exclude`, answers the row
  out-of-band); `new` shows `= unchanged` (muted) when not renamed; field cells show `now → new`, confidence,
  ✓ accept / ✗ keep current / ↺ reset, double-click to type (`action=edit`; date input pattern
  `\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}`). Decisions re-render all rows of that capture (`grid_oob`).
- Bottom bar (when any row): "N files get a new name", warning "M still need a decision; they use their current
  values", checkboxes **Write XMP sidecars** (checked) and **Set file date to capture date**, button
  **Review rename plan…** → `POST /plans/rename` → `HX-Redirect: /plans/{pid}`.

## 7. Workbench naming-pattern dialog (`.namer`)

### Context from `wb_ctx` (app.py)

| Key | Value |
|---|---|
| `name_tokens` | `harmonize.NAME_TOKENS` |
| `name_fields` | `wb.name_fields(catalog, shown_keys)` – `("Columns in this table", label, "{tag:KEY}")` for each shown column, then `(category, label, "{tag:KEY}")` for every catalog field; skips `kind == "special"` and `edit:` keys |
| `name_labels` | `{token: label}` over `NAME_TOKENS + name_fields` (JSON-passed to `namer(labels)`) |
| `name_options` | `[("Recommended values", label, tok) for NAME_TOKENS] + name_fields` – the list as rendered |
| `pattern` | `lib.setting("filename_pattern")` |
| `name_pattern` | `view.edits.filename.pattern` or `filename_pattern` or `DEFAULT_PATTERN`; rendered as `#wb-table[data-pattern]` |

### Entry points (all end in `openEdit({target: "filename"})` → `dialog = "naming"` + `window` event `wb-naming` → `namer.start()`)

1. **Header menu** (⋯ on a column header): *Edit these values…* on the Filename column (its `target` is
   `filename`), *Edit all* on the purple *New filename* column.
2. **Cell menu** (⋯ or right-click on a cell): on `filename` / `edit:filename` (`menu.isName`) the item reads
   **Rename with a pattern…**; header cells render `data-target=""` for filename, so `openCellEdit()` sees no
   target and opens the dialog.
3. **Double-click a file name** cell (`th[data-key="filename"]`): clears the text selection, dispatches
   `wb-edit {target: "filename"}`.
4. **Actions → File name pattern…** (wb_table.html), hint "text and parameters from any column; a purple “New
   filename” column previews it".
5. Also: clicking a source link (`.srclink`) in a *New filename* cell dispatches `wb-edit` for that target.

### Layout (workbench.html, inside `.modal`)

```
.modal__box.modal__box--wide.namer  (width min(1040px, 96vw); x-data='namer({{ name_labels|tojson }})')
├─ .colpick-head: h3 "Naming pattern", × close
├─ .namer__body  (grid: 240px | 1fr)
│  ├─ aside.namer__side "Presets"
│  │   ul.namer__presets > li.namer__preset[.is-active]  (button: name + " •" if modified; pattern via pretty())
│  │       hover: ✎ rename, × delete (not for builtin); inline input when renaming / adding
│  │   .namer__sidefoot: [Update “name”] (active, saved, modified) · [+ Save as new preset] · .namer__err
│  └─ .namer__main
│      .namer__preview: "New file name preview for '<file>'" (or "…for the first file") above <strong.mono> new name
│      label "New filename"; .namer__edit (resizable, wraps): parts = text inputs + chip buttons
│      p.namer__warn (red, role=alert) while !/\.\{(ext|EXT)\}$/.test(pattern())
│      p.namer__hint  (two variants, see below)
│      .namer__picker (one bordered box): search (× clear, line under it, stays on top) + .namer__list.wbsheet (max-height 320px, scrolls, always open)
└─ .modal__actions: [Cancel] [Apply]
```

List rendering (Jinja): for each `(g, label, tok)` in `name_options`, a section header
`div.wbsheet__section.namer__group[data-group=g]` whenever the group changes (`x-show="groupHas(g)"` hides empty
sections), then `button.namer__opt[data-tok][data-group][data-q]` showing `<label>` plus, on the right,
`span.namer__val` = `optVal(tok)` or `—` (`.is-empty`). `data-q = lower(label + (" " + tok[5:-1] if tag))`, i.e.
the name plus a column tag's key (`exif:EXIF:DateTimeOriginal`), never the format codes. Sections in order:
**Recommended values**, **Columns in this table**, then the catalog categories. `.is-current` marks the option
equal to the selected chip. "No parameter matches." when `q && !anyMatch()`.

Hint line: no chip selected → "Click a tag to add it at your text cursor; click a parameter in the name to
replace or remove it." Chip selected → "Click a tag to replace <techName> · Remove it · Keep it".

### Component state (`Alpine.data("namer", (labels = {}) => …)`)

`parts` (`[{t: "text"|"tok", v}]`, always alternating text/tok/…/text), `picking`
(selected chip index, −1 none), `q`, `at` (`{i, pos}` last text caret), `vals` (token → value in the current
pattern), `optVals` (token → value for the list), `name`, `file`, `presets`, `preset`, `loaded`, `renaming`,
`renameTo`, `adding`, `newName`, `err`.

| Method | Behaviour |
|---|---|
| `init` | listens to `wb-naming` → `start()`; deep-watches `parts` → debounced 250 ms `refresh()` |
| `parse(p)` | split on `/\{[^}]+\}/g` into text/tok/…/text (leading and trailing text parts always exist) |
| `pattern()` | `parts.map(p => p.v).join("")` |
| `start()` | reads `#wb-table.dataset.pattern`; resets picking −1, q, err, adding; `loadPresets()` then marks the preset whose pattern equals it (`preset`, `loaded`); `refresh(true)` |
| `techName(tok)` | `{tag:(exif:)?KEY…}` → `KEY` (e.g. `EXIF:DateTimeOriginal`, `file_date`); known token → `<Label>`; unknown → token without braces |
| `pretty(p)` | pattern with every token replaced by `techName` (preset subtitles) |
| `caret(i, ev)` | remember `at = {i, pos: selectionStart}` on focus/keyup/click/input of a text part |
| `focusEnd()` | click on the empty field area focuses the last text part |
| `selectTok(i)` | `picking = i`, clear `q`, focus the chip, scroll its `.is-current` option into view |
| `textAt(i, pos)` | caret into text part `i` (`pos -1` = end), `picking = -1` |
| `leaveTok(i, dir)` | `textAt(i + dir, dir < 0 ? -1 : 0)` (*Keep it* = `leaveTok(picking, 1)`) |
| `stepTok(i, dir)` | among visible options (`offsetParent !== null`, so search-filtered), replace chip with previous/next (clamped; not found → first/last), keep selected, scroll |
| `choose(tok)` | chip selected → replace it, stays selected; else split text part `at.i` (or last text part) at `at.pos` into text/tok/text, then `at = {i: idx + 2, pos: 0}` so repeated clicks add in order; `picking = -1` |
| `removeTok()` | merge `parts[i-1].v + parts[i+1].v` into one text part, caret at the join |
| `match(text)` | `q.trim().toLowerCase()` with whitespace collapsed; `!q` or `text.includes(q)` – a phrase, not words |
| `groupHas(g)` / `anyMatch()` / `pickFirst()` | section has a match / any match / Enter in search chooses first match |
| `refresh(options)` | `POST /wb/name-preview` (`pattern`, `fid = $store.wb.active`, `options = "1"|""`) with header `X-Meta-Duplo-Token`; sets `vals`, `name`, `file`, and `optVals` when returned |
| `apply()` | `#wb-table.dataset.pattern = pattern()`; `postView({action: "edit_set", target: "filename", pattern})`; close |

Chip text: `techName(tok)` (there is no Tech names / Preview switch; values show in the preview line and the list); title `label — token`. Text inputs size to
content (`width: <len>ch`, 2px when empty).

### Keyboard

| Where | Key | Effect |
|---|---|---|
| text part | ← at start (i > 0) | select previous chip |
| text part | → at end | select next chip |
| text part | Backspace at start | select previous chip (a second Backspace removes it) |
| text part | Delete at end | select next chip (a second Delete removes it) |
| chip | ← / → | caret to end of previous / start of next text part |
| chip | ↓ / ↑ | swap for next / previous tag of the (filtered) list, live |
| chip | Backspace / Delete | `removeTok()` |
| chip | Esc | leave chip to the right (does not close the dialog: `stopPropagation`) |
| picker | Esc | `picking = -1` (`@keydown.escape.stop`) |
| search | Enter | `pickFirst()` |
| dialog | Esc | `close()` (window-level, on `.modal`) |

Options use `@mousedown.prevent` so clicking one does not steal focus from the caret.

### Presets

Sidebar: click loads (`load(p)`: parse, `preset = name`, `loaded = pattern`); double-click or ✎ starts inline
rename (Enter or blur saves once, Esc cancels); × deletes after `confirm("Delete the preset “name”?")`.
`modified()` = `preset && pattern() !== loaded` → purple " •" and the *Update “name”* button (saved presets
only). *+ Save as new preset* opens an inline name field (Enter saves, Esc cancels). Client validation
`nameError`: empty → "Give the preset a name."; duplicate (incl. "Default") → "“name” exists already.".

## 8. Endpoints

### `POST /wb/name-preview` (form: `pattern`, `fid`, `options`)

1. `view = wb.load_view(lib)`, `rows = wb.build_rows(lib)[0]`, `wb.apply_edits(rows, edits without "filename")`.
2. Row = the one with `id == fid`, else the first row; none → `{"name": "", "values": {}, "file": ""}`.
3. `values = {capture_time: e.date ?? (effective.capture_time or v.date), location_name: e.place ?? (effective
   or v.place), device_name: e.device ?? (effective or v.device), country: effective.country or v.country,
   tags: v}` – the purple New date / place / device values win.
4. `alone(tok)` = `render_name(tok + ".x", …)` minus the trailing `.x` (`""` when None) – a token as it reads in
   a name.
5. Response:

```json
{"name": "<rendered name>" | "(no date for this file: it keeps its name)",
 "values": {"{date:%Y}": "2019", "{tag:make}": "Apple"},
 "file": "IMG_0001.JPG",
 "options": {"{date:%Y-%m-%d %H-%M-%S}": "…", "{tag:<every key of v>}": "…"}}
```
   `options` only when `options` is non-empty: every `NAME_TOKENS` token plus `{tag:K}` for every key of `v`.

### `GET /wb/presets` → `{"presets": [{"name": "Default", "pattern": DEFAULT_PATTERN, "builtin": true}, …saved]}`

### `POST /wb/presets` (form: `action`, `name`, `pattern`, `new`) → same shape

| action | Rule (names stripped) |
|---|---|
| `save` | if `name` and `name != "Default"`: remove any same-named preset, **append** `{name, pattern}` (update = moves to the end) |
| `delete` | drop presets named `name` |
| `rename` | if `new`, `new != "Default"` and no preset already named `new`: rename `name` → `new` in place |
| other / rule fails | no change |

Always rewrites setting `name_presets` and returns the list. Invalid JSON in the setting reads as `[]`.

### From dialog to disk

*Apply* stores `view.edits.filename = {kind: "pattern", pattern, overrides}` (`/wb/view action=edit_set`) and adds
the purple `edit:filename` column (width 304) after Filename. `wb.apply_edits` renders it per row with the same
values as the preview; `—` when `render_name` is None. The pattern becomes `filename_pattern` when the user
saves edits (`POST /wb/commit`, toast "Saved the filename pattern; review the rename plan to apply it") or
starts *Actions → Rename…* (`POST /wb/rename-plan`, which commits first; skill 08).

## Known gaps (as implemented; reproduce or fix deliberately)

| Gap | Effect |
|---|---|
| `strip(" -_")` runs on the whole name | a pattern whose stem renders empty (`{tag:none}.{ext}`, `- {location_name} -.{ext}`) gives `.jpg`, a hidden file |
| The JS splits on `/\{[^}]+\}/`, Python matches `\{(\w+)(?::([^}]*))?\}` | `{foo bar}` shows as a chip in the dialog but stays literal text in the name |
| Unknown tokens stay verbatim | `{foo}` ends up in the file name |
| Collision check covers only captured, present files of the same source | a clash with a sidecar or "other" file fails at apply time (`target exists`) |
| The tag provider uses current values (`v`) | `{tag:date}` ignores the purple New date; `{date}` follows it |
| step3.html hard-codes its chip list | `TOKENS_HELP` is passed as `tokens` but not rendered; no `{tag:…}` chips in step 3 |
| The text parts size with a bound `:style` width | an inline style, against CLAUDE.md rule 7 |
| `save` moves an updated preset to the end of the list | presets reorder when you click *Update* |

## Rebuild checklist

1. Add `TOKEN`, `ILLEGAL`, `DEFAULT_PATTERN`, `TOKENS_HELP`, `NAME_TOKENS` to `harmonize.py` exactly as in §2;
   seed setting `filename_pattern` with `DEFAULT_PATTERN`.
2. Implement `_tag` and `render_name` (§3): token table, LookupError → None, the five tidy regexes in order,
   ILLEGAL → `-`, 200-char stem cap, NFC. Check every row of the reference table.
3. Implement `set_tag_source` / `has_tags` / `tag_values`; register the provider at the bottom of
   `web/workbench.py` (`{r["id"]: r["v"]}` of `build_rows`).
4. Implement `rename_preview` (§5): query, case-insensitive `taken` per (source, folder), statuses in order
   excluded → no_date → ok/renumbered → unchanged, ` (n)` before the extension, discard old/add new.
5. Build `rename_grid.py` (columns, filters, labels, readonly/pending derivation, query/sort/paging) and
   `step3.html` with the rule form (400 ms autosave), chips, include checkbox (`rename_exclude`), OOB row updates
   and the plan bar.
6. Add `wb.name_fields` and the `wb_ctx` keys `name_tokens`, `name_fields`, `name_labels`, `name_options`,
   `name_pattern` (+ `data-pattern` on `#wb-table`).
7. Add `POST /wb/name-preview` (edit values win, `alone()`, `options=1`) and `GET/POST /wb/presets` with the
   save/rename/delete rules and the built-in Default.
8. Build the `.namer` markup and the `namer` Alpine component: parts model, preview line,
   extension warning, hint, search, always-open grouped list with per-tag values, presets sidebar.
9. Wire the entry points: header menu, cell menu "Rename with a pattern…", double-click on a file name,
   Actions → File name pattern…, source links.
10. Implement the keyboard table and the 250 ms debounced refresh; confirm Esc on a chip does not close the dialog.

## Verify

- `uv run pytest tests/test_workflow.py::test_render_name_tidies_missing_parts` – DEFAULT with time only →
  `2020-01-02 03-04-05 - IMG_1.jpg`; no time → `None`; place only → starts `2020-01-02 03-04-05 (Wien) - b`.
- `tests/test_workflow.py::test_steps_3_and_4_and_plans` – after `accept_above(0.7)`: `IMG_0001.JPG` →
  `2019-05-14 18-22-03 (Bratislava, Apple iPhone 12) - IMG_0001.jpg`, `IMG_4102.JPG` →
  `2018-12-24 17-31-12 (Canon EOS 5D Mark III) - IMG_4102.jpg`.
- `tests/test_workflow.py::test_excluded_files_keep_their_name` – excluded file: status `excluded`, new = old,
  and no plan action for it.
- `tests/test_workbench.py::test_name_pattern_with_column_tags` – `Apple 2020 - IMG_1.jpg`; tags without
  values → `None`; with the provider registered, `{tag:make}_{tag:date|%Y}_{original_filename}.{ext}` in
  `rename_preview` starts with `<make>_<year>_`.
- `tests/test_workbench.py::test_edit_rules_commit_and_partial_rename` – a shifted date + `{"kind": "pattern"}`
  renders `2018-12-24 18-31-12 (Canon EOS 5D Mark III)…`; after `wb.commit` the preview uses it.
- Manual / Playwright (skill 13): open the dialog four ways; click tags (inserted in order at the caret); select
  a chip, ↓ swaps it live, Backspace removes; search "date time" keeps only phrase matches and hides empty
  sections; Enter picks the first; remove `.{ext}` → red warning; save, modify (•, Update), rename, delete a
  preset; *Default* cannot be renamed or deleted; Apply adds the purple New filename column.
