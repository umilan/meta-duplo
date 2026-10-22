---
name: workbench-model
description: The server-side model of the Workbench ("Filomena") in meta_duplo/web/workbench.py — fields and column catalog, the stored view and its migrations, row building, grouping, reference rows and differences, filters, sorting, edit rules with source links, commit to decisions, selection presets and view undo/redo. Use it when rebuilding or changing anything the Workbench table computes (not the HTTP layer, see 10-web-routes).
---

# Workbench model (`meta_duplo/web/workbench.py`)

One table, one row per present image/video file. *Group by* chooses the task (duplicates, photo, folder,
day, device, place, author, none). Cells that differ from the group's reference row are marked; purple
*edit columns* hold rules for filename / date / place / device whose values feed the same decisions
(`proposals`, status `edited`) and plans as steps 1–4. Nothing here writes to source volumes.

Module imports: `json, re, defaultdict, dataclass/field, datetime/timedelta, PurePosixPath`,
`from .. import geo, harmonize`, `from ..db import Library`,
`from ..metadata import TIME_FMT, device_name, time_from_filename` (`TIME_FMT = "%Y-%m-%d %H:%M:%S"`).
All functions are pure over a `Library` (settings + SQLite); the view is a plain JSON-able `dict`.

---

## 1. Fields

```python
@dataclass(frozen=True)
class Field:
    key: str
    label: str
    group: str            # category in the column picker
    kind: str = "text"    # text | date | num | special
    editable: str = ""    # edit target: filename | date | place | device ("" = none)
    width: int = 140      # default width when the column is added
```

### `CORE` (order matters: it is the in-category rank)

| # | key | label | group | kind | editable | width |
|---|---|---|---|---|---|---|
| 0 | `thumb` | Th. | File | special | | 64 |
| 1 | `path` | Path | File | text | | 256 |
| 2 | `filename` | Filename | File | text | filename | 192 |
| 3 | `dup_count` | Dup. | Duplicates | num | | 72 |
| 4 | `dup_conf` | Dup. confidence | Duplicates | num | | 152 |
| 5 | `checks` | Meta checks | Duplicates | special | | 240 |
| 6 | `key_dto` | DateTimeOriginal (EXIF) | Dates | date | | 168 |
| 7 | `key_created` | DateCreated (IPTC/XMP) | Dates | date | | 168 |
| 8 | `date` | Date taken | Dates | date | date | 160 |
| 9 | `digitized` | Date digitized | Dates | date | | 160 |
| 10 | `modify` | Date modified (EXIF) | Dates | date | | 160 |
| 11 | `file_date` | File date | Dates | date | | 160 |
| 12 | `gps_date` | GPS date (UTC) | Dates | date | | 160 |
| 13 | `name_date` | Date from file name (third in Dates, after the two key dates) | Dates | date | | 160 |
| 14 | `rec_date` | Recommended date | Dates | date | | 160 |
| 15 | `place` | Place | Location | text | place | 120 |
| 16 | `rec_place` | Recommended place | Location | text | | 128 |
| 17 | `gps` | GPS | Location | text | | 152 |
| 18 | `country` | Country | Location | text | | 80 |
| 19 | `device` | Device | Device | text | device | 152 |
| 20 | `rec_device` | Recommended device | Device | text | | 152 |
| 21 | `make` | Make | Device | text | | 112 |
| 22 | `model` | Model | Device | text | | 144 |
| 23 | `serial` | Serial number | Device | text | | 120 |
| 24 | `lens` | Lens | Device | text | | 144 |
| 25 | `software` | Software | Device | text | | 128 |
| 26 | `author` | Author | Author | text | "" | 128 |
| 27 | `copyright` | Copyright | Author | text | | 160 |
| 28 | `owner` | Camera owner | Author | text | | 128 |
| 29 | `size` | Size | File | num | | 88 |
| 30 | `dims` | Dimensions | File | text | | 96 |
| 31 | `ext` | Type | File | text | | 64 |
| 32 | `kind` | Kind | File | text | | 72 |

`CORE_BY_KEY = {f.key: f for f in CORE}`.

### Edit and grouping constants

