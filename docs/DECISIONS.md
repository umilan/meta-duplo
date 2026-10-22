# Decision log (ADR-lite)

Format: **context → decision → consequences**. Add new entries at the bottom; never edit
accepted ones. Supersede them with a new entry instead.

---

### ADR-001 Local engine + web UI, not a pure in-browser app
- **Context:** 2 TB, ~500k files. The browser can't set file timestamps or read birth
  times/xattrs; folder write access is Chromium-only; WebAssembly memory is ~4 GB; tabs
  sleep. ExifTool coverage (MakerNotes, embedded streams) matters.
- **Decision:** a Python engine runs locally on the Mac; the UI is served on localhost.
- **Consequences:** full filesystem access, native ExifTool/ffprobe; the user starts a local
  server (or later the Tauri app does it).

### ADR-002 Python + FastAPI + Jinja + HTMX + plain CSS
- **Context:** the owner knows HTML/CSS and wants to restyle freely; no frontend build chain
  wanted.
- **Decision:** server-rendered templates with HTMX/Alpine; CSS custom properties as design
  tokens.
- **Consequences:** less client-side complexity; very large interactive views (map, charts)
  use small, focused JS libraries loaded per page.

### ADR-003 SQLite as the single source of truth
- **Decision:** one SQLite file per library (WAL), with raw metadata stored compressed.
- **Consequences:** easy backup and inspection (Datasette); re-runs never touch the disk.
  Concurrency is limited to one writer: the job runner serializes commits per chunk.

### ADR-004 Resolution per cluster; overrides as evidence
- **Decision:** results attach to duplicate clusters. Human overrides are tier-1 evidence
  (`origin=human`), not edits of results.
- **Consequences:** overrides propagate, are explainable, and are revocable.

### ADR-005 Read-only sources; writes only via approved plans
- **Decision:** sources are read-only by default; an apply engine with a journal, group
  atomicity, verification and undo.
- **Consequences:** slower to "just rename", but no irreversible accidents.

### ADR-006 Confidence = calibrated probability per tolerance level
- **Decision:** percentages per level (±1 min … year; 100 m … country; unit/model/brand),
  calibrated with masking tests.
- **Consequences:** needs a calibration job and stored maps; the UI shows ladders, not single
  numbers.

### ADR-007 Quick Look / sips for thumbnails on macOS
- **Decision:** use macOS native renderers first; Python decoders as a fallback.
- **Consequences:** fast, broad format support; macOS-specific (an adapter interface keeps it
  swappable).

### ADR-008 Tauri later, not now
- **Decision:** ship as a localhost web app first; wrap with Tauri v2 once stable.
- **Consequences:** quicker iteration now; keep the Tauri prerequisites (env-configured
  port/token, relative URLs, WKWebView-compatible UI).

### ADR-009 Domain logic lives in /skills
- **Decision:** the five skill documents are the specification for resolver behaviour. Code
  references skill sections; changes to logic update the skill first.
- **Consequences:** a single source of truth for humans and AI coding agents.

### ADR-010 One app: the duplicate finder becomes part of meta-duplo (Proposal A, htmx)
- **Context:** two design packs (metadata organizer, duplicate finder) shared the stack, the first
  pipeline stages and the safety model. Run separately, they would read every disk twice, keep two
  groupings of the same files and two undo logs. The options were A (one engine), B (shared core
  package, two apps), C (two apps on one database) and D (one engine, keep the dup-finder's
  static front end). See the "merge proposals" document.
- **Decision:** A. One codebase, one library database, one job runner, one plan/apply/undo engine.
  Duplicate review screens are htmx partials like the rest. The few heavy visual views (heatmap,
  treemap, compare) may use small per-page JS modules fed by JSON, as ADR-002 allows.
- **Consequences:**
  - Removed copies stay evidence for metadata resolution.
  - One set of decisions; "not duplicate" labels serve both duplicate scoring and calibration.
  - The dup-finder's JSON API and front end are not built.
  - About 4–5 weeks are added to the roadmap.

### ADR-011 The user-facing flow is four steps
- **Decision:**
  1. Identical files.
  2. Near duplicates.
  3. Harmonize metadata and rename by an editable rule.
  4. Re-check duplicates with the harmonized metadata.
  The navigation, counts and plans follow these steps. Pipeline stages stay as in ARCHITECTURE §5;
  the steps are views over them. See `WORKFLOW.md`.
