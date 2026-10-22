"""Workbench (experimental): one table for duplicates and metadata.

One row per file. "Group by" decides the task:
- duplicates → every duplicate set with its kept copy as the reference row;
- photo, folder, day, device, place → fixing metadata across related files;
- none → a flat list.

Cells that differ from the group's reference row are marked (red), equal ones in Path and Filename green.
Columns come from a catalog (core fields + every EXIF tag found), with user widths and order.
An *edit column* (purple) holds a rule for filename, date, place or device; its values feed the
same decisions (proposals) and plans as steps 1–4, so nothing here bypasses the plan engine.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import PurePosixPath

from .. import geo, harmonize
from ..db import Library
from ..metadata import TIME_FMT, device_name, time_from_filename


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    group: str
    kind: str = "text"        # text | date | num | special
    editable: str = ""        # edit target: filename | date | place | device
    width: int = 140


CORE = [
    Field("thumb", "Th.", "File", "special", width=64),
    Field("path", "Path", "File", width=256),
    Field("filename", "Filename", "File", editable="filename", width=192),
    Field("dup_count", "Dup.", "Duplicates", "num", width=72),
    Field("dup_conf", "Dup. confidence", "Duplicates", "num", width=152),
    Field("checks", "Meta checks", "Duplicates", "special", width=240),
    Field("key_dto", "DateTimeOriginal (EXIF)", "Dates", "date", width=168),
    Field("key_created", "DateCreated (IPTC/XMP)", "Dates", "date", width=168),
    Field("name_date", "Date from file name", "Dates", "date", width=160),  # third: right after the two key dates
    Field("rec_date", "Recommended date", "Dates", "date", width=160),  # fourth: the other derived date
    Field("date", "Date taken", "Dates", "date", editable="date", width=160),
    Field("digitized", "Date digitized", "Dates", "date", width=160),
    Field("modify", "Date modified (EXIF)", "Dates", "date", width=160),
    Field("file_date", "File date", "Dates", "date", width=160),
    Field("gps_date", "GPS date (UTC)", "Dates", "date", width=160),
    Field("place", "Place", "Location", editable="place", width=120),
    Field("rec_place", "Recommended place", "Location", width=128),
    Field("gps", "GPS", "Location", width=152),
    Field("country", "Country", "Location", width=80),
    Field("device", "Device", "Device", editable="device", width=152),
    Field("rec_device", "Recommended device", "Device", width=152),
    Field("make", "Make", "Device", width=112),
    Field("model", "Model", "Device", width=144),
    Field("serial", "Serial number", "Device", width=120),
    Field("lens", "Lens", "Device", width=144),
    Field("software", "Software", "Device", width=128),
    Field("author", "Author", "Author", editable="", width=128),
    Field("copyright", "Copyright", "Author", width=160),
    Field("owner", "Camera owner", "Author", width=128),
    Field("size", "Size", "File", "num", width=88),
    Field("dims", "Dimensions", "File", width=96),
    Field("ext", "Type", "File", width=64),
    Field("kind", "Kind", "File", width=72),
]
CORE_BY_KEY = {f.key: f for f in CORE}
EDIT_LABELS = {"filename": "New filename", "date": "New date", "place": "New place", "device": "New device"}
EDIT_BASE = {"filename": "filename", "date": "date", "place": "place", "device": "device"}
GROUPS = {"none": "None", "dups": "Duplicates", "capture": "Photo (all copies)", "folder": "Path / Folders",
          "day": "Day", "device": "Device", "place": "Place", "author": "Author"}
OPS = {"contains": "contains", "equals": "is", "in": "is one of", "not_equals": "is not", "empty": "is empty",
       "not_empty": "is not empty", "gte": "≥", "lte": "≤", "diff": "differs in group", "same": "same in group"}
NO_VALUE_OPS = {"empty", "not_empty", "diff", "same"}
IN_SEP = " | "  # "is one of": the values, joined (a filter made from several selected cells)
DATE_SOURCES = ["date", "digitized", "modify", "file_date", "gps_date", "name_date", "rec_date"]
VERDICT_TEXT = {"identical": "exact", "same_pixels": "diff meta", "same_photo": "same photo", "edited": "edited",
                "same_video": "same video", "likely": "likely", "similar_shot": "similar shot"}

VIEW_VERSION = 5
# Duplicates grouping starts with this filter (a removable chip); without it, files that have no copy
# show as one "No duplicates" group at the bottom.
DUP_FILTER = {"key": "dup_count", "op": "gte", "value": "2", "auto": True}
SINGLES = "__singles__"  # group key of files without duplicates
DEFAULT_VIEW = {
    "v": VIEW_VERSION,
    "columns": [["thumb", 48], ["path", 248], ["filename", 192], ["dup_count", 64], ["dup_conf", 144],
                ["checks", 240], ["key_dto", 168], ["key_created", 168], ["date", 160], ["modify", 160], ["gps_date", 160], ["place", 112],
                ["device", 152], ["author", 128]],
    "edits": {},              # purple edit columns appear only after "Edit…" in a header's Actions sheet
    "filters": [DUP_FILTER],
    "group": "dups",
    "sources_off": [],        # source ids left out of the view (Data input checkboxes)
    "group_width": 232,       # width of the grouping (tree) column
    "sort": ["", "asc"],
    "slim": {},               # column key → True (collapsed to 40px) / False (kept wide); unset: slim when empty
    "ref_ids": [],            # files chosen as the reference row of their group (red / green compare against them)
    "pinned": [],             # columns kept at the left while scrolling sideways (the thumbnail is, while first)
    "dates_seen": [],         # date columns already offered: each is shown once by default, then the user's choice holds
    "q": "",
    "colors": True,
}
# Grouping column (right after the checkbox): its label, and the regular column it replaces (shown only once).
GROUP_COLUMN = {  # grouping → (its name, the data column whose group-row cell shows the group's name)
    "dups": ("Duplicate set", "filename"), "capture": ("Photo", "filename"), "folder": ("Folder", "path"),
    "day": ("Day", "date"), "device": ("Device", "device"), "place": ("Place", "place"), "author": ("Author", "author"),
}
SLIM_WIDTH = 40  # a collapsed column: as narrow as the checkbox column
TREE_WIDTH = 40  # the expander column (› on group rows, └ on file rows) is as narrow as the checkbox column


# ───────────────────────── view state ─────────────────────────

def snap(width: float) -> int:
    """Column widths stay on the 8px grid (ADR-017), between 40 and 904px."""
    return max(40, min(904, round(float(width) / 8) * 8))


def load_view(lib: Library) -> dict:
    try:
        v = json.loads(lib.setting("workbench_view") or "null") or {}
    except ValueError:
        v = {}
    return load_view_from(v)


def load_view_from(v: dict) -> dict:
    """A stored view, upgraded to the current version and completed with defaults."""
    if v and v.get("v", 1) < 2:  # v1 opened with default edit columns: drop them once
        v["columns"] = [c for c in v.get("columns", []) if not str(c[0]).startswith("edit:")]
        v["edits"] = {}
    if v and v.get("v", 1) < 3:  # v3: the duplicates filter became a visible chip
        set_group(v, v.get("group", "dups"))
    if v and v.get("v", 1) < 5:  # v4/v5: the two key capture dates are shown, first among the date columns
        cols = v.setdefault("columns", [])
        widths = {c[0]: c[1] for c in cols}
        cols[:] = [c for c in cols if c[0] not in KEY_FIELDS]
        for key in KEY_FIELDS:
            insert_column(cols, key, widths.get(key, CORE_BY_KEY[key].width))
    if v:
        v["v"] = VIEW_VERSION
    view = json.loads(json.dumps(DEFAULT_VIEW))
    view.update({k: v[k] for k in v if k in DEFAULT_VIEW})
    return view


def set_group(view: dict, group: str) -> None:
    """Change the grouping; Duplicates adds the "Dup. ≥ 2" chip, other groupings drop it if it was added for them."""
    view["group"] = group
    filters = [f for f in view.get("filters", []) if not f.get("auto")]
    if group == "dups" and not any(f.get("key") == "dup_count" for f in filters):
        filters.insert(0, dict(DUP_FILTER))
    view["filters"] = filters


def save_view(lib: Library, view: dict) -> None:
    lib.set_setting("workbench_view", json.dumps(view))


# ───────────────────────── undo / redo of view changes ─────────────────────────
# Two stacks of earlier views in the library settings; only the view (what the table shows and the edit
# rules) is undoable here. Files on disk change only through plans, which have their own undo.

HISTORY_MAX = 100
HISTORY_LABELS = {"sort": "sorting", "group": "grouping", "filter_add": "filter", "filter_remove": "filter",
                  "q": "search", "colors": "colours", "source_toggle": "folders shown", "reset": "reset",
                  "col_add": "columns", "col_remove": "columns", "cols_set": "columns", "cols_order": "columns",
                  "col_move": "column order", "col_slim": "column width", "col_width": "column width",
                  "edit_set": "edit rule", "edit_clear": "edit rule", "edit_remove": "edit column",
                  "cell_set": "edited value", "cell_apply": "applied value", "cell_filter": "filter",
                  "cell_ref": "reference row", "col_pin": "pinned column", "cells": "selected cells"}
_RELOAD_KEYS = ("group", "q", "sources_off", "colors")  # rendered outside the table: the page reloads for these


def _stack(lib: Library, name: str) -> list:
    try:
        return json.loads(lib.setting(f"workbench_{name}") or "[]")
    except ValueError:
        return []


def history_push(lib: Library, previous: dict, act: str) -> bool:
    undo = (_stack(lib, "undo") + [{"view": previous, "act": act}])[-HISTORY_MAX:]
    lib.set_setting("workbench_undo", json.dumps(undo))
    lib.set_setting("workbench_redo", "[]")  # a new change ends the redo line
    return True


def history_step(lib: Library, direction: str) -> dict | None:
    """Swap the current view with the top of the undo (or redo) stack; None when there is nothing."""
    src, dst = ("undo", "redo") if direction == "undo" else ("redo", "undo")
    stack = _stack(lib, src)
    if not stack:
        return None
    entry, current = stack.pop(), load_view(lib)
    target = load_view_from(entry["view"])
    other = (_stack(lib, dst) + [{"view": current, "act": entry["act"]}])[-HISTORY_MAX:]
    lib.set_setting(f"workbench_{src}", json.dumps(stack))
    lib.set_setting(f"workbench_{dst}", json.dumps(other))
    save_view(lib, target)
    return {"ok": True, "label": HISTORY_LABELS.get(entry["act"], "change"),
            "reload": any(current.get(k) != target.get(k) for k in _RELOAD_KEYS)}


def field_for(key: str, exif_keys: set[str] | None = None) -> Field | None:
    if key in CORE_BY_KEY:
        return CORE_BY_KEY[key]
    if key.startswith("edit:") and key[5:] in EDIT_LABELS:
        return Field(key, EDIT_LABELS[key[5:]], "Edit", "date" if key == "edit:date" else "text", width=168)
    if key.startswith("exif:"):
        group, _, name = key[5:].partition(":")
        return Field(key, f"{name} ({group})", exif_category(key[5:]), width=152)
    return None


# Raw tags join the core categories by what they describe (first match wins, so ExposureTime is a setting,
# not a date); the rest go to "Other".
TAG_CATEGORIES = (
    ("Capture settings", ("exposure", "fnumber", "aperture", "isospeed", "focal", "flash", "shutter", "metering",
                          "whitebalance", "scene", "brightness", "gain", "contrast", "saturation", "sharpness",
                          "zoom", "subjectdistance", "lightsource", "sensing")),
    ("Dates", ("date", "time", "offset", "subsec")),
    ("Location", ("gps", "location", "latitude", "longitude", "altitude", "city", "country", "sublocation", "iso6709")),
    ("Device", ("make", "model", "serial", "lens", "software", "encoder", "firmware", "camera", "hostcomputer")),
    ("Author", ("artist", "author", "copyright", "owner", "creator", "byline", "credit", "rights")),
    ("File", ("width", "height", "dimension", "resolution", "orientation", "colorspace", "compression", "brand",
              "version", "bits", "ycbcr", "pixel", "thumbnail", "components", "duration", "codec")),
)
# The column picker lists categories in this order, and the table follows it: new columns land in their place,
# and "Order like this list" sorts the shown ones the same way.
CATEGORY_ORDER = ["File", "Duplicates", "Dates", "Location", "Device", "Author", "Capture settings", "Other"]


def exif_category(tag: str) -> str:
    name = tag.split(":", 1)[-1].rsplit(".", 1)[-1].lower().replace("_", "")  # com.apple.quicktime.model → model
    if name == "iso":
        return "Capture settings"
    return next((cat for cat, words in TAG_CATEGORIES if any(w in name for w in words)), "Other")


# Meta checks: one badge per kind of metadata, compared with the group's reference row. The number on a badge is how
# many metadata tags of that kind the file carries (from the camera / app, not the file system or our guesses).
CHECKS = {"date": ("date", "Dates"), "place": ("loc", "Location"), "device": ("dvc", "Device"), "author": ("aut", "Author")}


def meta_counts(tags: dict) -> dict[str, int]:
    """Non-empty metadata tags per check, sorted into the column-picker categories."""
    by_cat = {cat: t for t, (_, cat) in CHECKS.items()}
    out = dict.fromkeys(CHECKS, 0)
    for k, val in tags.items():
        t = by_cat.get(exif_category(k))
        if t and val not in ("", None):
            out[t] += 1
    return out


# The capture-date fields that matter most: marked with a red "!", first among the dates in the picker and in the
# table, shown by default. The raw tags they come from are not listed again.
MAGIC_FIELDS = {  # derived (not metadata) but worth a look: a yellow magic-wand badge
    "name_date": "Read from the file's name (e.g. IMG_20190518_114710): a guess from the name, not camera metadata",
    "rec_date": "Recommended: worked out from all the dates of the file and its copies (step 3), not read from one tag",
}
KEY_FIELDS = {
    "key_dto": "EXIF DateTimeOriginal: when the camera took the photo",
    "key_created": "DateCreated from XMP (photoshop:DateCreated) or IPTC (2:55 + 2:60): the creation date photo apps write",
}
KEY_SOURCE_TAGS = {"ExifIFD:DateTimeOriginal", "XMP:DateCreated", "IPTC:DateCreated"}


def column_rank(key: str) -> tuple:
    """Position of a column in catalog order; an edit column sits right after the field it edits."""
    if key.startswith("edit:"):
        return column_rank(EDIT_BASE.get(key[5:], key[5:]))[:3] + (1,)
    if key == "thumb":  # the thumbnail stays the first column, so it can stay pinned
        return (-1, 0, (0,), 0)
    f = field_for(key)
    cat = CATEGORY_ORDER.index(f.group) if f and f.group in CATEGORY_ORDER else len(CATEGORY_ORDER)
    if key in CORE_BY_KEY:
        return (cat, 0, (CORE.index(CORE_BY_KEY[key]),), 0)
    return (cat, 1, _exif_rank(key[5:]) if key.startswith("exif:") else (key,), 0)


def insert_column(columns: list, key: str, width: int) -> None:
    """Add a column at its catalog position among the shown ones (in place)."""
    rank = column_rank(key)
    at = next((i for i, (k, _) in enumerate(columns) if column_rank(k) > rank), len(columns))
    columns.insert(at, [key, width])


def show_new_dates(view: dict, catalog_: dict[str, list[Field]]) -> bool:
    """Show every Dates column not offered before (core dates and date tags found in the files); a date the user
    hid stays hidden. Columns without a value in any file render slim. True when the view changed."""
    seen = set(view.get("dates_seen", []))
    new = [f for f in catalog_.get("Dates", []) if f.key not in seen]
    if not new:
        return False
    shown = {c[0] for c in view["columns"]}
    for f in new:
        if f.key not in shown:
            insert_column(view["columns"], f.key, f.width)
    view["dates_seen"] = sorted(seen | {f.key for f in new})
    return True


def name_fields(catalog_: dict[str, list[Field]], shown: list[str]) -> list[tuple[str, str, str]]:
    """Naming-pattern parameters from the table: (group, label, token). The columns shown come first."""
    def ok(f: Field | None) -> bool:
        return bool(f) and f.kind != "special" and not f.key.startswith("edit:")
    out = [("Columns in this table", f.label, f"{{tag:{f.key}}}") for f in map(field_for, shown) if ok(f)]
    for g, fs in catalog_.items():
        out += [(g, f.label, f"{{tag:{f.key}}}") for f in fs if ok(f)]
    return out


def catalog(exif_keys: list[str]) -> dict[str, list[Field]]:
    """Every column the user can add, grouped; dates, location, device and author first."""
    out: dict[str, list[Field]] = defaultdict(list)
    for f in CORE:
        out[f.group].append(f)
    for k in exif_keys:
        if k not in KEY_SOURCE_TAGS:  # shown as the key date columns instead
            f = field_for(f"exif:{k}")
            out[f.group].append(f)
    return {g: out[g] for g in CATEGORY_ORDER if out.get(g)}


# ───────────────────────── rows ─────────────────────────

def _dup_clusters(con) -> tuple[dict[int, int], dict[int, dict]]:
    """Union of open duplicate groups from steps 1, 2 and 4 → cluster per file, plus per-file match info."""
    rows = con.execute(
        "SELECT g.id, g.step, g.verdict, g.score, g.keeper_file_id, gm.file_id, gm.role, gm.score AS mscore "
        "FROM groups g JOIN group_members gm ON gm.group_id = g.id "
        "WHERE g.status = 'open' AND g.step IN (1, 2, 4) AND g.verdict != 'similar_shot'").fetchall()
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    info: dict[int, dict] = {}
    for r in rows:
        a, b = find(r["keeper_file_id"]), find(r["file_id"])
        parent[max(a, b)] = min(a, b)
        score = 1.0 if r["verdict"] == "identical" else (r["score"] if r["role"] == "keeper" else r["mscore"]) or 0
        if r["verdict"] == "same_pixels":
            score = 0.99            # exact picture, different metadata
        elif r["verdict"] != "identical":
            score = min(score, 0.98)  # 100 % is reserved for byte-identical files
        cur = info.get(r["file_id"])
        entry = {"score": score or 0, "verdict": r["verdict"], "keeper": r["role"] == "keeper", "step": r["step"],
                 "group": r["id"]}
        if cur is None:
            info[r["file_id"]] = entry
        else:  # a file in several groups: keep the strongest link, stay keeper if kept anywhere in step 1
            if entry["score"] > cur["score"]:
                entry["keeper"] = entry["keeper"] or (cur["keeper"] and cur["step"] == 1)
                info[r["file_id"]] = entry
    return {f: find(f) for f in parent}, info


def build_rows(lib: Library) -> tuple[list[dict], list[str]]:
    """All present files with every catalog value; returns rows and the EXIF keys seen."""
    pattern = lib.setting("filename_pattern")
    with lib.connect() as con:
        files = con.execute(
            "SELECT f.id, f.name, f.original_name, f.rel_path, f.ext, f.kind, f.size, f.mtime, f.source_id, s.label AS source, "
            "s.writable, s.role AS source_role, c.capture_id, m.dto, m.make, m.model, m.software, m.gps_lat, m.gps_lon, "
            "m.width, m.height, m.duration, m.modify_date, m.raw_json "
            "FROM files f JOIN sources s ON s.id = f.source_id LEFT JOIN captures c ON c.file_id = f.id "
            "LEFT JOIN meta m ON m.file_id = f.id "
            "WHERE f.present = 1 AND f.removed = 0 AND f.kind IN ('image', 'video')").fetchall()
        props: dict[int, dict] = defaultdict(dict)
        for r in con.execute("SELECT * FROM proposals"):
            props[r["capture_id"]][r["field"]] = dict(r)
        effective = harmonize.effective_values(con)
        cluster, dupinfo = _dup_clusters(con)
    sizes: dict[int, int] = defaultdict(int)
    for f, c in cluster.items():
        sizes[c] += 1
    exif_keys: set[str] = set()
    rows = []
    for f in files:
        raw = json.loads(f["raw_json"] or "{}")
        tags = raw.get("tags") or {}
        exif_keys.update(tags)
        cp = props.get(f["capture_id"], {}) if f["capture_id"] is not None else {}
        place = geo.place(f["gps_lat"], f["gps_lon"]) if f["gps_lat"] is not None else {}
        nd, _ = time_from_filename(f["original_name"] or f["name"])
        di = dupinfo.get(f["id"])
        parent = str(PurePosixPath(f["rel_path"]).parent)
        v = {
            "path": f["source"] + ("" if parent == "." else "/" + parent),
            "filename": f["name"],
            "dup_count": sizes.get(cluster.get(f["id"]), 1) if f["id"] in cluster else 1,
            "dup_conf": round((di["score"] if di else 0) * 100) if di else None,
            "date": f["dto"] or "",
            "digitized": raw.get("DateTimeDigitized") or "",
            "modify": f["modify_date"] or "",
            "file_date": datetime.fromtimestamp(f["mtime"]).strftime(TIME_FMT) if f["mtime"] else "",
            "gps_date": raw.get("GPSDateTime") or "",
            "name_date": nd.strftime(TIME_FMT) if nd else "",
            "key_dto": _readable_date(tags.get("ExifIFD:DateTimeOriginal")),
            "key_created": _readable_date(tags.get("XMP:DateCreated") or tags.get("IPTC:DateCreated")),
            "rec_date": (cp.get("capture_time") or {}).get("value") or "",
            "place": place.get("city", ""),
            "rec_place": (cp.get("location_name") or {}).get("value") or "",
            "gps": "" if f["gps_lat"] is None else f"{f['gps_lat']:.5f}, {f['gps_lon']:.5f}",
            "country": place.get("country", ""),
            "device": device_name(f["make"], f["model"]),
            "rec_device": (cp.get("device_name") or {}).get("value") or "",
            "make": f["make"] or "", "model": f["model"] or "", "software": f["software"] or "",
            "serial": raw.get("SerialNumber") or "", "lens": raw.get("LensModel") or "",
            "author": raw.get("Artist") or "", "copyright": raw.get("Copyright") or "", "owner": raw.get("OwnerName") or "",
            "size": f["size"] or 0,
            "dims": f"{f['width']}×{f['height']}" if f["width"] else "",
            "ext": (f["ext"] or "").upper(), "kind": f["kind"],
        }
        for k, val in tags.items():
            v[f"exif:{k}"] = val if isinstance(val, (int, float)) else str(val)
        rows.append({
            "id": f["id"], "source_id": f["source_id"], "capture": f["capture_id"], "cluster": cluster.get(f["id"]), "dup": di,
            "keeper": bool(di and di["keeper"]), "verdict": di["verdict"] if di else None,
            "original_name": f["original_name"] or f["name"], "ext_raw": f["ext"] or "",
            "writable": bool(f["writable"]) and f["source_role"] != "reference",
            "effective": effective.get(f["capture_id"], {}) if f["capture_id"] is not None else {},
            "v": v, "tags": tags, "pattern": pattern, "meta_counts": meta_counts(tags),
        })
    return rows, sorted(exif_keys, key=_exif_rank)


def _readable_date(raw) -> str:
    """'2019:05:18 11:47:10' / '2019-05-18T11:47:10+02:00' → '2019-05-18 11:47:10…' (the stored text, tidied)."""
    if not raw:
        return ""
    s = str(raw).strip()
    if re.match(r"^\d{4}:\d{2}:\d{2}", s):
        s = s[:4] + "-" + s[5:7] + "-" + s[8:]
    return s.replace("T", " ", 1)


def _exif_rank(k: str) -> tuple:
    focus = ("Date", "Time", "GPS", "Make", "Model", "Serial", "Lens", "Artist", "Author", "Copyright", "Owner", "Software")
    name = k.split(":", 1)[-1]
    return (0 if any(w in name for w in focus) else 1, k)


# ───────────────────────── edit rules ─────────────────────────

def _shift(value: str, seconds: int) -> str:
    try:
        return (datetime.strptime(value, TIME_FMT) + timedelta(seconds=seconds)).strftime(TIME_FMT)
    except ValueError:
        return ""


def _label(key: str) -> str:
    f = field_for(key)
    return f.label if f else key


def _hms(seconds: int) -> str:
    sign, s = ("−" if seconds < 0 else "+"), abs(seconds)
    return f"{sign}{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def _rule_source(target: str, rule: dict, names: dict[int, str]) -> dict:
    """Where a rule takes its values from: a link (id, label, the column it reads, the row it copies)."""
    kind, base = rule.get("kind"), EDIT_BASE[target]
    if kind == "recommended":
        return {"id": "rec", "label": f"{_label('rec_' + target)} (best evidence)", "key": f"rec_{target}"}
    if kind == "column":
        col = rule.get("column", "")
        return {"id": f"col:{col}", "label": f"Copied from {_label(col)}", "key": col}
    if kind == "shift":
        col = rule.get("column") or "date"
        return {"id": "shift", "label": f"{_label(col)} shifted {_hms(int(rule.get('seconds') or 0))}", "key": col}
    if kind == "fixed" and rule.get("from"):
        fid, col = int(rule["from"]["fid"]), rule["from"]["key"]
        return {"id": f"cell:{fid}:{col}", "label": f"Copied from {_label(col)} of {names.get(fid, f'file {fid}')}",
                "key": col, "rid": fid}
    if kind == "fixed":
        return {"id": "fixed", "label": f"Fixed value {rule.get('value', '')}"}
    if kind == "pattern":
        return {"id": "pattern", "label": f"Naming pattern {rule.get('pattern') or ''}"}
    return {"id": "keep", "label": f"Unchanged: current {_label(base)}", "key": base}


def apply_edits(rows: list[dict], edits: dict) -> None:
    """Compute each row's edit-column values (r['e']) and where each one comes from (r['src'], a lettered link).

    A rule may be limited to some files ("ids": the others keep their value) and may carry per-file values typed
    in a cell ("overrides": file id → value); "keep" is a rule that only holds such overrides."""
    names = {r["id"]: r["v"]["filename"] for r in rows}
    sources = {t: _rule_source(t, rule, names) for t, rule in edits.items() if t in EDIT_BASE}
    keep = {t: _rule_source(t, {"kind": "keep"}, names) for t in EDIT_BASE}
    typed_src = {"id": "typed", "label": "Typed for this file", "typed": True}
    copied: dict[tuple, dict] = {}  # per-file values copied from a cell ("override_from"): one source per cell
    letters: dict[str, dict[str, str]] = {t: {} for t in EDIT_BASE}  # per column: source id → A, B, C… (first seen)
    for r in rows:
        v, e, src = r["v"], {}, {}
        for target in ("date", "place", "device"):
            rule = edits.get(target)
            if not rule:
                continue
            kind = rule.get("kind")
            rec = v[f"rec_{target}"] or v[target]
            src[target] = sources[target]
            if kind == "keep" or (rule.get("ids") is not None and r["id"] not in rule["ids"]):
                e[target] = v[target]
                src[target] = keep[target]
            elif kind == "recommended":
                e[target] = rec
            elif kind == "column":  # each file's own value of another column (a date: tidied to the table form)
                raw = v.get(rule.get("column", ""), "")
                e[target] = _readable_date(raw)[:19] if target == "date" else str(raw or "")
            elif kind == "fixed":
                e[target] = rule.get("value", "")
            elif kind == "shift" and target == "date":
                base = v.get(rule.get("column") or "date") or ""
                e[target] = _shift(base, int(rule.get("seconds") or 0)) if base else ""
        if "filename" in edits:
            pattern = edits["filename"].get("pattern") or r["pattern"]
            eff = r["effective"]
            values = {
                "capture_time": e.get("date", eff.get("capture_time") or v["date"]),
                "location_name": e.get("place", eff.get("location_name") or v["place"]),
                "device_name": e.get("device", eff.get("device_name") or v["device"]),
                "country": eff.get("country") or v["country"],
            }
            values["tags"] = v  # {tag:KEY}: any column of this row
            new = harmonize.render_name(pattern, values, r["original_name"], r["ext_raw"])
            e["filename"] = new if new is not None else ""
            src["filename"] = sources["filename"]
            if edits["filename"].get("kind") == "keep":
                e["filename"] = v["filename"]
        for target, rule in edits.items():  # values typed into single cells win
            typed = (rule.get("overrides") or {}).get(str(r["id"]))
            if typed is not None:
                e[target] = typed
                frm = (rule.get("override_from") or {}).get(str(r["id"]))
                if frm:
                    k = (frm["fid"], frm["key"])
                    copied.setdefault(k, {"id": f"cell:{k[0]}:{k[1]}", "label": f"Copied from {_label(k[1])} of "
                                                                                   f"{names.get(int(k[0]), 'file ' + str(k[0]))}",
                                          "key": k[1], "rid": int(k[0])})
                    src[target] = copied[k]
                else:
                    src[target] = typed_src
        for target, s in src.items():
            seen = letters[target]
            seen.setdefault(s["id"], chr(65 + len(seen)) if len(seen) < 26 else "*")
            src[target] = {**s, "letter": seen[s["id"]]}
        r["e"], r["src"] = e, src


# Cell actions: which edit column a cell's value can feed (None = not editable yet; plans write these four only)
def edit_target(key: str) -> str | None:
    if key.startswith("edit:"):
        return key[5:]
    if key in ("place", "rec_place"):
        return "place"
    if key in ("device", "rec_device"):
        return "device"
    f = field_for(key)
    if f and (f.kind == "date" or (key.startswith("exif:") and f.group == "Dates" and "Offset" not in key and "Subsec" not in key)):
        return "date"
    return None


def apply_targets(key: str) -> list[str]:
    """Edit columns a cell's value may feed ("Apply to column…"): a date only the capture date; any other value
    a place or a device (the fields plans can write)."""
    return ["date"] if edit_target(key) == "date" else ["place", "device"]


def cell_value(r: dict, key: str) -> str:
    """A cell's value as an edit value: dates in 'YYYY-MM-DD HH:MM:SS'."""
    val = value(r, key)
    val = "" if val is None else str(val)
    return _readable_date(val)[:19] if edit_target(key) == "date" else val


