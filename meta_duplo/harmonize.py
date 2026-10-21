"""Step 3: recommend one capture time, place and device per capture, and render new file names.

Evidence is pooled across every copy of a capture, including copies removed in steps 1–2.
Copies are not independent: agreement only counts between different origins
(camera clock, file name, file system), never between two copies of the same field.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import PurePosixPath

from . import geo
from .db import Library, tx
from .match import F, build_captures, keeper_key, load_files
from .metadata import TIME_FMT, device_name, time_from_filename

FIELDS = ("capture_time", "location_name", "device_name")
FIELD_LABELS = {"capture_time": "Date & time", "location_name": "Place", "device_name": "Device"}


# ───────────────────────── recommendations ─────────────────────────

def _time_candidates(f: F) -> list[dict]:
    out = []
    if f.dto:
        out.append({"t": f.dto, "tier": 1, "origin": "camera_clock" if f.kind == "image" else "container",
                    "label": f"metadata of {f.name}"})
    for name in dict.fromkeys([f.original_name, f.name]):
        t, precision = time_from_filename(name)
        if t:
            out.append({"t": t.strftime(TIME_FMT), "tier": 2 if precision == "second" else 3,
                        "origin": "filename", "label": f"file name {name}" + (" (date only)" if precision == "day" else "")})
    if f.mtime:
        out.append({"t": datetime.fromtimestamp(f.mtime).strftime(TIME_FMT), "tier": 4, "origin": "filesystem",
                    "label": f"file date of {f.name}"})
    return out


def _secs(a: str, b: str) -> float:
    return abs((datetime.strptime(a, TIME_FMT) - datetime.strptime(b, TIME_FMT)).total_seconds())


def recommend_time(members: list[F]) -> dict:
    cands = [c for f in members for c in _time_candidates(f)]
    if not cands:
        return {"value": "", "confidence": 0.0, "source": "", "explanation": "No date found in any copy."}
    best_tier = min(c["tier"] for c in cands)
    top = sorted((c for c in cands if c["tier"] == best_tier), key=lambda c: c["t"])
    pick = top[0]  # earliest plausible value: copies and edits only add later timestamps
    spread = _secs(top[0]["t"], top[-1]["t"])
    agree = {c["origin"] for c in cands if c["tier"] <= 2 and c["origin"] != pick["origin"] and _secs(c["t"], pick["t"]) <= 2}
    conf = {1: 0.93, 2: 0.75, 3: 0.5, 4: 0.25}[best_tier]
    notes = [f"From {pick['label']}."]
    if agree:
        conf = min(0.98, conf + 0.05)
        notes.append("Confirmed by " + ", ".join(sorted(agree)).replace("_", " ") + ".")
    if best_tier <= 2 and spread > 2:
        conf = min(conf, 0.7)
        notes.append(f"Copies disagree by {int(spread)} s; the earliest is used.")
    if best_tier == 3:
        notes.append("Only the day is known; time set to 00:00:00.")
    if best_tier == 4:
        notes.append("Only the file system date is known; it often shows the copy date, not the capture.")
    return {"value": pick["t"], "confidence": round(conf, 2), "source": pick["label"], "explanation": " ".join(notes)}


def recommend_place(members: list[F], keeper: F) -> dict:
    with_gps = [f for f in members if f.gps_lat is not None]
    if not with_gps:
        return {"value": "", "confidence": 0.0, "source": "", "extra": {},
                "explanation": "No GPS in any copy. Type a place to use it in the file name."}
    src = keeper if keeper.gps_lat is not None else max(with_gps, key=keeper_key)
    p = geo.place(src.gps_lat, src.gps_lon)
    name = p.get("city", "")
    extra = {"lat": src.gps_lat, "lon": src.gps_lon, "city": name, "country": p.get("country", "")}
    if not name:
        return {"value": "", "confidence": 0.3, "source": f"GPS of {src.name}", "extra": extra,
                "explanation": "GPS found, but offline place names are not installed (`uv sync --extra geo`)."}
    note = f"Nearest town to the GPS position in {src.name} ({src.gps_lat:.4f}, {src.gps_lon:.4f})."
    if src is not keeper:
        note += " The kept copy has no GPS; it comes from another copy."
    return {"value": name, "confidence": 0.9 if src is keeper else 0.85, "source": f"GPS of {src.name}",
            "extra": extra, "explanation": note}


def recommend_device(members: list[F], keeper: F) -> dict:
    named = [(f, f.device) for f in members if f.device]
    if not named:
        hint = " It looks like a messenger copy." if any(f.reencoded() for f in members) else ""
        return {"value": "", "confidence": 0.0, "source": "",
                "explanation": "No camera make or model in any copy." + hint}
    if keeper.device:
        src, name = keeper, keeper.device
    else:
        name, _ = Counter(n for f, n in named if not f.reencoded()).most_common(1)[0] if any(
            not f.reencoded() for f, _ in named) else Counter(n for _, n in named).most_common(1)[0]
        src = next(f for f, n in named if n == name)
    distinct = {n for _, n in named}
    conf = 0.9 if len(distinct) == 1 else 0.6
    note = f"Make and model in {src.name}."
    if len(distinct) > 1:
        note += " Copies disagree: " + ", ".join(sorted(distinct)) + "."
    return {"value": name, "confidence": conf, "source": f"metadata of {src.name}", "explanation": note}


def _current(keeper: F) -> dict:
    p = geo.place(keeper.gps_lat, keeper.gps_lon) if keeper.gps_lat is not None else {}
    return {"capture_time": keeper.dto or "", "location_name": p.get("city", ""), "device_name": keeper.device}


def build_proposals(lib: Library, job=None) -> int:
    """(Re)compute recommendations. Accepted, edited and rejected rows keep the user's choice."""
    build_captures(lib)
    with lib.connect() as con:
        files = {f.id: f for f in load_files(con, include_removed=True)}
        caps: dict[int, list[F]] = defaultdict(list)
        for r in con.execute("SELECT file_id, capture_id FROM captures"):
            if r["file_id"] in files:
                caps[r["capture_id"]].append(files[r["file_id"]])
        old = {(r["capture_id"], r["field"]): dict(r) for r in con.execute("SELECT * FROM proposals")}
        rows = []
        for i, (cid, members) in enumerate(caps.items()):
            if job and i % 100 == 0:
                job.update("step 3: recommendations", i, len(caps))
            present = [f for f in members if not f.removed] or members
            keeper = max(present, key=keeper_key)
            cur = _current(keeper)
            recs = {
                "capture_time": recommend_time(members),
                "location_name": recommend_place(members, keeper),
                "device_name": recommend_device(members, keeper),
            }
            for field, rec in recs.items():
                prev = old.get((cid, field))
                status, value = "suggested", rec["value"]
                if prev and prev["status"] in ("accepted", "edited", "rejected"):
                    status = prev["status"]
                    if status == "edited":
                        value = prev["value"]
                if status == "suggested" and value == cur[field] and (rec["confidence"] >= 0.9 or not value):
                    status = "accepted"  # nothing to change, and nothing to doubt
                rows.append((cid, field, cur[field], value, json.dumps(rec.get("extra") or {}), rec["source"],
                             rec["confidence"], status, rec["explanation"]))
        with tx(con):
            con.execute("DELETE FROM proposals")
            con.executemany(
                "INSERT INTO proposals(capture_id, field, current, value, extra, source, confidence, status, explanation) "
                "VALUES (?,?,?,?,?,?,?,?,?)", rows)
    if job:
        job.update("step 3: recommendations", len(caps), len(caps), f"{len(caps)} captures")
    return len(caps)