- **Consequences:**
  - Cleanup can happen before resolution, so removed copies must stay in the library as evidence (`files.removed`).
  - Step 4 replaces the "regroup after resolve" loop of the first merge draft. It is a separate stage
    between captures that never renumbers step-1/2 groups.

### ADR-012 File name rule syntax
- **Decision:** brace tokens with strftime for dates:
  `{date:%Y-%m-%d %H-%M-%S} ({location_name}, {device_name}) - {original_filename}.{ext}`.
  Empty tokens drop their separators. `{original_filename}` is the first name ever seen, so re-running a rename never nests.
- **Consequences:**
  - The plain form `YYYY-MM-DD HH-MM-SS` is ambiguous (`MM` is both month and minute), hence strftime.
  - Files without a date keep their name and are flagged.

### ADR-013 Proof-of-concept scope (v0.1)
- **Decision:** the POC implements the four steps end to end with lighter tooling:
  - Pillow (+ pillow-heif) and ffprobe instead of an ExifTool pool;
  - offline GeoNames towns via `reverse_geocoder`;
  - tier-based confidence instead of calibrated probabilities;
  - polling instead of SSE;
  - XMP sidecars and file dates, but no in-file writes;
  - no context/propagation stage.
- **Consequences:**
  - The schema is a working subset of `schema.sql`, created in `meta_duplo/db.py`.
  - Moving to the full spec replaces adapters and resolvers module by module; the plan engine, decisions and UI carry over.

### ADR-014 Results as one compact grouped table, not cards
- **Context:** cards with large thumbnails were unusable with hundreds of groups (user feedback on v0.1).
- **Decision:** steps 1, 2 and 4 share one table modelled on Disk Drill's duplicate finder:
  - collapsible group rows with one line per copy;
  - checkboxes mean "remove", backed by server-side selection;
  - Smart select rules and a sticky selection bar;
  - a toggleable preview panel;
  - search, sort, and paging by 100 groups.
- **Consequences:**
  - New `selection` table.
  - `build_cleanup_plan` accepts an explicit set of files, still never empties a group.
  - The card templates are gone; Compare stays for side-by-side checks.

### ADR-015 Step 3 as a rename grid, one row per file
- **Context:** the per-photo recommendation rows did not scale and hid the result that matters: the new file name.
- **Decision:** an Advanced Renamer-style grid:
  - one row per file: old name, new name, path, status;
  - harmonized fields as `now → new` cells with ✓ / ✗ / double-click edit;
  - every metadata column optional (saved per library) and sortable;
  - a per-file include checkbox (`rename_exclude`).

  Decisions still apply per photo (capture); the grid re-renders only that photo's rows.
- **Consequences:**
  - Row-level exclusion: `rename_preview` reports `excluded`, and the plan skips those files.
  - The rule is saved as you type (it changes nothing on disk).
  - Paging is 200 rows. The read model recomputes the preview per request, which is fine for thousands of files;
    hundreds of thousands need a cached preview table (milestone M5).

### ADR-016 Workbench: one table for every task (experimental)
- **Context:** the four step screens are a guided path, but power users want one place to look at duplicates
  and metadata together, as in the user's mockup.
- **Decision:** add `/workbench` (nav: *Workbench, beta*):
  - one row per file;
  - *Group by* chooses the task (duplicates, photo, folder, day, device, place, none);
  - red/green differences against the group's reference row;
  - a column catalog with every EXIF tag;
  - resizable and reorderable headers with Actions (sort, filter, search, edit);
  - purple edit columns driven by rules (rename pattern; date recommended / copy / shift / fixed;
    place and device recommended / copy / fixed);
  - a full-metadata preview.

  Edits go through the same decisions and plans as steps 1–4.
- **Consequences:**
  - Extraction now keeps every EXIF tag (`raw_json.tags`, `RAW_VERSION = 2`), plus digitized date, GPS date
    (UTC), artist, copyright, owner, serial and lens. Older libraries re-read their files once on the next scan.
  - Confidence display: 100 % only for byte-identical files, 99 % for same pixels, ≤ 98 % for other matches.
  - `build_rename_plan(only_ids=…)` limits a plan to the selection.
  - View state lives in one setting (`workbench_view`).
  - Column widths are inline styles because they are user data: the second exception to "no inline styles",
    after the swipe compare.
  - Not yet: column filters by value list, an undo of view changes. (Keyboard navigation came later: a cell cursor, see UI_SPEC.)