```python
EDIT_LABELS = {"filename": "New filename", "date": "New date", "place": "New place", "device": "New device"}
EDIT_BASE   = {"filename": "filename", "date": "date", "place": "place", "device": "device"}  # column an edit column follows
GROUPS = {"none": "None", "dups": "Duplicates", "capture": "Photo (all copies)", "folder": "Path / Folders",
          "day": "Day", "device": "Device", "place": "Place", "author": "Author"}
OPS = {"contains": "contains", "equals": "is", "not_equals": "is not", "empty": "is empty",
       "not_empty": "is not empty", "gte": "≥", "lte": "≤", "diff": "differs in group", "same": "same in group"}
NO_VALUE_OPS = {"empty", "not_empty", "diff", "same"}
DATE_SOURCES = ["date", "digitized", "modify", "file_date", "gps_date", "name_date", "rec_date"]  # date rule "copy from"/"shift" choices
VERDICT_TEXT = {"identical": "exact", "same_pixels": "diff meta", "same_photo": "same photo", "edited": "edited",
                "same_video": "same video", "likely": "likely", "similar_shot": "similar shot"}
```

### `field_for(key, exif_keys=None) -> Field | None`

1. `key in CORE_BY_KEY` → that field.
2. `edit:<t>` with `t in EDIT_LABELS` → `Field(key, EDIT_LABELS[t], "Edit", "date" if key == "edit:date" else "text", width=168)`.
3. `exif:<Group>:<Name>` → `Field(key, f"{Name} ({Group})", exif_category(Group:Name), width=152)`;
   e.g. `exif:ExifIFD:OffsetTimeDigitized` → label `OffsetTimeDigitized (ExifIFD)`. Kind is `text`, `editable` "".
4. Otherwise `None` (unknown keys are ignored everywhere: catalog, ctx, actions).
   The `exif_keys` parameter is accepted but unused.

### Categories of raw tags

`exif_category(tag)`: take the part after the first `:`, then after the last `.`
(`com.apple.quicktime.model` → `model`), lower-case, strip `_`. `iso` exactly → `Capture settings`. Otherwise
the first category in `TAG_CATEGORIES` with a word contained in the name; else `Other`. Order matters
(ExposureTime is a setting, not a date):

| category | words (substring match) |
|---|---|
| Capture settings | exposure, fnumber, aperture, isospeed, focal, flash, shutter, metering, whitebalance, scene, brightness, gain, contrast, saturation, sharpness, zoom, subjectdistance, lightsource, sensing |
| Dates | date, time, offset, subsec |
| Location | gps, location, latitude, longitude, altitude, city, country, sublocation, iso6709 |
| Device | make, model, serial, lens, software, encoder, firmware, camera, hostcomputer |
| Author | artist, author, copyright, owner, creator, byline, credit, rights |
| File | width, height, dimension, resolution, orientation, colorspace, compression, brand, version, bits, ycbcr, pixel, thumbnail, components, duration, codec |

```python
CATEGORY_ORDER = ["File", "Duplicates", "Dates", "Location", "Device", "Author", "Capture settings", "Other"]
```

### Key capture dates

```python
KEY_FIELDS = {
  "key_dto": "EXIF DateTimeOriginal: when the camera took the photo",
  "key_created": "DateCreated from XMP (photoshop:DateCreated) or IPTC (2:55 + 2:60): the creation date photo apps write",
}
KEY_SOURCE_TAGS = {"ExifIFD:DateTimeOriginal", "XMP:DateCreated", "IPTC:DateCreated"}  # not listed again as raw tags
```

The values of `KEY_FIELDS` are tooltips for the red “!” badge in headers. They are display-only (not
capture-time candidates).

### `catalog(exif_keys: list[str]) -> dict[str, list[Field]]`

Every `CORE` field into its group (CORE order), then `field_for("exif:"+k)` for each `k` in `exif_keys`
(already sorted by `_exif_rank`) except `KEY_SOURCE_TAGS`. Returned as an ordered dict over `CATEGORY_ORDER`,
dropping empty categories. `list(catalog([]))[:3] == ["File", "Duplicates", "Dates"]`; the Dates list starts
`key_dto, key_created`.

`_exif_rank(k) = (0 if any(w in name for w in FOCUS) else 1, k)` with `name = k.split(":",1)[-1]` and
`FOCUS = ("Date","Time","GPS","Make","Model","Serial","Lens","Artist","Author","Copyright","Owner","Software")`.

### `acronym(label) -> str` (slim header text)

Drop ` (…)`; words = regex `[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+`; first letter of each, upper; fallback
`name[:2].upper()`; return `letters[:3]` if ≥ 2 letters else `name[:3].upper()`.
`"OffsetTimeDigitized (ExifIFD)" → "OTD"`, `"Date taken" → "DT"`.

### Meta checks

```python
CHECKS = {"date": ("date", "Dates"), "place": ("loc", "Location"), "device": ("dvc", "Device"), "author": ("aut", "Author")}
```
`meta_counts(tags) -> {"date": n, "place": n, "device": n, "author": n}`: for each raw tag with a value
not in `("", None)`, `exif_category(tag)` mapped back to the check (Dates→date, Location→place, Device→device,
Author→author); other categories are not counted. File-system dates and recommendations never count.

