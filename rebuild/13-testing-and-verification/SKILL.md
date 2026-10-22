---
name: testing-and-verification
description: How to prove a (re)built meta-duplo works - the generated demo disk and what it must produce, the pytest suite and its expected outcomes, and the Playwright browser recipes used to verify every UI change. Use before every commit and whenever rebuilding a module.
---

# Testing and verification

Every change is proven three ways, in this order:

1. **Unit / end-to-end tests** (`uv run pytest`): the engine on a generated demo disk, including apply and
   undo of real plans on temp copies (never on user data).
2. **The real app in a headless browser** (Playwright against `meta-duplo serve`): the screen behaves, no
   console errors, screenshots look right.
3. **Docs follow the code**: `docs/UI_SPEC.md` (and `docs/DECISIONS.md` for decisions) are updated in the
   same commit.

Nothing is pushed until all three are clean.

## 1. The demo disk (`meta_duplo/demo.py`)

`make_demo(root, extra=0) -> Path` deletes and recreates `root` and fills it with files that exercise
every workflow step. It writes real JPEG/EXIF (`piexif`, `PIL`) and, when `ffmpeg` is available, videos.
`uv run meta-duplo demo` builds it and adds it as a writable source of the library.

| Folder | Files | Exercises |
|---|---|---|
| `Camera/DCIM/` | `IMG_0001…0005.JPG`, `IMG_0010.JPG`, `IMG_0011.JPG` (Apple iPhone 12; dates 2019-05-14 … 2019-07-20; GPS Bratislava / Vienna / Prague / Split) | originals; `IMG_0011` is a burst neighbour of `IMG_0010` (1 s later, same seed) |
| `Canon/100CANON/` | `IMG_4101.JPG`, `IMG_4102.JPG` (Canon EOS 5D Mark III, 2018-12-24, no GPS) | second device |
| `Backup 2019/` | byte copies of DCIM files, `Copy of IMG_0003.JPG`, `christmas.jpg` (= `IMG_4101`), `beach video.MOV` | step 1: identical copies, renamed copies |
| `WhatsApp Images/` | `IMG-20190515-WA0003.jpg` (resized, recompressed, no metadata, receive date in the name) | step 2: same photo; the date must come from the original, not the name |
| `Edited/` | `prague castle.jpg` (same pixels as `IMG_0004`, EXIF date changed), `export-0005.jpg` (no metadata) | step 2: same pixels, different metadata |
| crop | `IMG_20190518_114710_crop.jpg` (only date is in its name) | step 4: matches only after harmonizing |
| videos | `IMG_0020.MOV`, renamed identical copy, re-encoded `VID-20190721-WA0001.mp4` | video duplicates |

`extra=n` adds `n` more photos, each with a backup copy, every third also as a messenger copy (paging and
smart-select tests). A large test library for the job stripe: 3000 tiny JPEGs made with PIL in a scratch
folder, added with `uv run meta-duplo --library <lib> add-source <folder>`.

GPS in the demo is written as UTC (local summer time minus 2 h), so time-zone handling is exercised.

## 2. The test suite (`tests/`)

Run: `uv run pytest -q` (about 45 s; 22 tests). Fixtures build the demo disk under `tmp_path`, add it as a
source, and run `Runner(lib).run_sync("scan", lambda job: scan_all(lib, job))`.

### `tests/test_workflow.py` — the four steps, end to end

| Test | Must hold |
|---|---|
| `test_step1_identical` | 5 identical groups; the `Copy of IMG_0003.JPG` group has 3 members; keeper is `Camera/DCIM/IMG_0003.JPG` (camera folder beats a backup) |
| `test_step2_near` | WhatsApp copy → `same_photo`; `prague castle.jpg` → `same_pixels`; `VID-…WA0001.mp4` → `same_video`; `IMG_0011` → `similar_shot` (review only, never a duplicate); the undated crop is not in step 2 |
| `test_steps_3_and_4_and_plans` | cleanup plan applies 6 moves into `_duplicates/`; proposals: WhatsApp date `2019-05-14 19:05:41` (from the original), crop `2019-05-18 11:47:10` *suggested*; after `accept_above(0.7)` names are `2019-05-14 18-22-03 (Bratislava, Apple iPhone 12) - IMG_0001.jpg` and `2018-12-24 17-31-12 (Canon EOS 5D Mark III) - IMG_4102.jpg`; `step4_recheck` pairs the crop with `IMG_0003`, never the burst; rename plan with XMP + mtime applies with 0 failures; undoing both plans restores **every byte** (MD5 tree equal) |
| `test_render_name_tidies_missing_parts` | empty place/device leave no `( , )`; no date → `None` |
| `test_read_only_source_blocks_writes` | a read-only source gets a plan with 0 actions |
| `test_excluded_files_keep_their_name` | `rename_exclude` → status `excluded`, name kept, no rename action |