### ADR-017 One design system on a 2 · 4 · 8 · 16 · 24 · 32 · 40 · 48 · 64 scale
- **Context:** controls had grown different heights, paddings and radii screen by screen (6, 10, 12, 13 px …),
  and the user asked for one consistent style with sizes in multiples of 2 on that scale.
- **Decision:** model the UI on the Atlassian Design System: its spacing tokens (`space.025` … `space.800`) are
  exactly the scale above, with 32 px controls (24 px compact), 4 px control radius and a 12/14/20/24 type ramp.
  - `tokens.css` holds every length: spacing, type with line heights, radii, control and row heights, thumbnail
    size, gaps and page padding. Components use only tokens; larger sizes are multiples of 8; 1 px hairlines are
    the one exception.
  - One component per kind: button (default, primary, danger; small; icon), text field / select, checkbox and
    radio, menu and sheet, chip, pill, small icon button. The same classes serve every screen.
  - *Compact view* changes component tokens (control 24, row 24, ⋯ 16, body 12/16, gaps 8), not the scale.
  - Workbench column widths snap to 8 px (`workbench.snap`, also applied to saved views and while dragging).
  - Colours stay as they were (light and dark sets); only sizes, spacing, radius and type changed.
- **Consequences:** a new style value is a token or a step of the scale, never an ad-hoc number. Inline styles
  are still limited to user data (column widths, the menu position, the swipe clip).

### ADR-018 Undo / redo in the Workbench: view steps on the server, one ordered list in the page
- **Context:** users want to undo ticking, sorting, filters, a reference row… The view lives on the server
  (`workbench_view`), the selection only in the page.
- **Decision:** `/wb/view` pushes the previous view onto `workbench_undo` (max 100, a change clears
  `workbench_redo`) and answers `X-Wb-History: <action>`; `/wb/history` swaps views between the stacks. The page keeps
  one ordered list (sessionStorage) of selection steps (before / after) and view-step markers, so ⌘Z walks back
  through both in the order they happened.
- **Consequences:** files on disk are never undone here; that stays with plans (`undo_plan`). Saved decisions
  (Apply / Save edits) are not in the history. A view change made from another tab is on the server stack but not
  in this page's list, so the two can drift until the next reload of history; acceptable for a single-user tool.

### ADR-019 Desktop app: Tauri v2 around the bundled Python server (supersedes the timing in ADR-008)
- **Context:** the owner wants to run the app on other Macs without installing Python, `uv` or the repository.
- **Decision:** the server is frozen with PyInstaller into one binary (`packaging/`), and a Tauri v2 shell
  (`desktop/`) starts it as a sidecar. The shell picks a free port and a random token and passes them in
  `META_DUPLO_HOST` / `META_DUPLO_PORT` / `META_DUPLO_TOKEN`, together with its own pid in
  `META_DUPLO_PARENT_PID`. It shows a start page until the port answers, then points its window at the server.
  The UI is the same HTML as in the browser. The token link answers with a page that moves on by itself instead
  of a redirect, because a redirect from the shell's own origin would keep the strict cookie back in WebKit. On
  quit the shell sends SIGTERM, which the one-file binary passes on to Python (SIGKILL would not reach it). If
  the shell dies, the server stops itself once that pid is gone. GitHub Actions (`.github/workflows/desktop.yml`)
  builds an arm64 and an x86_64 `.dmg` on macOS runners.
- **Consequences:** no signing with a Developer ID and no notarization yet. The app is only ad-hoc signed, so on
  first start the user right-clicks it and chooses Open, or clears the quarantine flag. `ffprobe` is not bundled:
  videos get dates and devices only where Homebrew's `ffmpeg` is installed. The library stays in
  `~/MetaDuploLibraries/default`, shared with the command line. Start-up takes a few seconds while the one-file
  binary unpacks itself. A one-folder build would start faster, but it would need the bundle's resources folder.