---

## 2. The view (stored in setting `workbench_view`)

```python
VIEW_VERSION = 5
DUP_FILTER = {"key": "dup_count", "op": "gte", "value": "2", "auto": True}
SINGLES = "__singles__"
DEFAULT_VIEW = {
  "v": 5,
  "columns": [["thumb",48],["path",248],["filename",192],["dup_count",64],["dup_conf",144],["checks",240],
              ["key_dto",168],["key_created",168],["date",160],["modify",160],["gps_date",160],["place",112],
              ["device",152],["author",128]],
  "edits": {}, "filters": [DUP_FILTER], "group": "dups", "sources_off": [], "group_width": 232,
  "sort": ["", "asc"], "slim": {}, "ref_ids": [], "pinned": [], "q": "", "colors": True,
}
```

| key | type | meaning |
|---|---|---|
| `v` | int | view schema version (`VIEW_VERSION`) |
| `columns` | `[[key, width], …]` | shown columns in table order; width in px (snapped on render) |
| `edits` | `{target: rule}` | edit rules per target `filename/date/place/device`; each makes an `edit:<target>` column. Empty by default: purple columns only on demand |
| `filters` | `[{key, op, value, auto?}]` | AND-combined filters; `auto: True` marks the chip added by `set_group("dups")` |
| `group` | str | key of `GROUPS`; unknown values are treated as `dups` by `table()` |
| `sources_off` | `[source_id]` | sources left out of the view (Data input checkboxes); files stay in the library |
| `group_width` | int | width of the grouping column, settable via `col_width key=__group`; **not used when rendering** (expander is fixed `TREE_WIDTH`) |
| `sort` | `[key, "asc"|"desc"]` | `""` key = default order |
| `slim` | `{key: bool}` | True = collapsed to `SLIM_WIDTH`, False = kept wide; unset → slim iff the column is empty |
| `ref_ids` | `[file_id]` | files chosen as their group's reference row |
| `pinned` | `[key]` | columns kept at the left (thumbnail is implicitly pinned while first) |
| `q` | str | search over path, filename, new filename |
| `colors` | bool | red/green text colours (appearance; survives `reset`) |

Constants: `SLIM_WIDTH = 40`, `TREE_WIDTH = 40`.

```python
GROUP_COLUMN = {  # grouping → (tree column label, data column whose group-row cell shows the group's name)
  "dups": ("Duplicate set", "filename"), "capture": ("Photo", "filename"), "folder": ("Folder", "path"),
  "day": ("Day", "date"), "device": ("Device", "device"), "place": ("Place", "place"), "author": ("Author", "author")}
```
(`none` has no entry → no tree column.)

### Loading, migrating, saving

- `load_view(lib)`: `json.loads(lib.setting("workbench_view") or "null") or {}` (bad JSON → `{}`), then
  `load_view_from`.
- `load_view_from(v)` (mutates `v`; a missing `v["v"]` counts as 1):
  1. `v < 2`: drop columns starting with `edit:` and set `edits = {}` (v1 opened with default edit columns).
  2. `v < 3`: `set_group(v, v.get("group", "dups"))` (duplicates chip becomes visible).
  3. `v < 5`: remove `key_dto`/`key_created` from columns and re-insert both with `insert_column`, keeping a
     previously stored width else the CORE width (they land first among the date columns).
  4. if `v` non-empty: `v["v"] = VIEW_VERSION`.
  5. Result = deep copy of `DEFAULT_VIEW` updated with **only keys present in `DEFAULT_VIEW`**.
- `save_view(lib, view)`: `lib.set_setting("workbench_view", json.dumps(view))`.
- `set_group(view, group)`: set `group`; drop filters with `auto`; if `group == "dups"` and no remaining
  filter on `dup_count`, insert `dict(DUP_FILTER)` at index 0. (A user-made `dup_count` filter is kept and
  suppresses the chip.)

### Column order helpers

- `snap(width) = max(40, min(904, round(float(width) / 8) * 8))` (8 px grid, ADR-017).
- `column_rank(key) -> tuple`:
  - `edit:<t>` → `column_rank(EDIT_BASE.get(t, t))[:3] + (1,)` (right after the field it edits);
  - `thumb` → `(-1, 0, (0,), 0)` (always first);
  - core → `(CATEGORY_ORDER.index(group), 0, (CORE index,), 0)`;
  - exif → `(cat, 1, _exif_rank(tag), 0)`; unknown → `(len(CATEGORY_ORDER), 1, (key,), 0)`.
