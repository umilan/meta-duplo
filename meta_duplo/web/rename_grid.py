"""Read model for the step-3 rename grid: one row per file, old → new, with metadata columns.

Modelled on Advanced Renamer: every column can be shown or hidden and every header sorts.
Harmonized fields (date, place, device) show the current value and the new one side by side.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath

from .. import harmonize
from ..db import Library


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    default: bool = True
    kind: str = "text"            # text | num | field (harmonized: current → new) | status
    field: str = ""               # proposals.field for kind == "field"


COLUMNS = [
    Column("old", "Filename"),
    Column("new", "New filename"),
    Column("folder", "Path"),
    Column("status", "Status", kind="status"),
    Column("date", "Date", kind="field", field="capture_time"),
    Column("place", "Place", kind="field", field="location_name"),
    Column("device", "Device", kind="field", field="device_name"),
    Column("confidence", "Date confidence", kind="num"),
    Column("date_source", "Date from", default=False),
    Column("dto", "DateTimeOriginal", default=False),
    Column("modify", "DateTime (modified)", default=False),
    Column("file_date", "File date", default=False),
    Column("make", "Make", default=False),
    Column("model", "Model", default=False),
    Column("software", "Software", default=False),
    Column("gps", "GPS", default=False),
    Column("size", "Size", default=False, kind="num"),
    Column("dims", "Dimensions", default=False),
    Column("copies", "Copies", default=False, kind="num"),
]
BY_KEY = {c.key: c for c in COLUMNS}
DEFAULT_COLUMNS = [c.key for c in COLUMNS if c.default]
FILTERS = {
    "all": "All files",
    "pending": "Needs a decision",
    "changes": "Name changes",
    "problems": "Problems",
    "excluded": "Excluded",
}
STATUS_LABELS = {
    "ok": "OK", "unchanged": "Unchanged", "no_date": "No date", "renumbered": "Renumbered",
    "excluded": "Excluded", "readonly": "Read-only", "pending": "Needs decision",
}


def visible_columns(lib: Library) -> list[Column]:
    try:
        keys = json.loads(lib.setting("rename_columns") or "null") or DEFAULT_COLUMNS
    except ValueError:
        keys = DEFAULT_COLUMNS
    cols = [BY_KEY[k] for k in keys if k in BY_KEY]
    return cols or [BY_KEY[k] for k in DEFAULT_COLUMNS]


def save_columns(lib: Library, keys: list[str]) -> None:
    keys = [c.key for c in COLUMNS if c.key in keys]  # keep the canonical order
    lib.set_setting("rename_columns", json.dumps(keys or DEFAULT_COLUMNS))


def _gps(lat, lon) -> str:
    return "" if lat is None else f"{lat:.4f}, {lon:.4f}"


def build_rows(lib: Library) -> list[dict]:
    preview = {p["file_id"]: p for p in harmonize.rename_preview(lib)}
    with lib.connect() as con:
        files = con.execute(
            "SELECT f.id, f.name, f.rel_path, f.size, f.mtime, f.kind, s.label AS source, s.writable, s.role, "
            "c.capture_id, m.dto, m.dto_source, m.modify_date, m.make, m.model, m.software, m.gps_lat, m.gps_lon, "
            "m.width, m.height, m.duration "
            "FROM files f JOIN captures c ON c.file_id = f.id JOIN sources s ON s.id = f.source_id "
            "LEFT JOIN meta m ON m.file_id = f.id WHERE f.present = 1 AND f.removed = 0").fetchall()
        props: dict[int, dict[str, dict]] = defaultdict(dict)
        for r in con.execute("SELECT * FROM proposals"):
            props[r["capture_id"]][r["field"]] = dict(r)
        copies = Counter(r["capture_id"] for r in con.execute("SELECT capture_id FROM captures"))
    rows = []
    for f in files:
        p = preview.get(f["id"])
        if not p:
            continue
        cp = props.get(f["capture_id"], {})
        fields = {}
        pending = False
        for col in COLUMNS:
            if col.kind != "field":
                continue
            pr = cp.get(col.field)
            if pr is None:
                fields[col.key] = {"now": "", "new": "", "status": "none", "changed": False}
                continue
            changed = bool(pr["value"]) and pr["value"] != (pr["current"] or "")
            fields[col.key] = {
                "now": pr["current"] or "", "new": pr["value"] or "", "status": pr["status"], "changed": changed,
                "confidence": pr["confidence"], "why": pr["explanation"] or "", "capture": f["capture_id"],
                "field": col.field,
            }
            pending = pending or (pr["status"] == "suggested" and changed)
        status = p["status"]
        if status in ("ok", "renumbered", "unchanged") and (not f["writable"] or f["role"] == "reference") \
                and p["new"] != p["old"]:
            status = "readonly"
        if pending and status not in ("excluded",):
            status = "pending"
        parent = str(PurePosixPath(f["rel_path"]).parent)
        rows.append({
            "id": f["id"], "capture": f["capture_id"], "kind": f["kind"],
            "old": p["old"], "new": p["new"], "renamed": p["new"] != p["old"],
            "folder": f["source"] + ("" if parent == "." else "/" + parent),
            "status": status, "excluded": p["status"] == "excluded",
            "fields": fields,
            "date": fields["date"]["new"] or fields["date"]["now"],
            "place": fields["place"]["new"] or fields["place"]["now"],
            "device": fields["device"]["new"] or fields["device"]["now"],
            "confidence": cp.get("capture_time", {}).get("confidence"),
            "date_source": f["dto_source"] or "",
            "dto": f["dto"] or "",
            "modify": f["modify_date"] or "",
            "file_date": datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d %H:%M:%S") if f["mtime"] else "",
            "make": f["make"] or "", "model": f["model"] or "", "software": f["software"] or "",
            "gps": _gps(f["gps_lat"], f["gps_lon"]),
            "size": f["size"] or 0,
            "dims": f"{f['width']}×{f['height']}" if f["width"] else "",
            "copies": copies.get(f["capture_id"], 1),
        })
    return rows


def query(lib: Library, filt: str = "all", q: str = "", sort: str = "folder", desc: bool = False,
          offset: int = 0, limit: int = 200) -> tuple[list[dict], int, dict]:
    rows = build_rows(lib)
    counts = {
        "all": len(rows),
        "pending": sum(r["status"] == "pending" for r in rows),
        "changes": sum(r["renamed"] for r in rows),
        "problems": sum(r["status"] in ("no_date", "readonly", "renumbered") for r in rows),
        "excluded": sum(r["excluded"] for r in rows),
    }
    keep = {
        "all": lambda r: True,
        "pending": lambda r: r["status"] == "pending",
        "changes": lambda r: r["renamed"],
        "problems": lambda r: r["status"] in ("no_date", "readonly", "renumbered"),
        "excluded": lambda r: r["excluded"],
    }.get(filt, lambda r: True)
    rows = [r for r in rows if keep(r)]
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r["old"].lower() or ql in r["new"].lower() or ql in r["folder"].lower()]
    col = BY_KEY.get(sort, BY_KEY["folder"])

    def key(r):
        v = r.get(col.key)
        if col.key == "status":
            v = STATUS_LABELS.get(r["status"], r["status"])
        if v is None or v == "":
            return (1, "")  # empty values last in either direction
        return (0, v.lower() if isinstance(v, str) else v)

    rows.sort(key=lambda r: (key(r), r["folder"].lower(), r["old"].lower()), reverse=desc)
    if desc:  # keep empty values last when sorting descending too
        rows.sort(key=lambda r: key(r)[0])
    return rows[offset:offset + limit], len(rows), counts
