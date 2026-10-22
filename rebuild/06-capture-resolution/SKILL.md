---
name: capture-resolution
description: How meta-duplo recommends one capture time, place and device per capture (step 3), pooling evidence from every copy including removed ones, how confidence is computed, how proposals and their statuses are stored and changed, and how effective values feed renaming, step 4 and the Workbench. Use when rebuilding or changing harmonize.py (except file naming), the proposal routes, the step-3 grid cells or Workbench commit.
---

# Capture resolution (step 3 recommendations and decisions)

Code is the source of truth: `meta_duplo/harmonize.py` (recommendations, proposals, `effective_values`,
`set_status`, `accept_above`; file naming is skill 07), consumers in `meta_duplo/web/rename_grid.py`,
`partials/grid_row.html`, `meta_duplo/web/workbench.py`, `meta_duplo/match.py::step4_recheck`, `plans.py`.
Domain background: `skills/media-capture-date-resolver`, `media-location-resolver`, `media-device-resolver`,
`media-context-resolver`, `media-confidence-scoring`. The code implements a small, explainable subset of those
skills (POC); the rest is listed at the end.

## 1. Concepts

| Term | Meaning in code |
|---|---|
| capture | a set of files that are the same photo/video: `captures(file_id PK, capture_id)` built by `match.build_captures` from step-1 and step-2 duplicate groups (not `not_duplicate` / `ignored`, not `similar_shot`) plus done `hold` actions; `capture_id` = smallest file id in the set (skill 05 §7) |
| member | every image/video file of the capture that is `present = 1`, **including removed copies** (`load_files(con, include_removed=True)`) |
| keeper | `max(present non-removed members, key=keeper_key)`, or of all members if every copy is removed |
| field | one of `FIELDS = ("capture_time", "location_name", "device_name")`; labels `FIELD_LABELS = {"capture_time": "Date & time", "location_name": "Place", "device_name": "Device"}` |
| proposal | one row per `(capture_id, field)`: current value, recommended value, confidence, source, explanation, status |
| origin | independence group of a time candidate: `camera_clock`, `container`, `filename`, `filesystem` |

Values are strings: time `'YYYY-MM-DD HH:MM:SS'` (`metadata.TIME_FMT`, naive local), place = town name,
device = normalized name (`metadata.device_name`). Empty string = unknown.

## 2. Storage

```sql
CREATE TABLE proposals (
  capture_id INTEGER NOT NULL,
  field TEXT NOT NULL,                        -- capture_time | location_name | device_name
  current TEXT,                               -- the keeper's own value now
  value TEXT,                                 -- recommended value, or the user's typed value (edited)
  extra TEXT,                                 -- JSON; location: {"lat","lon","city","country"}
  source TEXT,                                -- e.g. "metadata of IMG_0002.JPG", "GPS of IMG_0003.JPG"
  confidence REAL,
  status TEXT NOT NULL DEFAULT 'suggested',   -- suggested | accepted | edited | rejected
  explanation TEXT,
  PRIMARY KEY (capture_id, field)
);
```

`country` is **not** a proposal field: it comes from the place proposal's `extra.country`.

## 3. Inputs the resolver reads

From `match.F` (skill 05 §2): `dto` (already a naive local time; videos' QuickTime UTC was converted to the
`home_timezone` zone per timestamp with `zoneinfo` during extraction, skill 04), `kind`, `name`,
`original_name` (first name ever seen), `mtime`, `gps_lat/gps_lon`, `device` (`device_name(make, model)`),
`reencoded()`, `keeper_key`.

Helpers from `metadata.py`:
- `time_from_filename(name) -> (datetime | None, precision)` with precision `"second"`, `"day"` or `""`.
  Order: WhatsApp `(?:IMG|VID|AUD|PTT)-(YYYYMMDD)-WA\d+` → `"day"`; `_FN_FULL`
  (`YYYY[-_.]?MM[-_.]?DD[ _\-T.]{0,3}HH[-_.:h]?MM[-_.:m]?SS`) → `"second"`; `_FN_DATE` (`YYYY[-_.]?MM[-_.]?DD`)
  → `"day"`. Calendar-invalid values are rejected (ValueError caught), never fatal.