- `insert_column(columns, key, width)`: insert `[key, width]` before the first column whose rank is greater;
  else append. Example: into `thumb,path,filename,date,author`: `dims` → after `filename`; `dup_conf` → before
  `date`; `exif:GPS:GPSLatitude` → after `date`, before `author`.

---

## 3. Rows: `build_rows(lib) -> (rows, exif_keys)`

Query (one connection):
```sql
SELECT f.id, f.name, f.original_name, f.rel_path, f.ext, f.kind, f.size, f.mtime, f.source_id, s.label AS source,
       s.writable, s.role AS source_role, c.capture_id, m.dto, m.make, m.model, m.software, m.gps_lat, m.gps_lon,
       m.width, m.height, m.duration, m.modify_date, m.raw_json
FROM files f JOIN sources s ON s.id = f.source_id LEFT JOIN captures c ON c.file_id = f.id
LEFT JOIN meta m ON m.file_id = f.id
WHERE f.present = 1 AND f.removed = 0 AND f.kind IN ('image', 'video')
```
plus all `proposals` (indexed `props[capture_id][field] = row`), `harmonize.effective_values(con)`
(capture → `{capture_time, location_name, device_name, country…}` from accepted/edited proposals, else
current) and `_dup_clusters(con)`. `raw = json.loads(raw_json or "{}")`, `tags = raw.get("tags") or {}`.

### `r["v"]` (every catalog value)

| key | value |
|---|---|
| `path` | `source.label` + `"/" + parent(rel_path)` (no suffix when parent is `.`) |
| `filename` | `f.name` |
| `dup_count` | size of the file's duplicate cluster; 1 when not in any cluster |
| `dup_conf` | `round(score*100)` from dup info, else `None` |
| `date` | `meta.dto` or "" |
| `digitized` | `raw["DateTimeDigitized"]` or "" |
| `modify` | `meta.modify_date` or "" |
| `file_date` | `datetime.fromtimestamp(mtime).strftime(TIME_FMT)` (local) or "" |
| `gps_date` | `raw["GPSDateTime"]` or "" |
| `name_date` | `time_from_filename(original_name or name)` formatted, or "" |
| `key_dto` | `_readable_date(tags["ExifIFD:DateTimeOriginal"])` |
| `key_created` | `_readable_date(tags["XMP:DateCreated"] or tags["IPTC:DateCreated"])` |
| `rec_date` | `proposals[capture_time].value` or "" |
| `place` | `geo.place(lat, lon)["city"]` (only when lat set) or "" |
| `rec_place` | `proposals[location_name].value` or "" |
| `gps` | `f"{lat:.5f}, {lon:.5f}"` or "" |
| `country` | `geo.place(...)["country"]` or "" |
| `device` | `device_name(make, model)` |
| `rec_device` | `proposals[device_name].value` or "" |
| `make`, `model`, `software` | meta columns or "" |
| `serial`, `lens` | `raw["SerialNumber"]`, `raw["LensModel"]` or "" |
| `author`, `copyright`, `owner` | `raw["Artist"]`, `raw["Copyright"]`, `raw["OwnerName"]` or "" |
| `size` | bytes (0 if null) |
| `dims` | `f"{w}×{h}"` or "" |
| `ext` | `ext.upper()` |
| `kind` | `image` / `video` |
| `exif:<tag>` | raw tag value; int/float kept, everything else `str()` |

`_readable_date(raw)`: "" if falsy; `YYYY:MM:DD…` → `YYYY-MM-DD…`; first `T` → space. Stored text otherwise
kept (offsets/subseconds survive).

### Row dict

```python
{"id", "source_id", "capture": capture_id|None, "cluster": cluster_root|None, "dup": dupinfo|None,
 "keeper": bool(dup and dup["keeper"]), "verdict": dup["verdict"]|None,
 "original_name": original_name or name, "ext_raw": ext or "",
 "writable": bool(s.writable) and s.role != "reference",
 "effective": effective_values[capture] or {}, "v": {...}, "tags": tags,
 "pattern": lib.setting("filename_pattern"), "meta_counts": meta_counts(tags)}
```
Later stages add `e`, `src` (apply_edits) and `diff`, `is_ref`, `user_ref`, `dup_explain`, `path_parts`,
`checks` (table). Returned `exif_keys` = union of tag names, sorted by `_exif_rank`.

### Duplicate clusters: `_dup_clusters(con) -> (cluster_of_file, info_of_file)`

```sql
SELECT g.id, g.step, g.verdict, g.score, g.keeper_file_id, gm.file_id, gm.role, gm.score AS mscore
FROM groups g JOIN group_members gm ON gm.group_id = g.id
WHERE g.status = 'open' AND g.step IN (1, 2, 4) AND g.verdict != 'similar_shot'
```
- Union-find (path halving) over `keeper_file_id` ↔ `file_id`; the smaller id becomes the root, so the
  cluster id is the smallest file id. Clusters merge groups from steps 1, 2 and 4.