def value(r: dict, key: str):
    if key.startswith("edit:"):
        return r["e"].get(key[5:], "")
    return r["v"].get(key, "")


# ───────────────────────── grouping, differences, filters, sorting ─────────────────────────

def _group_key(r: dict, by: str):
    v = r["v"]
    return {
        "dups": r["cluster"] if r["cluster"] is not None else SINGLES,
        "capture": r["capture"] if r["capture"] is not None else f"f{r['id']}",
        "folder": v["path"],
        "day": v["date"][:10] or "No date",
        "device": v["device"] or "Unknown device",
        "place": v["place"] or "No place",
        "author": v["author"] or "No author",
        "none": "all",
    }[by]


def _match(val, op: str, arg: str, differs: bool) -> bool:
    s = "" if val is None else str(val)
    if op == "diff":
        return differs
    if op == "same":
        return not differs
    if op == "empty":
        return s == ""
    if op == "not_empty":
        return s != ""
    if op == "contains":
        return arg.lower() in s.lower()
    if op == "equals":
        return s.lower() == arg.lower()
    if op == "in":
        return s.lower() in {a.strip().lower() for a in arg.split(IN_SEP.strip())}
    if op == "not_equals":
        return s.lower() != arg.lower()
    if op in ("gte", "lte"):
        try:
            a, b = float(val), float(arg)
        except (TypeError, ValueError):
            a, b = s, arg  # dates and text compare as text (ISO dates sort correctly)
        return a >= b if op == "gte" else a <= b
    return True


