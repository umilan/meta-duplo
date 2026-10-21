# Architecture: Media Organizer (working codename `medorg`)

> The duplicate finder is part of this app (ADR-010). The user-facing flow is four steps (ADR-011, `WORKFLOW.md`); the stages below are the engine underneath. The v0.1 proof of concept implements a subset (ADR-013).

A local, single-user tool for macOS. It analyses large media disks (~2 TB, 200k–600k
photos/videos) and resolves each file's **real capture time**, **location** and **device**,
with an honest **confidence**. It handles duplicates, then reorganizes or annotates the files
safely after human review.

The domain logic is specified in `/skills`:

| Skill | Engine module |
|---|---|
| `media-capture-date-resolver` | `resolvers/time` |
| `media-location-resolver` | `resolvers/place` |
| `media-device-resolver` | `resolvers/device` |
| `media-context-resolver` | `context/` |
| `media-confidence-scoring` | `scoring/` |

This document defines how those pieces run as software.

---

## 1. Goals and non-goals

**Goals**
- Process a 2 TB disk end-to-end on a MacBook: resumable, restartable, no data loss.
- Never modify originals until the user approves an explicit plan. Every write is undoable.
- Re-runnable: when an algorithm improves, re-run only the affected stage, not the whole disk.
- A UI the owner can restyle with plain HTML/CSS, with no frontend build step.
- Transparent: every resolved value can be explained (sources, heuristics, confidence).

**Non-goals (v1)**
- Multi-user, cloud hosting, or remote access.
- Photo editing or a viewer replacement. Use Finder, Quick Look and Photos for that.
- Windows/Linux support. It is likely portable later, but untested.
- Real-time watching of folders.

---

## 2. Stack (chosen)

| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.12+**, managed with **uv** | Best media/geo ecosystem; `uv run` = one-command start |
| Web server | **FastAPI** + Uvicorn (localhost only) | Async, simple routing, SSE for progress |
| Templates | **Jinja2** (server-rendered HTML) | The UI is plain HTML files |
| Interactivity | **HTMX** (partial swaps, infinite scroll, SSE) + **Alpine.js** (small client state) | No build step; behaviour lives in HTML attributes |
| Styling | **Plain CSS** with custom properties (design tokens), no framework | Owner-tunable |
| Maps | **MapLibre GL JS** (or Leaflet + markercluster) from CDN; OSM raster or offline tiles | Clustering for 100k+ points |
| Database | **SQLite** (WAL mode), stdlib `sqlite3`, numbered SQL migrations | Single file, fast, inspectable with Datasette |
| Metadata | **ExifTool** (Homebrew) via `pyexiftool` in `-stay_open` mode, N parallel instances | Widest format and MakerNote coverage |
| Video | **ffprobe/ffmpeg** (Homebrew) | Streams, encoder, keyframes |
| Thumbnails | macOS **Quick Look** (`qlmanage -t`) and **`sips`**; fallback Pillow + `pillow-heif` + `rawpy` | Native HEIC/RAW/video support, fast |
| Hashing | `blake3` (bytes), ExifTool `ImageDataHash`, `imagehash` (pHash/dHash) | Fast, layered dedup |
| Geo | `timezonefinder`, `reverse_geocoder` / GeoNames, `shapely` + Natural Earth/GADM admin polygons, `gpxpy`, `pyproj` | Offline |
| Dev inspection | **Datasette** (+ `datasette-cluster-map`) | Zero-code DB browsing during development |
| Tests | `pytest`, a fixture corpus, golden outputs | |
| Future shell | **Tauri** (see §13) | Real `.app` later, same HTML/CSS UI |

---

## 3. High-level picture

