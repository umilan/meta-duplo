---
name: scan-pipeline
description: The background job runner (Job / Runner / Cancelled) and the scan stages inventory → extract → hash → fingerprint (+ thumbnails) as implemented in meta_duplo/scan.py, with what each stage reads and writes, idempotency, error handling, progress and how the CLI and web layer start scans. Use when rebuilding or changing scanning, jobs, cancel or progress.
---

# Scan pipeline (`meta_duplo/scan.py`)

The scan turns the folders in `sources` into rows in `files`, `meta`, `hashes`, `fingerprints` and JPEG
thumbnails in `<library>/thumbs/`, then runs duplicate matching (steps 1 and 2, see skill 05). Everything runs
in **one background thread at a time**, reports progress into the `jobs` table, and can be cancelled between
two progress updates.

Code is the source of truth. `docs/ARCHITECTURE.md` §5–6 describe a larger engine (ExifTool pool, process
pool, chunks, SSE, `STAGE_VERSION`, `file_errors`, `meta_raw`); ADR-013 scopes the POC down. Where this skill
says **specified, not implemented**, the docs describe it and the code does not do it.

Module header (keep it): `"""Scan stages: inventory → extract → hash → fingerprint (+ thumbnails). Every stage
is idempotent: it only processes files that lack its output, and inventory clears a file's derived rows when
its size or mtime changes."""`

## Dependencies and constants

```python
import blake3                      # pip "blake3>=0.4"
import imagehash                   # pip "imagehash>=4.3"
from PIL import Image, ImageOps    # pip "pillow>=10.4" (+ "pillow-heif>=0.18", registered in metadata.py)
from . import metadata
from .db import Library, now, tx

HOLD_DIR = "_duplicates"                                  # holding folder for removed copies (plans.py imports it)
JUNK = {".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r"}
PARTIAL_BYTES = 1024 * 1024                               # first + last 1 MB
```

`db.now()` returns UTC `"%Y-%m-%dT%H:%M:%SZ"`. `tx(con)` wraps `BEGIN IMMEDIATE … COMMIT` (rollback on any
`BaseException`). `Library.connect()` opens an autocommit connection (`isolation_level=None`, WAL,
`foreign_keys=ON`, `busy_timeout=30000`, `row_factory=sqlite3.Row`). Every stage opens short-lived
connections; there is no long-held connection across file I/O.

## Job runner

### Tables used

`jobs` (from `meta_duplo/db.py`, the implemented schema, not `docs/schema.sql`):

```sql
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  name TEXT, stage TEXT, status TEXT,         -- running | done | failed | cancelled
  done INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
  message TEXT, started_at TEXT, finished_at TEXT
);
```

(The comment in `db.py` lists `running | done | failed`; the code also writes `cancelled`.)

### `class Cancelled(BaseException)`

Raised inside a job when the user cancels. It derives from `BaseException` **on purpose**: per-file handlers
use `except Exception` and must never swallow a cancel.

### `class Job`

```python
class Job:
    def __init__(self, lib: Library, job_id: int, cancel: threading.Event | None = None)
    def update(self, stage: str, done: int = 0, total: int = 0, message: str = "") -> None
```

- `update` first checks `self.cancel.is_set()` → `raise Cancelled()`. This is **the only cancellation point**.
- Then: `UPDATE jobs SET stage = ?, done = ?, total = ?, message = ? WHERE id = ?` (one autocommit statement,
  new connection each call). Note `message` is overwritten every call (an update without a message sets `''`).
- Because stages are idempotent, stopping between two updates loses nothing a later scan will not redo.

### `class Runner`

"One background job at a time (single writer)". State: `lib`, `_thread`, `_lock = threading.Lock()`,
`_cancel = threading.Event()`.

| Method | Behaviour |
|---|---|
| `busy() -> bool` | `_thread is not None and _thread.is_alive()` |
| `cancel() -> bool` | `False` if not busy; else `_cancel.set()` and `True` |
| `start(name, fn: Callable[[Job], None]) -> int \| None` | Under `_lock`: if busy return `None` (the request is **dropped**, not queued). Else `INSERT INTO jobs(name, stage, status, started_at) VALUES (?, 'starting', 'running', ?)` with `now()`; create a **fresh** `_cancel = threading.Event()`; build `Job`; start a daemon thread named `f"job-{job_id}"`; return `job_id`. |
| `run_sync(name, fn) -> None` | `start(...)`, then `join()` the thread if a job was started (CLI and tests). If busy, returns immediately without running. |