- Per member: `score = 1.0` if verdict `identical`, else `(g.score if role == keeper else gm.score) or 0`;
  then `same_pixels` → `0.99`; any other non-identical verdict → `min(score, 0.98)` (100 % reserved for
  byte-identical).
- `entry = {"score", "verdict", "keeper": role == "keeper", "step", "group": g.id}`. A file in several groups
  keeps the strongest entry; when replaced, `keeper = new.keeper or (old.keeper and old.step == 1)`.
- `dup_count` = number of files in the cluster (`sizes[root]`).

---

## 4. Edit rules: `apply_edits(rows, edits)`

Rule shapes in `view["edits"][target]`:

| kind | fields | value per row |
|---|---|---|
| `recommended` | — | `v["rec_<t>"] or v[t]` |
| `column` | `column` | `str(v[column] or "")` |
| `fixed` | `value`, optional `from: {fid, key}` | `value` |
| `shift` (date only) | `column` (default `date`), `seconds` (signed int) | `_shift(v[column], seconds)`; "" when the base is empty or unparsable (`TIME_FMT`) |
| `keep` | — | current `v[t]` (a rule that only carries overrides) |
| `pattern` (filename) | `pattern` (falls back to `r["pattern"]`, the library setting) | `harmonize.render_name(...)` |

Common optional fields: `ids` (list → only those files get the rule, the others keep `v[t]` and the
*keep* source); `overrides` (`{str(file_id): value}`, typed per file, always win).

Algorithm per row:
1. For `date`, `place`, `device` with a rule: compute as above (a `shift` on place/device yields nothing).
   `src[t]` = the rule source, or the *keep* source when `kind == keep` or the row is outside `ids`.
2. If `"filename"` in edits: render with `pattern = rule.get("pattern") or r["pattern"]` and
   ```python
   values = {"capture_time": e.get("date", eff.get("capture_time") or v["date"]),
             "location_name": e.get("place", eff.get("location_name") or v["place"]),
             "device_name": e.get("device", eff.get("device_name") or v["device"]),
             "country": eff.get("country") or v["country"], "tags": v}   # {tag:KEY} = any column of this row
   ```
   `e["filename"] = render_name(pattern, values, original_name, ext_raw) or ""` (None → "" = no date);
   rule `kind == "keep"` → `e["filename"] = v["filename"]`.
3. Overrides: for every target, `rule["overrides"][str(id)]` (if not None) replaces `e[t]`, source *typed*.
4. Letters: per target, each distinct source id gets `A`, `B`, … in first-seen row order (`*` after 26);
   `src[t] = {**source, "letter": L}`.
5. `r["e"] = e` (`target → new value`), `r["src"] = src`.

### Sources (`_rule_source(target, rule, names)`; `names = {id: filename}`)

| rule | `id` | `label` | `key` | extra |
|---|---|---|---|---|
| recommended | `rec` | `"<label of rec_t> (best evidence)"` | `rec_<t>` | |
| column | `col:<col>` | `"Copied from <label>"` | col | |
| shift | `shift` | `"<label> shifted ±H:MM:SS"` (`_hms`, `−` for negative) | col | |
| fixed with `from` | `cell:<fid>:<col>` | `"Copied from <label> of <filename>"` | col | `rid: fid` |
| fixed | `fixed` | `"Fixed value <value>"` | | |
| pattern | `pattern` | `"Naming pattern <pattern>"` | | |
| keep / default | `keep` | `"Unchanged: current <label of base>"` | base | |
| typed override | `typed` | `"Typed for this file"` | | `typed: True` |

Note: the web layer re-letters sources in display order (see 10-web-routes `wb_ctx`).

### Cell helpers

- `edit_target(key) -> str | None`: `edit:<t>` → `t`; `place`/`rec_place` → `place`; `device`/`rec_device` →
  `device`; any field of kind `date`, or an `exif:` field in category Dates whose key contains neither
  `Offset` nor `Subsec` → `date`; else `None`. (`filename` returns None; `edit:filename` returns `filename`.)
- `value(r, key)`: `edit:<t>` → `r["e"].get(t, "")`; else `r["v"].get(key, "")`.
- `cell_value(r, key)`: `str(value)` ("" for None); date targets → `_readable_date(val)[:19]`
  (`YYYY-MM-DD HH:MM:SS`).

---

## 5. Grouping, differences, filters, sorting: `table(lib, view) -> (groups, exif_keys, nrows)`