```
 ┌───────────────────────────── Mac (localhost) ─────────────────────────────┐
 │                                                                           │
 │  Safari / Chrome (Add to Dock)                                            │
 │     │  HTML pages, HTMX partials, SSE progress                            │
 │     ▼                                                                     │
 │  FastAPI app ── routes ── Jinja templates ── static/ (your CSS, JS libs)  │
 │     │                                                                     │
 │     ├── Query layer (read models for UI)                                  │
 │     ├── Command layer (start job, save override, approve plan)            │
 │     ▼                                                                     │
 │  Job runner (durable queue in SQLite, worker processes)                   │
 │     │                                                                     │
 │     ├── Stages: inventory → extract → hash → fingerprint → context →      │
 │     │           resolve(time, device, place) → propagate → score →        │
 │     │           plan → apply → verify                                     │
 │     ▼                                                                     │
 │  Adapters: ExifTool pool · ffprobe · Quick Look/sips · filesystem ·       │
 │            geo data · track importers (GPX, Google Timeline, Takeout)     │
 │     │                                                                     │
 │     ▼                                                                     │
 │  Storage: library.sqlite · thumbs/ · exports/ · logs/ · undo/             │
 │                                                                           │
 │  Source volumes (external disks, mounted READ-ONLY by default)            │
 └───────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Core concepts

| Concept | Meaning |
|---|---|
| **Library** | One working database + caches (a folder `~/MedorgLibraries/<name>/`). It can hold several sources. |
| **Source** | A root folder on a volume. It is identified by the volume UUID + relative root, so it survives remounts and changed mount paths. |
| **File** | One physical file in a source (path, size, inode, timestamps). |
| **Cluster** | Files that are the same capture (exact/pixel/near duplicates). Resolution happens per cluster. |
| **Sibling group** | Different products of one moment (RAW+JPEG, Live Photo, burst). They share values but are kept together. |
| **Candidate** | One piece of evidence for a value (time, place, device, origin), with source, tier and origin. |
| **Constraint** | Implicit evidence from context (interval, order, equality, disc, membership). |
| **Resolution** | The chosen value per cluster and dimension, plus confidence levels and explanation. |
| **Override** | A human decision; tier-1 evidence that propagates. |
| **Plan** | A proposed set of actions (rename, move, write sidecar, set timestamp). Always a dry run first. |
| **Run** | One execution of a stage over a scope, with algorithm version, timing and counts. |

---

## 5. Pipeline stages

Each stage is **idempotent per file or cluster**, **resumable**, and **versioned**.

| # | Stage | Input → Output | Notes |
|---|---|---|---|
| 1 | `inventory` | Walk the source → `files` | Path (NFC-normalized), size, mtime, birthtime, inode, ext; skip junk (`.DS_Store`, `Thumbs.db`); detect sidecars |
| 2 | `extract` | `files` → `meta_raw` | ExifTool JSON (`-a -G1 -s -ee3 -api LargeFileSupport=1`), ffprobe for video; integrity checks (key by SourceFile, count, re-read empties, sample re-check) |
| 3 | `hash` | → `hashes` | Size groups → head/tail partial BLAKE3 → full BLAKE3 only for collisions; `ImageDataHash` / video stream hash for candidates |
| 4 | `fingerprint` | → `fingerprints` | pHash/dHash (orientation-corrected), JPEG quant-table hash, structure hash, dimensions; video keyframe hashes |
| 5 | `thumbs` | → `thumbs/` | Quick Look / sips at 256 px and 1024 px, WebP/JPEG; lazy and background |
| 6 | `cluster` | → `clusters`, `siblings` | L1–L4 duplicate levels, identity keys, sibling detection, master selection |
| 7 | `context` | → `context_evidence` | Folder tokens, folder types, coherence tests, counter sequences, batches, derivations |
| 8 | `resolve_device` | → `candidates(device)`, `device_units`, `resolution(device)` | Runs before place: units decide whose tracks apply |
| 9 | `resolve_time` | → `candidates(time)`, `resolution(time)` | Includes clock-offset learning and the QuickTime convention per model |
| 10 | `resolve_place` | → `candidates(place)`, `tracks`, `resolution(place)` | Track matching, session fill, geocoding |
| 11 | `propagate` | Constraints → narrowed intervals and discs | Fixed-point iteration; conflict detection |
| 12 | `score` | → `confidence` | Log-odds combination, calibration maps, bands |
| 13 | `review` | UI, human | Overrides feed back into stages 9–12 for the affected clusters only |
| 14 | `plan` | → `plans`, `actions` | Target layout rules + confidence bands → proposed actions (dry run) |
| 15 | `apply` | Execute actions | Only after approval; journaled; per-group atomicity (sidecars, siblings) |
| 16 | `verify` | Re-read and compare | Metadata dates, filesystem times, hashes of moved files; mismatch report |

Stages 9–12 iterate (dates → device → place → timezone feedback → dates) until stable or
`max_passes = 3`.

### Algorithm versioning
Each stage has a `STAGE_VERSION` constant. Results store the version that produced them. When
a version is bumped, the runner marks the dependent results stale and re-runs only those
stages. Raw extraction (stage 2) is rarely re-run, which matters because it is the expensive
I/O stage.

---

## 6. Job runner

- **Durable queue in SQLite** (`jobs` table): `stage, scope, status, progress, version,
  created/started/finished, error`. There is no Redis or Celery.
- **Workers**: a `ProcessPoolExecutor` for CPU stages (hash, fingerprint, scoring); an
  ExifTool pool of N `-stay_open` processes (N ≈ performance cores) for I/O stages.
- **Chunking**: work units of ~500 files (images) or ~100 (videos). Each chunk commits in one
  transaction. A crash loses at most one chunk.
- **Pause/resume/cancel**: workers check a flag between chunks.
- **Progress**: Server-Sent Events (`/events/jobs/{id}`), rendered via HTMX's SSE extension.
- **Sleep prevention**: the runner calls `caffeinate -i -w <pid>` while jobs are active.
- **Disk awareness**: one I/O-heavy stage per physical disk at a time. Parallel random reads
  on an HDD are slower than sequential reads.
- **Errors**: per-file errors are recorded in `file_errors` and never abort a run.

---

## 7. Data model (summary)

See `DATA_MODEL.md` and `schema.sql` for full definitions.

```
sources ─< files ─< meta_raw (1:1, compressed JSON)
                 ─< hashes, fingerprints (1:1)
                 ─< candidates (time|place|device|origin)
                 ─< context_evidence
