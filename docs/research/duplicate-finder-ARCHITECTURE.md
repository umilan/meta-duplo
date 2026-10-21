# Duplicate Finder — Architecture

Local duplicate and similar-media finder for multi-TB photo/video disks.
Runs on one Mac: a Python engine plus a local web server, with a plain
HTML/CSS/JS front end in the browser. Nothing leaves the machine.

Related files in this pack:
- `skill/duplicate-finder/SKILL.md` — algorithms, scoring, thresholds, pitfalls
- `UI-DESIGN.md` — screens, views, ignore model, action basket
- `research/` — feature map of existing apps and user wishes

---

## 1. Overview

```
 ┌──────────────────────────── Browser (localhost) ────────────────────────────┐
 │  Setup page        Results page (Groups · Table · Folders · Timeline)       │
 │  Compare view      Action basket       Project picker                       │
 │  plain HTML + CSS + small vanilla JS modules, no build step                 │
 └───────────────▲───────────────────────────────▲─────────────────────────────┘
                 │ REST (JSON)                   │ SSE (progress, events)
 ┌───────────────┴───────────────────────────────┴─────────────────────────────┐
 │  API server  (FastAPI + uvicorn, bound to 127.0.0.1 only)                   │
 ├─────────────────────────────────────────────────────────────────────────────┤
 │  Scheduler ── jobs table ──► Worker pool (processes)                        │
 │     │                         ├─ inventory      ├─ exact hashing (I/O)      │
 │     │                         ├─ metadata       ├─ image fingerprints (CPU) │
 │     │                         ├─ video sampling ├─ thumbnails               │
 │  Matcher  (candidates → pairs → scores → groups)                            │
 │  Decisions service (keep / remove / not duplicate / ignore / resolved)      │
 │  Action executor (quarantine / trash / link, dry-run, undo log)             │
 ├─────────────────────────────────────────────────────────────────────────────┤
 │  Project DB (SQLite, WAL)        Thumbnail cache (WebP on disk)             │
 └───────────────▲───────────────────────────────▲─────────────────────────────┘
                 │ read-only scanning            │ actions only after confirm
          External disks / folders (identified by volume UUID)
          macOS ImageIO · ffmpeg/ffprobe (VideoToolbox) · exiftool
```

Principles
1. **Read-only by default.** Scanning never writes to scanned disks. Only the
   action executor changes files, and only from the action basket.
2. **Everything is resumable.** All work is a row in the `jobs` table.
3. **Decisions live in the project, never in the media files.**
4. **Cheap filters first, expensive checks last** (see SKILL.md §2).
5. **One project = one SQLite file.** Reopen it any time, even with disks unplugged.

---

## 2. Process model

| Process | Role | Concurrency |
|---|---|---|
| `server` | FastAPI app, serves UI + API, owns the DB writer connection | 1 |
| `scheduler` (thread in server) | Pulls pending jobs, dispatches to pools, writes results in batches | 1 |
| I/O pool | Exact hashing, metadata reads | 1 reader per physical disk (HDD), 4–8 (SSD) |
| CPU pool | Thumbnail decode, dHash/pHash, video frames | = performance cores |
| Thumbnail worker | On-demand thumbnails for files in groups | 1–2 |

- Workers never write the DB; they return results to the scheduler, which
  commits every few hundred rows (single writer avoids SQLite lock contention).
- Run at low priority (`os.nice(10)` or `taskpolicy -b`) so the Mac stays usable.
- Pause = scheduler stops dispatching; in-flight jobs finish.

---

## 3. Code layout

