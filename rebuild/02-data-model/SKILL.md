---
name: data-model
description: The SQLite data model meta-duplo actually creates (db.py SCHEMA) - every table, column, index and status value, JSON column shapes, every settings key with its default and JSON shape, jobs usage, file identity rules, and how derived rows are invalidated when files change or move. Use it when rebuilding the storage layer or when writing any code that reads or writes the library database.
---

# meta-duplo: data model (as implemented)

**Source of truth:** `meta_duplo/db.py` (`SCHEMA`, `DEFAULT_SETTINGS`, `Library`). `docs/schema.sql` and
`docs/DATA_MODEL.md` describe the **target** model (migration 0001: clusters, candidates, resolution,
confidence…). The POC creates a smaller, different schema (ADR-013). A mapping is given in §9.

## 1. Storage basics

- One library = one file, `<library>/library.sqlite`. See the architecture skill for the folder layout.
- Every connection is opened by `Library.connect()`:

```python
con = sqlite3.connect(db_path, timeout=30, isolation_level=None)   # autocommit
con.row_factory = sqlite3.Row
con.execute("PRAGMA journal_mode=WAL")
con.execute("PRAGMA foreign_keys=ON")
con.execute("PRAGMA busy_timeout=30000")
```

- Multi-statement writes use `with tx(con):`, which runs `BEGIN IMMEDIATE`, then `COMMIT`, or `ROLLBACK` and
  re-raises on any `BaseException`. Single statements autocommit.
- Timestamps written by the app use `now()`, which gives UTC `YYYY-MM-DDTHH:MM:SSZ`. Exception: `decisions.decided_at`
  uses SQLite `datetime('now')`, which gives `YYYY-MM-DD HH:MM:SS` (UTC, no `T`/`Z`).
- Capture times are **naive local** strings `YYYY-MM-DD HH:MM:SS` (`metadata.TIME_FMT`). They are never stored as UTC.
- `files.mtime` is a float POSIX timestamp from `lstat`.
- Queries are always parameterized. Dynamic `IN (…)` lists are built as `",".join("?" * n) or "NULL"`.

### Migrations

**As implemented:** there are no migrations and no `meta_duplo/db/migrations/` folder. `Library.__init__` runs
`con.executescript(SCHEMA)` on every open. Each statement is `CREATE TABLE/INDEX IF NOT EXISTS`. Then
`DEFAULT_SETTINGS` are written with `INSERT OR IGNORE`. New tables appear in old libraries automatically.
**Column changes to existing tables never reach an existing database.** No `PRAGMA user_version` is used.

**Rule for the rebuild (CLAUDE.md, "never change tables without a new numbered migration"):**

- Keep `migrations/NNNN_<slug>.sql` with a 4-digit, gap-free, never-renumbered prefix.
- `0001` must be exactly the schema in §2. This is the POC baseline, not `docs/schema.sql`.
- On open, read `PRAGMA user_version` and apply each higher-numbered file in one `tx`, then set `user_version = N`.
- If a DB has tables but `user_version = 0`, it is a POC library: stamp it `1` and do not re-create its tables.

## 2. Full schema (verbatim from `db.SCHEMA`)