files >─ clusters ─< resolution (per dimension) ─< confidence
files >─ sibling_groups
device_units ─< files (via resolution)
tracks (points from GPX/Timeline/telemetry)
overrides (human decisions) · plans ─< actions · runs · jobs · file_errors
settings (home tz, thresholds, folder stop words, place overrides)
```

Design rules:
- **Raw metadata is never discarded.** It is stored compressed (zstd JSON) so every later stage
  can be re-run without touching the disk.
- **Candidates are append-only per run**; resolutions point to the run that made them.
- **All times are stored as UTC ISO-8601 plus an offset and a tz-status field.** Naive local
  times are kept as separate columns.
- Paths are stored relative to the source root, NFC-normalized, with the original bytes kept
  for exact filesystem operations.

---

## 8. Web layer

- **Server-rendered pages** (Jinja). **HTMX partials** for lists, detail panels, forms. **No
  SPA.**
- **URL design**: every view is a bookmarkable URL with its filters in the query string,
  e.g. `/files?band=low&dim=time&device=3&folder=…`.
- **Read models**: SQL views tailored to the screens (e.g. `v_review_queue`,
  `v_folder_report`), so templates stay dumb.
- **Pagination**: keyset pagination (`WHERE id > ? LIMIT 200`) plus HTMX `hx-trigger="revealed"`
  for infinite scroll. Grids are virtual or windowed for 100k+ items.
- **Thumbnails**: `/thumb/{file_id}/{size}` served from the cache, generated on demand if
  missing. Long cache headers.
- **Security**: bind to `127.0.0.1` only; a random session token in the URL/cookie on startup
  (protects against other local web pages); CSRF token on POSTs; no CORS.
- Screen inventory and UI conventions: see `UI_SPEC.md`.

---

## 9. Filesystem and safety model

1. **Read-only by default.** Sources are opened read-only. The app refuses to write unless the
   source is explicitly marked `writable` and a plan is approved.
2. **Writes only through the apply engine.** No other module may write to a source.
3. **Plans are diffs**: every action records `before` and `after` (path, timestamps, metadata
   fields). Executing a plan writes a journal first (`undo/<plan_id>.jsonl`), then acts.
4. **Group atomicity**: siblings and sidecars move or rename together. On failure the whole
   group rolls back.
5. **Preferred write targets**, in order of safety:
   1. the DB only;
   2. XMP sidecars;
   3. a new organized tree via APFS clones (`cp -c`) or hardlinks;
   4. in-place rename;
   5. in-file metadata writes (last resort; never for videos by default).
6. **Verify after apply** (stage 16) and produce a mismatch report.
7. **Pre-flight checks**:
   - Volume filesystem: NTFS is read-only on macOS → warn and offer an output disk.
   - Free space for the largest planned temp file.
   - SMART/health hint for old HDDs; recommend imaging failing disks first.
   - Path length, reserved characters and case-insensitive collisions on the target.
8. **macOS permissions**: on first run, detect missing Full Disk Access / removable-volume
   permission and show instructions.

---

## 10. Performance budget (targets for a 2 TB USB-HDD source, Apple Silicon)

| Stage | Target | Strategy |
|---|---|---|
| inventory | < 10 min for 500k files | `os.scandir`, single pass, batch inserts |
| extract | < 2 h | ExifTool pool; images read headers only; `-fast2` where safe; videos `-ee3` |
| hash | < 1 h typical | Only size-collision groups get partial, then full, hashes |
| fingerprint | < 1 h | Decode at reduced size (Quick Look/sips thumbnails reused) |
| thumbs | Background, lazy | 256 px first; 1024 px on demand |
| resolve + propagate + score | < 15 min | Pure DB/CPU; vectorize where possible |
| UI | < 200 ms per page | Indexed queries, keyset pagination, cached thumbs |

The overall bound is disk throughput: full reads of 2 TB take 3–6 h on a USB HDD. The design
avoids full reads except for duplicate verification.

---

## 11. Configuration

`library/config.toml` (editable in the Settings screen):

```toml
[general]
home_timezone = "Europe/Bratislava"
languages = ["sk", "cs", "en", "de"]       # for folder-token parsing (months, holidays)

