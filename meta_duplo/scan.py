"""Scan stages: inventory → extract → hash → fingerprint (+ thumbnails).

Every stage is idempotent: it only processes files that lack its output, and inventory
clears a file's derived rows when its size or mtime changes.
"""
from __future__ import annotations

import json
import os
import threading
import traceback
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Callable

import blake3
import imagehash
from PIL import Image, ImageOps

from . import metadata
from .db import Library, now, tx

HOLD_DIR = "_duplicates"
JUNK = {".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r"}
PARTIAL_BYTES = 1024 * 1024  # first + last 1 MB


# ───────────────────────── job runner ─────────────────────────

class Job:
    def __init__(self, lib: Library, job_id: int):
        self.lib, self.id = lib, job_id

    def update(self, stage: str, done: int = 0, total: int = 0, message: str = "") -> None:
        with self.lib.connect() as con:
            con.execute(
                "UPDATE jobs SET stage = ?, done = ?, total = ?, message = ? WHERE id = ?",
                (stage, done, total, message, self.id),
            )


class Runner:
    """One background job at a time (single writer, like the spec's runner)."""

    def __init__(self, lib: Library):
        self.lib = lib
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, name: str, fn: Callable[[Job], None]) -> int | None:
        with self._lock:
            if self.busy():
                return None
            with self.lib.connect() as con:
                job_id = con.execute(
                    "INSERT INTO jobs(name, stage, status, started_at) VALUES (?, 'starting', 'running', ?)",
                    (name, now()),
                ).lastrowid
            job = Job(self.lib, job_id)

            def run() -> None:
                status, msg = "done", ""
                try:
                    fn(job)
                except Exception as exc:  # recorded for the UI; per-file errors never get here
                    status, msg = "failed", f"{exc}\n{traceback.format_exc(limit=3)}"
                with self.lib.connect() as con:
                    con.execute(
                        "UPDATE jobs SET status = ?, finished_at = ?, message = CASE WHEN ? = '' THEN message ELSE ? END WHERE id = ?",
                        (status, now(), msg, msg, job_id),
                    )

            self._thread = threading.Thread(target=run, name=f"job-{job_id}", daemon=True)
            self._thread.start()
            return job_id

    def run_sync(self, name: str, fn: Callable[[Job], None]) -> None:
        """Run in the calling thread (CLI and tests)."""
        job_id = self.start(name, fn)
        if job_id is not None and self._thread:
            self._thread.join()


# ───────────────────────── stage 1: inventory ─────────────────────────

def inventory(lib: Library, job: Job) -> None:
    with lib.connect() as con:
        sources = con.execute("SELECT * FROM sources").fetchall()
    for src in sources:
        root = Path(src["root"])
        if not root.is_dir():
            continue
        job.update("inventory", message=str(root))
        seen: list[tuple] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                d for d in dirnames
                if not d.startswith(".") and d != HOLD_DIR and not d.endswith(".photoslibrary")
            )
            for fn in sorted(filenames):
                if fn in JUNK or fn.startswith("._") or fn.startswith("."):
                    continue
                p = Path(dirpath) / fn
                try:
                    st = p.lstat()
                except OSError:
                    continue
                if p.is_symlink() or not p.is_file():
                    continue
                if getattr(st, "st_flags", 0) & 0x40000000:  # SF_DATALESS: cloud placeholder, reading would download it
                    continue
                rel = unicodedata.normalize("NFC", str(p.relative_to(root)))
                ext = p.suffix[1:].lower()
                seen.append((rel, unicodedata.normalize("NFC", fn), ext, st.st_size, st.st_mtime, st.st_ino))
        with lib.connect() as con, tx(con):
            existing = {
                r["rel_path"]: r
                for r in con.execute("SELECT id, rel_path, size, mtime, removed FROM files WHERE source_id = ?", (src["id"],))
            }
            seen_paths = set()
            for rel, name, ext, size, mtime, ino in seen:
                seen_paths.add(rel)
                row = existing.get(rel)
                if row is None:
                    con.execute(
                        "INSERT INTO files(source_id, rel_path, name, ext, kind, size, mtime, inode, original_name) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (src["id"], rel, name, ext, metadata.kind_of(ext), size, mtime, ino, name),
                    )
                elif row["size"] != size or abs((row["mtime"] or 0) - mtime) > 1e-3:
                    fid = row["id"]
                    con.execute(
                        "UPDATE files SET size = ?, mtime = ?, inode = ?, present = 1, error = NULL WHERE id = ?",
                        (size, mtime, ino, fid),
                    )
                    for table in ("meta", "hashes", "fingerprints"):
                        con.execute(f"DELETE FROM {table} WHERE file_id = ?", (fid,))
                else:
                    con.execute("UPDATE files SET present = 1 WHERE id = ?", (row["id"],))
            for rel, row in existing.items():
                if rel not in seen_paths and not row["removed"]:
                    con.execute("UPDATE files SET present = 0 WHERE id = ?", (row["id"],))
        job.update("inventory", len(seen), len(seen), f"{len(seen)} files in {root}")