@dataclass
class Group:
    key: object
    label: str
    rows: list[dict]
    ref: dict
    summary: dict = field(default_factory=dict)


def table(lib: Library, view: dict) -> tuple[list[Group], list[str], int]:
    """Grouped, compared, filtered and sorted rows for the current view."""
    rows, exif_keys = build_rows(lib)
    ref_ids = set(view.get("ref_ids") or [])
    off = set(view.get("sources_off") or [])
    rows = [r for r in rows if r["source_id"] not in off]
    apply_edits(rows, view["edits"])
    by = view["group"] if view["group"] in GROUPS else "dups"
    q = (view.get("q") or "").strip().lower()
    if q:
        rows = [r for r in rows if q in r["v"]["path"].lower() or q in r["v"]["filename"].lower()
                or q in str(r["e"].get("filename", "")).lower()]
    buckets: dict = defaultdict(list)
    for r in rows:
        buckets[_group_key(r, by)].append(r)
    keys = [c[0] for c in view["columns"]]
    sort_key, sort_dir = (view.get("sort") or ["", "asc"])[:2]

    def sk(r):
        val = value(r, sort_key)
        return val.lower() if isinstance(val, str) else val

    def ordered(items: list, row) -> list:
        """Sort by the column in either direction; empty values always last."""
        filled = [i for i in items if sk(row(i)) not in ("", None)]
        filled.sort(key=lambda i: sk(row(i)), reverse=sort_dir == "desc")
        return filled + [i for i in items if sk(row(i)) in ("", None)]

    groups = []
    for gk, rs in buckets.items():
        if sort_key:
            rs[:] = ordered(rs, lambda r: r)
        else:
            rs.sort(key=lambda r: (not r["keeper"], r["v"]["path"].lower(), r["v"]["filename"].lower()))
        chosen = next((r for r in rs if r["id"] in ref_ids), None)  # "use this cell as reference"
        ref = chosen or (next((r for r in rs if r["keeper"]), rs[0]) if by == "dups" else rs[0])
        unrelated = gk == SINGLES or by == "none"  # files without copies, or no grouping: nothing to compare
        common = _common_segments([r["v"]["path"] for r in rs])
        row_dates = _row_date_keys(keys) if by == "none" else []
        for r in rs:  # differences against the group's reference row
            r["rowcmp"] = row_date_states(r, row_dates) if row_dates else {}
            r["diff"] = {k: not unrelated and len(rs) > 1 and r is not ref and str(value(r, k)) != str(value(ref, k)) for k in keys}
            r["is_ref"] = r is ref
            r["user_ref"] = r is chosen
            if by == "dups" and not unrelated and r["dup"]:
                r["dup_explain"] = explain_diff(r, ref)
            r["path_parts"] = _path_parts(r["v"]["path"], ref["v"]["path"], common)
            # per field: True same as the reference row, False differs, None when neither has a value
            r["checks"] = {t: None if not r["v"][t] and not ref["v"][t] else str(r["v"][t]) == str(ref["v"][t])
                           for t in CHECKS}
        label = {"dups": ref["v"]["filename"], "capture": ref["v"]["filename"], "none": "All files"}.get(by, str(gk))
        if gk == SINGLES:
            label = "No duplicates"
        g = Group(gk, label, rs, ref)
        g.summary = {k: ("" if unrelated
                         else "N/A" if all(value(r, k) in ("", None) for r in rs)  # nothing to compare
                         else "Diff" if any(r["diff"].get(k) for r in rs) else "Same") for k in keys}
        if "checks" in keys and not unrelated:
            states = [ok for r in rs for ok in r["checks"].values()]
            g.summary["checks"] = ("N/A" if all(ok is None for ok in states)
                                   else "Diff" if any(ok is False for ok in states) else "Same")
        groups.append(g)

    # filters: per row when ungrouped, per group otherwise (a duplicate set needs its context)
    for flt in view.get("filters", []):
        k, op, arg = flt.get("key"), flt.get("op"), flt.get("value", "")
        if by == "none":
            for g in groups:
                g.rows = [r for r in g.rows if _match(value(r, k), op, arg, r["diff"].get(k, False))]
            groups = [g for g in groups if g.rows]
        else:
            groups = [g for g in groups if any(_match(value(r, k), op, arg, r["diff"].get(k, False)) for r in g.rows)]

    if sort_key:
        groups = ordered(groups, lambda g: g.rows[0])
    elif by == "dups":
        groups.sort(key=lambda g: -sum(r["v"]["size"] for r in g.rows))
    else:
        groups.sort(key=lambda g: str(g.key))
    groups.sort(key=lambda g: g.key == SINGLES)  # stable: "No duplicates" always last
    return groups, exif_keys, sum(len(g.rows) for g in groups)