def effective_values(con) -> dict[int, dict]:
    """The values used for renaming and step 4: accepted/edited recommendations, else the current value."""
    out: dict[int, dict] = defaultdict(dict)
    for r in con.execute("SELECT * FROM proposals"):
        use = r["value"] if r["status"] in ("accepted", "edited") else r["current"]
        out[r["capture_id"]][r["field"]] = use or ""
        if r["field"] == "location_name":
            extra = json.loads(r["extra"] or "{}")
            if r["status"] in ("accepted", "edited") and extra.get("lat") is not None:
                out[r["capture_id"]].update(lat=extra["lat"], lon=extra["lon"], country=extra.get("country", ""))
    return out


def set_status(lib: Library, capture_id: int, field: str, status: str, value: str | None = None) -> None:
    with lib.connect() as con:
        if value is not None:
            con.execute("UPDATE proposals SET status = ?, value = ? WHERE capture_id = ? AND field = ?",
                        (status, value, capture_id, field))
        else:
            con.execute("UPDATE proposals SET status = ? WHERE capture_id = ? AND field = ?", (status, capture_id, field))


def accept_above(lib: Library, min_conf: float, field: str | None = None) -> int:
    with lib.connect() as con:
        sql = "UPDATE proposals SET status = 'accepted' WHERE status = 'suggested' AND confidence >= ? AND value != ''"
        args: list = [min_conf]
        if field:
            sql += " AND field = ?"
            args.append(field)
        return con.execute(sql, args).rowcount


# ───────────────────────── file names ─────────────────────────

