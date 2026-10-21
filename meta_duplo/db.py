"""SQLite storage: one library = one database file (WAL)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
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
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

DEFAULT_SETTINGS = {
    "filename_pattern": "{date:%Y-%m-%d %H-%M-%S} ({location_name}, {device_name}) - {original_filename}.{ext}",
    "home_timezone": "Europe/Bratislava",
    "near_threshold": "0.85",
    "recheck_min_confidence": "0.5",
}

@contextmanager
def tx(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One write transaction: commit on success, roll back on error."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Library:
    """A library folder: library.sqlite, thumbs/ and undo/ journals."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / "thumbs").mkdir(exist_ok=True)
        (self.path / "undo").mkdir(exist_ok=True)
        self.db_path = self.path / "library.sqlite"
        with self.connect() as con:
            con.executescript(SCHEMA)
            for k, v in DEFAULT_SETTINGS.items():
                con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Autocommit connection; use `with tx(con):` for multi-statement transactions."""
        con = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=30000")
        try:
            yield con
        finally:
            con.close()

    def setting(self, key: str) -> str:
        with self.connect() as con:
            row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else DEFAULT_SETTINGS.get(key, "")

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, value))