Thread body:

```python
status, msg = "done", ""
try:
    fn(job)
except Cancelled:
    status, msg = "cancelled", "Cancelled"
except Exception as exc:                      # per-file errors never get here
    status, msg = "failed", f"{exc}\n{traceback.format_exc(limit=3)}"
UPDATE jobs SET status = ?, finished_at = ?,
       message = CASE WHEN ? = '' THEN message ELSE ? END WHERE id = ?   -- keep last progress message on success
```

Notes for a faithful rebuild:
- One `Runner` per process. The CLI and the web server each create their own; nothing coordinates two
  processes, and a job row left `running` by a killed process is never repaired.
- The web `/job` endpoint reads `runner._cancel.is_set()` to render "Cancelling…".
- **Specified, not implemented**: durable queue with `queued|paused`, process pool, ~500-file chunks,
  pause/resume, SSE progress (`/events/jobs/{id}`), `caffeinate`, one I/O stage per physical disk.

## `scan_all(lib, job)` — stage order

```python
def scan_all(lib: Library, job: Job) -> None:
    from . import match                 # imported inside the function
    inventory(lib, job)
    extract(lib, job)
    hash_stage(lib, job)
    fingerprint(lib, job)               # also writes thumbnails and hashes.content
    match.step1_exact(lib, job)         # identical groups (skill 05)
    match.step2_near(lib, job)          # near duplicates + similar shots (skill 05)
    job.update("done", 1, 1, "Scan complete")
```

There is no separate `thumbs` stage: thumbnails are a by-product of `fingerprint`. `harmonize.build_proposals`
(step 3) is **not** part of `scan_all`; the web layer chains it (see below).

## Stage 1: `inventory(lib, job)`

Reads `SELECT * FROM sources`. For each source (in `id` order as returned):

1. `root = Path(src["root"])`; if `not root.is_dir()` → `continue` (offline disk: its files keep their
   current `present` value; nothing is marked missing).
2. `job.update("inventory", message=str(root))`.
3. Walk with `os.walk(root)` (top-down, follows no symlinked dirs by default) and collect tuples
   `(rel, name, ext, size, mtime, inode)` in memory:

| Rule | Code |
|---|---|
| Directories pruned (in place, then sorted) | name starts with `.`; name `== HOLD_DIR` (`_duplicates`, at **any** depth); name ends with `.photoslibrary` |
| Files skipped by name | in `JUNK` (`.DS_Store`, `Thumbs.db`, `desktop.ini`, `Icon\r`); starts with `._` (AppleDouble); starts with `.` (any hidden file) |
| `lstat()` raises `OSError` | skipped silently |
| Symlinks, non-regular files | `p.is_symlink() or not p.is_file()` → skipped |
| Cloud placeholders | `getattr(st, "st_flags", 0) & 0x40000000` (`SF_DATALESS`, macOS iCloud/Dropbox online-only) → skipped, because reading would download it |
| Order | `sorted(dirnames)`, `sorted(filenames)` (deterministic ids) |
| `rel` | `unicodedata.normalize("NFC", str(p.relative_to(root)))` |
| `name` | `unicodedata.normalize("NFC", fn)` |
| `ext` | `p.suffix[1:].lower()` (no dot; `""` if none) |
| `size, mtime, inode` | `st.st_size`, `st.st_mtime` (float seconds), `st.st_ino` |

Every regular file is inventoried, whatever its kind (`kind` = `metadata.kind_of(ext)`: image / video /
sidecar / other, see skill 04). Only later stages filter by kind.

4. Reconcile in **one transaction per source** (`with lib.connect() as con, tx(con)`):

```sql
SELECT id, rel_path, size, mtime, removed FROM files WHERE source_id = ?     -- keyed by rel_path
```