```python
@dataclass
class Group:
    key: object; label: str; rows: list[dict]; ref: dict
    summary: dict = field(default_factory=dict)   # column key → "Same" | "Diff" | "N/A" | ""
```

Pipeline (order matters):
1. `rows, exif_keys = build_rows(lib)`; drop rows whose `source_id` is in `sources_off`.
2. `apply_edits(rows, view["edits"])` (before search, so search sees new filenames).
3. `by = view["group"] if in GROUPS else "dups"`.
4. Search `q` (stripped, lower): keep rows whose path, filename or `e["filename"]` contains it.
5. Bucket by `_group_key(r, by)`:

| by | key |
|---|---|
| dups | `r["cluster"]`, else `SINGLES` |
| capture | `r["capture"]`, else `f"f{id}"` |
| folder | `v["path"]` |
| day | `v["date"][:10]` or `"No date"` |
| device | `v["device"]` or `"Unknown device"` |
| place | `v["place"]` or `"No place"` |
| author | `v["author"]` or `"No author"` |
| none | `"all"` |

6. Rows inside a bucket: with a sort key → `ordered()` (below); else `(not keeper, path.lower(), filename.lower())`.
7. Reference: `chosen` = first row (in that order) whose id is in `ref_ids`; else for `dups` the first keeper
   (else first row); for other groupings the first row.
8. `unrelated = key == SINGLES or by == "none"`. For each row, with `keys = [c[0] for c in view["columns"]]`:
   - `r["diff"][k] = not unrelated and len(rs) > 1 and r is not ref and str(value(r,k)) != str(value(ref,k))`;
   - `r["is_ref"] = r is ref`; `r["user_ref"] = r is chosen`;
   - `by == "dups"`, related, `r["dup"]` → `r["dup_explain"] = explain_diff(r, ref)`;
   - `r["path_parts"] = _path_diff(path, ref_path, r is ref or len(rs) == 1)` → `[(segment, same)]` per `/`
     segment, `same` = equal segment at the same index (all True for the ref or a single row);
   - `r["checks"][t]` for `t in CHECKS`: `None` if both `v[t]` and `ref.v[t]` are empty, else equality as str.
9. Label: `dups`/`capture` → ref filename; `none` → `"All files"`; else `str(key)`; `SINGLES` → `"No duplicates"`.
10. Summary per shown key: `""` when unrelated; `"N/A"` when every row's value is `""`/None; `"Diff"` if any
    row's diff is True; else `"Same"`. If `checks` is shown and related: `"N/A"` if all check states are None,
    `"Diff"` if any False, else `"Same"`.
11. **Filters** (each in order, AND): `by == "none"` → filter rows, drop empty groups; otherwise keep a whole
    group if **any** row matches (a duplicate set keeps its context). Match via
    `_match(value(r, k), op, arg, r["diff"].get(k, False))` — `diff` for a column that is not shown is False.
12. Group order: with a sort key → `ordered(groups, by first row)`; else `dups` → by total size descending;
    else `str(key)` ascending. Finally a stable sort puts `SINGLES` last.
13. `nrows = sum(len(g.rows))`.

Summaries and diffs are computed **before** filters, against the full bucket.

### `_match(val, op, arg, differs)`

| op | true when |
|---|---|
| diff / same | `differs` / `not differs` |
| empty / not_empty | `str(val) == ""` / `!= ""` (None counts as "") |
| contains | `arg.lower() in s.lower()` |
| equals / not_equals | case-insensitive equality / inequality |
| gte / lte | numeric compare if both parse as float, else string compare (ISO dates sort as text) |
| other | True |

### Sorting `ordered(items, row)`

`sk(r) = value(r, sort_key)`, lower-cased if str. Items with `sk` in `("", None)` go last **in both
directions**; the rest sort by `sk`, reversed for `desc`. Applied to rows in each bucket and to groups (by
their first row after row sorting). Mixed int/str values in one column (raw EXIF) are not coerced.

### Confidence text

- `conf_parts(r) -> (pct, why)`: `("", "")` without `dup`; `pct = f"{round(score*100)} %"`; verdict
  `identical` → `why = "identical"`; else `r.get("dup_explain") or VERDICT_TEXT[verdict]`.
- `conf_label(r)`: `"pct · why"` or `pct`.
- `explain_diff(r, ref)`: `"kept copy"` if `r is ref`; compare `EXPLAIN_FIELDS = (date→"date", place→"place",
  device→"device", author→"author", dims→"pixel size", filename→"name")`: filename differs → `"renamed"`;
  ref empty → `"<name> added"`; row empty → `"<name> missing"`; else `"<name> differs"`. None differ →
  `"same metadata"` (or `"same pixels"` for `same_pixels`). At most 3 joined by `", "`, then `" +n"`.

