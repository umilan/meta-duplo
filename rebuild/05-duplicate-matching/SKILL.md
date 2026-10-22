---
name: duplicate-matching
description: How meta-duplo finds and stores duplicates in workflow steps 1 (identical), 2 (near duplicates, similar shots) and 4 (re-check on harmonized values), chooses keepers, builds captures, and serves the results pages with server-side selection that hands off to a cleanup plan. Use when rebuilding or changing match.py, web/results.py, the step 1/2/4 routes or their templates.
---

# Duplicate matching (steps 1, 2 and 4)

Code is the source of truth. Files: `meta_duplo/match.py` (engine), `meta_duplo/web/results.py` (read model
and selection), step 1/2/4 routes in `meta_duplo/web/app.py`, templates `results.html`, `compare.html`,
`partials/results_group.html`, `partials/results_rows.html`, `partials/selection_bar.html`,
`partials/preview.html`. Domain background: `skills/duplicate-finder/SKILL.md` (only partly implemented, see
the last sections).

## 1. Where it sits in the pipeline

```
scan_all(lib, job):  inventory → extract → hash_stage → fingerprint → match.step1_exact → match.step2_near
POST /harmonize:     harmonize.build_proposals            (calls match.build_captures first)
POST /recheck:       harmonize.build_proposals → match.step4_recheck
```

Inputs produced earlier (skills 03/04):

| Table.column | Meaning used here |
|---|---|
| `hashes.full` | BLAKE3 of the whole file, only for files whose size + partial hash collide |
| `hashes.content` | BLAKE3 of `f"{w}x{h}"` + decoded RGB bytes after `ImageOps.exif_transpose` (images only) |
| `fingerprints.dhash`, `.phash` | 64-bit hex (`imagehash.dhash` / `phash`); `NULL` for decode errors and degenerate hashes (all 0 / all 1) |
| `meta.dto` | capture time, naive local `'YYYY-MM-DD HH:MM:SS'` |
| `meta.make/model/software/width/height/duration/gps_lat/gps_lon` | as read |
| `sources.role` | `'scan'` or `'reference'` |
| `files.removed` | 1 = moved to `_duplicates/` by a plan, still evidence |

Settings (table `settings`, defaults in `db.DEFAULT_SETTINGS`, editable on `/settings`):
`near_threshold = "0.85"`, `recheck_min_confidence = "0.5"`.

## 2. The file record `F` and its helpers (`match.py`)

`load_files(con, include_removed=False) -> list[F]` runs `FILE_SQL`:

```sql
SELECT f.id, f.source_id, f.rel_path, f.name, f.original_name, f.ext, f.kind, f.size, f.mtime, f.inode,
       s.role, f.removed, m.width, m.height, m.duration, m.make, m.model, m.software, m.dto,
       m.gps_lat, m.gps_lon, h.full, h.content, p.dhash, p.phash
FROM files f JOIN sources s ON s.id = f.source_id
LEFT JOIN meta m ON m.file_id = f.id LEFT JOIN hashes h ON h.file_id = f.id
LEFT JOIN fingerprints p ON p.file_id = f.id
WHERE f.kind IN ('image', 'video') AND f.present = 1
-- + " AND f.removed = 0" unless include_removed
```

`dhash`/`phash` are converted with `int(x, 16)`. `F` is a `@dataclass` with exactly those 25 fields plus:

| Member | Definition |
|---|---|
| `device` (property) | `metadata.device_name(make, model)` (normalized, e.g. `Apple iPhone 12`, `Canon EOS 5D Mark III`) |
| `pixels` | `(width or 0) * (height or 0)` |
| `key` | `content or full or f"file:{id}"`: content identity for decisions (survives renames) |
| `reencoded()` | `software.lower()` contains any of `REENCODERS = ("whatsapp","facebook","instagram","telegram","messenger","snapseed")`, or `"-WA"` in `name.upper()` |
| `in_copy_folder()` | `rel_path.lower()` contains any of `COPY_WORDS = ("backup","copy","export","download","whatsapp","telegram","edited","kopie","zaloha")`, or name starts with `"copy of"` |

Other constants and helpers:

```python
DUP_VERDICTS = {"identical", "same_pixels", "same_photo", "edited", "same_video"}
_ts(s)          # datetime.strptime(s, "%Y-%m-%d %H:%M:%S") or None
_ham(a, b)      # (a ^ b).bit_count(), None if either is None
_stem(name)     # PurePath(name).stem.lower() minus ONE trailing suffix of
                # (" copy","-edited","_edited"," (1)"," (2)"," (3)","_1","_2","-1","-2")
pair_subject(a, b) = "|".join(sorted([a.key, b.key]))
_known_avg(parts: list[tuple[weight, signal|None]])  # weighted mean over known signals; 0.0 if none
```

`_known_avg` is the key scoring idea: an unknown signal (missing date, missing device) neither helps nor hurts.

## 3. Keeper selection: `keeper_key(f) -> tuple`

The best file has the **largest** tuple (`max(..., key=keeper_key)`, or sort `reverse=True`):

| # | Component | Prefers |
|---|---|---|
| 1 | `f.role == "reference"` | file in a reference (protected) source |
| 2 | `not f.reencoded()` | original over messenger / social copy |
| 3 | `f.dto is not None` | has a capture time |
| 4 | `f.pixels` | more pixels |
| 5 | `f.size or 0` | larger file |
| 6 | `f.gps_lat is not None` | has GPS |
| 7 | `not f.in_copy_folder()` | not in backup / export / download / `Edited` folder, not "Copy of …" |
| 8 | `-(f.mtime or 0)` | older file date |
| 9 | `-len(f.rel_path)` | shorter path |
| 10 | `-f.id` | lower id (deterministic tie-break) |

Demo: the 3-file group `Camera/DCIM/IMG_0003.JPG`, `Backup 2019/IMG_0003.JPG`, `Backup 2019/Copy of IMG_0003.JPG`
keeps `Camera/DCIM/IMG_0003.JPG` (component 7).

## 4. Step 1: identical files — `step1_exact(lib, job=None) -> int`

1. `files = load_files(con)`; `_, ignored = _decided(con)`.
2. Bucket files by `full` (files without `full` never take part: their size or partial hash was unique).
3. Collapse hardlinks: `uniq.setdefault((f.source_id, f.inode) if f.inode else ("id", f.id), f)`; drop buckets with < 2.
4. `keeper = max(members, key=keeper_key)`; `sig = ",".join(sorted member ids)`; skip if `sig in ignored`.
5. Group dict: `verdict "identical"`, `score 1.0`, members sorted by `keeper_key` desc, each with
   `score 1.0`, `evidence {"full_hash": full[:16]}`.
6. `with tx(con): _save_groups(con, 1, groups)`; job message `f"{n} identical groups"`.

Pair decisions (`not_duplicate`) are **not** consulted in step 1.

## 5. Step 2: near duplicates — `step2_near(lib, job=None) -> int`

### 5.1 Representatives and candidates
- `threshold = float(lib.setting("near_threshold"))` (0.85).
- `reps` = all loaded files except **non-keepers of step-1 groups** (any status): one representative per
  identical group, i.e. the step-1 keeper (a keeper changed with *Keep this one* becomes the representative).
- `_candidate_pairs(reps) -> set[(min_id, max_id)]`:
  - images: if `len(imgs) <= 2500` → **all pairs** (`itertools.combinations`); else buckets keyed by
    `("c", content)` and, for `dhash` and `phash`, 4 chunks `("d"|"p", i, (h >> 16*i) & 0xFFFF)`; every bucket
    with `1 < len <= 200` contributes all its pairs (multi-index hashing: guarantees pairs within Hamming ≤ 3).
  - videos: sorted by duration, sweep; pair while `b.duration - a.duration <= max(1.0, 0.01 * b.duration)`.
- Skip pairs whose `pair_subject` is in the `not_duplicate` set. Progress: `job.update("step 2: comparing", i, n)` every 2000.

### 5.2 Pair scoring: `compare(a, b) -> (score, verdict | None, evidence)`

Evidence keys: `time_delta_s`, `same_device`, then (videos) `duration`, (images) `visual`, `dhash_dist`,
`phash_dist`, `size_ratio`, `same_aspect`, optional `same_name`.

```
dt  = |a.dto - b.dto| seconds, or None if either has no dto
dev = 1.0 same device name / 0.0 different / None if either is unknown
```

Rules, evaluated in this order (first return wins):