| Case | Action |
|---|---|
| New `rel` | `INSERT INTO files(source_id, rel_path, name, ext, kind, size, mtime, inode, original_name) VALUES (?,?,?,?,?,?,?,?,?)` with `original_name = name` (first name ever seen; renames never change it) |
| Changed: `row.size != size or abs((row.mtime or 0) - mtime) > 1e-3` | `UPDATE files SET size=?, mtime=?, inode=?, present=1, error=NULL WHERE id=?`, then `DELETE FROM meta / hashes / fingerprints WHERE file_id=?` (invalidation) |
| Unchanged | `UPDATE files SET present = 1 WHERE id = ?` |
| Known row not seen and `removed = 0` | `UPDATE files SET present = 0 WHERE id = ?` |
| Known row not seen and `removed = 1` | untouched (it lives in `_duplicates/…`, which the walk skips; it stays evidence) |

Invalidation does **not** delete the old thumbnail file (fingerprint overwrites `thumbs/{id}.jpg` later), nor
`captures`, `group_members`, `proposals` (matching and harmonize rebuild those). `name`, `ext`, `kind` are
never updated for an existing row (same `rel_path` ⇒ same name).

5. `job.update("inventory", len(seen), len(seen), f"{len(seen)} files in {root}")`.

Progress granularity: two updates per source. A long walk cannot be cancelled until the source is finished.

Paths: `files.rel_path` is NFC and filesystem access later uses `Path(root) / rel_path` (works on APFS/HFS+,
which are normalization-insensitive). **Specified, not implemented**: storing the original path bytes
(`rel_path_raw`) for exact filesystem operations (CLAUDE.md rule 9), `birthtime`, `folders`, sidecar
detection/linking (`sidecars` table), `seen_run`, volume UUIDs. Sidecars are only classified by `kind`.

## Stage 2: `extract(lib, job)`

Helper (also used elsewhere): `abs_path(con, file_id) -> Path` = `Path(root) / rel_path` via
`files JOIN sources`.

```python
home_tz = lib.setting("home_timezone")       # default "Europe/Bratislava" (IANA name)
todo = SELECT f.id, f.kind, s.root, f.rel_path FROM files f JOIN sources s ON s.id = f.source_id
       LEFT JOIN meta m ON m.file_id = f.id
       WHERE f.present = 1 AND f.kind IN ('image', 'video')
         AND (m.file_id IS NULL OR instr(COALESCE(m.raw_json, ''), ?) = 0)
       -- param: f'"_v": {metadata.RAW_VERSION}'   e.g. '"_v": 3'
```

- Selects files without a `meta` row **or** whose `raw_json` was written by an older `RAW_VERSION` (string
  match on the default `json.dumps` spacing `"_v": 3`). This is the only versioning mechanism in the code.
- Removed files (`removed = 1`, `present = 1`) are included: copies in the holding folder stay evidence.
- For each row `i`: every 25 files `job.update("extract", i, len(todo))`; then
  `metadata.read(path, kind, home_tz)` (skill 04). Any `Exception` → `m = metadata.Meta()`,
  `err = f"extract: {exc}"`.
- Write (one autocommit connection per file):

```sql
INSERT OR REPLACE INTO meta(file_id, width, height, duration, make, model, software, dto, dto_source,
  modify_date, gps_lat, gps_lon, unique_id, orientation, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
-- raw_json = json.dumps(m.raw, default=str)
UPDATE files SET error = ? WHERE id = ?            -- only when err
```

- End: `job.update("extract", len(todo), len(todo))`.
- Consequence: a failed read stores `raw_json = '{}'` (no `_v`), so the file is **retried on every scan**;
  so is every video while `ffprobe` is missing (empty `Meta`). This is intentional enough to keep.

## Stage 3: `hash_stage(lib, job)` — size → partial → full cascade

```sql
SELECT f.id, f.size, f.inode, f.mtime, s.root, f.rel_path, h.partial, h.full FROM files f
JOIN sources s ON s.id = f.source_id LEFT JOIN hashes h ON h.file_id = f.id
WHERE f.present = 1 AND f.removed = 0 AND f.kind IN ('image', 'video') AND f.size > 0
```

1. Group by `size` across **all sources**. `cands` = rows in groups with more than one file. A file with a
   unique size is never read.
2. Partial hash for each candidate (progress every 50: `job.update("hash: partial", i, len(cands))`):
   reuse `r["partial"]` if stored, else `_partial(path, size)`; `OSError` → skip the file (no error recorded).