_DATE_TEXT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


# columns that show the same EXIF tag: one vote between them (Date modified is IFD0 DateTime, Date taken is the
# capture date read from DateTimeOriginal, Date digitized is DateTimeDigitized)
_SAME_TAG = {"exif:IFD0:DateTime": "modify", "key_dto": "date", "exif:ExifIFD:DateTimeOriginal": "date",
             "exif:ExifIFD:DateTimeDigitized": "digitized"}


def _row_date_keys(keys: list[str]) -> list[str]:
    """The date-time columns a row compares across (no grouping): local dates read from the file only — no UTC
    (GPS, "…Z"), no derived values (the recommended date)."""
    return [k for k in keys if not k.startswith("edit:") and edit_target(k) == "date" and "gps" not in k.lower()
            and k != "rec_date"]


def row_date_states(r: dict, keys: list[str]) -> dict[str, str]:
    """No grouping: compare a row's date-times with each other. All present values equal → every one "same" (green);
    otherwise only the values outside the single most common one are "diff" (red), counting columns that show the
    same tag once; with no single majority (a tie) nothing is red: there is no minority."""
    vals: dict[str, str] = {}
    for k in keys:
        raw = value(r, k)
        if raw in ("", None) or str(raw).strip().endswith("Z"):
            continue
        d = _readable_date(raw)[:19]
        if _DATE_TEXT.match(d):
            vals[k] = d
    if len(vals) < 2:
        return {}
    counts = Counter(d for _, d in {(_SAME_TAG.get(k, k), d) for k, d in vals.items()})  # one vote per tag and value
    if len(counts) == 1:
        return {k: "same" for k in vals}
    (top, n), *rest = counts.most_common()
    if rest and rest[0][1] == n:  # a tie: no minority to point at
        return {}
    return {k: "diff" for k, d in vals.items() if d != top}