| # | Condition | Result |
|---|---|---|
| 1 | either is video: kinds differ or a duration missing | `(0.0, None)` |
| 2 | video: `same_len = |Δduration| <= max(1.0, 1 % of longer)`; if `same_len and dt is not None and dt <= 2 and dev != 0.0` | `(0.9 if dev else 0.85, "same_video")`, else `(0.0, None)` |
| 3 | images with equal non-null `content` | `(0.99, "same_pixels")`, evidence `visual 1.0` |
| 4 | either `dhash` or `phash` missing | `(0.0, None)` |
| — | compute `dist = min(dhash_dist, phash_dist)`, `visual = 1 - dist/64`; `geometry = 1.0` if aspect ratios differ ≤ 2 % (relative) else `0.0`, `None` without dimensions; `size_ratio = min(pixels)/max(pixels)`; `tsig = None if dt is None else (1.0 if dt == 0 else exp(-dt/5))` | |
| — | `score = _known_avg([(0.6, visual), (0.2, tsig), (0.1, dev), (0.1, geometry)])`; `+0.03` if `_stem(a.name) == _stem(b.name)` (`same_name: True`); `min(score, 1.0)` | |
| 5 | `dev == 0.0 and dt is not None and dt > 60` (two people, same subject) | `(min(score, 0.7), None)` |
| 6 | `dt > 0 and dev == 1.0` and both camera originals (`not reencoded()`) | burst: `"similar_shot"` if `visual >= 0.80 and dt <= 10`, else `None` — never a duplicate |
| 7 | `visual >= 0.97 and (dt is None or dt <= 2)` | `"same_photo"` |
| 8 | `visual >= 0.90 and dt is not None and dt <= 2 and geometry == 0.0` | `"edited"` |
| 9 | `visual >= 0.80 and dt is not None and 0 < dt <= 10 and dev == 1.0` | `"similar_shot"` (only reachable when one side is re-encoded) |
| 10 | `visual >= 0.94 and dt is None` | `"same_photo"` |
| — | otherwise | `(round(score, 3), None)` |

Score is returned as `round(score, 3)`. Hamming equivalents: `visual ≥ 0.97` ⇔ dist ≤ 1; `≥ 0.94` ⇔ ≤ 3;
`≥ 0.90` ⇔ ≤ 6; `≥ 0.80` ⇔ ≤ 12. (Rule 7 contains a dead inner check `if dt is None and visual < 0.94`.)

Only pairs **with a verdict** become edges: `edges[(a, b)] = (score, verdict, evidence)`.

### 5.3 Grouping: `_anchor_groups(files, edges, accept, ignored) -> list[dict]`