```python
def _partial(path: Path, size: int) -> str:
    h = blake3.blake3()
    with open(path, "rb") as fh:
        h.update(fh.read(PARTIAL_BYTES))                      # first 1 MB
        if size > PARTIAL_BYTES:
            fh.seek(max(PARTIAL_BYTES, size - PARTIAL_BYTES)) # last 1 MB, never overlapping the head
            h.update(fh.read(PARTIAL_BYTES))
    return h.hexdigest()
```

3. Group candidates by `(size, partial)`; `full_cands` = groups with more than one file. Full hash (progress
   every 10: `job.update("hash: full", i, len(full_cands))`): reuse `r["full"]` or `_full(path)` — BLAKE3 over
   8 MB chunks (`fh.read(8 * 1024 * 1024)`), `hexdigest()`. `OSError` → skip.
4. Upsert **every** selected row in one transaction:

```sql
INSERT INTO hashes(file_id, partial, full, cache_key) VALUES (?, ?, ?, ?)
ON CONFLICT(file_id) DO UPDATE SET partial = excluded.partial, full = excluded.full, cache_key = excluded.cache_key
-- cache_key = f"{inode}:{size}:{mtime}"; partial/full are NULL when the file was not a candidate
```

   So a file that no longer collides has its `partial`/`full` reset to `NULL`; `content` (written by
   fingerprint) is preserved. `cache_key` is stored but not read anywhere (reuse is by the stored columns,
   which inventory deletes on change).
5. `job.update("hash", len(rows), len(rows))`.

Hash cost is reduced by reuse: a rescan rereads nothing unless inventory invalidated the row.
**Specified, not implemented**: ExifTool `ImageDataHash`, video stream hashes, `stage_version` column.

## Stage 4: `fingerprint(lib, job)` — one decode per image

```sql
SELECT f.id, s.root, f.rel_path FROM files f JOIN sources s ON s.id = f.source_id
LEFT JOIN fingerprints p ON p.file_id = f.id
WHERE f.present = 1 AND f.removed = 0 AND f.kind = 'image' AND p.file_id IS NULL
```

Images only (videos get no fingerprint and no thumbnail; `/thumb/{id}` then serves
`static/img/placeholder.svg`). For each file (progress every 10: `job.update("fingerprint", i, len(todo))`):

```python
with Image.open(path) as im:
    img = ImageOps.exif_transpose(im).convert("RGB")     # orientation first, or rotated copies never match
content = blake3.blake3(f"{img.width}x{img.height}".encode() + img.tobytes()).hexdigest()
dh, ph = str(imagehash.dhash(img)), str(imagehash.phash(img))   # 64-bit, 16 hex chars
if int(dh, 16) in (0, 2**64 - 1):                        # solid images: degenerate hashes never match
    dh = ph = None
img.thumbnail((360, 360))                                # in place, keeps aspect ratio
img.save(thumbs / f"{r['id']}.jpg", "JPEG", quality=82)  # thumbs = lib.path / "thumbs"
```

Success writes:

```sql
INSERT OR REPLACE INTO fingerprints(file_id, dhash, phash) VALUES (?, ?, ?)
INSERT INTO hashes(file_id, content) VALUES (?, ?) ON CONFLICT(file_id) DO UPDATE SET content = excluded.content
```

Any `Exception` (decode, HEIC without plugin, save error):

```sql
UPDATE files SET error = ? WHERE id = ?                       -- f"decode: {exc}"
INSERT OR REPLACE INTO fingerprints(file_id, dhash, phash) VALUES (?, NULL, NULL)  -- marks "tried": not retried
```

End: `job.update("fingerprint", len(todo), len(todo))`.

`hashes.content` = "hash of decoded, orientation-corrected pixels" (same pixels, different metadata ⇒ same
content). `match.load_files` parses `dhash`/`phash` with `int(x, 16)`.
**Specified, not implemented**: JPEG quant-table hash, structure hash, video keyframe hashes, Quick Look /
`sips` thumbnails at 256/1024 px (ADR-007), decoding from embedded EXIF thumbnails.

## Idempotency and versions