# ───────────────────────── stage 2: extract ─────────────────────────

def abs_path(con, file_id: int) -> Path:
    r = con.execute(
        "SELECT s.root, f.rel_path FROM files f JOIN sources s ON s.id = f.source_id WHERE f.id = ?", (file_id,)
    ).fetchone()
    return Path(r["root"]) / r["rel_path"]


def extract(lib: Library, job: Job) -> None:
    home_tz = lib.setting("home_timezone")
    with lib.connect() as con:
        todo = con.execute(
            "SELECT f.id, f.kind, s.root, f.rel_path FROM files f JOIN sources s ON s.id = f.source_id "
            "LEFT JOIN meta m ON m.file_id = f.id "
            "WHERE f.present = 1 AND f.kind IN ('image', 'video') "
            "AND (m.file_id IS NULL OR instr(COALESCE(m.raw_json, ''), ?) = 0)",
            (f'"_v": {metadata.RAW_VERSION}',),  # older libraries re-read once to get every tag
        ).fetchall()
    for i, r in enumerate(todo):
        if i % 25 == 0:
            job.update("extract", i, len(todo))
        path = Path(r["root"]) / r["rel_path"]
        try:
            m = metadata.read(path, r["kind"], home_tz)
            err = None
        except Exception as exc:  # per-file errors never abort a run
            m, err = metadata.Meta(), f"extract: {exc}"
        with lib.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO meta(file_id, width, height, duration, make, model, software, dto, dto_source, "
                "modify_date, gps_lat, gps_lon, unique_id, orientation, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["id"], m.width, m.height, m.duration, m.make, m.model, m.software, m.dto, m.dto_source,
                 m.modify_date, m.gps_lat, m.gps_lon, m.unique_id, m.orientation, json.dumps(m.raw, default=str)),
            )
            if err:
                con.execute("UPDATE files SET error = ? WHERE id = ?", (err, r["id"]))
    job.update("extract", len(todo), len(todo))


# ───────────────────────── stage 3: hash ─────────────────────────

def _partial(path: Path, size: int) -> str:
    h = blake3.blake3()
    with open(path, "rb") as fh:
        h.update(fh.read(PARTIAL_BYTES))
        if size > PARTIAL_BYTES:
            fh.seek(max(PARTIAL_BYTES, size - PARTIAL_BYTES))
            h.update(fh.read(PARTIAL_BYTES))
    return h.hexdigest()