```
dupfinder/
  app.py                  # entry: python app.py → opens http://127.0.0.1:8765
  config.py               # defaults, paths, thresholds
  db/
    schema.sql            # tables below
    migrate.py
    repo.py               # all SQL in one place
  engine/
    volumes.py            # volume UUID, mount detection, online/offline
    inventory.py          # walk, stat, type detection (magic bytes)
    rules.py              # include/exclude rule evaluation (path, size, regex, metadata)
    metadata.py           # exiftool -stay_open batch, QuickTime, sidecars
    capture_time.py       # capture-time fallback chain + trust level
    exact.py              # size → partial → suffix → full hash; pixel/stream hash
    image_fp.py           # ImageIO thumbnails, dHash/pHash, histograms
    video_fp.py           # ffprobe, keyframe sampling, frame hashes
    units.py              # Live Photo, RAW+JPEG, sidecar grouping
    candidates.py         # capture-time buckets, BK-tree, multi-index hashing
    scoring.py            # evidence vector → score → verdict label
    grouping.py           # anchor-based grouping, group signatures
    keeper.py             # keeper rules
    benchmark.py          # pre-scan speed test → cost estimates
    scheduler.py          # jobs, pools, pause/resume
  services/
    decisions.py          # decisions store, re-attach on rescan
    actions.py            # basket, dry-run, quarantine/trash/link, undo log
    folders.py            # folder-pair overlap (≡, ⊂, ∩ %)
  api/
    routes_projects.py  routes_scan.py  routes_results.py
    routes_decisions.py routes_actions.py  routes_media.py  sse.py
  web/
    index.html  setup.html  results.html  compare.html
    css/  tokens.css  layout.css  components.css
    js/   api.js  setup.js  results.js  compare.js  basket.js  sse.js
  tests/
    fixtures/  (generated variants: resize, recompress, crop, rotate, strip EXIF)
```

---

## 4. Data model (SQLite)

```sql
-- Project-level
CREATE TABLE volumes (
  id INTEGER PRIMARY KEY, uuid TEXT UNIQUE, label TEXT,
  last_mount TEXT, kind TEXT,            -- hdd | ssd | network | unknown
  online INTEGER, last_seen TEXT
);
CREATE TABLE roots (
  id INTEGER PRIMARY KEY, volume_id INTEGER REFERENCES volumes,
  rel_path TEXT, role TEXT               -- scan | reference
);
CREATE TABLE rules (
  id INTEGER PRIMARY KEY, root_id INTEGER NULL, ord INTEGER,
  action TEXT, field TEXT, op TEXT, value TEXT, enabled INTEGER
);
CREATE TABLE runs (
  id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT,
  mode TEXT,                             -- files | media
  methods TEXT,                          -- JSON list
  estimate_json TEXT, actual_json TEXT, status TEXT
);

-- Files and fingerprints
CREATE TABLE files (
  id INTEGER PRIMARY KEY, volume_id INTEGER, rel_path TEXT,
  inode INTEGER, size INTEGER, mtime REAL, kind TEXT,  -- image | video | audio | sidecar | other
  unit_id INTEGER NULL,                  -- Live Photo / RAW+JPEG / sidecar unit
  present INTEGER, first_run INTEGER, last_run INTEGER,
  UNIQUE(volume_id, rel_path)
);
CREATE INDEX files_size ON files(size);
CREATE INDEX files_inode ON files(volume_id, inode);

CREATE TABLE hashes (
  file_id INTEGER PRIMARY KEY REFERENCES files,
  partial_head BLOB, partial_tail BLOB,
  full BLOB,                             -- xxh3-128
  content BLOB,                          -- pixel / stream hash (metadata-insensitive)
  key TEXT                               -- (volume uuid, inode, size, mtime) cache key
);
CREATE INDEX hashes_full ON hashes(full);
CREATE INDEX hashes_content ON hashes(content);

CREATE TABLE media_meta (
  file_id INTEGER PRIMARY KEY, width INTEGER, height INTEGER,
  duration REAL, codec TEXT, make TEXT, model TEXT, serial TEXT,
  capture_time TEXT, capture_source TEXT, capture_trust INTEGER,
  gps_lat REAL, gps_lon REAL, unique_id TEXT, content_identifier TEXT,
  orientation INTEGER, raw_json TEXT
);
CREATE INDEX media_capture ON media_meta(make, model, capture_time);

CREATE TABLE fingerprints (
  file_id INTEGER, algo TEXT,            -- dhash64 | phash64 | hist | frame_k
  value BLOB, PRIMARY KEY(file_id, algo)
);

-- Matching
CREATE TABLE pairs (
  a INTEGER, b INTEGER, method TEXT, score REAL,
  evidence_json TEXT,                    -- visual, time, device, geometry, name…
  verdict TEXT,                          -- identical | same_pixels | same_photo | edited | similar_shot
  run_id INTEGER, PRIMARY KEY(a, b, method)
);
CREATE TABLE groups (
  id INTEGER PRIMARY KEY, signature TEXT UNIQUE,   -- sorted member content hashes
  verdict TEXT, best_score REAL, reclaimable INTEGER,
  keeper_file_id INTEGER, status TEXT,             -- open | resolved | ignored | deferred | changed
  run_id INTEGER
);
CREATE TABLE group_members (group_id INTEGER, file_id INTEGER, role TEXT,
  PRIMARY KEY(group_id, file_id));                 -- role: keeper | candidate | reference
CREATE TABLE folder_pairs (
  a_path TEXT, b_path TEXT, relation TEXT,         -- equal | subset | overlap
  shared_bytes INTEGER, overlap REAL, PRIMARY KEY(a_path, b_path)
);

-- Work queue
CREATE TABLE jobs (
  id INTEGER PRIMARY KEY, file_id INTEGER, stage TEXT,
  status TEXT,                           -- pending | running | done | failed | paused
  attempts INTEGER, error TEXT, run_id INTEGER
);
CREATE INDEX jobs_pending ON jobs(status, stage);

-- Decisions (never written into media files)
CREATE TABLE decisions (
  id INTEGER PRIMARY KEY,
  scope TEXT,                            -- file | pair | group | folder_pair | rule
  subject TEXT,                          -- content hash, "hashA|hashB", group signature, path pair, rule JSON
  decision TEXT,                         -- keep | remove | not_duplicate | ignore | resolved | deferred
  note TEXT, decided_at TEXT, run_id INTEGER
);
CREATE INDEX decisions_subject ON decisions(scope, subject);

-- Actions
CREATE TABLE basket (file_id INTEGER PRIMARY KEY, action TEXT, group_id INTEGER);
CREATE TABLE action_log (
  id INTEGER PRIMARY KEY, batch_id TEXT, file_id INTEGER, action TEXT,
  src TEXT, dst TEXT, verified INTEGER, done_at TEXT, undone_at TEXT
);

-- UI state
CREATE TABLE saved_views (id INTEGER PRIMARY KEY, name TEXT, json TEXT);
CREATE TABLE ui_state (key TEXT PRIMARY KEY, json TEXT);  -- last filter, scroll, selection
```

