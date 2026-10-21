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