def _full(path: Path) -> str:
    h = blake3.blake3()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_stage(lib: Library, job: Job) -> None:
    """Size → partial → full cascade: a file with a unique size is never read."""
    with lib.connect() as con:
        rows = con.execute(
            "SELECT f.id, f.size, f.inode, f.mtime, s.root, f.rel_path, h.partial, h.full FROM files f "
            "JOIN sources s ON s.id = f.source_id LEFT JOIN hashes h ON h.file_id = f.id "
            "WHERE f.present = 1 AND f.removed = 0 AND f.kind IN ('image', 'video') AND f.size > 0"
        ).fetchall()
    by_size: dict[int, list] = defaultdict(list)
    for r in rows:
        by_size[r["size"]].append(r)
    cands = [r for g in by_size.values() if len(g) > 1 for r in g]
    partial: dict[int, str] = {}
    for i, r in enumerate(cands):
        if i % 50 == 0:
            job.update("hash: partial", i, len(cands))
        try:
            partial[r["id"]] = r["partial"] or _partial(Path(r["root"]) / r["rel_path"], r["size"])
        except OSError:
            continue
    by_partial: dict[tuple, list] = defaultdict(list)
    for r in cands:
        if r["id"] in partial:
            by_partial[(r["size"], partial[r["id"]])].append(r)
    full_cands = [r for g in by_partial.values() if len(g) > 1 for r in g]
    full: dict[int, str] = {}
    for i, r in enumerate(full_cands):
        if i % 10 == 0:
            job.update("hash: full", i, len(full_cands))
        try:
            full[r["id"]] = r["full"] or _full(Path(r["root"]) / r["rel_path"])
        except OSError:
            continue
    with lib.connect() as con, tx(con):
        for r in rows:
            key = f"{r['inode']}:{r['size']}:{r['mtime']}"
            con.execute(
                "INSERT INTO hashes(file_id, partial, full, cache_key) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(file_id) DO UPDATE SET partial = excluded.partial, full = excluded.full, cache_key = excluded.cache_key",
                (r["id"], partial.get(r["id"]), full.get(r["id"]), key),
            )
    job.update("hash", len(rows), len(rows))


# ───────────────────────── stage 4: fingerprint + thumbnails ─────────────────────────

def fingerprint(lib: Library, job: Job) -> None:
    """One decode per image: pixel content hash, dHash, pHash and the thumbnail."""
    with lib.connect() as con:
        todo = con.execute(
            "SELECT f.id, s.root, f.rel_path FROM files f JOIN sources s ON s.id = f.source_id "
            "LEFT JOIN fingerprints p ON p.file_id = f.id "
            "WHERE f.present = 1 AND f.removed = 0 AND f.kind = 'image' AND p.file_id IS NULL"
        ).fetchall()
    thumbs = lib.path / "thumbs"
    for i, r in enumerate(todo):
        if i % 10 == 0:
            job.update("fingerprint", i, len(todo))
        path = Path(r["root"]) / r["rel_path"]
        try:
            with Image.open(path) as im:
                img = ImageOps.exif_transpose(im).convert("RGB")  # orientation first, or rotated copies never match
            content = blake3.blake3(f"{img.width}x{img.height}".encode() + img.tobytes()).hexdigest()
            dh, ph = str(imagehash.dhash(img)), str(imagehash.phash(img))
            if int(dh, 16) in (0, 2**64 - 1):  # solid images give degenerate hashes: never match on them
                dh = ph = None
            img.thumbnail((360, 360))
            img.save(thumbs / f"{r['id']}.jpg", "JPEG", quality=82)
        except Exception as exc:
            with lib.connect() as con:
                con.execute("UPDATE files SET error = ? WHERE id = ?", (f"decode: {exc}", r["id"]))
                con.execute("INSERT OR REPLACE INTO fingerprints(file_id, dhash, phash) VALUES (?, NULL, NULL)", (r["id"],))
            continue
        with lib.connect() as con:
            con.execute("INSERT OR REPLACE INTO fingerprints(file_id, dhash, phash) VALUES (?, ?, ?)", (r["id"], dh, ph))
            con.execute(
                "INSERT INTO hashes(file_id, content) VALUES (?, ?) ON CONFLICT(file_id) DO UPDATE SET content = excluded.content",
                (r["id"], content),
            )
    job.update("fingerprint", len(todo), len(todo))


def scan_all(lib: Library, job: Job) -> None:
    from . import match

    inventory(lib, job)
    extract(lib, job)
    hash_stage(lib, job)
    fingerprint(lib, job)
    match.step1_exact(lib, job)
    match.step2_near(lib, job)
    job.update("done", 1, 1, "Scan complete")