- `valid_time(dt)` rejects: year < 1826 or future; 1904-01-01, 1970-01-01, 1980-01-01; Jan 1 00:00–00:10 of
  2000–2012 (factory defaults).
- `geo.place(lat, lon) -> {"city", "country", "region"}` (offline `reverse_geocoder`, rounded to 4 decimals,
  `lru_cache`); `{}` when the optional `geo` extra is missing (`geo.available()`).

## 4. Recommendations per field

### 4.1 Date & time: `recommend_time(members) -> dict`

Candidates per member (`_time_candidates(f)`):

| Candidate | Tier | Origin | Label |
|---|---|---|---|
| `f.dto` | 1 | `camera_clock` (image) / `container` (video) | `metadata of {name}` |
| `time_from_filename` of `original_name` then `name` (deduplicated), precision `second` | 2 | `filename` | `file name {name}` |
| same, precision `day` (time `00:00:00`) | 3 | `filename` | `file name {name} (date only)` |
| `datetime.fromtimestamp(f.mtime)` | 4 | `filesystem` | `file date of {name}` |

Algorithm:

```python
if not cands: return {"value": "", "confidence": 0.0, "source": "", "explanation": "No date found in any copy."}
best_tier = min(tiers)
top   = sorted(candidates of best_tier, key=time)       # string sort = chronological
pick  = top[0]                                           # earliest: copies and edits only add later times
spread = seconds(top[0], top[-1])
agree = {c.origin for c in cands if c.tier <= 2 and c.origin != pick.origin and |c.t - pick.t| <= 2 s}
conf  = {1: 0.93, 2: 0.75, 3: 0.5, 4: 0.25}[best_tier]
if agree:                    conf = min(0.98, conf + 0.05)     # "Confirmed by filename."
if best_tier <= 2 and spread > 2: conf = min(conf, 0.7)        # "Copies disagree by N s; the earliest is used."
round(conf, 2)
```

Explanation = `"From {pick.label}."` + optional `"Confirmed by {origins}."` (underscores → spaces) +
`"Copies disagree by {int(spread)} s; the earliest is used."` + tier 3 `"Only the day is known; time set to
00:00:00."` + tier 4 `"Only the file system date is known; it often shows the copy date, not the capture."`.
`source = pick.label`.

Independence rule (CLAUDE.md hard rule 4) as implemented: agreement only counts from an origin **different** from
the pick's, and only tiers 1–2; copies of the same field (two EXIF copies, two file systems) never add votes.
The filesystem (tier 4) never confirms.

### 4.2 Place: `recommend_place(members, keeper) -> dict`

```
with_gps = members with gps_lat
none            → value "", conf 0.0, extra {}, "No GPS in any copy. Type a place to use it in the file name."
src = keeper if keeper has GPS else max(with_gps, key=keeper_key)
p = geo.place(src.gps_lat, src.gps_lon); name = p["city"]
extra = {"lat", "lon", "city": name, "country": p["country"]}
no name (geo missing) → value "", conf 0.3, source "GPS of {src}",
                        "GPS found, but offline place names are not installed (`uv sync --extra geo`)."
else → value name, conf 0.9 (keeper's own GPS) / 0.85 (another copy), source "GPS of {src.name}",
       "Nearest town to the GPS position in {src.name} ({lat:.4f}, {lon:.4f})." [+ " The kept copy has no GPS; it comes from another copy."]
```

### 4.3 Device: `recommend_device(members, keeper) -> dict`

```
named = [(f, f.device) for members with a device]
none → value "", conf 0.0, "No camera make or model in any copy." [+ " It looks like a messenger copy." if any reencoded]
if keeper.device: src, name = keeper, keeper.device
else: name = most common device among non-re-encoded named copies (or among all if none), src = first copy with it
conf = 0.9 if all named copies agree else 0.6
explanation "Make and model in {src.name}." [+ " Copies disagree: A, B."]; source "metadata of {src.name}"
```