### `tests/test_results.py` — steps 1–2 result pages and jobs

| Test | Must hold |
|---|---|
| `test_load_sorts_and_pages` | with `extra=12`: 17 groups, sorted by size desc, pages of 5 |
| `test_smart_select_and_plan` | smart selection never ticks a keeper; the plan holds exactly the selection |
| `test_selecting_every_copy_is_blocked` | a group with all copies ticked is refused |
| `test_cancel_stops_a_running_job` | `Runner.cancel()` stops a job at its next `job.update`; status `cancelled`; `cancel()` returns False when idle |

### `tests/test_workbench.py` — the Workbench model

| Test | Must hold |
|---|---|
| `test_duplicates_grouping_and_differences` | 3-row group for `Copy of IMG_0003.JPG`, reference in `Camera/DCIM`; diff on filename not date; `conf_label` `100 % · identical`; castle `("99 %", "date differs, place missing, renamed")`; `acronym("OffsetTimeDigitized (ExifIFD)") == "OTD"` |
| `test_filters_and_grouping_modes` | `dup_conf ≥ 100` keeps whole groups; with no grouping, `date is empty` filters rows |
| `test_edit_rules_commit_and_partial_rename` | edit rules → `commit` writes `edited` decisions; a rename plan for only the selected ids |
| `test_old_views_lose_default_edit_columns` | view migration from v1 |
| `test_selection_presets` | `preset_ids` for all / non-keepers / newer / older / less metadata |
| `test_duplicates_filter_chip_and_singles_group` | the automatic `Dup. ≥ 2` chip; singles group last |
| `test_columns_follow_the_picker_order` | `column_rank` order |
| `test_meta_check_counts` | meta check counts per tag family |
| `test_key_date_fields_first_and_xmp_date_read` | `key_dto` / `key_created` first in Dates; XMP DateCreated read |
| `test_cell_actions_scope_overrides_and_reference` | `cell_apply` scopes, typed overrides win, `ref_ids` |
| `test_view_undo_redo` | two pushes, two undos (labels *grouping* then *sorting*, `reload` true then false), nothing left, redo, a new push clears redo |
| `test_name_pattern_with_column_tags` | `{tag:make} {tag:file_date\|%Y} ({tag:lens}) - …` → `Apple 2020 - IMG_1.jpg`; no tag values → `None`; `rename_preview` resolves tags through the registered source |

When rebuilding, port these tests first; they are the acceptance criteria.

## 3. Browser verification (Playwright)

### Environment

- Chromium + Playwright are global Node packages: run scripts with `NODE_PATH=$(npm root -g) node script.js`.
  Never run `playwright install` (the browser is pre-installed; `PLAYWRIGHT_BROWSERS_PATH` points to it).
- Keep scripts and screenshots in a scratch folder, not in the repo.

### Start / stop a server on a scratch library

```bash
W=<scratch>/wb                                    # library + log live here
(export UV_SYSTEM_CERTS=1; META_DUPLO_TOKEN=devtoken \
   nohup uv run meta-duplo --library $W/lib serve --port 8766 > $W/serve.log 2>&1 &)
sleep 5                                           # first start compiles; then GET / answers 307
# … tests …
pkill -f "meta-dupl[o] --library"                 # the [o] keeps pkill from matching its own shell
```

Restart the server after any Python change (templates and static files reload without it).
First run on an empty library: `uv run meta-duplo --library $W/lib demo` (or add a folder and scan).

### Script skeleton

```js
const { chromium } = require('playwright');
const B = 'http://127.0.0.1:8766', H = { 'X-Meta-Duplo-Token': 'devtoken' };
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1400, height: 900 } });   // also check 1100 and narrow widths
  const errs = [];
  p.on('pageerror', e => errs.push(String(e)));
  p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  await p.goto(B + '/?token=devtoken');                                   // sets the session cookie
  await p.request.post(B + '/wb/view', { form: { action: 'reset' }, headers: H });   // known start state
  await p.goto(B + '/workbench'); await p.waitForTimeout(1000);
  // … interact, collect facts into `info`, take screenshots …
  console.log(JSON.stringify({ ...info, errs }, null, 1)); await b.close();
})();
```

