-- medorg initial schema (migration 0001). SQLite, WAL mode.
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ───────────────────────── sources & files ─────────────────────────
CREATE TABLE sources (
  id            INTEGER PRIMARY KEY,
  label         TEXT NOT NULL,
  volume_uuid   TEXT,                 -- survives remounts
  volume_name   TEXT,
  fs_type       TEXT,                 -- apfs|hfs|exfat|msdos|ntfs|smbfs…
  root_rel      TEXT NOT NULL,        -- root path relative to volume
  writable      INTEGER NOT NULL DEFAULT 0,
  added_at      TEXT NOT NULL
);

CREATE TABLE files (
  id            INTEGER PRIMARY KEY,
  source_id     INTEGER NOT NULL REFERENCES sources(id),
  rel_path      TEXT NOT NULL,        -- NFC-normalized, relative to source root
  rel_path_raw  BLOB,                 -- original bytes if different
  dir_id        INTEGER REFERENCES folders(id),
  name          TEXT NOT NULL,
  ext           TEXT,
  kind          TEXT,                 -- image|video|raw|sidecar|other
  size          INTEGER,
  inode         INTEGER,
  fs_mtime      TEXT, fs_btime TEXT,  -- UTC ISO
  seen_run      INTEGER,
  missing       INTEGER NOT NULL DEFAULT 0,
  corrupt       INTEGER NOT NULL DEFAULT 0,
  UNIQUE (source_id, rel_path)
);
CREATE INDEX files_dir  ON files(dir_id);
CREATE INDEX files_size ON files(size);

CREATE TABLE folders (
  id            INTEGER PRIMARY KEY,
  source_id     INTEGER NOT NULL REFERENCES sources(id),
  rel_path      TEXT NOT NULL,
  parent_id     INTEGER REFERENCES folders(id),
  name          TEXT,
  fs_btime      TEXT, fs_mtime TEXT,
  folder_type   TEXT,                 -- dcim|event|capture_import|backup|device_dump|messenger|export|recovered|unknown
  tokens        TEXT,                 -- JSON: parsed date/place/event/person/device tokens
  UNIQUE (source_id, rel_path)
);

CREATE TABLE sidecars (               -- sidecar file → parent media file
  file_id       INTEGER PRIMARY KEY REFERENCES files(id),
  parent_id     INTEGER NOT NULL REFERENCES files(id),
  sidecar_type  TEXT                  -- xmp|thm|aae|srt|lrv|xml|takeout_json|pp3|dop
);

-- ───────────────────────── raw extraction ─────────────────────────
CREATE TABLE meta_raw (
  file_id       INTEGER PRIMARY KEY REFERENCES files(id),
  exiftool_zst  BLOB,                 -- zstd JSON (-a -G1 -s -ee3)
  ffprobe_zst   BLOB,
  extract_pass  INTEGER,              -- 1 = batch, 2 = re-read
  extract_ok    INTEGER,              -- integrity verified
  stage_version INTEGER,
  run_id        INTEGER REFERENCES runs(id)
);

CREATE TABLE hashes (
  file_id       INTEGER PRIMARY KEY REFERENCES files(id),
  head_tail     TEXT,                 -- partial hash (first+last 1 MB)
  blake3        TEXT,                 -- full, only when needed
  data_hash     TEXT,                 -- ImageDataHash / video stream hash
  stage_version INTEGER
);
CREATE INDEX hashes_blake3 ON hashes(blake3);
CREATE INDEX hashes_data   ON hashes(data_hash);

CREATE TABLE fingerprints (
  file_id       INTEGER PRIMARY KEY REFERENCES files(id),
  phash         INTEGER, dhash INTEGER,         -- 64-bit
  qt_hash       TEXT, struct_fp TEXT,
  width         INTEGER, height INTEGER, duration REAL,
  make_raw      TEXT, model_raw TEXT, serial TEXT, software TEXT,
  counter_prefix TEXT, counter_folder INTEGER, counter_num INTEGER, shutter_count INTEGER,
  content_id    TEXT, burst_uuid TEXT, image_unique_id TEXT, original_document_id TEXT,
  is_reencoded  INTEGER, origin_hint TEXT,
  stage_version INTEGER
);

-- ───────────────────────── grouping ─────────────────────────
CREATE TABLE clusters (
  id            INTEGER PRIMARY KEY,
  master_file_id INTEGER REFERENCES files(id),
  level         TEXT,                 -- L1|L2|L3|L4|single
  member_count  INTEGER
);
CREATE TABLE cluster_members (
  cluster_id    INTEGER NOT NULL REFERENCES clusters(id),
  file_id       INTEGER NOT NULL UNIQUE REFERENCES files(id),
  link_level    TEXT, link_score REAL,
  PRIMARY KEY (cluster_id, file_id)
);
CREATE TABLE sibling_groups (
  id            INTEGER PRIMARY KEY,
  kind          TEXT                  -- raw_jpeg|live_photo|burst|bracket|panorama|edit
);
CREATE TABLE sibling_members (
  group_id      INTEGER NOT NULL REFERENCES sibling_groups(id),
  file_id       INTEGER NOT NULL REFERENCES files(id),
  role          TEXT,
  PRIMARY KEY (group_id, file_id)
);

-- ───────────────────────── devices ─────────────────────────
CREATE TABLE device_units (
  id            INTEGER PRIMARY KEY,
  brand TEXT, model TEXT, model_code TEXT, serial TEXT,
  owner_label   TEXT,
  first_seen    TEXT, last_seen TEXT,
  fw_min TEXT, fw_max TEXT,
  notes         TEXT
);