### 4.4 Current values: `_current(keeper)`

`{"capture_time": keeper.dto or "", "location_name": geo city of the keeper's GPS or "", "device_name": keeper.device}`.

## 5. Building proposals: `build_proposals(lib, job=None) -> int`

1. `build_captures(lib)` (keeps captures in sync with steps 1–2).
2. Load members (`load_files(con, include_removed=True)`) per capture; `old = {(capture_id, field): row}`.
3. Per capture: keeper, `cur = _current(keeper)`, the three recommendations. Per field:

```python
status, value = "suggested", rec["value"]
if prev and prev.status in ("accepted", "edited", "rejected"):
    status = prev.status
    if status == "edited": value = prev.value            # the typed value survives
if status == "suggested" and value == cur[field] and (rec["confidence"] >= 0.9 or not value):
    status = "accepted"                                   # nothing to change, nothing to doubt
row = (cid, field, cur[field], value, json.dumps(rec.get("extra") or {}), rec["source"],
       rec["confidence"], status, rec["explanation"])
```

4. In one `tx`: `DELETE FROM proposals`, insert all rows. Progress `job.update("step 3: recommendations", i, n)`
   every 100 captures; final message `f"{n} captures"`. Returns the capture count.

Triggers: `POST /harmonize` (job "recommend"), `POST /recheck` (before step 4), every Workbench rescan
(`_rescan`: `scan_all` + `build_proposals`), and tests.

## 6. Statuses and decisions

| Status | Set by | Effective value (`effective_values`) | Grid cell |
|---|---|---|---|
| `suggested` | default; `reset` | `current` | `now → new` + confidence + ✓ / ✗ when the value differs from current |
| `accepted` | ✓ (`accept`), `accept_above`, auto-accept in `build_proposals` | `value` | struck-through now → bold new, ↺ |
| `edited` | double-click + Enter (`edit`), Workbench `commit` | `value` (typed) | as accepted, accent colour, ↺ |
| `rejected` | ✗ "Keep current" (`reject`) | `current` | current value, ↺ to show the recommendation again |

API:

```python
def set_status(lib, capture_id: int, field: str, status: str, value: str | None = None) -> None
    # UPDATE proposals SET status = ?[, value = ?] WHERE capture_id = ? AND field = ?   (no validation)

def accept_above(lib, min_conf: float, field: str | None = None) -> int
    # UPDATE proposals SET status = 'accepted'
    # WHERE status = 'suggested' AND confidence >= ? AND value != '' [AND field = ?]   → rowcount
```

Routes (`app.py`):

| Route | Form | Effect | Response |
|---|---|---|---|
| `POST /proposals/{cid}/{field}` | `action` ∈ `accept`/`reject`/`reset`/`edit`, `value` | `edit` → `set_status(…, "edited", value.strip())`; others map to `accepted` / `rejected` / `suggested` | `partials/grid_oob.html`: every row of the capture re-rendered out-of-band |
| `POST /proposals/accept` | `min_conf`, `field?` | `accept_above` | 204, `HX-Trigger {"grid-reload": true, "toast": "Accepted n recommendations"}` |
| `POST /harmonize` | — | background job `build_proposals` | `partials/job.html` |

Step-3 page buttons: *Accept all ≥ 90 %* (`min_conf=0.9`) and *Accept all ≥ 70 %* (`min_conf=0.7`). Nav count
`step3 = COUNT(DISTINCT capture_id) FROM proposals WHERE status = 'suggested'`; `captures` = all.

## 7. Effective values: `effective_values(con) -> dict[capture_id, dict]`

```python
for r in proposals:
    use = r.value if r.status in ("accepted", "edited") else r.current
    out[r.capture_id][r.field] = use or ""
    if r.field == "location_name":
        extra = json.loads(r.extra or "{}")
        if r.status in ("accepted", "edited") and extra.get("lat") is not None:
            out[r.capture_id].update(lat=extra["lat"], lon=extra["lon"], country=extra.get("country", ""))
```