[extract]
exiftool_instances = 6
image_batch = 500
video_batch = 100

[cluster]
phash_max = 6
dhash_max = 8

[context]
session_gap_minutes = 120
folder_coherence_min = 0.8

[scoring]
cap_unverified = 0.995
bands = { verified = 0.98, high = 0.90, medium = 0.70, low = 0.40 }

[layout]
pattern = "{year}/{year}-{month}-{day}{ place?' '+place }/{yyyymmdd}_{hhmmss}_{device}_{orig}"
unknown_date = "unknown_date/{bounds}"
```

---

## 12. Testing and calibration

- **Fixture corpus** (`tests/fixtures/`): small, hand-curated files covering every
  source/edge case in the skills:
  - iPhone HEIC+MOV; Pixel `PXL_`; Samsung MP4 with `©xyz`;
  - GoPro with GPMF; DJI with SRT; AVCHD MTS;
  - Canon CR2+JPG, Sony bursts sharing subsec;
  - WhatsApp copies; Takeout JSON pairs; scans; screenshots;
  - AI-generated PNGs;
  - bad dates (`2026-09-31`, 1970, 2000-01-01), GCJ-02 coordinates, missing refs.
  - Each fixture has an `expected.yaml`.
- **Golden tests** per resolver: input fixture → expected candidates and resolution.
- **Property tests** for propagation (monotonic narrowing; conflicts detected).
- **Masking calibration** (from `media-confidence-scoring`): runs on the real library as a job
  and stores the calibration maps per heuristic/bucket in the DB.
- **Integrity tests**: extraction keyed by SourceFile; re-read of empties; sample re-check.

---

## 13. Future: Tauri desktop shell

When the tool is stable, wrap it as a native macOS app **without rewriting the UI**:

- **Tauri v2** window pointing at the local FastAPI server. The HTML/CSS/HTMX UI is unchanged.
- **Python engine as a Tauri sidecar**: bundle with PyInstaller (or `uv`-managed Python + venv)
  as a signed sidecar binary. Tauri starts and stops it, picks a free port, and passes the
  session token.
- ExifTool and ffprobe are bundled as sidecars too (no Homebrew dependency for end users).
- Native affordances via Tauri plugins: folder picker dialogs, notifications when long jobs
  finish, a dock badge with progress, "Reveal in Finder", auto-update.
- Code signing and notarization for distribution; sandbox entitlements for user-selected
  folders (security-scoped bookmarks to remember source access).

**Preparing for it now (cheap):**
- Never hard-code the host or port; read them from the environment.
- Keep all UI assets under `web/` with relative URLs.
- Keep the engine startable as `medorg serve --port X --token Y`.
- No browser-only APIs the Tauri webview (WKWebView) lacks. Test the UI in Safari as the
  primary browser.

---

## 14. Repository layout (planned)

```
medorg/
  pyproject.toml            # uv-managed
  CLAUDE.md                 # guidance for AI coding agents
  README.md
  docs/                     # this spec kit
  skills/                   # domain skills (source of truth for logic)
  medorg/
    __main__.py             # CLI: init, add-source, run, serve
    config.py
    db/
      migrations/0001_init.sql …
      repo.py               # small query helpers
      views.sql             # read models for UI
    jobs/
      runner.py  queue.py  chunks.py
    adapters/
      exiftool_pool.py  ffprobe.py  quicklook.py  fs.py  geo.py  tracks/ (gpx.py, google_timeline.py, takeout.py, telemetry.py)
    stages/
      inventory.py  extract.py  hash.py  fingerprint.py  thumbs.py  cluster.py
    context/
      folders.py  sequences.py  batches.py  derivations.py  events.py
    resolvers/
      time/  place/  device/      # each: candidates.py, validate.py, resolve.py
    propagate/
      intervals.py  discs.py
    scoring/
      combine.py  calibrate.py  bands.py
    plan/
      layout.py  planner.py  apply.py  verify.py  undo.py
    web/
      app.py  routes/  templates/  static/ (css/, js/, img/)
  tests/
    fixtures/  test_*.py
```

---

## 15. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A wrong mass rename | Dry-run plans, review UI, journaled apply, undo scripts, verify stage |
| Extractor mixes up metadata in large batches | Integrity checks; keyed by SourceFile; sample re-reads |
| NTFS disk not writable on macOS | Pre-flight detection; output-disk mode |
| Disk failure during a long read | Health hint; resumable chunks; recommend imaging |
| Overconfident results | Calibration by masking; confidence caps; bands drive actions |
| Scope creep (faces, CLIP, PRNU) | Marked optional in the roadmap; plugin-style stages |
| Long runs interrupted (sleep, crash) | `caffeinate`, chunk commits, resumable jobs |
