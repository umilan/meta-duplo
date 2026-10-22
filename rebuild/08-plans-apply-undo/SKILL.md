---
name: plans-apply-undo
description: The plan engine, the only code allowed to write to source folders - cleanup (hold) and rename plans, their actions, the journal, apply with per-file verification and group rollback, undo, XMP sidecars and the _duplicates holding folder - plus the routes and pages that build, show, apply, undo and discard plans. Use when rebuilding or changing anything that moves, renames, annotates or re-dates files on disk.
---

# Plans, apply and undo

Code: `meta_duplo/plans.py` (whole module), routes in `meta_duplo/web/app.py` (`/plans…`, `/wb/rename-plan`,
`/wb/remove-plan`, `/wb/commit`), templates `meta_duplo/web/templates/plans.html` and `plan.html`, schema in
`meta_duplo/db.py` (`SCHEMA`: `plans`, `actions`), `HOLD_DIR` in `meta_duplo/scan.py`. Decision: ADR-005
("Read-only sources; writes only via approved plans").

**Hard rule:** nothing else writes to a source volume. Every change is a *plan*: built as a dry run (rows in
`plans` + `actions`), reviewed on `/plans/{id}`, applied with a journal, undoable later. Tests run on temp
copies of the demo disk.

## 1. Tables (as implemented in `db.py`; `docs/schema.sql` shows an older, different design)

```sql
CREATE TABLE IF NOT EXISTS plans (
  id INTEGER PRIMARY KEY,
  kind TEXT,                                  -- cleanup | rename
  status TEXT,                                -- draft | applied | partially_applied | failed | undone
  created_at TEXT, applied_at TEXT, undone_at TEXT,   -- db.now(): UTC "%Y-%m-%dT%H:%M:%SZ"
  summary TEXT                                -- JSON, per kind (below)
);
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY,
  plan_id INTEGER NOT NULL REFERENCES plans(id),
  group_key TEXT,                             -- actions sharing a key apply atomically ("file-<id>")
  file_id INTEGER,                            -- NULL for sidecar moves
  op TEXT,                                    -- hold | move | rename | write_xmp | set_mtime
  src TEXT, dst TEXT,                         -- absolute paths (src NULL for write_xmp)
  payload TEXT,                               -- JSON; gets "undo" info once done
  status TEXT NOT NULL DEFAULT 'planned',     -- planned | done | failed | undone  ("skipped" never set)
  error TEXT
);
```

`sources(writable INTEGER, role TEXT 'scan'|'reference')` decides where plans may act: a source is writable only
when `writable = 1 AND role != 'reference'`. `files.removed = 1` marks a file moved to the holding folder (still
evidence for steps 3–4); `files.original_name` is never changed by a plan.

Library folder: `<library>/undo/<plan_id>.jsonl` journals (`Library.__init__` creates `undo/`).

## 2. Constants and helpers

```python
SIDECAR_EXTS = (".xmp", ".XMP", ".aae", ".AAE", ".json", ".JSON")
HOLD_DIR = "_duplicates"            # scan.py; the scanner never descends into it
class PlanError(Exception): ...
def _sidecars(path: Path) -> list[Path]
def _new_plan(con, kind: str, summary: dict) -> int        # INSERT status 'draft', created_at now()
def _writable_sources(con) -> dict[int, dict]              # NB: returns ALL sources by id; callers check writable/role
```

`_sidecars(path)`: for each ext in `SIDECAR_EXTS`, candidates `path.with_suffix(ext)` (`IMG_1.xmp`) and
`path.with_name(path.name + ext)` (`IMG_1.JPG.xmp`, Takeout `IMG_1.JPG.json`); keep those that exist, are not
`path` itself, and are not already listed. Order = SIDECAR_EXTS order, stem form first.

## 3. Plan kinds and operations