Rules that keep these checks honest:

- **Set state through the API, test through the UI.** `p.request.post('/wb/view', {form: {action: …}})` builds
  a scenario fast (sort, filter, edit rules, cell_apply…); then click, type and press keys like a user.
- **Report facts, not "it worked"**: computed styles (`getComputedStyle(el).borderTopWidth`, `::after`),
  bounding boxes (pinned columns keep `left` while `.wb-scroll.scrollLeft` changes), `document.activeElement`,
  Alpine state (`Alpine.store('wb')`, `Alpine.$data(el)`), counts of `.is-diff` cells, DB rows for jobs.
- **Always print `errs`**; any console error fails the check.
- **Look at every screenshot** (crop with `clip`) in light and, for colour work, dark theme
  (`newPage({colorScheme: 'dark'})` plus `document.documentElement.dataset.theme = 'dark'`).
- **Reset the view at the end** so the next script starts clean.

### Pitfalls met while building

| Symptom | Cause / fix |
|---|---|
| Click times out on a list item right after typing in a search | `x-show` updates on the next tick: `waitForTimeout(150)` after `fill` |
| Strict-mode violation (2 elements) | two inputs share a class (new-preset vs rename field): select by role/label (`getByRole('textbox', {name: …, exact: true})`) |
| Right-click on an off-screen cell closes the menu | scrolling closes menus: `scrollIntoView` first, wait, then click |
| Keys typed too early go nowhere | focus moves in `$nextTick`: wait ~200 ms after the key that moves focus |
| Element "not visible" though rendered | sheets use `x-transition.opacity`: wait for the transition |
| Server killed the shell (exit 144) | `pkill -f "meta-duplo …"` matched itself: use `meta-dupl[o]` |
| Job stripe untestable on the demo | it scans in < 1 s: use the 3000-file scratch folder and act within 2 s |

### What to check per area (minimum)

| Area | Facts to assert |
|---|---|
| Table | no `.is-diff` with grouping *None*; filtered column orange, sorted cyan, both dashed (`::after` gradient); pinned column `left` constant while scrolling; slim column tooltip appears without delay |
| Menus | header ⋯ items (Pin / Unpin, Sort, Find, Change; edit columns: Apply · Edit all · Clear · Remove); cell menu enables *Rename with a pattern…* on file names |
| Edit columns | `= value` vs `→ value`; no `.srclink`; hovering an edit cell with `data-src` outlines the source (`.is-recsrc`) and shows `#wb-srcarrow` (line between the facing edges); leaving the table hides it |
| Naming dialog | chips and text parts, insert at caret, ← → ↑ ↓ Backspace Esc, search phrase filtering and hidden empty sections, per-tag values, extension warning, presets (save / update / rename / delete / Default protected), Apply adds *FileName ✎* |
| Undo / redo | sequence of selection and view steps undone in order; toasts name the step; buttons disabled at the ends |
| Lightbox | fit / width / height / 100 %, wheel zoom around the pointer, drag pan clamped, keys |
| Jobs | stripe fixed at top, full width, inverted theme; Cancel → *Cancelling…* → page reload; DB job `cancelled` |

## 4. Before every commit

1. `uv run pytest -q` → all pass.
2. Browser script for the changed area → facts as expected, `errs: []`, screenshots reviewed.
3. Re-read the diff for: tokens-only CSS on the scale, no inline styles beyond user data / JS layout, no
   writes outside plans, no fixed UTC offsets, Safari-safe JS (no unsupported APIs; CSS `:has()` is fine).
4. `docs/UI_SPEC.md` (and ADRs) updated; commit message says what and why.

## Rebuild checklist

1. Port `meta_duplo/demo.py` exactly (seeds, names, dates, GPS, copies) — the tests depend on its contents.
2. Port the three test files; make them pass module by module (scan → match → harmonize → plans → workbench).
3. Recreate the Playwright skeleton and the server start/stop commands in a scratch folder.
4. For every UI skill (11, 12, 07), write one script per area in the table above and keep it.

## Verify

- `uv run pytest -q` prints `31 passed` (`tests/test_desktop.py` covers the token page and the parent watchdog the desktop app relies on).
- On a fresh library, `uv run meta-duplo demo && uv run meta-duplo serve` opens Filomena showing the demo disk
  (21 files when generated in this environment) grouped as duplicate sets, the `IMG_0003.JPG (3)` set among them, and no
  console errors.
- The full apply-and-undo round trip leaves the demo disk byte-identical (the MD5 tree check).