def _common_segments(paths: list[str]) -> list[str | None]:
    """Per position, the segment every path shares there (None where they differ or one is shorter)."""
    split = [p.split("/") for p in paths]
    common: list[str | None] = list(split[0]) if split else []
    for s in split[1:]:
        common = [c if i < len(s) and s[i] == c else None for i, c in enumerate(common[:len(s)])]
    return common


def _path_parts(path: str, ref: str, common: list[str | None]) -> list[tuple[str, str]]:
    """Path segments with a state: "same" when every file of the group has that segment there (green), "diff" when
    it differs from the reference row's (red), "" otherwise (equal to the reference while other files differ)."""
    a, b = path.split("/"), ref.split("/")
    return [(seg, "same" if i < len(common) and common[i] == seg else "" if i < len(b) and b[i] == seg else "diff")
            for i, seg in enumerate(a)]


def acronym(label: str) -> str:
    """'OffsetTimeDigitized (ExifIFD)' → 'OTD', 'Date taken' → 'DT': the name of a slim column (3 letters at most)."""
    name = label.split(" (")[0]
    words = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", name)
    letters = "".join(w[0].upper() for w in words) or name[:2].upper()
    return letters[:3] if len(letters) > 1 else name[:3].upper()


def conf_parts(r: dict) -> tuple[str, str]:
    """('98 %', 'date added, renamed'): the score (100 % = byte-identical, 99 % = same pixels with other metadata,
    lower = the matcher's score) and, in grey, what differs from the kept copy of the duplicate set."""
    if not r["dup"]:
        return "", ""
    pct = round(r["dup"]["score"] * 100)
    if r["verdict"] == "identical":
        return f"{pct} %", "identical"
    return f"{pct} %", r.get("dup_explain") or VERDICT_TEXT.get(r["verdict"], "")