TOKEN = re.compile(r"\{(\w+)(?::([^}]*))?\}")
ILLEGAL = re.compile(r'[/\\:*?"<>|\x00-\x1f]')
DEFAULT_PATTERN = "{date:%Y-%m-%d %H-%M-%S} ({location_name}, {device_name}) - {original_filename}.{ext}"
TOKENS_HELP = {
    "{date:%Y-%m-%d %H-%M-%S}": "capture date and time (strftime codes)",
    "{location_name}": "place (town)",
    "{country}": "country code",
    "{device_name}": "camera or phone",
    "{original_filename}": "first name the file was seen with, without extension",
    "{ext} / {EXT}": "extension, lower / upper case",
}


# The naming-pattern dialog's parameter list: (group, label, token). Only what render_name knows.
NAME_TOKENS = [
    ("Date", "Date and time", "{date:%Y-%m-%d %H-%M-%S}"), ("Date", "Date", "{date:%Y-%m-%d}"),
    ("Date", "Time", "{date:%H-%M-%S}"), ("Date", "Year", "{date:%Y}"), ("Date", "Month", "{date:%m}"),
    ("Date", "Day", "{date:%d}"), ("Date", "Hour", "{date:%H}"), ("Date", "Min", "{date:%M}"), ("Date", "Sec", "{date:%S}"),
    ("Place", "Place", "{location_name}"), ("Place", "Country", "{country}"),
    ("Device", "Device", "{device_name}"),
    ("File", "Original filename", "{original_filename}"), ("File", "Extension", "{ext}"), ("File", "EXTENSION", "{EXT}"),
]


def render_name(pattern: str, values: dict, original_name: str, ext: str) -> str | None:
    """Render a file name; None when the pattern needs a date the capture does not have."""
    t = values.get("capture_time") or ""
    stem = PurePosixPath(original_name).stem

    def sub(m: re.Match) -> str:
        key, fmt = m.group(1), m.group(2)
        if key == "date":
            if not t:
                raise LookupError("no date")
            return datetime.strptime(t, TIME_FMT).strftime(fmt or "%Y-%m-%d %H-%M-%S")
        return {
            "location_name": values.get("location_name", ""),
            "country": values.get("country", ""),
            "device_name": values.get("device_name", ""),
            "original_filename": stem,
            "ext": ext.lower(),
            "EXT": ext.upper(),
        }.get(key, m.group(0))

    try:
        name = TOKEN.sub(sub, pattern)
    except LookupError:
        return None
    # tidy the separators left by empty tokens: "( , Apple)" → "(Apple)", "()" → ""
    name = re.sub(r"\(\s*,\s*", "(", name)
    name = re.sub(r",\s*\)", ")", name)
    name = re.sub(r"\(\s*\)", "", name)
    name = re.sub(r"\s{2,}", " ", name)
    name = re.sub(r"\s+\.", ".", name).strip(" -_")
    name = ILLEGAL.sub("-", name)
    stem_part, dot, ext_part = name.rpartition(".")
    if dot and len(stem_part) > 200:
        name = stem_part[:200].rstrip() + "." + ext_part
    return unicodedata.normalize("NFC", name)


def rename_preview(lib: Library, pattern: str | None = None, limit: int | None = None) -> list[dict]:
    """New names for every present file, collision-free by construction."""
    pattern = pattern or lib.setting("filename_pattern")
    with lib.connect() as con:
        values = effective_values(con)
        rows = con.execute(
            "SELECT f.id, f.source_id, f.rel_path, f.name, f.original_name, f.ext, c.capture_id, "
            "(x.file_id IS NOT NULL) AS excluded "
            "FROM files f JOIN captures c ON c.file_id = f.id LEFT JOIN rename_exclude x ON x.file_id = f.id "
            "WHERE f.present = 1 AND f.removed = 0 ORDER BY f.rel_path"
        ).fetchall()
    taken: dict[tuple, set[str]] = defaultdict(set)
    for r in rows:
        parent = str(PurePosixPath(r["rel_path"]).parent)
        taken[(r["source_id"], parent)].add(r["name"].lower())
    out = []
    for r in rows:
        parent = str(PurePosixPath(r["rel_path"]).parent)
        new = render_name(pattern, values.get(r["capture_id"], {}), r["original_name"], r["ext"] or "")
        status = "ok"
        if r["excluded"]:
            new, status = r["name"], "excluded"
        elif new is None:
            new, status = r["name"], "no_date"
        elif new != r["name"]:
            used = taken[(r["source_id"], parent)]
            base, dot, ext = new.rpartition(".")
            n, candidate = 2, new
            while candidate.lower() in used:
                candidate = f"{base} ({n}).{ext}" if dot else f"{new} ({n})"
                n += 1
            if candidate != new:
                status = "renumbered"
            new = candidate
            used.discard(r["name"].lower())
            used.add(new.lower())
        else:
            status = "unchanged"
        out.append({"file_id": r["id"], "capture_id": r["capture_id"], "rel_path": r["rel_path"],
                    "old": r["name"], "new": new, "status": status})
        if limit and len(out) >= limit:
            break
    return out