---

## 6. Commit, presets, groups

### `commit(lib, view, ids, only=None) -> int`

1. Fresh `build_rows` + `apply_edits(view["edits"])` (no filters: ids decide).
2. If `only in (None, "filename")` and the filename rule has a `pattern` → `lib.set_setting("filename_pattern", pattern)`.
3. `fields = {"date": "capture_time", "place": "location_name", "device": "device_name"}`, limited to `only`
   (so `only="filename"` writes no proposals and returns 0).
4. For rows in `ids` with a capture: for each target present in `r["e"]`, skip if `(capture, field)` already
   done; if `new` is non-empty and `!= v[target]` → `harmonize.set_status(lib, capture, field, "edited", new)`
   and count it. One write per photo (first copy in build order wins).

### Selection presets

`SELECTION_PRESETS = ("all", "nonkeepers", "dup_newer", "dup_older", "dup_lessmeta")`.
`preset_ids(lib, view, preset)` runs `table()` (filters, search and sources apply; all groups, not one page):
- `all` → every row id; `nonkeepers` → rows with `dup` and not `keeper`.
- Duplicate presets: per `cluster` (rows with `dup`, ≥ 2 members) keep one and tick the rest.
  `tie = (not keeper, path.lower(), filename.lower())`; `_created(r)` = first non-empty of
  `("date", "digitized", "gps_date", "name_date")`; `_richness(r)` = non-empty tags + non-empty of
  `date, digitized, gps, make, model, lens, serial, author, copyright, owner`.
  - `dup_lessmeta`: keep `min(members, key=(-richness, *tie))`;
  - `dup_newer` (keeps oldest) / `dup_older` (keeps newest): among dated members, keep the min-`tie` row with
    the min/max date; no dated member → keep `min(tie)`. Undated copies are therefore always ticked.

`groups_for(lib, ids) -> [group_id]`: distinct open groups of steps 1, 2, 4 containing any of the files
(`IN (NULL)` when empty).

---

## 7. View undo / redo (ADR-018)

Settings `workbench_undo` / `workbench_redo` hold JSON lists of `{"view": <view>, "act": <action>}`;
`HISTORY_MAX = 100`. Only the view is undoable; files change only through plans.

```python
HISTORY_LABELS = {"sort": "sorting", "group": "grouping", "filter_add": "filter", "filter_remove": "filter",
  "q": "search", "colors": "colours", "source_toggle": "folders shown", "reset": "reset",
  "col_add": "columns", "col_remove": "columns", "cols_set": "columns", "cols_order": "columns",
  "col_move": "column order", "col_slim": "column width", "col_width": "column width",
  "edit_set": "edit rule", "edit_clear": "edit rule", "edit_remove": "edit column",
  "cell_set": "edited value", "cell_apply": "applied value", "cell_filter": "filter",
  "cell_ref": "reference row", "col_pin": "pinned column"}
_RELOAD_KEYS = ("group", "q", "sources_off", "colors")   # rendered outside the table → full page reload
```
- `history_push(lib, previous, act) -> True`: append, trim to last 100, clear redo.
- `history_step(lib, "undo"|"redo") -> dict | None`: pop from the source stack (None if empty); push the
  **current** view with the same `act` on the other stack (trimmed); save `load_view_from(entry["view"])`;
  return `{"ok": True, "label": HISTORY_LABELS.get(act, "change"), "reload": any(current[k] != target[k] for k in _RELOAD_KEYS)}`.
- Bad JSON in a stack reads as `[]`.

---

## 8. Naming-pattern parameters and tag source

- `name_fields(catalog, shown) -> [(group, label, token)]`: first `("Columns in this table", label,
  "{tag:<key>}")` for shown columns, then every catalog field by category; excludes `special` fields and
  `edit:` keys. Duplicates between the two parts are intended.
- Module import side effect (last line):
  ```python
  harmonize.set_tag_source(lambda lib: {r["id"]: r["v"] for r in build_rows(lib)[0]})
  ```
  `harmonize.rename_preview` / rename plans call it only when the pattern contains `{tag:`; `{tag:KEY}` and
  `{tag:KEY|%Y}` then read exactly what the table shows. Without the registration, a tag pattern renders
  `None` (files keep their names). Importing `meta_duplo.web.workbench` is required for tag patterns in plans.

---

## Rebuild checklist

1. Define `Field`, `CORE` (33 fields, order and widths above), `CORE_BY_KEY`, `EDIT_LABELS`, `EDIT_BASE`,
   `GROUPS`, `OPS`, `NO_VALUE_OPS`, `DATE_SOURCES`, `VERDICT_TEXT`, `CHECKS`, `KEY_FIELDS`,
   `KEY_SOURCE_TAGS`, `TAG_CATEGORIES`, `CATEGORY_ORDER`, `SLIM_WIDTH = TREE_WIDTH = 40`.