| Plan `kind` | Built by | Ops it can contain | Title in UI |
|---|---|---|---|
| `cleanup` | `build_cleanup_plan` | `hold` (the copy), `move` (its sidecars) | Clean up duplicates |
| `rename` | `build_rename_plan` | `rename`, `move` (sidecars), `write_xmp`, `set_mtime` | Rename + annotate |

| op | src → dst | file_id | payload (at build) | verified before acting | DB sync |
|---|---|---|---|---|---|
| `hold` | file → `<root>/_duplicates/group-<gid>/<rel_path>` | yes | `{keeper_id, verdict, step, size, mtime, rel_src, rel_dst}` | size+mtime; byte compare when verdict `identical` | `removed = 1, rel_path = rel_dst` |
| `move` | sidecar → new place | NULL | `{}` | no | none |
| `rename` | file → same folder, new name | yes | `{}` | size+mtime | `rel_path` (new name), `name` |
| `write_xmp` | – → `<final>.xmp` | yes | `{"xml": "<xpacket…>"}` | no | none |
| `set_mtime` | final → final | yes | `{"mtime": <epoch float>}` | no | `mtime = stat(dst).st_mtime` |

## 4. `build_cleanup_plan(lib, group_ids: list[int], remove_file_ids: list[int] | None = None) -> int`

Moves every non-keeper of the given groups (or only `remove_file_ids`) to the holding folder.

Per `gid` in `group_ids`:
1. Load the group; skip silently unless it exists and `status == 'open'`.
2. Members: `group_members gm JOIN files f`, with `gm.role`.
3. `to_remove` = members with `role != 'keeper'` (default), or members whose id is in `remove_file_ids`
   (explicit selection: any copy may go, the keeper too, as long as one stays).
4. `kept` = the others. If none → `blocked.append((gid, "would leave no copy"))`, nothing for this group.
5. `keeper` = first kept member with role `keeper`, else `kept[0]` (used for the byte compare).
6. For each member to remove:
   - source not writable or role `reference` → `blocked.append((gid, f"{root} is read-only"))`, skip it;
   - `dst_rel = PurePosixPath("_duplicates") / f"group-{gid}" / rel_path`;
   - action `("file-<id>", id, "hold", root/rel_path, root/dst_rel, payload)`;
   - each `_sidecars(path)`: `("file-<id>", None, "move", sc, root/_duplicates/group-<gid>/<sc relative to root>, "{}")`;
   - `total += size`.
7. One transaction: plan `kind='cleanup'`, summary
   `{"files": <hold count>, "bytes": total, "blocked": [[gid, reason], …], "groups": group_ids}` (all requested
   ids, even skipped ones); insert actions.

The holding folder lives **inside the same source root** (same volume → `shutil.move` is an atomic rename) and
keeps the original relative path, so undo and manual recovery are obvious:
`/Volumes/Disk/_duplicates/group-12/Backup 2019/Copy of IMG_0003.JPG`.

Callers:

| Route | Inputs | Call |
|---|---|---|
| `POST /plans/cleanup` (steps 1/2/4 pages) | `step`, `group_ids` (multi) | `build_cleanup_plan(lib, group_ids)` |
| `POST /plans/cleanup-selection` | `step` | `results.selected_by_group(lib, step)` → `{gid: [fid…]}`; none → 204 toast "Nothing selected"; else plan with `remove_file_ids` = all selected, then `results.clear(lib, step)` |
| `POST /wb/remove-plan` | `ids` (CSV) | `gids = wb.groups_for(lib, ids)` (open groups of step 1, 2 or 4 containing any id); none → 204 toast "No duplicates in the selection"; else `build_cleanup_plan(lib, gids, remove_file_ids=ids)` |

All successful builds answer `HX-Redirect: /plans/{pid}`. Workbench: *Actions → Delete…* (`runAction("remove")`,
disabled without a selection).

## 5. `build_rename_plan(lib, write_xmp=True, set_mtime=False, only_ids: set[int] | None = None) -> int`

Renames (and annotates) every file of `rename_preview(lib)` (skill 07; uses setting `filename_pattern`), or only
`only_ids`.