| Stage | "Already done" test | Invalidated by |
|---|---|---|
| inventory | always runs; upserts by `(source_id, rel_path)` (`UNIQUE`) | – |
| extract | `meta` row exists with `"_v": RAW_VERSION` in `raw_json` | size/mtime change (row deleted); `RAW_VERSION` bump |
| hash | stored `partial` / `full` reused | size/mtime change |
| fingerprint | `fingerprints` row exists (even all-NULL after a failure) | size/mtime change |
| match step 1/2 | recomputed every scan (`_save_groups` deletes and reinserts the step's groups and `selection` rows, carrying non-open statuses over by member signature; skill 05) | – |

There is **no `STAGE_VERSION` constant** anywhere in the code (CLAUDE.md rule 6 and ARCHITECTURE §5 specify
one per stage, with stale-marking): **specified, not implemented**. `metadata.RAW_VERSION` is the only
version. If you change hash or fingerprint semantics in a rebuild, add a version and a re-run condition.

## Per-file errors

| Where | What happens |
|---|---|
| inventory `lstat` fails | file silently skipped |
| extract raises | empty `meta` row + `files.error = "extract: …"`; run continues |
| hash `OSError` | file silently left out of partial/full maps (`NULL`s) |
| fingerprint raises | `files.error = "decode: …"` + NULL fingerprint row |
| anything else (DB error, bug) | propagates → job `failed` with message + 3-frame traceback |

`files.error` is a single column, overwritten by the latest stage and cleared by inventory when the file
changes. The `file_errors(file_id, stage, error, at)` table in `docs/schema.sql` and CLAUDE.md rule 5 is
**specified, not implemented** (no such table in `db.py`).

## Progress reporting summary

| Stage string(s) | Frequency | done/total |
|---|---|---|
| `starting` | job insert | 0/0 |
| `inventory` | start + end of each source | 0/0 with root as message; then n/n, `"{n} files in {root}"` |
| `extract` | every 25 files + final | i/len(todo) |
| `hash: partial` | every 50 candidates | i/len(cands) |
| `hash: full` | every 10 candidates | i/len(full_cands) |
| `hash` | once at end | rows/rows |
| `fingerprint` | every 10 images + final | i/len(todo) |
| `step 1: identical` | once | groups/groups, `"{n} identical groups"` |
| `step 2: comparing` | every 2000 pairs | i/len(pairs) |
| `step 2: near` | once | `"{n} near-duplicate groups, {m} similar-shot groups"` |
| `step 3: recommendations` | (only in web chains) | captures |
| `done` | end of scan_all | 1/1, `"Scan complete"` |

The UI polls: `partials/job.html` renders while `status == 'running'` with
`hx-get="/job?watch=1" hx-trigger="every 1s"`, a `<progress value=done max=total>` (indeterminate when
`total` is 0), and a Cancel button (`hx-post="/job/cancel"`). `GET /job?watch=1` answers `HX-Refresh: true`
once the last job is `done` or `cancelled`; a `failed` job renders an alert with `message[:300]`.

## Callers

### CLI (`meta_duplo/__main__.py`)

```python
elif args.cmd == "scan":                 # "run steps 1-2 (inventory, metadata, hashes, duplicates)"
    from .scan import Runner, scan_all
    Runner(lib).run_sync("scan", lambda job: scan_all(lib, job))
    # then prints dict(name, status, message) of the newest jobs row
```

`add_source(lib, root, writable=False, role="scan") -> int` resolves `Path(root).expanduser().resolve()` and
upserts `sources(label=Path(root).name, root, writable, role, added_at)` `ON CONFLICT(root) DO UPDATE SET
writable, role`. It does not scan. `remove_source(lib, sid) -> bool` refuses (False) when any `actions` row
refers to the source's files; otherwise deletes the files' rows in `selection, rename_exclude, captures,
group_members, meta, hashes, fingerprints`, then `files`, then the `sources` row. Nothing on disk is touched;
thumbnails are left in `thumbs/`.

### Web (`meta_duplo/web/app.py`)

`create_app` builds one `runner = Runner(lib)`; `start(name, fn)` calls `runner.start` and ignores a `None`
(busy) result.

| Route | Job name | Work |
|---|---|---|
| `POST /scan` | `scan` | `scan_all` only; returns `partials/job.html` |
| `GET /job` / `POST /job/cancel` | – | progress partial / `runner.cancel()` |
| `POST /harmonize` | `recommend` | `harmonize.build_proposals(lib, job)` |
| `POST /recheck` | `re-check` | `build_proposals` then `match.step4_recheck` |
| `POST /wb/sources/pick`, `POST /wb/sources`, `POST /wb/sources/demo` | `scan` | `add_source(...)` then `_rescan()`; respond `HX-Refresh: true` |
| `POST /wb/sources/{sid}/remove`, `POST /wb/sources/remove-all` | `scan` | `remove_source` (refusal → toast, no rescan when nothing removed), drop the id from `view["sources_off"]`, then `_rescan()` |
| `POST /sources` (classic page) | – | `add_source` only, no scan (`HX-Redirect: /sources`) |

```python
def _rescan() -> None:
    def fn(job):
        scan_all(lib, job)
        harmonize.build_proposals(lib, job)     # keep captures and proposals in sync with the new file set
    start("scan", fn)
```

If a job is already running, `_rescan` is silently dropped; the user must scan again later.

## Rebuild checklist

1. Implement `Cancelled(BaseException)`, `Job.update` (cancel check first, then one `UPDATE jobs`), and
   `Runner` with `busy/cancel/start/run_sync` exactly as above, including the fresh `Event` per start and the
   `CASE WHEN ? = '' THEN message ELSE ? END` final update.
2. Define `HOLD_DIR`, `JUNK`, `PARTIAL_BYTES`; export `HOLD_DIR` for `plans.py`.
3. `inventory`: walk rules (pruned dirs, junk, `._`, hidden, symlinks, `SF_DATALESS 0x40000000`), NFC `rel`
   and `name`, lowercase `ext`, per-source transaction, the four reconcile cases, 1e-3 s mtime tolerance,
   delete `meta/hashes/fingerprints` on change, never touch `removed = 1` rows.
4. `extract`: query with the `"_v": RAW_VERSION` re-read condition, `home_timezone` setting, per-file
   try/except, `INSERT OR REPLACE INTO meta` with `json.dumps(raw, default=str)`, `files.error`.
5. `hash_stage`: size grouping → `_partial` (head + non-overlapping tail) → `_full` (8 MB chunks), reuse of
   stored values, upsert of all rows with `cache_key`, never touching `content`.
6. `fingerprint`: `exif_transpose` → RGB → content BLAKE3 (`"{w}x{h}"` prefix) → dHash/pHash with the
   degenerate-hash guard → 360 px JPEG q82 thumbnail at `thumbs/{id}.jpg`; NULL row + `decode:` error on failure.
7. `scan_all` in the stated order ending with `job.update("done", 1, 1, "Scan complete")`.
8. Wire the CLI `scan` command (`run_sync`) and the web routes, including `_rescan` = `scan_all` +
   `build_proposals` after adding/removing sources.

## Verify

- `uv run pytest`: every fixture scans the generated demo disk with
  `Runner(lib).run_sync("scan", lambda job: scan_all(lib, job))` (`tests/test_workflow.py`,
  `tests/test_results.py`, `tests/test_workbench.py`). `test_step1_identical` expects 5 identical groups;
  `test_step2_near` expects `same_pixels`, `same_photo`, `same_video`, `similar_shot` verdicts.
- `tests/test_results.py::test_cancel_stops_a_running_job`: a slow job cancelled mid-way ends with
  `status = 'cancelled'`, `done < 9999`, and `runner.cancel()` then returns `False`.
- Manual check on a demo disk (with `ffmpeg` installed) plus a `.DS_Store`, a `._x` file, a
  `_duplicates/a.jpg`, `notes.txt` and `IMG_0001.xmp` added at the root: after one scan
  - `files`: 23 rows (18 image, 3 video, 1 sidecar, 1 other); nothing from `_duplicates/`, `.DS_Store`, `._*`;
  - `meta`: 21 rows; `hashes`: 21 rows, 11 with `partial` and `full`, 18 with `content`;
  - `fingerprints`: 18 rows; `thumbs/`: 18 JPEGs, none larger than 360 px;
  - last job: `stage='done', status='done', done=1, total=1, message='Scan complete'`.
- Rescan with no changes: `extract` / `fingerprint` report `0/0`; no file content is reread (partials and
  fulls come from the DB). Touch one image's mtime → exactly that file is re-extracted and re-fingerprinted.
  Delete one → its row gets `present = 0`.
- Start a second job while one runs: `start` returns `None` and no new `jobs` row appears.