Sizes for ~400k files: a few hundred MB of DB; fingerprints and pairs are small.
Thumbnails (only for grouped files) live outside the DB.

---

## 5. Pipeline

```
 inventory ─► rules ─► metadata ─► units ─► exact ─► fingerprints ─► candidates ─► score ─► group ─► keeper
    │                    (media)   (media)   │          (image/video)                              │
    └─ jobs rows per file and stage ─────────┴──────────────────────────────────────────────────────┘
```

| Stage | Input | Output | Mode | Bound by |
|---|---|---|---|---|
| inventory | roots | `files` | both | metadata I/O |
| rules | files | excluded flags | both | CPU (cheap) |
| metadata | media files | `media_meta`, capture time + trust | media | I/O |
| units | media_meta | `files.unit_id` | media | CPU (cheap) |
| exact | same-size groups (files) / all media | `hashes` | both | disk |
| fingerprints | images, videos | `fingerprints` | both | CPU |
| candidates | fingerprints, capture buckets | candidate pairs | both | CPU / RAM |
| score | candidate pairs | `pairs` with evidence + verdict | both | CPU |
| group | pairs | `groups`, `group_members` | both | CPU |
| keeper | groups | `groups.keeper_file_id` | both | CPU |
| folders | hashes | `folder_pairs` | both | CPU |

**Files mode** = generic: size grouping → partial/suffix/full hash; optional
name rules and image similarity.

**Media mode** = capture-event first: metadata → units → container-aware hash →
capture-time buckets (camera + time ± N s, unique IDs) → visual check inside
buckets → BK-tree only for files without a capture time. Details in SKILL.md §9c.

---

## 6. Re-scan and decision re-attachment

1. New run: inventory marks `files.present`; unchanged cache keys skip hashing.
2. Matching rebuilds candidate pairs only for new or changed files.
3. For each group: compute signature (sorted content hashes).
   - Signature matches a group with a `resolved` / `ignored` decision → keep that status.
   - Superset of a resolved signature → status `changed`, shown with a badge.
