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
from collections import defaultdict
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
    Field("checks", "Meta checks", "Duplicates", "special", width=152),
    Field("date", "Date taken", "Dates", "date", editable="date", width=160),
    Field("digitized", "Date digitized", "Dates", "date", width=160),
    Field("modify", "Date modified (EXIF)", "Dates", "date", width=160),
    Field("file_date", "File date", "Dates", "date", width=160),
    Field("gps_date", "GPS date (UTC)", "Dates", "date", width=160),
    Field("name_date", "Date in filename", "Dates", "date", width=160),
    Field("rec_date", "Recommended date", "Dates", "date", width=160),
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
OPS = {"contains": "contains", "equals": "is", "not_equals": "is not", "empty": "is empty",
       "not_empty": "is not empty", "gte": "≥", "lte": "≤", "diff": "differs in group", "same": "same in group"}
NO_VALUE_OPS = {"empty", "not_empty", "diff", "same"}
DATE_SOURCES = ["date", "digitized", "modify", "file_date", "gps_date", "name_date", "rec_date"]
VERDICT_TEXT = {"identical": "exact", "same_pixels": "diff meta", "same_photo": "same photo", "edited": "edited",
                "same_video": "same video", "likely": "likely", "similar_shot": "similar shot"}

VIEW_VERSION = 3
# Duplicates grouping starts with this filter (a removable chip); without it, files that have no copy
# show as one "No duplicates" group at the bottom.
DUP_FILTER = {"key": "dup_count", "op": "gte", "value": "2", "auto": True}
SINGLES = "__singles__"  # group key of files without duplicates
DEFAULT_VIEW = {
    "v": VIEW_VERSION,
    "columns": [["thumb", 48], ["path", 248], ["filename", 192], ["dup_count", 64], ["dup_conf", 144],
                ["checks", 152], ["date", 160], ["modify", 160], ["gps_date", 160], ["place", 112],
                ["device", 152], ["author", 128]],
    "edits": {},              # purple edit columns appear only after "Edit…" in a header's Actions sheet
    "filters": [DUP_FILTER],
    "group": "dups",
    "sources_off": [],        # source ids left out of the view (Data input checkboxes)
    "group_width": 232,       # width of the grouping (tree) column
    "sort": ["", "asc"],
    "q": "",
    "colors": True,
}
# Grouping column (right after the checkbox): its label, and the regular column it replaces (shown only once).
GROUP_COLUMN = {
    "dups": ("Duplicate set", ""), "capture": ("Photo", ""), "folder": ("Folder", "path"),
    "day": ("Day", ""), "device": ("Device", "device"), "place": ("Place", "place"), "author": ("Author", "author"),
}


# ───────────────────────── view state ─────────────────────────

def snap(width: float) -> int:
    """Column widths stay on the 8px grid (ADR-017), between 40 and 904px."""
    return max(40, min(904, round(float(width) / 8) * 8))


def load_view(lib: Library) -> dict:
    try:
        v = json.loads(lib.setting("workbench_view") or "null") or {}
    except ValueError:
        v = {}
    if v and v.get("v", 1) < 2:  # v1 opened with default edit columns: drop them once
        v["columns"] = [c for c in v.get("columns", []) if not str(c[0]).startswith("edit:")]
        v["edits"] = {}
    if v and v.get("v", 1) < 3:  # v3: the duplicates filter became a visible chip
        set_group(v, v.get("group", "dups"))
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
CATEGORY_ORDER = ["Dates", "Location", "Device", "Author", "Capture settings", "Duplicates", "File", "Other"]


def exif_category(tag: str) -> str:
    name = tag.split(":", 1)[-1].rsplit(".", 1)[-1].lower().replace("_", "")  # com.apple.quicktime.model → model
    if name == "iso":
        return "Capture settings"
    return next((cat for cat, words in TAG_CATEGORIES if any(w in name for w in words)), "Other")


def catalog(exif_keys: list[str]) -> dict[str, list[Field]]:
    """Every column the user can add, grouped; dates, location, device and author first."""
    out: dict[str, list[Field]] = defaultdict(list)
    for f in CORE:
        out[f.group].append(f)
    for k in exif_keys:
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
            "v": v, "tags": tags, "pattern": pattern,
        })
    return rows, sorted(exif_keys, key=_exif_rank)


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


def apply_edits(rows: list[dict], edits: dict) -> None:
    """Compute each row's edit-column values (r['e'])."""
    for r in rows:
        v, e = r["v"], {}
        for target in ("date", "place", "device"):
            rule = edits.get(target)
            if not rule:
                continue
            kind = rule.get("kind")
            rec = v[f"rec_{target}"] or v[target]
            if kind == "recommended":
                e[target] = rec
            elif kind == "column":
                e[target] = str(v.get(rule.get("column", ""), "") or "")
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
            new = harmonize.render_name(pattern, values, r["original_name"], r["ext_raw"])
            e["filename"] = new if new is not None else ""
        r["e"] = e


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
        ref = next((r for r in rs if r["keeper"]), rs[0]) if by == "dups" else rs[0]
        unrelated = gk == SINGLES  # files without copies: nothing to compare
        for r in rs:  # differences against the group's reference row
            r["diff"] = {k: not unrelated and len(rs) > 1 and r is not ref and str(value(r, k)) != str(value(ref, k)) for k in keys}
            r["is_ref"] = r is ref
            r["path_parts"] = _path_diff(r["v"]["path"], ref["v"]["path"], r is ref or len(rs) == 1)
            r["checks"] = {t: (str(r["v"][t]) == str(ref["v"][t])) for t in ("date", "place", "device")}
        label = {"dups": ref["v"]["filename"], "capture": ref["v"]["filename"], "none": "All files"}.get(by, str(gk))
        if unrelated:
            label = "No duplicates"
        g = Group(gk, label, rs, ref)
        g.summary = {k: ("" if unrelated
                         else "N/A" if all(value(r, k) in ("", None) for r in rs)  # nothing to compare
                         else "Diff" if any(r["diff"].get(k) for r in rs) else "Same") for k in keys}
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


def _path_diff(path: str, ref: str, same: bool) -> list[tuple[str, bool]]:
    a, b = path.split("/"), ref.split("/")
    return [(seg, same or (i < len(b) and b[i] == seg)) for i, seg in enumerate(a)]


def conf_label(r: dict) -> str:
    """100 % = byte-identical, 99 % = same pixels with different metadata, lower = the matcher's score."""
    if not r["dup"]:
        return ""
    pct = round(r["dup"]["score"] * 100)
    text = VERDICT_TEXT.get(r["verdict"], "")
    return f"{pct} %" + (f" · {text}" if text and r["verdict"] != "identical" else "")


# ───────────────────────── actions ─────────────────────────

def commit(lib: Library, view: dict, ids: set[int]) -> int:
    """Turn the edit columns of the selected rows into decisions (proposals 'edited'), per photo."""
    rows, _ = build_rows(lib)
    apply_edits(rows, view["edits"])
    if "filename" in view["edits"] and view["edits"]["filename"].get("pattern"):
        lib.set_setting("filename_pattern", view["edits"]["filename"]["pattern"])
    fields = {"date": "capture_time", "place": "location_name", "device": "device_name"}
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