Deterministic anchor grouping, no chaining (A≈B, B≈C never pulls C into A's group):

1. Build neighbour lists from edges where `accept(edge)` is true.
2. Visit files that have neighbours in `keeper_key` order, best first. Skip files already assigned.
3. Mates = unassigned neighbours of the anchor. Members = anchor (`score 1.0`, `evidence {}`) + mates sorted by
   edge score desc (`score = edge score`, `evidence = edge evidence`).
4. `sig` = sorted member ids joined by `,`; if `sig in ignored` → skip (members stay free for other anchors).
5. Mark members assigned. Group: `keeper = anchor`, `verdict = most frequent mate verdict`
   (`max(set(v), key=v.count)`), `score = round(min(mate edge scores), 3)`.

Step 2 runs it twice:

```python
dup    = _anchor_groups(reps, edges, lambda e: e[1] in DUP_VERDICTS and e[0] >= threshold, ignored)
in_dup = ids of non-keeper members of dup groups
bursts = _anchor_groups([f for f in reps if f.id not in in_dup],
                        {k: v for k, v in edges.items() if neither end in in_dup},
                        lambda e: e[1] == "similar_shot", ignored)   # any score
for g in bursts: g["verdict"] = "similar_shot"
_save_groups(con, 2, dup + bursts)
```

Job message: `f"{len(dup)} near-duplicate groups, {len(bursts)} similar-shot groups"`; returns `len(dup)`.

### 5.4 Verdicts (all steps)

| Verdict | Step | Score stored | Label (`results_group.html`) | Workbench text (`VERDICT_TEXT`) |
|---|---|---|---|---|
| `identical` | 1 | 1.0 | Identical | exact |
| `same_pixels` | 2 | 0.99 | Same pixels | diff meta |
| `same_photo` | 2 | min edge score (can be 1.0) | Same photo | same photo |
| `edited` | 2 | min edge score | Edited | edited |
| `same_video` | 2 | 0.9 / 0.85 | Same video | same video |
| `similar_shot` | 2 | min edge score | Similar shot | similar shot |
| `likely` | 4 | min edge confidence | Likely duplicate | likely |

`similar_shot` groups are **review only**: never in captures, never in smart select, never in the Workbench
duplicate clusters, shown on their own tab.

## 6. Storage: `groups`, `group_members`, `decisions`, `selection`

```sql
groups(id, step INTEGER,            -- 1 | 2 | 4
       verdict, score REAL, keeper_file_id,
       status TEXT DEFAULT 'open',  -- open | resolved | ignored | not_duplicate
       signature TEXT,              -- sorted member file ids, comma-joined
       reclaimable INTEGER)         -- sum of non-keeper sizes
group_members(group_id → groups ON DELETE CASCADE, file_id, role 'keeper'|'member', score, evidence JSON,
              PRIMARY KEY (group_id, file_id))
decisions(id, scope 'pair'|'group', subject, decision 'not_duplicate'|'ignore', note, decided_at)
selection(file_id PRIMARY KEY, group_id, step)   -- files ticked for removal
```

`_save_groups(con, step, groups)` (inside the caller's `tx`):
1. Remember `{signature: status}` of this step's groups whose status is not `open`.
2. `DELETE FROM groups WHERE step = ?` (members cascade) and `DELETE FROM selection WHERE step = ?`.
3. Insert each group with `status = old.get(sig, "open")`, `reclaimable = Σ size of non-keepers`, and its members
   (`role = 'keeper'` for `keeper.id`, `evidence = json.dumps(evidence or {})`).

So a rebuild keeps `resolved` / `not_duplicate` / `ignored` statuses by signature, but **drops** manual keeper
changes and the selection of that step. Group ids change on every rebuild.

`_decided(con) -> (not_dup_pairs, ignored_signatures)`: pairs from `scope='pair' AND decision='not_duplicate'`
(subjects are `pair_subject`, content-keyed), signatures from `scope='group' AND decision='ignore'`.

## 7. Captures (input to step 3): `build_captures(lib)`

A capture = files that are the same photo or video. Union-find over
`SELECT id FROM files WHERE kind IN ('image','video') AND (present = 1 OR removed = 1)`:

```sql
SELECT g.keeper_file_id, gm.file_id FROM groups g JOIN group_members gm ON gm.group_id = g.id
WHERE g.step IN (1, 2) AND g.verdict IN ('identical','same_pixels','same_photo','edited','same_video')
  AND g.status NOT IN ('not_duplicate', 'ignored')          -- open and resolved groups count
```

plus `SELECT file_id, payload FROM actions WHERE op = 'hold' AND status = 'done'` → union with
`payload.keeper_id` (removed copies are no longer in any group after a rescan). Union always points the larger
root at the smaller, so `capture_id = the smallest file id` of the set. Then `DELETE FROM captures` and insert
`(file_id, capture_id)` for every id. Step-4 groups and `similar_shot` groups never join captures.

## 8. Step 4: re-check on harmonized values — `step4_recheck(lib, job=None) -> int`

Runs between **captures**, using `harmonize.effective_values(con)` (accepted / edited proposals, else current
values; keys `capture_time`, `location_name`, `device_name`, and `lat`/`lon`/`country` for an accepted place).

1. `min_conf = float(lib.setting("recheck_min_confidence"))` (0.5). Files = `load_files` (present, not removed).
2. One representative per capture: `reps[cid] = max(capture files, key=keeper_key)`; `t(cid)` = harmonized time.
3. Candidates: (a) captures whose harmonized times are within **2 s** (sorted sweep), (b) `_candidate_pairs`
   over the representatives mapped back to capture pairs.
4. Skip: `not_duplicate` pairs; **bursts** = both reps have `dto`, different `dto`, same `device`, neither re-encoded.
5. Signals:

| Signal | Value | Weight |
|---|---|---|
| `visual` | images only: `1 - min(dhash_dist, phash_dist)/64`, else None | 0.45 |
| `tsig` | `1.0` if `dt <= 2`, else `exp(-dt/30)`; None without both times | 0.20 |
| `dev` | harmonized `device_name` equal 1.0 / different 0.0 / None | 0.10 |
| `loc` | `location_name` equal 1.0 / 0.0; **overridden** by haversine `_distance_m < 200` when both have `lat` | 0.10 |
| `name` | `SequenceMatcher(None, _stem(a.original_name), _stem(b.original_name)).ratio()` (always known) | 0.10 |
| `size` | `min(size)/max(size)` | 0.05 |

6. `strong = (visual is not None and visual >= 0.8) or (dt is not None and dt <= 2 and (dev == 1.0 or loc == 1.0))`;
   drop the pair if not strong.
7. `conf = _known_avg(...)`; `conf *= 0.9` when `visual is None`; keep if `conf >= min_conf` as
   `edges[(fa.id, fb.id)] = (round(conf, 3), "likely", evidence)` with evidence keys `visual`, `time_delta_s`,
   `same_device`, `same_place`, `name_similarity`, `size_ratio`.
8. `groups = _anchor_groups(rep_files, edges, lambda e: True, ignored)`; `_save_groups(con, 4, groups)`.

Demo: the crop `Edited/IMG_20190518_114710_crop.jpg` (no EXIF) groups with `Camera/DCIM/IMG_0003.JPG` at
**0.853** (`visual 0.891`, `dt 0`, `name 0.44`, `size 0.761`) once its file-name date is accepted in step 3.

## 9. Results pages (steps 1, 2, 4)

### 9.1 Routes (`app.py`)

| Route | Handler | Notes |
|---|---|---|
| `GET /step/1` | `results_page(1, kind="")` | params `status=open, q, sort=size, dir=desc` |
| `GET /step/2` | `results_page(2, kind)` | `kind` ∈ `dups` (default) / `bursts` |
| `GET /step/4` | `results_page(4)` | defaults `sort=confidence`, `min_conf=0.5` |
| `GET /step/{step}/rows?offset=` | `partials/results_rows.html` | next page (infinite scroll) |
| `POST /groups/{gid}/keeper/{fid}` | `results.set_keeper` | returns the re-rendered `partials/results_group.html` (`open=True`), `HX-Trigger: selection-changed` |
| `POST /select` | form `file_id, group_id, step, on?` → `results.toggle` | 204, `HX-Trigger: selection-changed` |
| `POST /select/smart` | form `step, mode, kind`; `mode="folder:<key>"` → folder mode | 204, triggers `selection-changed`, `results-reload`, toast `Selected n copies` / `Selection cleared` |
| `GET /selection/bar?step=` | `partials/selection_bar.html` with `results.summary` | |
| `POST /plans/cleanup-selection` | form `step` | builds the plan, clears the selection, `HX-Redirect: /plans/{pid}`; toast `Nothing selected` if empty |
| `POST /plans/cleanup` | form `step, group_ids[]` | plan removing every non-keeper (no template uses it now) |
| `POST /groups/{gid}/decide` | form `decision` ∈ `not_duplicate` / `ignore` / `reopen` | returns `""` (row removed by `outerHTML` swap), `HX-Trigger: selection-changed` |
| `GET /preview/{fid}?group=` | `partials/preview.html` | right-hand panel |
| `GET /compare?group=` | `compare.html` | swipe view + metadata table |

`decide` effects: `not_duplicate` inserts one `decisions` row per member pair (`scope 'pair'`, subject
`pair_subject`) and sets status `not_duplicate`; `ignore` inserts `scope 'group'` with the signature and sets
`ignored`; `reopen` deletes the group decision and sets `open`. All three delete the group's `selection` rows.

`_step_of(gid) -> (step, status)` (falls back to `(1, "open")`). Nav counts (`counts()`): `step1` open groups,
`step1_bytes` Σ reclaimable, `step2` open non-burst, `bursts` open `similar_shot`, `step4` open groups.

### 9.2 Read model: `results.load(lib, step, *, status="open", kind="", q="", sort="size", desc=True, min_conf=0.0, offset=0, limit=100, group_ids=None) -> (groups, total)`

- WHERE (`_where`): `g.step = ? AND g.status = ?` + `KIND_FILTER[kind]` (`dups` → `verdict != 'similar_shot'`,
  `bursts` → `= 'similar_shot'`) + `g.score >= min_conf` if set + search
  `g.id IN (SELECT gm2.group_id … WHERE f2.name LIKE %q% OR f2.rel_path LIKE %q%)`.
- Members (`_member_rows`): files + source + meta + `(sel.file_id IS NOT NULL) AS selected`, ordered keeper
  first then `rel_path`. `crumbs = [source label] + parent folders`. Values: steps 1/2 show `date = dto`,
  `device = device_name(make, model)`, `place = geo.place(lat, lon)["city"]`; **step 4 shows the harmonized
  values** of the file's capture (`capture_time`, `device_name`, `location_name`).
- Per group: `keeper` (role keeper, else first), `total` = Σ member sizes, `selected` count; per member
  `diff` = the keys among `date, device, place, size, name` that differ from the keeper.
- Sort keys (`SORTS`): `size` → total, `qty` → member count, `name` → keeper name (lower), `date`, `device`,
  `place` → keeper's value, `confidence` → `g.score`; tie-break `-id`; `reverse=desc`. Sorting is in Python
  over all matching groups, then `[offset:offset+limit]`. Page size `PAGE = 100`.

### 9.3 Templates

- `results.html`: title/intro per step (`1 · Identical files`, `2 · Near duplicates`, `4 · Re-check`); step 2 tabs
  *Duplicates* / *Similar shots, review only* with counts; toolbar form (`hx-get /step/N`, `hx-select #results`,
  `hx-push-url`, search debounced 300 ms) with Status (Open / Resolved / Not duplicates / Ignored), Sort (Size,
  Qty / bindings, Name, Date, Device, Place, Confidence — Confidence hidden on step 1), direction, and on step 4
  a *Min. confidence* range 0.5…1 step 0.05 plus *Run re-check* (`hx-post /recheck`). Smart select appears only
  for `status == open` and not on the bursts tab (there: "Similar shots are never selected automatically").
  `#results` reloads on `results-reload from:body`; `#selbar` loads on `load, selection-changed from:body`.
  *◧ Preview* toggles the right panel (Alpine `panel`).
- `partials/results_group.html`: one `<tbody class="rgroup" id="g-{id}">` per group. Head row: chevron, keeper
  thumbnail (`/thumb/{id}`), keeper name, verdict chip (steps 2/4), *n selected*, total size, member count,
  keeper device and place, confidence badge (`score|band`, `score|pct`) on steps 2/4; tools *Compare*,
  *Not duplicates*, *Ignore* (open) or *Reopen* (ignored / not_duplicate). File rows: checkbox (disabled unless
  the group is open) posting `/select`, breadcrumbs (more than 3 crumbs → first, `…`, last two), name, *Keep*
  pill or *Keep this one*, size, date, device, place (`is-diff` class when in `m.diff`), member score (steps 2/4).
  Clicking a row loads `/preview/{fid}?group={gid}` into `#preview`.
- `partials/results_rows.html`: groups (expanded) + a `<tbody class="more" hx-trigger="revealed">` loader while
  `next_offset < total`.
- `partials/preview.html`: image (`/media/` for images, `/thumb/` for videos), role (Kept copy / Selected for
  removal / Copy), date, device, place with coordinates, size and dimensions, duration, software, the evidence
  chips (non-keepers), *Compare the group*.
- `compare.html`: uses the older `load_groups` helper; swipe slider (`clip-path: inset(0 0 0 pos%)`) between the
  kept copy and a chosen member (images only), and a metadata table: Path, Date, Date from, Device, Place,
  Software, Width, Height, Size.
- Badges: `band(p)` = `verified ≥ 0.98`, `high ≥ 0.9`, `medium ≥ 0.7`, `low ≥ 0.4`, else `unknown`;
  `pct` = `f"{p*100:.0f} %"`.

`partials/grid_row.html` belongs to the step-3 rename grid (proposals), not to duplicates; see skill 06.

### 9.4 Selection and smart select (`results.py`)

- `toggle(lib, file_id, group_id, step, on)`: `INSERT OR REPLACE INTO selection` or `DELETE … WHERE file_id = ?`.
  A file can be selected in one group only (PK).
- `set_keeper(lib, gid, fid)`: `_set_keeper` updates member roles and `groups.keeper_file_id` and recomputes
  `reclaimable`; then removes the new keeper from the selection.
- `smart_select(lib, step, mode, kind="", folder="") -> int`: in one `tx`, clears the step's selection; `mode
  "none"` stops there. Over open groups of the step with `verdict != 'similar_shot'` (+ kind filter):

| Mode | Keeper | Selected |
|---|---|---|
| `keeper` | current keeper | every other member |
| `oldest` | `min(mtime, id)` | others |
| `largest` | `max(width*height, size, -id)` | others |
| `folder` | first member (keeper preferred) whose `_folder_key` equals or is under `folder`; groups with none are **skipped** | others |

  When the rule picks another file it becomes the keeper (`_set_keeper`). Returns the number selected.
- `folders(lib, step, kind) -> [(folder_key, count)]`: folders holding members of open groups, most first, max 60;
  `_folder_key = f"{source label}/{parent}"` (or the label at the root).
- `summary(lib, step) -> {"files", "bytes", "groups", "whole_groups"}`; `whole_groups` = groups whose selected
  count ≥ member count.
- `selected_by_group(lib, step) -> {group_id: [file_id]}`; `clear(lib, step)`.

**Selecting every copy is blocked.** The UI does not stop ticking the keeper, but the selection bar warns
"n groups would lose every copy and will be skipped", and `plans.build_cleanup_plan` skips any group whose
removal set would leave no member (`blocked: (gid, "would leave no copy")`): zero actions for it.

### 9.5 Hand-off to the cleanup plan (details in skill 08)

`plans.build_cleanup_plan(lib, group_ids, remove_file_ids=None) -> plan_id`: for each **open** group, removes
either every non-keeper (default) or exactly `remove_file_ids` (any copy may go if one stays; the payload keeper
is the kept keeper, else the first kept member). Each file becomes a `hold` action to
`<root>/_duplicates/group-<gid>/<rel_path>` (sidecars as `move` actions in the same `group_key = file-<id>`),
payload `{keeper_id, verdict, step, size, mtime, rel_src, rel_dst}`. Read-only or reference sources are
blocked per file. Plan summary `{files, bytes, blocked, groups}`. On apply, `_verify` re-checks size/mtime and
byte-compares `identical` copies with the keeper; afterwards every open group with no non-removed non-keeper
becomes `resolved`. Undo reopens the plan's `resolved` groups.

### 9.6 Workbench consumers (skill 09)

`workbench._dup_clusters` unions **open** groups of steps 1, 2, 4 except `similar_shot` into clusters; per file
score: `identical` 1.0, `same_pixels` 0.99, others `min(score, 0.98)` (100 % is reserved for byte-identical).
`groups_for(lib, ids)` returns open step 1/2/4 groups containing the files; `/wb/remove-plan` calls
`build_cleanup_plan(lib, gids, remove_file_ids=chosen)`.

## 10. Specified, not implemented yet (from skills/duplicate-finder and docs)

- Exact pass: byte-by-byte compare is done only at apply time (done); partial hash uses first+last 1 MB, not
  4 KiB. JPEG scan-data hash (SOS…EOI) and video stream hashes (`ffmpeg -f streamhash`) — videos have no
  content hash; images use the decoded-pixel hash.
- Video fingerprints (~10 sampled keyframes, median pHash); today videos match on duration + time only.
- Media mode: capture-time buckets, identity keys (`ImageUniqueID`, Apple `ContentIdentifier`, serial +
  shutter count), siblings never deduplicated (Live Photo HEIC+MOV, RAW+JPEG, brackets).
- BK-tree / embeddings; a `pairs` table so the threshold slider regroups live without rescanning.
- Calibration (labelled pairs → logistic regression) so the % is a probability; *Not duplicates* labels feed it.
- Decisions: `keep`, `remove`, `resolved`, `deferred`, folder-pair scope, JSON export/import; group signatures
  by content hash (today: file ids); "changed since you resolved it" badge; manual keepers that survive rescans.
- Keeper rule "original format over derivative" (RAW/HEIC > JPG > PNG) and "more metadata" beyond dto/GPS.
- UI: folder overlap view (`A ≡ B`, `A ⊂ B`), keyboard review, timeline view, macOS Trash option, per-file
  step-4 list with *bindings*, per-group *Remove others…* and *Same capture* (merge captures) buttons.

## 11. Code/doc inconsistencies to keep in mind

- `docs/WORKFLOW.md` step 4 describes a per-file list sortable by folder / bindings / group with *Remove others…*;
  the code shows the same grouped table as steps 1–2 (sort `qty` = member count) and no per-group remove button.
- Rule gap: same time (`dt <= 2`), Hamming distance 2–6 and same aspect (or unknown dimensions) yields **no
  verdict** (neither `same_photo` nor `edited`).
- `same_photo` groups can show 100 % (score 1.0 when only visual and geometry are known); the Workbench caps them
  at 98 %.
- Ignored groups are not recreated on a rebuild (their signature is skipped), so they disappear from the
  *Ignored* filter; *Reopen* of a `not_duplicate` group keeps its pair decisions, so a rebuild will not form it.
- `docs/schema.sql` (target schema) has no `groups` / `selection` tables; the live schema is `db.SCHEMA`.

## Rebuild checklist

1. Implement `F`, `FILE_SQL`, `load_files`, `keeper_key`, helpers (`_ts`, `_ham`, `_stem`, `pair_subject`,
   `_known_avg`) and constants exactly as in §2–3.
2. Add the `groups`, `group_members`, `decisions`, `selection`, `captures` tables and settings
   `near_threshold=0.85`, `recheck_min_confidence=0.5`.
3. `_save_groups` with status carry-over by signature and selection reset; `_decided`.
4. `step1_exact` (hardlink collapse, keeper, ignore check); call it from `scan_all` after `fingerprint`.
   Test: demo gives 5 identical groups.
5. `compare` with the rule table of §5.2 in the same order; `_candidate_pairs` (≤ 2500 all pairs, else
   multi-index buckets; video duration sweep).
6. `_anchor_groups`; `step2_near` with the dup pass then the burst pass. Test: verdicts on the demo (§Verify).
7. `build_captures` (union-find, hold-action links, min id as capture id).
8. `step4_recheck` with the weights and gates of §8; wire `POST /recheck`.
9. `web/results.py`: `load`, `folders`, `toggle`, `set_keeper`, `smart_select`, `summary`, `selected_by_group`,
   `clear`.
10. Routes of §9.1 and templates of §9.3 (htmx attributes, `hx-push-url`, infinite scroll, selection bar,
    preview panel, compare page).
11. Cleanup hand-off: `/plans/cleanup-selection` → `build_cleanup_plan` with `remove_file_ids` → clear selection →
    redirect; confirm the "would leave no copy" block.
12. Nav counts and the `band` / `pct` filters.

## Verify

Run `uv run pytest` (needs `ffmpeg` for the video cases). Relevant tests:

- `tests/test_workflow.py::test_step1_identical`: 5 step-1 groups on the demo disk; the triple
  (`IMG_0003.JPG`, backup copy, `Copy of IMG_0003.JPG`) keeps `Camera/DCIM/IMG_0003.JPG`.
- `tests/test_workflow.py::test_step2_near`: `IMG-20190515-WA0003.jpg` → `same_photo` (score 1.0, `dt None`,
  dist 0); `prague castle.jpg` → `same_pixels` (0.99, `time_delta_s 81870`); `VID-20190721-WA0001.mp4` →
  `same_video` (0.9); `IMG_0011.JPG` → `similar_shot` (0.954, `dt 1`); `export-0005.jpg` → `same_photo`;
  the undated crop is in no step-2 group.
- `tests/test_workflow.py::test_steps_3_and_4_and_plans`: applying the step-1 plan moves 6 copies to
  `_duplicates/`; after `accept_above(0.7)` step 4 groups the crop with `IMG_0003.JPG` (verdict `likely`,
  0.853) and never groups the burst `IMG_0011.JPG`; undo restores every byte.
- `tests/test_workflow.py::test_read_only_source_blocks_writes`: read-only source → 0 cleanup actions.
- `tests/test_results.py` (demo with `extra=12`): `test_load_sorts_and_pages` (17 step-1 groups, size-desc order,
  disjoint pages, search `christmas` → 1 group); `test_smart_select_and_plan` (`keeper` mode selects 18 files,
  `whole_groups == 0`; folder mode `disk/Backup 2019` moves the keeper into `Backup 2019/`; the plan applies with
  0 failures); `test_selecting_every_copy_is_blocked` (`whole_groups == 1`, 0 actions).
- Manual: `uv run meta-duplo demo && uv run meta-duplo serve`, open `/step/1` (5 groups), `/step/2` (4 duplicate
  groups, 1 similar shot on its tab), tick every copy of one group and see the selection-bar warning; run
  *Run re-check* on `/step/4` after accepting recommendations in step 3 and see one *Likely duplicate* at 85 %.