4. Pair-level `not_duplicate` decisions suppress the pair before grouping.
5. Decisions export/import as JSON (`/api/decisions/export`).

---

## 7. API

```
GET  /api/projects                          list projects
POST /api/projects {name}                   create
POST /api/projects/{id}/open                open (sets active project)

GET  /api/volumes                           online/offline, kind, UUID
GET  /api/tree?root=…&depth=1               folder tree with counts/sizes (inventory)
POST /api/rules  · GET /api/rules/preview   rule list · live match counts + sample paths

POST /api/estimate {mode, methods}          benchmark + per-method time, marginal cost
POST /api/scan {mode, methods, roots}       start run
POST /api/scan/pause · /resume · /cancel
GET  /api/events                            SSE: phase, done/total, bytes, ETA, per-phase actuals

GET  /api/groups?verdict=&min=&status=&sort=&page=
GET  /api/groups/{id}                       members, evidence, keeper, metadata diff
GET  /api/table?…                           flat file rows for the Table view
GET  /api/folders?…                         folder pairs, overlap, heatmap data
GET  /api/timeline?…                        groups by capture date
GET  /api/thumb/{file_id}?size=256          cached WebP
GET  /api/media/{file_id}                   full file with HTTP Range (images, video)

POST /api/decisions {scope, subject, decision, note}
GET  /api/decisions/export · POST /api/decisions/import

POST /api/basket {file_ids, action}  · GET /api/basket   summary: bytes, per verdict, warnings
POST /api/actions/apply {dry_run}           re-verify, then quarantine / trash / link
POST /api/actions/undo {batch_id}
```

---

## 8. Front end

- Plain HTML pages, one CSS token file, small ES modules. No framework, no build.
- `tokens.css`: colours, spacing, type; light/dark via `prefers-color-scheme`.
- Confidence badge: `--conf` custom property → hue (see SKILL.md §8).
- Large lists: server-side paging + `content-visibility: auto`; lazy images.
- SSE drives progress bars and live counters; reconnects automatically.
- UI state (filters, sort, scroll, selection) saved to `ui_state` so
  "continue where I left off" works after reopening a project.
- Keyboard: ←/→ group, K keep, X mark, N not duplicate, I ignore, Space compare.

Screens and views are specified in `UI-DESIGN.md`.

---

## 9. Safety

- Scanning is read-only; the DB and caches live in `~/Library/Application Support/<app>`
  and `~/Library/Caches/<app>`, never on scanned disks.
- Actions: dry-run summary → re-verify size + mtime (+ byte compare for
  identical) → quarantine (mirrors original paths) or Trash → `action_log` → undo.
- Never act on a group that would keep zero files; never act on offline volumes.
- Library-aware: never touch files inside `.photoslibrary` packages; for Apple
  Photos, export candidate lists instead (library-safe workflow).

---

## 10. Performance targets (2 TB, ~400k files, Apple Silicon)

| Step | Target |
|---|---|
| Reopen project and show results | < 2 s |
| Regroup after slider change | < 1 s (from stored pairs) |
| Rescan of unchanged disk | minutes (cache hits) |
| First full media-mode scan | a few hours on USB HDD, under an hour on SSD |
| Peak RAM | < 2 GB |

---

## 11. Testing

- Fixture generator: from ~300 photos create resized, recompressed, cropped,
  rotated, HEIC↔JPG, EXIF-stripped, date-edited variants, plus bursts and
  same-scene-different-day non-duplicates.
- Unit tests per stage; golden tests for verdict labels.
- Crash test: kill the server mid-scan, restart, verify no lost or duplicated work.
- Action tests on a temp volume: dry-run equals real run; undo restores bytes.

---

## 12. Milestones

1. Project file, inventory, jobs (pause/resume), exact cascade, minimal results.
2. Decisions store, reopen project, re-attachment on rescan.
3. Action basket: dry-run, quarantine/Trash, undo log.
4. Media mode: metadata, capture-time buckets, thumbnail dHash, units, verdicts.
5. Compare view, keeper rules, timeline and folders views.
6. Video pass.
7. Cost estimates, calibration, "not duplicate" learning.
8. Optional: embeddings, audio fingerprints.