```sql
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  label TEXT,
  root TEXT NOT NULL UNIQUE,
  writable INTEGER NOT NULL DEFAULT 0,
  role TEXT NOT NULL DEFAULT 'scan',          -- scan | reference
  added_at TEXT
);
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES sources(id),
  rel_path TEXT NOT NULL,                     -- NFC, relative to source root
  name TEXT NOT NULL,
  ext TEXT,
  kind TEXT,                                  -- image | video | sidecar | other
  size INTEGER,
  mtime REAL,
  inode INTEGER,
  original_name TEXT,                         -- first name ever seen; renames never change it
  present INTEGER NOT NULL DEFAULT 1,
  removed INTEGER NOT NULL DEFAULT 0,         -- moved to the holding folder; still evidence
  error TEXT,
  UNIQUE (source_id, rel_path)
);
CREATE INDEX IF NOT EXISTS files_size ON files(size);
CREATE TABLE IF NOT EXISTS meta (
  file_id INTEGER PRIMARY KEY REFERENCES files(id),
  width INTEGER, height INTEGER, duration REAL,
  make TEXT, model TEXT, software TEXT,
  dto TEXT,                                   -- capture time as naive local 'YYYY-MM-DD HH:MM:SS'
  dto_source TEXT,
  modify_date TEXT,
  gps_lat REAL, gps_lon REAL,
  unique_id TEXT,
  orientation INTEGER,
  raw_json TEXT
);
CREATE TABLE IF NOT EXISTS hashes (
  file_id INTEGER PRIMARY KEY REFERENCES files(id),
  partial TEXT,                               -- BLAKE3 of first + last 1 MB (size collisions only)
  full TEXT,                                  -- BLAKE3 of the whole file (partial collisions only)
  content TEXT,                               -- hash of decoded, orientation-corrected pixels
  cache_key TEXT
);
CREATE INDEX IF NOT EXISTS hashes_full ON hashes(full);
CREATE INDEX IF NOT EXISTS hashes_content ON hashes(content);
CREATE TABLE IF NOT EXISTS fingerprints (
  file_id INTEGER PRIMARY KEY REFERENCES files(id),
  dhash TEXT, phash TEXT                      -- 64-bit hex
);
CREATE TABLE IF NOT EXISTS groups (
  id INTEGER PRIMARY KEY,
  step INTEGER NOT NULL,                      -- 1 exact | 2 near | 4 re-check
  verdict TEXT,                               -- identical | same_pixels | same_photo | edited | similar_shot | likely
  score REAL,
  keeper_file_id INTEGER,
  status TEXT NOT NULL DEFAULT 'open',        -- open | resolved | ignored | not_duplicate
  signature TEXT,
  reclaimable INTEGER
);
CREATE TABLE IF NOT EXISTS group_members (
  group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  file_id INTEGER NOT NULL REFERENCES files(id),
  role TEXT,                                  -- keeper | member
  score REAL,
  evidence TEXT,                              -- JSON evidence vector against the keeper
  PRIMARY KEY (group_id, file_id)
);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY,
  scope TEXT,                                 -- pair | group
  subject TEXT,                               -- 'hashA|hashB' (sorted) or group signature
  decision TEXT,                              -- not_duplicate | ignore
  note TEXT,
  decided_at TEXT
);
CREATE TABLE IF NOT EXISTS captures (
  file_id INTEGER PRIMARY KEY REFERENCES files(id),
  capture_id INTEGER NOT NULL                 -- one capture = files that are the same photo or video
);
CREATE TABLE IF NOT EXISTS proposals (
  capture_id INTEGER NOT NULL,
  field TEXT NOT NULL,                        -- capture_time | location_name | device_name
  current TEXT,
  value TEXT,
  extra TEXT,                                 -- JSON (e.g. lat/lon for location)
  source TEXT,
  confidence REAL,
  status TEXT NOT NULL DEFAULT 'suggested',   -- suggested | accepted | edited | rejected
  explanation TEXT,
  PRIMARY KEY (capture_id, field)
);
CREATE TABLE IF NOT EXISTS plans (
  id INTEGER PRIMARY KEY,
  kind TEXT,                                  -- cleanup | rename
  status TEXT,                                -- draft | applied | partially_applied | undone
  created_at TEXT, applied_at TEXT, undone_at TEXT,
  summary TEXT
);
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY,
  plan_id INTEGER NOT NULL REFERENCES plans(id),
  group_key TEXT,                             -- actions sharing a key apply atomically
  file_id INTEGER,
  op TEXT,                                    -- hold | move | rename | write_xmp | set_mtime
  src TEXT, dst TEXT,
  payload TEXT,
  status TEXT NOT NULL DEFAULT 'planned',     -- planned | done | skipped | failed | undone
  error TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  name TEXT, stage TEXT, status TEXT,         -- running | done | failed
  done INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
  message TEXT, started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS selection (     -- files ticked for removal in the results table
  file_id INTEGER PRIMARY KEY REFERENCES files(id),
  group_id INTEGER NOT NULL,
  step INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS rename_exclude (  -- files the user left out of renaming (step 3)
  file_id INTEGER PRIMARY KEY REFERENCES files(id)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
```

That is 16 tables. Explicit indexes: `files_size`, `hashes_full`, `hashes_content`. Implicit unique indexes:
`sources(root)`, `files(source_id, rel_path)`, the PKs of `group_members`, `proposals` and `settings`. Every other
`… INTEGER PRIMARY KEY` column is a rowid alias. The comments are not complete; the real status sets are in §3.

## 3. Tables: meaning of each column

### sources
| Column | Meaning |
|---|---|
| `label` | `Path(root).name`, shown in paths as the first segment (`<label>/<parent>`) |
| `root` | absolute, `expanduser().resolve()`d folder. **This is the source's identity** (no volume UUID) |
| `writable` | 0/1. Plans skip files on non-writable sources (cleanup: `blocked`; rename: `blocked` unless unchanged/no_date). The Sources page toggles it (`1 - writable`) |
| `role` | `scan` or `reference`. A reference copy wins keeper ranking (`keeper_key` first element) and is never written to |
| `added_at` | `now()` |