def conf_label(r: dict) -> str:
    pct, why = conf_parts(r)
    return f"{pct} · {why}" if why else pct


# what the duplicate explanation names when a copy differs from the kept one
EXPLAIN_FIELDS = (("date", "date"), ("place", "place"), ("device", "device"), ("author", "author"),
                  ("dims", "pixel size"), ("filename", "name"))


def explain_diff(r: dict, ref: dict) -> str:
    """'date added, renamed, place differs' — a short summary of how a copy differs from the kept copy."""
    if r is ref:
        return "kept copy"
    out = []
    for key, name in EXPLAIN_FIELDS:
        a, b = r["v"].get(key) or "", ref["v"].get(key) or ""
        if a == b:
            continue
        if key == "filename":
            out.append("renamed")
        elif not b:
            out.append(f"{name} added")
        elif not a:
            out.append(f"{name} missing")
        else:
            out.append(f"{name} differs")
    if not out:
        return "same metadata" if r["verdict"] != "same_pixels" else "same pixels"
    return ", ".join(out[:3]) + (f" +{len(out) - 3}" if len(out) > 3 else "")


# ───────────────────────── actions ─────────────────────────

def commit(lib: Library, view: dict, ids: set[int], only: str | None = None) -> int:
    """Turn the edit columns of the selected rows into decisions (proposals 'edited'), per photo.
    `only` limits it to one edit column (date / place / device / filename)."""
    rows, _ = build_rows(lib)
    apply_edits(rows, view["edits"])
    if only in (None, "filename") and "filename" in view["edits"] and view["edits"]["filename"].get("pattern"):
        lib.set_setting("filename_pattern", view["edits"]["filename"]["pattern"])
    fields = {"date": "capture_time", "place": "location_name", "device": "device_name"}
    if only:
        fields = {k: v for k, v in fields.items() if k == only}
    n = 0
    done: set[tuple] = set()
    for r in rows:
        if r["id"] not in ids or r["capture"] is None:
            continue
        for target, pfield in fields.items():
            if target not in r["e"] or (r["capture"], pfield) in done:
                continue
            new = r["e"][target]
            if new and new != r["v"][target]:
                harmonize.set_status(lib, r["capture"], pfield, "edited", new)
                done.add((r["capture"], pfield))
                n += 1
    return n