Consumers: `harmonize.rename_preview` and `plans.build_rename_plan` (names, XMP sidecar values, set mtime;
skill 07/08), `match.step4_recheck` (time, device, place, lat/lon), `results._member_rows` (step 4 shows these
values), Workbench rows (`r["effective"]`).

## 8. Step-3 rename grid (`rename_grid.py`, `partials/grid_row.html`)

`rename_grid.build_rows(lib)` joins every present, non-removed file with its capture and the capture's
proposals. Field columns `date` / `place` / `device` (`Column(kind="field", field=…)`) get

```python
{"now": current or "", "new": value or "", "status": status,
 "changed": bool(value) and value != (current or ""),
 "confidence", "why": explanation, "capture": capture_id, "field": field}
```

A row is `pending` ("Needs decision") when any field is `suggested` and `changed`. Column `confidence` = the
capture_time proposal's confidence. Cells: suggested+changed → `now → new`, `pct`, ✓ (`accept`) and ✗
(`reject`); accepted/edited+changed → ↺ (`reset`); rejected → current + ↺; double-click opens an inline form
(`action=edit`; for `capture_time` the input has `pattern="\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"`). The
explanation is the cell tooltip. Every copy of the capture updates together (out-of-band swap).

## 9. Workbench consumption (`web/workbench.py`, skill 09)

- `build_rows` loads all proposals per capture and `effective_values`. Per row:
  `v["rec_date"]`, `v["rec_place"]`, `v["rec_device"]` = the proposal **value regardless of status** (or `""`);
  `r["effective"]` = the capture's effective dict; `r["capture"]` = capture id.
- Catalog fields `rec_date` ("Recommended date", Dates), `rec_place` ("Recommended place", Location),
  `rec_device` ("Recommended device", Device); `rec_date` is in `DATE_SOURCES` (shift source);
  `edit_target("rec_place") = "place"`, `edit_target("rec_device") = "device"`, date-kind keys → `"date"`.
  The preview panel (`partials/wb_preview.html`) lists them as *Recommended*.
- Edit rule `kind "recommended"`: `e[target] = v["rec_" + target] or v[target]` (targets `date`, `place`, `device`).
- FileName ✎ preview: `capture_time = e.get("date") or effective.capture_time or v.date`, likewise place and
  device; `country = effective.country or v.country`.
- `commit(lib, view, ids, only=None) -> int` ("Save as decisions"): recomputes rows and edits; if `only` is
  `None` or `"filename"` and the filename rule has a pattern, saves it to setting `filename_pattern`. For each
  selected row with a capture and each target in `{"date": "capture_time", "place": "location_name",
  "device": "device_name"}` (or just `only`): if `new = e[target]` is non-empty and differs from the row's own
  current `v[target]`, `harmonize.set_status(lib, capture, field, "edited", new)` once per `(capture, field)`;
  returns the number written. It never writes `accepted` or `rejected`.
- Routes: `POST /wb/commit` (`ids`, `target`, `all`) → toast "Saved n values as decisions" / "Saved the filename
  pattern; review the rename plan to apply it"; `POST /wb/rename-plan` commits the selection first, then
  `build_rename_plan(lib, write_xmp, only_ids=chosen)`.

## 10. Demo outcomes (after a scan and `build_proposals`, geo extra installed)