`add_source` SQL: `INSERT … ON CONFLICT(root) DO UPDATE SET writable = excluded.writable, role = excluded.role`.

### files
| Column | Meaning |
|---|---|
| `rel_path` | NFC (`unicodedata.normalize("NFC", …)`), POSIX-style, relative to `sources.root`. Changed by plans (hold → `_duplicates/group-<gid>/<old rel_path>`, rename → same folder with the new name) |
| `name` | NFC basename; follows renames |
| `ext` | lowercase suffix without the dot (`""` if none) |
| `kind` | `metadata.kind_of(ext)`: `image` {jpg jpeg png heic heif tif tiff webp gif bmp dng}, `video` {mov mp4 m4v avi mts 3gp mkv}, `sidecar` {xmp aae json thm}, else `other`. Only image/video are processed; sidecar rows are inventory only |
| `size`, `mtime`, `inode` | from `lstat` at the last inventory (mtime is also updated by a `set_mtime` action) |
| `original_name` | the name at first insert; never updated. Feeds `{original_filename}` and file-name dates |
| `present` | 1 = seen at the last inventory (or moved by a hold); 0 = missing. Rows are never deleted by scans |
| `removed` | 1 = moved to the holding folder by an applied cleanup plan. Still used as evidence in step 3 |
| `error` | last per-file error, prefixed `extract: …` or `decode: …`. Cleared when the file changes |

Skipped by inventory: names in `JUNK`, names starting with `.` or `._`, symlinks, non-regular files, files with
`st_flags & 0x40000000` (SF_DATALESS cloud placeholders), and folders starting with `.`, named `_duplicates`, or
ending in `.photoslibrary`.

### meta (one row per extracted image/video)
| Column | Meaning |
|---|---|
| `width`, `height` | pixels. Images: swapped when EXIF orientation ∈ {5,6,7,8}. Videos: the first video stream |
| `duration` | seconds, rounded to 2 decimals (videos) |
| `make`, `model`, `software` | EXIF 0x010F/0x0110/0x0131, or QuickTime `com.apple.quicktime.make/model/software` (else `make`/`model`/`encoder`) |
| `dto` | capture time, naive local. Images: ExifIFD DateTimeOriginal (0x9003), else DateTimeDigitized (0x9004). Videos: `com.apple.quicktime.creationdate` wall clock, else `creation_time` UTC → `ZoneInfo(home_timezone)`. Passed through `valid_time` (rejects < 1826, the future, 1904/1970/1980-01-01, and 2000–2012 Jan 1 00:00–00:10) |
| `dto_source` | `EXIF DateTimeOriginal` (also used when the value came from DateTimeDigitized), `QuickTime CreationDate`, `QuickTime creation_time (UTC)`, or NULL |
| `modify_date` | IFD0 DateTime (0x0132), validated |
| `gps_lat`, `gps_lon` | decimal degrees, 6 dp. `(0,0)` becomes NULL. Videos: ISO 6709 `com.apple.quicktime.location.iso6709` or `location` |
| `unique_id` | ExifIFD ImageUniqueID (0xA420) |
| `orientation` | EXIF 0x0112 |
| `raw_json` | JSON; see the shapes below |

`raw_json` shapes:

```jsonc
// image
{"_v": 3, "Make": "...", "Model": "...", "Software": "...", "DateTimeOriginal": "2019:05:14 18:22:03",
 "DateTime": "...", "OffsetTimeOriginal": "+02:00", "Orientation": 1, "GPS": [48.144, 17.1101] | null,
 "Format": "JPEG", "DateTimeDigitized": "2019-05-14 18:22:03" | null, "GPSDateTime": "2019-05-14 16:22:03" | null,
 "Artist": "...", "Copyright": "...", "OwnerName": "...", "SerialNumber": "...", "LensModel": "...",
 "DateCreated": "..." /* XMP or IPTC */,
 "tags": {"IFD0:Make": "Apple", "ExifIFD:DateTimeOriginal": "...", "GPS:GPSLatitude": "48.0, 8.0, 38.4",
          "XMP:DateCreated": "...", "IPTC:DateCreated": "2019-05-14 18:22:03+0200", "...": "..."}}
// video
{"_v": 3, "format": "mov,mp4,m4a,3gp,3g2,mj2", "tags": {"QuickTime:creation_time": "...", "QuickTime:com.apple.quicktime.make": "..."}}
```