-- ───────────────────────── evidence ─────────────────────────
CREATE TABLE candidates (
  id            INTEGER PRIMARY KEY,
  file_id       INTEGER NOT NULL REFERENCES files(id),
  dim           TEXT NOT NULL,        -- time|place|device|origin
  source        TEXT NOT NULL,        -- e.g. ExifIFD:DateTimeOriginal, fn_pixel, track:gpx:3
  origin        TEXT NOT NULL,        -- independence group: camera_clock|gps|phone_net|server|human|fs_copy|gazetteer|prnu|counter…
  tier          INTEGER, weight REAL, reliability REAL,
  kind          TEXT,                 -- point|bound_lo|bound_hi|interval|disc|unit|model|class
  -- time
  t_utc TEXT, t_local TEXT, tz_offset_min INTEGER, tz_status TEXT, precision_s REAL,
  t_lo TEXT, t_hi TEXT,
  -- place
  lat REAL, lon REAL, alt REAL, radius_m REAL,
  -- device / origin
  unit_id INTEGER REFERENCES device_units(id), brand TEXT, model TEXT, class TEXT,
  valid         INTEGER NOT NULL DEFAULT 1,
  reject_reason TEXT, repair TEXT,
  run_id        INTEGER REFERENCES runs(id)
);
CREATE INDEX cand_file_dim ON candidates(file_id, dim);

CREATE TABLE context_evidence (
  id            INTEGER PRIMARY KEY,
  target        TEXT,                 -- time|place|device|event|origin
  file_id       INTEGER REFERENCES files(id),
  group_id      TEXT,
  kind          TEXT,                 -- bound_lo|bound_hi|interval|order|equal|disc|candidate_set|member_of
  value         TEXT,                 -- JSON
  related_file_id INTEGER REFERENCES files(id),
  heuristic     TEXT, strength TEXT, reliability REAL,
  sources       TEXT, notes TEXT,
  run_id        INTEGER REFERENCES runs(id)
);
CREATE INDEX ctx_file ON context_evidence(file_id);

CREATE TABLE tracks (
  id            INTEGER PRIMARY KEY,
  track_id      TEXT, source TEXT, device TEXT, person TEXT,
  utc           TEXT NOT NULL, lat REAL, lon REAL, alt REAL,
  accuracy_m    REAL, speed REAL, kind TEXT        -- raw|path|visit
);
CREATE INDEX tracks_time ON tracks(utc);

-- ───────────────────────── results ─────────────────────────
CREATE TABLE resolution (
  cluster_id    INTEGER NOT NULL REFERENCES clusters(id),
  dim           TEXT NOT NULL,        -- time|place|device|origin
  value         TEXT,                 -- JSON: chosen value (point/interval/disc/unit/class)
  method        TEXT, winning_source TEXT,
  basis         TEXT,                 -- JSON: contributing candidates/constraints
  conflicts     TEXT,                 -- JSON
  run_id        INTEGER REFERENCES runs(id),
  PRIMARY KEY (cluster_id, dim)
);

CREATE TABLE confidence (
  cluster_id    INTEGER NOT NULL REFERENCES clusters(id),
  dim           TEXT NOT NULL,
  level         TEXT NOT NULL,        -- 1min|1h|day|month|year|100m|1km|10km|region|country|unit|model|brand|class
  p             REAL NOT NULL,
  PRIMARY KEY (cluster_id, dim, level)
);
CREATE TABLE cluster_band (
  cluster_id    INTEGER PRIMARY KEY REFERENCES clusters(id),
  overall       REAL, weakest_dim TEXT, band TEXT
);

CREATE TABLE calibration (
  heuristic TEXT, bucket TEXT, level TEXT,
  n INTEGER, p_hit REAL, cdf TEXT,       -- JSON empirical CDF
  updated_at TEXT,
  PRIMARY KEY (heuristic, bucket, level)
);

-- ───────────────────────── human & actions ─────────────────────────
CREATE TABLE overrides (
  id            INTEGER PRIMARY KEY,
  scope         TEXT,                 -- file|cluster|folder|device_unit|session|place_name
  scope_id      TEXT,
  dim           TEXT,
  value         TEXT,                 -- JSON
  note          TEXT,
  created_at    TEXT, revoked_at TEXT
);

CREATE TABLE plans (
  id            INTEGER PRIMARY KEY,
  name          TEXT, mode TEXT,      -- sidecar|clone_tree|hardlink_tree|rename_in_place|write_in_file
  layout        TEXT,                 -- layout pattern used
  status        TEXT,                 -- draft|approved|applying|applied|rolled_back|failed
  created_at    TEXT, approved_at TEXT, applied_at TEXT,
  stats         TEXT
);
CREATE TABLE actions (
  id            INTEGER PRIMARY KEY,
  plan_id       INTEGER NOT NULL REFERENCES plans(id),
  group_key     TEXT,                 -- sibling/sidecar group: applied atomically
  file_id       INTEGER REFERENCES files(id),
  op            TEXT,                 -- move|clone|hardlink|rename|write_xmp|write_meta|set_times
  before        TEXT, after TEXT,     -- JSON
  status        TEXT, error TEXT, done_at TEXT
);

-- ───────────────────────── ops ─────────────────────────
CREATE TABLE runs (
  id            INTEGER PRIMARY KEY,
  stage         TEXT, stage_version INTEGER, scope TEXT,
  started_at    TEXT, finished_at TEXT, stats TEXT
);
CREATE TABLE jobs (
  id            INTEGER PRIMARY KEY,
  stage         TEXT, scope TEXT, status TEXT,      -- queued|running|paused|done|failed|cancelled
  progress_done INTEGER, progress_total INTEGER,
  created_at TEXT, started_at TEXT, finished_at TEXT, error TEXT
);
CREATE TABLE file_errors (
  file_id INTEGER, stage TEXT, error TEXT, at TEXT
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