| Capture (files) | capture_time | location_name | device_name |
|---|---|---|---|
| IMG_0002 + backup + `IMG-20190515-WA0003.jpg` | `2019-05-14 19:05:41`, 0.93, accepted (the WhatsApp copy gets the original's EXIF time, not its receive date) | Bratislava 0.9 accepted | Apple iPhone 12 0.9 accepted |
| IMG_0004 + `prague castle.jpg` | `2019-06-02 10:15:30`, **0.7, suggested**, "Copies disagree by 81870 s; the earliest is used." | Prague 0.9 | Apple iPhone 12 0.9 |
| IMG_0020.MOV + `beach video.MOV` + `VID-20190721-WA0001.mp4` | `2019-07-20 16:05:00` 0.93 | `""` 0.0 accepted ("No GPS in any copy…") | Apple iPhone 12 0.9 |
| `IMG_20190518_114710_crop.jpg` alone | current `""` → `2019-05-18 11:47:10`, **0.75, suggested**, "From file name IMG_20190518_114710_crop.jpg." | `""` 0.0 accepted | `""` 0.0 accepted |
| IMG_4101 + `christmas.jpg` | `2018-12-24 17:30:00` 0.93 | `""` 0.0 | Canon EOS 5D Mark III 0.9 |

After `accept_above(lib, 0.7)` the crop's date is accepted, `IMG_0001.JPG` renders as
`2019-05-14 18-22-03 (Bratislava, Apple iPhone 12) - IMG_0001.jpg`, and step 4 links the crop to `IMG_0003.JPG`.

## 11. Specified, not implemented yet (from the resolver skills)

Time (`media-capture-date-resolver`):
- A `candidates` table with raw value, UTC / local-naive / tz offset, clock, tier, weight, kind
  (`point` / `upper_bound` / `lower_bound`), `valid` + `reject_reason`, and the rejected alternatives per result.
- Full source catalogue (CreateDate, GPS UTC, MakerNotes, Keys:CreationDate with offset, XMP/IPTC, sidecars,
  Takeout JSON, Photos.sqlite…); ExifTool extraction keyed by `SourceFile` (milestone M1, ADR-013).
- Bounds: WhatsApp / upload / export names as **upper bounds** (today the WhatsApp date is a tier-3 point);
  model release dates as lower bounds; bound filtering.
- Tier weights 10/6/3/bound/1 with modifiers, agglomeration within ±2 s, one vote per clock, margin-based
  high/medium/low; timezone reconciliation (naive vs UTC, `tz_status`).
- Camera clock correction per body and session, QuickTime UTC/local convention per model, drift.
- Gap filling: counter sequences, folder coherence, import batches, derivation order, content cues
  (`media-context-resolver`, `context_evidence` rows, interval propagation).
- Factory-default rule "same timestamp on > 20 files from different devices".

Place (`media-location-resolver`): points with accuracy radius; validation (Null Island, ref/sign repair, datum,
GCJ-02, stale fix, teleport, placeholders); duplicate-cluster conflict check; track and location-history
matching; session fill (WORKFLOW idea 2); text geocoding of folders / IPTC; overlap-group scoring with origins;
timezone from coordinates (`timezonefinder`).

Device (`media-device-resolver`): brand/model/unit/owner levels, serials, structural fingerprint, PRNU, origin
type classification (screenshot, scan, messenger, edit, AI), alias tables (Android model codes), propagation by
session, tiered scoring 10/7/4/2/1.

Confidence (`media-confidence-scoring`): per-tolerance-level probabilities (±1 min, same day…), log-odds
combination with dependence discount κ, caps at 99.5 %, monotonicity, calibration (masking tests, isotonic
regression, ECE), purpose-specific overall scores, bands driving actions. Today confidences are fixed POC numbers;
the `band()` filter (`verified ≥ 0.98`, `high ≥ 0.9`, `medium ≥ 0.7`, `low ≥ 0.4`) is display only.

Workflow ideas: step-4 *Same capture* merges captures (inherit place/device); bulk edit by folder/event;
impact preview; calibration from accepted/rejected decisions.

## 12. Code/doc inconsistencies to keep in mind

- WORKFLOW says a value equal to the current one but below 90 % shows *keep as is?* with a **Confirm** button;
  the grid shows no buttons when `changed` is false, and such rows are not `pending` (they still count in the
  nav's `step3` count).
- A previously `accepted` proposal keeps `accepted` on rebuild but takes the **new** recommended value, so a
  changed recommendation (or an auto-accepted one) can apply without a new decision. Decisions are keyed by
  `capture_id` (min file id), so they move or vanish when captures merge or split.
- `reset` on an `edited` row keeps the typed value as a `suggested` value until the next rebuild.
- An `edited` place keeps the recommendation's `lat`/`lon`/`country` in `extra`, so effective coordinates and
  country can contradict the typed town (step 4 then compares by coordinates).
- `filesystem` candidates use `datetime.fromtimestamp` (machine zone), not the `home_timezone` setting.
- Workbench `commit` turns a recommended value into `edited`, never `accepted`; step 3 and the Workbench share
  the same rows.

## Rebuild checklist

1. Create `proposals` (PK `capture_id, field`) and make sure `captures` is filled by `build_captures` (skill 05).
2. Implement `_time_candidates` and `recommend_time` with the tier table, origins, earliest-pick, `+0.05`
   agreement (cap 0.98), disagreement cap 0.7 and the exact explanation strings.
3. Implement `recommend_place` (keeper GPS first, `geo.place`, `extra` JSON, 0.9 / 0.85 / 0.3 / 0.0) and
   `recommend_device` (keeper first, majority of originals, 0.9 / 0.6).
4. Implement `_current` and `build_proposals` with status carry-over and the auto-accept rule; one transaction.
5. Implement `effective_values`, `set_status`, `accept_above`.
6. Wire `POST /harmonize`, `POST /proposals/{cid}/{field}`, `POST /proposals/accept`, nav counts.
7. Rename grid field cells (`rename_grid.build_rows` fields dict, `pending`, `grid_row.html` buttons, OOB update).
8. Workbench: `rec_*` values, `effective`, edit kind `recommended`, `commit` writing `edited`, `/wb/commit`,
   `/wb/rename-plan`.
9. Make `step4_recheck`, `rename_preview` and `build_rename_plan` read `effective_values`.

## Verify

- `tests/test_workflow.py::test_steps_3_and_4_and_plans`: after the step-1 cleanup (removed copies still count),
  the WhatsApp copy's `capture_time` value is `2019-05-14 19:05:41`; the crop's is `2019-05-18 11:47:10` with
  status `suggested`; after `accept_above(lib, 0.7)` the names `2019-05-14 18-22-03 (Bratislava, Apple iPhone
  12) - IMG_0001.jpg` and `2018-12-24 17-31-12 (Canon EOS 5D Mark III) - IMG_4102.jpg`; step 4 finds the crop.
- `tests/test_workflow.py::test_excluded_files_keep_their_name`: proposals + `accept_above(0.7)`; an excluded
  file keeps its name and gets no action.
- `tests/test_workbench.py::test_edit_rules_commit_and_partial_rename`: a date shift of +3600 s on `IMG_4102.JPG`
  gives `2018-12-24 18:31:12`; `wb.commit(...) == 1` (an `edited` proposal); `rename_preview` then starts with
  `2018-12-24 18-31-12`; the rename plan touches only that file.
- `tests/test_workbench.py::test_cell_actions_scope_overrides_and_reference` and `test_selection_presets`: edit
  rules and presets over rows that carry `rec_*` and `effective`.
- Demo by hand: `uv sync --extra geo`, `uv run meta-duplo demo`, `uv run meta-duplo serve`, open `/step/3`: only the
  crop shows *Needs decision* (its value changes); the Prague capture (0.7, value = current) is `suggested` but shows
  no buttons. *Accept all ≥ 70 %* toasts "Accepted 2 recommendations" (the crop's date and the Prague date).
  Without the geo extra, places are empty with confidence 0.3 and the "not installed" explanation.

## Copied decisions (update)

A date cell dropped on Recommended date in the Workbench (`rec_drop`) sets the capture_time proposal to `edited`
with that value, `source = "copied from <column> of <file>"` and `extra = {"from": {"fid", "key"}}`
(`harmonize.set_copied`). `build_proposals` keeps an edited row's value and source, and for capture_time its
`extra`, so the Workbench can still point at the source cell after a rebuild. `proposal_state` /
`restore_proposals` read and write `status, value, source, extra` for undo.