SELECTION_PRESETS = ("all", "nonkeepers", "dup_newer", "dup_older", "dup_lessmeta")
_DATE_KEYS = ("date", "digitized", "gps_date", "name_date")  # what the metadata says about creation, best first


def _created(r: dict) -> str:
    return next((str(r["v"][k]) for k in _DATE_KEYS if r["v"].get(k)), "")


def _richness(r: dict) -> int:
    """How much metadata a copy carries: non-empty tags plus the core date/place/device/author fields."""
    core = ("date", "digitized", "gps", "make", "model", "lens", "serial", "author", "copyright", "owner")
    return sum(1 for v in r["tags"].values() if v not in ("", None)) + sum(1 for k in core if r["v"].get(k))


def filled_ids(lib: Library, view: dict, keys: list[str], filled: bool) -> list[int]:
    """Files of the view (filters apply, all groups) with a value in every one of the columns (filled), or with
    none in any of them (not filled)."""
    groups, _, _ = table(lib, view)
    return [r["id"] for g in groups for r in g.rows if all((value(r, k) not in ("", None)) == filled for k in keys)]


def preset_ids(lib: Library, view: dict, preset: str) -> list[int]:
    """Rows a selection preset ticks, within the current view (filters apply).

    all: every row. The duplicate presets keep one copy per duplicate set and tick the others:
    dup_newer keeps the oldest creation date (ticks the newer copies), dup_older keeps the newest,
    dup_lessmeta keeps the copy with the most metadata. A copy without any date counts as newest
    for dup_newer and as oldest for dup_older, so it is ticked either way. Ties keep the current keeper.
    """
    groups, _, _ = table(lib, view)
    rows = [r for g in groups for r in g.rows]
    if preset == "all":
        return [r["id"] for r in rows]
    if preset == "nonkeepers":
        return [r["id"] for r in rows if r["dup"] and not r["keeper"]]
    sets: dict = defaultdict(list)
    for r in rows:
        if r["dup"] and r["cluster"] is not None:
            sets[r["cluster"]].append(r)
    out = []
    for members in sets.values():
        if len(members) < 2:
            continue
        tie = lambda r: (not r["keeper"], r["v"]["path"].lower(), r["v"]["filename"].lower())  # noqa: E731
        dated = [r for r in members if _created(r)]
        if preset == "dup_lessmeta":
            keep = min(members, key=lambda r: (-_richness(r), *tie(r)))
        elif preset in ("dup_newer", "dup_older") and dated:
            newest = preset == "dup_older"
            best = (max if newest else min)(_created(r) for r in dated)
            keep = min((r for r in dated if _created(r) == best), key=tie)
        else:
            keep = min(members, key=tie)
        out += [r["id"] for r in members if r is not keep]
    return out


def groups_for(lib: Library, ids: set[int]) -> list[int]:
    """Open duplicate groups that contain any of the files."""
    with lib.connect() as con:
        marks = ",".join("?" * len(ids)) or "NULL"
        return [r["group_id"] for r in con.execute(
            f"SELECT DISTINCT gm.group_id FROM group_members gm JOIN groups g ON g.id = gm.group_id "
            f"WHERE g.status = 'open' AND g.step IN (1, 2, 4) AND gm.file_id IN ({marks})", list(ids))]


# file names may use any column ({tag:KEY}); plans get the values the table shows
harmonize.set_tag_source(lambda lib: {r["id"]: r["v"] for r in build_rows(lib)[0]})