Per preview item `p`:
1. Skip if `status == "excluded"` or (`only_ids` given and `file_id` not in it).
2. Source not writable / reference → if status not in (`unchanged`, `no_date`) add
   `blocked.append((file_id, f"{root} is read-only"))`; skip the file entirely (no XMP, no mtime either).
3. `path = root / rel_path`, `gkey = f"file-{file_id}"`, `vals = effective_values(con)[capture_id]`, `final = path`.
4. Status `ok` or `renumbered`: `final = path.with_name(p["new"])`; action `rename path → final`; then each
   sidecar of `path`:
   - name starts with `path.name` (`IMG_1.JPG.xmp`) → `final.name + tail` (`<new name>.xmp`);
   - else (`IMG_1.xmp`) → `final.with_suffix(sc.suffix)` (`<new stem>.xmp`);
   - action `move sc → new_sc`, file_id NULL.
5. `write_xmp` and any of `capture_time`, `location_name`, `device_name` non-empty: `xmp = final.with_suffix(".xmp")`;
   add `write_xmp → xmp` with payload `{"xml": _xmp(vals)}` **only if** `xmp` does not exist and the original
   has no sidecar at all (`not _sidecars(path)`) – an existing XMP/AAE/JSON is never overwritten or duplicated.
6. `set_mtime` and a `capture_time`: `ts = datetime.strptime(capture_time, TIME_FMT).timestamp()` (the naive
   local time interpreted in the **host's** local zone via the OS, no fixed offset); action
   `set_mtime final → final`, payload `{"mtime": ts}`.
7. One transaction: plan `kind='rename'`, summary

```json
{"renames": 12, "sidecars": 9, "mtimes": 0,
 "blocked": [[file_id, "/Volumes/X is read-only"]], "blocked_count": 1,
 "pattern": "<filename_pattern>"}
```
   `sidecars` counts `write_xmp` actions only (moved sidecars are not counted); `blocked` is cut to 50.

Callers:

| Route | Inputs | Notes |
|---|---|---|
| `POST /plans/rename` (step 3 bar) | `write_xmp`, `set_mtime` checkboxes | all files |
| `POST /wb/rename-plan` (Workbench *Actions → Rename…*) | `ids` CSV; the page sends `write_xmp=1` | no ids → 204 toast "Select files first"; first `wb.commit(lib, view, ids)` ("the plan uses exactly what the table shows": purple date/place/device values become `edited` proposals, the edit pattern becomes `filename_pattern`); then `build_rename_plan(lib, write_xmp=True, only_ids=ids)`; `set_mtime` is not offered |
| `POST /wb/commit` | `ids` CSV or `all=1`, `target` (`filename`/`date`/`place`/`device` or empty) | not a plan: `wb.commit(..., only=target)`; 204 + `HX-Trigger {"wb-reload": true, "toast": …}`: "Saved the filename pattern; review the rename plan to apply it" (target filename) / "Saved N value(s) as decisions" / "Nothing to save for these files" |

## 6. XMP sidecar content: `_xmp(values) -> str`

Properties, in order, only when the value is present (`iso = capture_time` as `%Y-%m-%dT%H:%M:%S`, no zone):

| Value | Elements |
|---|---|
| capture_time | `<exif:DateTimeOriginal>iso</…>`, `<xmp:CreateDate>iso</…>`, `<photoshop:DateCreated>iso</…>` |
| location_name | `<photoshop:City>…</photoshop:City>` |
| country | `<photoshop:Country>…</photoshop:Country>` |
| lat (not None) | `<exif:GPSLatitude>D,MM.MMMMN|S</…>`, `<exif:GPSLongitude>D,MM.MMMME|W</…>` – `f"{int(d)},{(d-int(d))*60:.4f}{hemisphere}"`, d = abs(v) |
| device_name | `<tiff:Model>…</tiff:Model>` |

`lat`/`lon`/`country` exist in `effective_values` only when the place proposal is accepted/edited and has GPS.
Wrapper (props joined by `"\n      "`):

```xml
<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="meta-duplo 0.1">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about=""
      xmlns:exif="http://ns.adobe.com/exif/1.0/" xmlns:xmp="http://ns.adobe.com/xap/1.0/"
      xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" xmlns:tiff="http://ns.adobe.com/tiff/1.0/">
      {props}
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
```

Values are inserted without XML escaping (see "Known gaps"). Written as UTF-8.

## 7. `apply_plan(lib, plan_id) -> {"done", "failed", "status"}`

1. Load plan; not found or `status != 'draft'` → `PlanError("only a draft plan can be applied")` (so a plan is
   applied at most once).
2. Load actions `ORDER BY id`.
3. **Journal first:** write `<library>/undo/<plan_id>.jsonl`, one line per action
   `{"id", "op", "src", "dst", "file_id"}` (mode `w`). It is a crash record; `undo_plan` itself reads the DB.
4. Group actions by `group_key` in first-seen order (one group = a file plus its sidecars/XMP/mtime).
5. Per group:
   - for each action in order: `_verify(con, a)`, then `info = _do(a)`; collect `(a, info)`;
   - **any exception** (PlanError, OSError, …): undo the finished actions of this group in reverse with
     `_undo_one` (errors ignored), mark **every** action of the group `status='failed', error=str(exc)` in one
     transaction, `failed += 1`, continue with the next group (sidecars never get separated from their file);
   - success: one transaction marking each action `done` with `payload = {...payload, "undo": info}` and
     `_sync_db(con, a)`; `done += 1`.
6. Plan `status = "applied"` if no group failed, `"partially_applied"` if some succeeded, else `"failed"`;
   `applied_at = now()`.
7. Cleanup plans: mark `resolved` every open group (any, not only this plan's) that has no non-keeper member left
   with `removed = 0`.

`done`/`failed` count **groups** (= files), not actions.

### `_verify(con, a)` – re-check right before acting

- Only for `hold` and `rename` with a `file_id`.
- `os.stat(src)`: size must equal `files.size` and `abs(st_mtime - files.mtime) <= 1e-3`, else
  `PlanError("file changed since the scan; rescan first")`. A missing file raises `FileNotFoundError` (group fails).
- `hold` with payload verdict `identical`: `filecmp.cmp(src, keeper_path, shallow=False)` against the keeper
  (`payload.keeper_id` → `sources.root / files.rel_path`); different → `PlanError("bytes differ from the kept copy")`.

### `_do(a) -> dict` (what undo needs)

| op | Action | Errors | Returns |
|---|---|---|---|
| hold / move / rename | `mkdir -p dst.parent`; `shutil.move(src, dst)` | dst exists → `PlanError(f"target exists: {dst}")` (case-insensitive FS: a case-only rename hits this) | `{}` |
| write_xmp | `dst.write_text(payload.xml, "utf-8")` | dst exists → `PlanError(f"sidecar exists: {dst}")` | `{}` |
| set_mtime | `os.utime(dst, (st.st_atime, payload.mtime))` | – | `{"old_atime", "old_mtime"}` |
| other | – | `PlanError(f"unknown op {op}")` | – |

### `_sync_db(con, a, undo=False)` (skipped when `file_id` is NULL)

| op | apply | undo |
|---|---|---|
| hold | `removed = 1, rel_path = payload.rel_dst` | `removed = 0, rel_path = payload.rel_src` |
| rename | `rel_path = parent/<dst name>`, `name = <dst name>` | same with the src name |
| set_mtime | `mtime = stat(dst).st_mtime` | same (restored value) |

Keeping `files.mtime` in sync means a later plan's `_verify` still matches.

## 8. `undo_plan(lib, plan_id) -> {"undone", "errors"}`

1. Plan must be `applied` or `partially_applied`, else `PlanError("only an applied plan can be undone")`.
2. Actions with `status = 'done'` `ORDER BY id DESC` (reverse of apply; failed actions were already rolled back).
3. Per action: `info = payload.undo`; `_undo_one(a, info)`:
   - hold / move / rename: `mkdir -p src.parent`; `shutil.move(dst, src)`;
   - write_xmp: `dst.unlink(missing_ok=True)`;
   - set_mtime: `os.utime(dst, (old_atime, old_mtime))`.
   On exception: `error = "undo: <exc>"`, `errors += 1`, the action stays `done`; continue.
   On success: one transaction: `status = 'undone'` + `_sync_db(con, a, undo=True)`.
4. Plan `status = 'undone'`, `undone_at = now()` – **even when some actions failed to undo**.
5. Cleanup plans: groups in `summary.groups` with status `resolved` go back to `open`.

No size/mtime verification happens before undo. The holding folder's empty `group-N` directories are left
behind after undo (the test tree compares files only).

## 9. `discard_plan(lib, plan_id)`

One transaction: delete the plan's `planned` actions and the plan row if `status = 'draft'`. Applied plans are
never deleted (their actions are the undo record). `remove_source` (`__main__.py`) refuses to forget a source
while any action refers to its files ("These folders are used by plans. Keep them so the plans can still be
undone."); `/wb/sources/demo` never recreates an existing demo folder.

## 10. Routes and pages

| Route | Behaviour |
|---|---|
| `GET /plans` | all plans `ORDER BY id DESC`, summary parsed → `plans.html` |
| `GET /plans/{pid}` | unknown → redirect `/plans`; first **1000** actions `ORDER BY id`; `src`/`dst` shown relative to the longest matching source root (`root + "/"` prefix stripped) → `plan.html` with `plan` (summary parsed), `actions`, `roots` |
| `POST /plans/{pid}/apply` | `apply_plan` → toast "Applied: {done} done, {failed} failed" |
| `POST /plans/{pid}/undo` | `undo_plan` → toast "Undone: {undone} actions" |
| `POST /plans/{pid}/discard` | `discard_plan` → `HX-Redirect: /plans` |
| any `PlanError` | toast = its message |

Apply/undo answer `HX-Redirect: /plans/{pid}` + `HX-Trigger: {"toast": msg}`. Other exceptions are not caught
(HTTP 500). All POSTs need the session token header (`X-Meta-Duplo-Token`, skill 10).

`plans.html` – "Plans & undo" ("Every change to your files is a plan: reviewed as a dry run, applied with a
journal, undoable later."); table `# · Kind · Status · Summary · Created · Applied`; kind label
`Clean up duplicates` for cleanup, else `Rename + annotate`; summary `N files · <size>` or
`N renames · M sidecars`; empty state "No plans yet.". The nav item *Plans & undo* shows the count of drafts
(`counts.drafts`).

`plan.html` – title `Plan #id: <kind label>`; "Paths are relative to `<roots>`."; cleanup: "Moves N files
(size) to `_duplicates/`, with their sidecars. Each copy is re-checked right before it moves; identical copies
are compared byte by byte with the kept file."; rename: "N renames, M XMP sidecars, K file dates. Rule:
`<pattern>`". Blocked banner: "`{blocked_count or len(blocked)}` items skipped: `<first reason>`. Mark the
source writable on the Sources page to include them." Toolbar: status chip; draft → **Apply plan** (primary,
`hx-confirm="Apply N actions to your files?"`, disabled with no actions) and **Discard**; applied /
partially_applied → **Undo plan** (danger, `hx-confirm="Undo every action of this plan?"`). Table
`Action · Before · → · After · Status` (+ error text); "Nothing to do." when empty.

## Known gaps (as implemented; reproduce or fix deliberately)

| Gap | Effect |
|---|---|
| `_xmp` does not XML-escape values | a place or device containing `&` or `<` produces an invalid XMP file |
| `set_mtime` uses the host's local zone | a photo taken in another zone gets an mtime shifted by the zone difference |
| `_verify` skips `move`, `write_xmp` and `set_mtime` | a sidecar changed since the scan is moved without any check |
| Undo does not verify | `shutil.move(dst, src)` silently replaces a file that appeared at `src` since apply (POSIX rename) |
| Plan status `undone` even with undo errors | the actions that could not be undone stay `done`, and the plan cannot be undone again |
| Journal never read | a crash in the middle of a group leaves the disk and the DB out of step; nothing recovers it |
| A sidecar stem shared by two files (`IMG_1.JPG` + `IMG_1.MOV` → `IMG_1.xmp`) | both groups plan to move it; the second fails and is rolled back |
| Sidecar moves don't update `files` rows | a rescan sees moved sidecars as new files and the old rows as gone |
| `summary.sidecars` counts XMP writes only | the UI's "M XMP sidecars" leaves out the sidecars that are moved |
| The cleanup banner always says "Mark the source writable…" | misleading when the reason is "would leave no copy" |
| The detail page shows at most 1000 actions | `hx-confirm` then reports at most 1000 actions too |

## Rebuild checklist

1. Create `plans` / `actions` exactly as in §1 and the `undo/` folder in the library.
2. Implement `SIDECAR_EXTS`, `_sidecars`, `PlanError`, `_new_plan`, `_writable_sources`.
3. Implement `build_cleanup_plan`: open groups only, default non-keepers vs explicit ids, "would leave no copy",
   read-only/reference blocking, `_duplicates/group-<gid>/<rel_path>`, sidecar moves in the same group key,
   summary `{files, bytes, blocked, groups}`.
4. Implement `_xmp` byte-for-byte as in §6 and `build_rename_plan` (§5): excluded / only_ids skip, read-only
   blocking rule, rename + sidecar naming, `write_xmp` only without any existing sidecar, `set_mtime` via naive
   local `timestamp()`, summary keys incl. `blocked_count` and `pattern`.
5. Implement `_verify`, `_do`, `_undo_one`, `_sync_db` per the tables.
6. Implement `apply_plan`: draft check, journal first, group loop with rollback and per-group failure,
   statuses applied / partially_applied / failed, cleanup group resolution.
7. Implement `undo_plan` (reverse `done` actions, errors recorded, plan `undone`, groups reopened) and
   `discard_plan`.
8. Add the routes of §4, §5 and §10 and the two templates; strip roots in the detail view; toasts via
   `HX-Trigger`.
9. Guard source removal while actions reference its files.

## Verify

- `uv run pytest tests/test_workflow.py::test_steps_3_and_4_and_plans` – step 1 cleanup of all 5 groups:
  `done == 6`, `failed == 0`, `Backup 2019/Copy of IMG_0003.JPG` gone, `_duplicates/` exists; rename plan with
  `write_xmp=True, set_mtime=True` applies with `failed == 0`, creating
  `Camera/DCIM/2019-05-14 18-22-03 (Bratislava, Apple iPhone 12) - IMG_0001.jpg` and the matching `.xmp`;
  undo rename then undo cleanup → every file's md5 and relative path equal to before.
- `tests/test_workflow.py::test_read_only_source_blocks_writes` – non-writable source: cleanup plan has 0 actions.
- `tests/test_workflow.py::test_excluded_files_keep_their_name` – excluded file has no action in the rename plan.
- `tests/test_results.py::test_smart_select_and_plan` – selection-based cleanup (keeper moved to the backup
  folder) applies with `failed == 0` and `done` = number of selected files.
- `tests/test_results.py::test_selecting_every_copy_is_blocked` – selecting every copy → 0 actions.
- `tests/test_workbench.py::test_edit_rules_commit_and_partial_rename` – `only_ids={id}` → exactly one `rename`
  action, for that file.
- Extra checks worth adding: apply twice → `PlanError("only a draft plan can be applied")`; touch a file after
  planning → its group `failed` with "file changed since the scan; rescan first" and its sidecars untouched;
  pre-create a target → "target exists: …" and the plan ends `partially_applied`.