2. Implement `field_for`, `exif_category`, `_exif_rank`, `catalog`, `acronym`, `meta_counts`; check
   `acronym` examples and `meta_counts` on the test tags.
3. Implement `snap`, `column_rank`, `insert_column`; check the insert/sort examples.
4. Implement `DEFAULT_VIEW`, `DUP_FILTER`, `SINGLES`, `set_group`, `load_view`, `load_view_from` (3
   migrations + default fill restricted to known keys), `save_view`.
5. Implement `_dup_clusters` (union-find, score caps 1.0/0.99/0.98, keeper merge rule) and `build_rows` with
   every `v` key; register `harmonize.set_tag_source` at import.
6. Implement `_rule_source`, `_shift`, `_hms`, `apply_edits` (rules, `ids`, overrides, filename rendering via
   `harmonize.render_name`, lettering), `edit_target`, `value`, `cell_value`.
7. Implement `Group`, `_group_key`, `_match`, `_path_diff`, `explain_diff`, `conf_parts`, `conf_label`, and
   `table()` in the exact pipeline order (search → buckets → ref → diffs → summaries → filters → group order →
   singles last).
8. Implement `commit` (pattern setting, one write per capture/field, `only`), `preset_ids`, `groups_for`.
9. Implement `history_push` / `history_step` with `HISTORY_LABELS` and `_RELOAD_KEYS`.
10. Implement `name_fields`.
11. Run `uv run pytest tests/test_workbench.py`.

## Verify

`tests/test_workbench.py` (demo disk via `make_demo`, scanned, proposals built); all 12 pass:

| test | expected |
|---|---|
| `test_duplicates_grouping_and_differences` | group containing *Copy of IMG_0003.JPG* has 3 rows, ref path ends `Camera/DCIM`; the copy has `diff.filename` True, `diff.date` False, `conf_label == "100 % · identical"`; *prague castle.jpg*: `conf_label` starts `99 %`, `diff.date` True, `conf_parts == ("99 %", "date differs, place missing, renamed")`; `acronym` OTD / DT; exif keys include `IFD0:Artist`, `GPS:GPSDateStamp` |
| `test_filters_and_grouping_modes` | `dup_conf ≥ 100` keeps groups where some row has 100; `group=none` + `date empty` keeps *IMG-20190515-WA0003.jpg*, drops *IMG_0001.JPG* |
| `test_edit_rules_commit_and_partial_rename` | default `edits == {}`; shift +3600 s gives `2018-12-24 18:31:12` for *IMG_4102.JPG*; filename starts `2018-12-24 18-31-12 (Canon EOS 5D Mark III)`; `commit` returns 1; `rename_preview` and `build_rename_plan(only_ids=…)` rename only that file |
| `test_old_views_lose_default_edit_columns` | stored v1 view → `edits == {}`, group kept, columns `[["path",200],["key_dto",168],["key_created",168]]` |
| `test_selection_presets` | `all` = nrows; *prague castle.jpg* in newer not older; *IMG_0004.JPG* in older not newer; WhatsApp copy in less-metadata; every group keeps ≥ 1 unticked row |
| `test_duplicates_filter_chip_and_singles_group` | default `filters == [DUP_FILTER]`, no SINGLES group; without filters the last group is SINGLES labelled *No duplicates* with no diffs; `set_group` folder/dups/folder toggles the chip |
| `test_columns_follow_the_picker_order` | insert order `thumb, path, filename, dims, dup_conf, date, exif:GPS:GPSLatitude, author`; rank sort `path, dup_count, date, edit:date, author` |
| `test_meta_check_counts` | `{"date": 2, "place": 1, "device": 2, "author": 1}` |
| `test_key_date_fields_first_and_xmp_date_read` | Dates starts `key_dto, key_created`, raw sources not listed; `key_created` inserted before `date`; v4 view re-orders key dates keeping width 200 |
| `test_cell_actions_scope_overrides_and_reference` | `edit_target` key_dto→date, place→place, lens→None; fixed rule with `ids` + override: other member gets the fixed value, castle gets the typed one, outsiders keep theirs; `ref_ids` makes castle the ref with `user_ref` |
| `test_view_undo_redo` | undo → `{"ok": True, "label": "grouping", "reload": True}`; next undo label *sorting*, `reload` False; third undo None; redo restores sort; a push clears redo |
| `test_name_pattern_with_column_tags` | `{tag:…}` rendering and tidying; `rename_preview` uses table values via the registered tag source |