Rules for `tags`: blobs longer than 64 bytes become `"<N bytes>"`; XP* tags are decoded as UTF-16LE; rationals become floats
rounded to 6 places; tuples are joined with `", "`; empty values are dropped; sub-IFD pointers (0x8769, 0x8825, 0xA005)
are skipped. Without ffprobe, a video row gets `raw_json = "{}"` (no `_v`), so it is re-read on every scan.

### hashes / fingerprints
| Column | Meaning |
|---|---|
| `hashes.partial` | BLAKE3 hex of the first 1 MiB + the last 1 MiB. Only for files whose size collides with another present, non-removed image/video |
| `hashes.full` | BLAKE3 hex of the whole file (8 MiB reads). Only when `(size, partial)` collides |
| `hashes.content` | `blake3(f"{w}x{h}".encode() + RGB bytes)` after `ImageOps.exif_transpose`. Images only, written by `fingerprint` |
| `hashes.cache_key` | `"{inode}:{size}:{mtime}"`. Written, **never read** |
| `fingerprints.dhash`, `.phash` | `str(imagehash.dhash/phash(img))`, 16 hex chars. NULL when the dHash is all zeros or all ones (solid image) or decoding failed. A NULL row means "done, nothing usable" |

### groups / group_members (steps 1, 2, 4)
| Column | Meaning |
|---|---|
| `step` | 1 identical, 2 near (dups and bursts), 4 re-check |
| `verdict` | step 1 `identical`; step 2 the most common edge verdict of `same_pixels`/`same_photo`/`edited`/`same_video`, or `similar_shot` for bursts; step 4 `likely` |
| `score` | step 1 `1.0`; otherwise the minimum edge score between the keeper and its members |
| `keeper_file_id` | the anchor (best `keeper_key`); `results.set_keeper` and `smart_select` change it |
| `status` | `open`, `resolved` (an applied cleanup removed every non-keeper), `ignored`, `not_duplicate` |
| `signature` | sorted member file ids, comma-joined (`"3,7,12"`). Used to carry status across rebuilds and as the subject of group decisions |
| `reclaimable` | sum of the sizes of the non-keepers (recomputed on a keeper change) |
| `group_members.role` | `keeper` or `member` |
| `group_members.score` | 1.0 for the keeper, otherwise the edge score |
| `group_members.evidence` | JSON. Step 1: `{"full_hash": "<16 hex>"}`. Step 2: `{"time_delta_s", "same_device", "duration", "visual", "dhash_dist", "phash_dist", "size_ratio", "same_aspect", "same_name"}` (subset). Step 4: `{"visual", "time_delta_s", "same_device", "same_place", "name_similarity", "size_ratio"}`. Keeper: `{}` |

Grouping is anchor-based (no chaining) and gives one group per file within a step. Hardlinks (same `(source_id, inode)`)
count as one copy in step 1.

### decisions
| `scope` | `subject` | `decision` | Written by |
|---|---|---|---|
| `pair` | `"|".join(sorted([a.key, b.key]))`, where `F.key = content or full or f"file:{id}"` | `not_duplicate` | `/groups/{gid}/decide` with `not_duplicate`: every pair of members |
| `group` | `groups.signature` | `ignore` | `decide` with `ignore`; `reopen` deletes it (pair rows stay) |

Pair decisions are keyed by **content**, so they survive renames and moves. Steps 2 and 4 skip those pairs.
Group decisions are keyed by **file ids**, and any step's grouping skips a group with that signature.

### captures / proposals (step 3)
- `captures(file_id, capture_id)`: union-find over step 1/2 groups whose verdict is in `DUP_VERDICTS` and whose status
  is not `not_duplicate`/`ignored`, plus `actions(op='hold', status='done').payload.keeper_id` (so removed copies
  stay attached). `capture_id` is the **smallest file id** in the set. It covers image/video files with
  `present = 1 OR removed = 1`, and is rebuilt completely by `build_captures`.
- `proposals`: one row per `(capture_id, field)` with `field` ∈ `capture_time`, `location_name`, `device_name`.

| Column | Meaning |
|---|---|
| `current` | the keeper's value now (`dto`, geocoded town, `device_name(make, model)`) |
| `value` | the recommended value, or the user's typed value when `status = 'edited'` |
| `extra` | JSON. Location: `{"lat", "lon", "city", "country"}`; otherwise `{}` |
| `source`, `confidence`, `explanation` | where the value came from (`"metadata of IMG_1.JPG"`, `"GPS of …"`), a 0–1 tier score, and the text shown |
| `status` | `suggested`, `accepted`, `edited`, `rejected`. A rebuild keeps the user statuses. A `suggested` row whose value equals `current` and whose confidence is ≥ 0.9 (or whose value is empty) is auto-`accepted` |

`effective_values(con)` gives `{capture_id: {capture_time, location_name, device_name[, lat, lon, country]}}`, using
`value` for accepted/edited rows and `current` otherwise.

### plans / actions
| Column | Values |
|---|---|
| `plans.kind` | `cleanup`, `rename` |
| `plans.status` | `draft` → `applied` / `partially_applied` / `failed` (failed is set by code but missing from the comment) → `undone`. `discard_plan` deletes a draft and its `planned` actions |
| `plans.summary` | cleanup: `{"files", "bytes", "blocked": [[gid, reason]], "groups": [gid…]}`; rename: `{"renames", "sidecars", "mtimes", "blocked": [[file_id, reason]] (first 50), "blocked_count", "pattern"}` |
| `actions.group_key` | `"file-<file_id>"`. A file and its sidecars apply or roll back together |
| `actions.op` | `hold` (move to the holding folder), `move` (sidecar), `rename`, `write_xmp`, `set_mtime` |
| `actions.src`, `.dst` | **absolute** paths (`root` + NFC rel path). `write_xmp` has `src = NULL` |
| `actions.payload` | hold: `{"keeper_id", "verdict", "step", "size", "mtime", "rel_src", "rel_dst"}`; write_xmp: `{"xml": "<?xpacket…"}`; set_mtime: `{"mtime": <float>}`; others `{}`. After apply, `"undo": {}` is merged in, or for set_mtime `{"old_atime", "old_mtime"}` |
| `actions.status` | `planned` → `done` / `failed` (with `error`) → `undone`. `skipped` is declared but never written. Undo errors go to `error` as `"undo: …"` |

Apply re-verifies each hold/rename source (`size` equal and `abs(mtime diff) ≤ 1e-3`, else "file changed since the
scan; rescan first"). For `identical` holds it also byte-compares with the keeper (`filecmp.cmp(shallow=False)`).

### jobs, selection, rename_exclude, settings
- `jobs`: see §5.
- `selection(file_id PK, group_id, step)`: the results-table ticks ("remove this copy"). This state lives on the server, so it
  survives paging and reloads. A keeper is never selected. Cleared per step when that step's groups are rebuilt,
  when a group is decided, and after `/plans/cleanup-selection`.
- `rename_exclude(file_id PK)`: a file left out of renaming. `rename_preview` then reports `status = 'excluded'` and
  the name is kept.
- `settings(key PK, value TEXT)`: see §4.

## 4. Settings keys

`Library.setting(key)` returns the stored value, else `DEFAULT_SETTINGS.get(key, "")`. `set_setting` is
`INSERT OR REPLACE`. All values are TEXT. JSON values are stored with `json.dumps`.

| Key | Default (seeded) | Shape | Written by | Read by |
|---|---|---|---|---|
| `filename_pattern` | `{date:%Y-%m-%d %H-%M-%S} ({location_name}, {device_name}) - {original_filename}.{ext}` | pattern string (tokens: `{date:<strftime>}`, `{location_name}`, `{country}`, `{device_name}`, `{original_filename}`, `{ext}`, `{EXT}`, `{tag:KEY}` / `{tag:KEY\|<strftime>}`) | `POST /settings/pattern` (empty → default); `workbench.commit` from the filename edit rule | `rename_preview`, plans summary, step 3, Workbench |
| `home_timezone` | `Europe/Bratislava` | IANA zone name | `POST /settings` | `extract` → `read_video` |
| `near_threshold` | `0.85` | float as text | `POST /settings` | `step2_near` (minimum edge score for a dup group) |
| `recheck_min_confidence` | `0.5` | float as text | `POST /settings` | `step4_recheck` |
| `rename_columns` | not seeded → `DEFAULT_COLUMNS` | JSON list of column keys in canonical order, e.g. `["old","new","folder","status","date","place","device","confidence"]` | `POST /step/3/columns` | `rename_grid.visible_columns` |
| `workbench_view` | not seeded → `DEFAULT_VIEW` | JSON view (below) | `workbench.save_view` (every `/wb/view` action, source removal) | `load_view` |
| `workbench_undo` | not seeded → `[]` | JSON list, max 100: `[{"view": <view>, "act": "<action>"}]` | `history_push`, `history_step` | `/wb/history` |
| `workbench_redo` | not seeded → `[]` | same shape; reset to `[]` by every new change | same | same |
| `name_presets` | not seeded → `[]` | JSON list `[{"name": "Trip", "pattern": "…"}]`. The built-in `{"name": "Default", "pattern": DEFAULT_PATTERN, "builtin": true}` is prepended when read and never stored | `POST /wb/presets` (`action=save\|delete\|rename`; "Default" is reserved) | `GET /wb/presets` |

`workbench_view` (`DEFAULT_VIEW`, `VIEW_VERSION = 5`; unknown keys are dropped on load):

```jsonc
{
  "v": 5,
  "columns": [["thumb",48],["path",248],["filename",192],["dup_count",64],["dup_conf",144],["checks",240],
              ["key_dto",168],["key_created",168],["date",160],["modify",160],["gps_date",160],["place",112],
              ["device",152],["author",128]],          // [key, width px]; width snapped to 8, range 40..904
  "edits": {},          // target -> rule; target ∈ filename | date | place | device
  "filters": [{"key": "dup_count", "op": "gte", "value": "2", "auto": true}],
  "group": "dups",      // none | dups | capture | folder | day | device | place | author
  "sources_off": [],    // source ids hidden
  "group_width": 232,   // tree column width, minimum 80
  "sort": ["", "asc"],  // [column key, "asc" | "desc"]; "" = default order
  "slim": {},           // key -> true (collapsed to 40 px) | false (kept wide); unset = slim when the column is empty
  "ref_ids": [],        // file ids used as the reference row of their group
  "pinned": [],         // column keys pinned to the left
  "q": "",              // search in path / filename / new filename
  "colors": true        // red/green difference colouring
}
```

Column keys are the core fields (`thumb path filename dup_count dup_conf checks key_dto key_created date digitized
modify file_date gps_date name_date rec_date place rec_place gps country device rec_device make model serial lens
software author copyright owner size dims ext kind`), `exif:<Group>:<Tag>` (any key of `raw_json.tags`) and
`edit:<target>`.

Filter: `{"key", "op", "value", "auto"?}`, where `op` ∈ `contains equals not_equals empty not_empty gte lte diff same`.

Edit rule (one per target):

```jsonc
{"kind": "recommended" | "column" | "fixed" | "shift" | "pattern" | "keep",
 "column": "date",            // column / shift: the source column
 "seconds": -3600,            // shift (date only)
 "value": "Vienna",           // fixed
 "pattern": "…",              // filename (kind "pattern"; "keep" + "" after edit_clear)
 "ids": [12, 15] | null,      // fixed via cell_apply: limited to these files (null = all)
 "from": {"fid": 12, "key": "rec_place"},  // fixed via cell_apply: the cell it was copied from
 "overrides": {"12": "2019-05-14 18:22:03"}}  // per-file typed values (string file id), win over the rule
```

**Not in the DB (per browser):** `localStorage` keys `meta-duplo-theme` (`light`/`dark`), `meta-duplo-borders`,
`meta-duplo-zebra`, `meta-duplo-density`, `meta-duplo-panel-w`; `sessionStorage` key `meta-duplo-history`
(`{undo: [...], redo: [...]}`: selection steps and view-step markers).

## 5. jobs table usage

| Column | Use |
|---|---|
| `name` | `scan`, `recommend`, `re-check` |
| `stage` | free text progress label (see the architecture skill §7), starts at `starting` |
| `status` | `running` → `done` / `failed` / `cancelled`. **The schema comment lists only running/done/failed; `cancelled` is real.** There is no `queued` or `paused` |
| `done`, `total` | progress counters, overwritten by every `Job.update` |
| `message` | last progress message. At the end: the error text + traceback (failed), `Cancelled`, or kept as is (done, e.g. `Scan complete`) |
| `started_at`, `finished_at` | `now()` |

- UI reads only the last row (`SELECT * FROM jobs ORDER BY id DESC LIMIT 1`). Rows are never deleted.
- A cancel takes effect at the next `Job.update`. Work already committed is kept, and stages are idempotent, so the next
  scan continues from there.
- There is no recovery after a crash: a `running` row stays `running`. A rebuild should mark it `failed` on start-up.

## 6. File identity rules

- **Key:** `files.id` (INTEGER rowid) is the identity used everywhere (thumbs, captures, selection, actions).
  The natural key is `UNIQUE(source_id, rel_path)`.
- **NFC in the DB:** `rel_path` and `name` are `unicodedata.normalize("NFC", …)`. New names from `render_name` are NFC too.
- **Original bytes:** **Doc vs code:** `docs/schema.sql` has `rel_path_raw BLOB`, and CLAUDE.md rule 9 says FS operations
  use the original bytes. The POC stores no raw bytes. Every FS path is `Path(sources.root) / files.rel_path`
  (`scan.abs_path`, plans, `/media`). On APFS (normalization-insensitive) this works. A rebuild must add
  `rel_path_raw` (set only when it differs from NFC) and use it for every open, move and stat.
- **Renames and moves by plans** keep the row and its id: `rename` updates `rel_path` and `name`; `hold` sets `removed = 1` and
  `rel_path = payload.rel_dst`; undo restores both. `original_name` never changes.
- **Renames outside the app** look like delete + add: the old row becomes `present = 0` (kept, with its derived rows) and a new
  row with a new id is inserted. Nothing links the two, except content-keyed pair decisions (`F.key`).
- **Content identity** for user decisions: `F.key = hashes.content or hashes.full or f"file:{id}"`.
- **Duplicate-set identity:** `groups.signature` (sorted ids). **Capture identity:** `capture_id` = smallest member id.
  This can shift when grouping changes, and proposal statuses are re-attached by `(capture_id, field)`, so they can
  move to a different capture. Keep that in mind (or key by a stable capture signature) in a rebuild.

## 7. Invalidation of derived rows

| Trigger | What happens |
|---|---|
| Inventory sees a different `size` or `abs(mtime - stored) > 1e-3` | `UPDATE files SET size, mtime, inode, present = 1, error = NULL`; `DELETE FROM meta/hashes/fingerprints WHERE file_id = ?`. The next stages in the same scan re-extract, re-hash and re-fingerprint (the thumbnail file is overwritten) |
| Inventory does not see a path | `present = 0` unless `removed = 1`. Derived rows are kept. Files with `present = 0` drop out of `load_files`, the Workbench and new groups |
| Inventory sees a path again | `present = 1` |
| `RAW_VERSION` bumped | `extract` re-reads every `meta` row whose `raw_json` lacks `"_v": <RAW_VERSION>` |
| Every scan | `hash_stage` upserts every eligible row (values are recomputed from size groups, reusing stored partial/full). `step1_exact`/`step2_near` delete and rebuild their groups (members cascade), carry `status` over by signature, and clear `selection` for the step |
| Harmonize / re-check / Workbench rescan | `build_captures` deletes and rebuilds `captures`; `build_proposals` deletes and rebuilds `proposals`, keeping user statuses |
| Re-check | `step4_recheck` rebuilds step-4 groups. They are **not** refreshed by a plain scan, so they can go stale |
| Plan apply / undo | `_sync_db` updates `files` (hold: `removed`, `rel_path`; rename: `rel_path`, `name`; set_mtime: `mtime` from a fresh `stat`, so the next inventory does not treat the file as changed). Cleanup apply marks `open` groups `resolved` when none of their non-keepers is unremoved; undo re-opens the plan's `summary.groups` that are `resolved` |
| Keeper change | `group_members.role` and `groups.keeper_file_id/reclaimable` are updated, and the keeper's selection row is deleted |
| `remove_source(lib, sid)` | Refused (returns False) if any `actions.file_id` belongs to the source. Otherwise, in one `tx`, deletes `selection`, `rename_exclude`, `captures`, `group_members`, `meta`, `hashes`, `fingerprints`, then `files`, then the `sources` row. **Not cleaned:** `groups` rows (now memberless until the next rebuild), `proposals`, `decisions`, `thumbs/<id>.jpg` files. The web routes rescan right after |

Not invalidated (gaps to fix in a rebuild): `hashes.cache_key` is never compared; `fingerprints`/`thumbs` are not
refreshed when only the decode logic changes (there is no stage version); `files` rows for missing files are never purged.

## 8. Foreign keys and delete order

`foreign_keys=ON` is enforced. Only `group_members.group_id` has `ON DELETE CASCADE`. Delete children before
`files`: `selection`, `rename_exclude`, `captures`, `group_members`, `meta`, `hashes`, `fingerprints`. Then delete the
source. `actions.file_id`, `groups.keeper_file_id`, `selection.group_id`, `captures.capture_id` and
`proposals.capture_id` are **not** foreign keys.

## 9. docs/schema.sql versus the implemented schema

| Target (`docs/schema.sql`) | POC equivalent |
|---|---|
| `sources(volume_uuid, volume_name, fs_type, root_rel)` | `sources(root, role)`; no volume identity |
| `files(rel_path_raw, dir_id, fs_mtime, fs_btime, seen_run, missing, corrupt)` | `files(mtime REAL, present, removed, error, original_name)` |
| `folders`, `sidecars` | none (sidecars are found by file name at plan time) |
| `meta_raw` (zstd ExifTool/ffprobe blobs) | `meta.raw_json` (plain JSON) + hot columns in `meta` |
| `hashes(head_tail, blake3, data_hash, stage_version)` | `hashes(partial, full, content, cache_key)` |
| `fingerprints` (phash INTEGER, many identity fields) | `fingerprints(dhash, phash)` as hex TEXT |
| `clusters`, `cluster_members`, `sibling_*` | `groups` + `group_members` (per step); `captures` |
| `candidates`, `context_evidence`, `tracks`, `resolution`, `confidence`, `cluster_band`, `calibration`, `device_units` | `proposals` (one recommended value per capture and field) |
| `overrides` | `proposals.status = 'edited'` + `decisions` |
| `plans(mode, layout, approved_at, stats)`, `actions(before, after, done_at)` | `plans(kind, summary, undone_at)`, `actions(src, dst, payload)`; no `approved` state: a draft is applied directly |
| `runs`, `file_errors` | none; `files.error` |
| `jobs(scope, progress_done, progress_total, created_at, error)` | `jobs(name, done, total, message)` |
| none | `decisions`, `selection`, `rename_exclude` (POC-only) |

## Rebuild checklist

1. Write migration `0001` with exactly the SQL in §2, and an `apply_migrations(con)` driven by `PRAGMA user_version`.
2. Implement `Library(path)`: create the folder plus `thumbs/` and `undo/`, apply migrations, seed the 4 `DEFAULT_SETTINGS`
   with `INSERT OR IGNORE`.
3. Implement `connect()` with the 4 PRAGMAs and autocommit, `tx()` with `BEGIN IMMEDIATE`, and `now()` in UTC `…Z`.
4. Implement `setting()` falling back to `DEFAULT_SETTINGS`, and `set_setting()` as `INSERT OR REPLACE`.
5. Implement inventory upsert and invalidation exactly as in §7 (size or mtime > 1e-3 → delete meta/hashes/fingerprints).
6. Implement `RAW_VERSION` tagging in `raw_json` and the re-read query.
7. Implement `_save_groups`: replace per step, carry status by signature, clear selection for the step.
8. Implement `build_captures` (union-find, min id, hold payload keeper) and `build_proposals` (keep user statuses,
   auto-accept rule).
9. Implement plan rows and payloads as in §3, and `_sync_db` for apply and undo.
10. Implement `remove_source` with the refusal rule and the delete order of §8.
11. Implement the `workbench_view` load, upgrade and save (`VIEW_VERSION = 5`), the undo/redo stacks (max 100), and `name_presets`.
12. Add `rel_path_raw` (a new migration) and use raw bytes for FS operations (closes the rule-9 gap).

## Verify

```bash
uv run pytest -q          # 22 passed; test_workflow covers inventory, groups, captures, proposals, plans, undo
uv run python - <<'EOF'
import tempfile, sqlite3
from meta_duplo.db import Library
lib = Library(tempfile.mkdtemp())
con = sqlite3.connect(lib.db_path)
print(con.execute("PRAGMA journal_mode").fetchone()[0])                       # wal
print(sorted(r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")))
# ['actions','captures','decisions','files','fingerprints','group_members','groups','hashes','jobs','meta',
#  'plans','proposals','rename_exclude','selection','settings','sources']
print(sorted(r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")))
# ['files_size','hashes_content','hashes_full']
print(dict(con.execute("SELECT key, value FROM settings")))
# filename_pattern, home_timezone=Europe/Bratislava, near_threshold=0.85, recheck_min_confidence=0.5
EOF
```

After `meta-duplo demo` + `scan` in a temp library (ffmpeg present): `files` has 18 `image` + 3 `video` rows;
`groups` has step 1 `identical` × 5 and step 2 `same_photo` × 2, `same_pixels` × 1, `same_video` × 1, `similar_shot` × 1;
every `meta.raw_json` contains `"_v": 3`; the last `jobs` row is `('scan', 'done', 'Scan complete')`. Touch one file
(`touch -m`), scan again, and only that file is re-extracted and re-fingerprinted (its `meta` and `fingerprints` rows
were deleted by inventory; `hashes` rows are upserted for every file on every scan). After applying and
undoing a cleanup plan, `files.removed` is back to 0 and `rel_path` is back to the original.
