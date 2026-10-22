# Known issues found while writing the rebuild skills

Writing the skills meant reading the whole code base against `CLAUDE.md`, `docs/` and `skills/`. These are the
gaps and defects found. They are **not fixed**; a rebuild should decide each one on purpose instead of copying
it. Items marked ✔ were re-checked in the code; the others come from one careful reading and should be
confirmed before work starts. Skill numbers point to where the details are.

## 1. Data safety (fix first)

| # | Issue | Where | Skill |
|---|---|---|---|
| 1.1 ✔ | **Undo can overwrite**: `_undo_one` moves a file back with `shutil.move(dst, src)` without checking that `src` is still free; a file that appeared there since is replaced. | `plans._undo_one` | 08 |
| 1.2 ✔ | **A partly failed undo is marked `undone`** and cannot be retried. | `plans.undo_plan` | 08 |
| 1.3 | The apply **journal** (`undo/<pid>.jsonl`) is written but never read: a crash mid-apply is not recovered. | `plans.apply_plan` | 08 |
| 1.4 ✔ | **XMP sidecars are not XML-escaped**: a place or device with `&` or `<` writes an invalid sidecar. | `plans._xmp` | 08 |
| 1.5 | `_verify` (size / mtime check before acting) skips sidecar moves, XMP writes and mtime changes. | `plans._verify` | 08 |
| 1.6 | Two files sharing a sidecar stem (`IMG_1.JPG` + `IMG_1.MOV` → `IMG_1.xmp`) both plan to move it; the second fails and its group rolls back. | `plans.build_rename_plan` | 08 |
| 1.7 | Name tidying can reduce a name to `.jpg` (a hidden file) when every part is empty. | `harmonize.render_name` | 07 |
| 1.8 | On rebuild of proposals, an **accepted** value takes the new recommendation without a new decision; decisions keyed by `capture_id` (smallest file id) can move to another photo when captures merge or split. | `harmonize.build_proposals` | 06 |

## 2. Rules in CLAUDE.md the code does not meet yet

| Rule | Today | Skill |
|---|---|---|
| ExifTool keyed by `SourceFile`, integrity checks | Pillow + pillow-heif and ffprobe (ADR-013 POC scope); no ExifTool | 04 |
| Per-file errors in `file_errors` | one `files.error` column; some hash / inventory errors are not recorded | 03 |
| Versioned stages (`STAGE_VERSION`) | only `RAW_VERSION` (and `VIEW_VERSION` for the view) | 01, 03 |
| Numbered migrations | none; `CREATE IF NOT EXISTS` only, so column changes never reach old libraries; `docs/schema.sql` describes a different target schema | 02 |
| Original path bytes for filesystem operations | NFC path is used (fine on APFS / HFS+, not elsewhere) | 02, 04 |
| Never fixed offsets / correct zones | `set_mtime` and file dates use the host time zone, not the photo's or `home_timezone` | 06, 08 |
| Localhost only; relative asset URLs | `--host` can bind any address; `/static/...` URLs are absolute | 01 |

## 3. Behaviour gaps and bugs

- Step 2: two photos within 2 s, Hamming distance 2–6, same or unknown aspect ratio get no verdict (05).
- Ignored groups are never recreated on rebuild; *Reopen* on a not-duplicate group keeps its pair decisions; manual keeper choices are lost on rescan (05).
- A busy runner silently drops `_rescan` and `POST /scan`; a job left `running` by a killed server stays `running` (03, 01).
- Failed extracts and all videos without ffprobe are re-read on every scan; a long inventory walk cannot be cancelled until that source ends (03).
- `{tag:…}` in names uses the file's current values, not the edited purple ones (dates do follow edits) (07).
- The naming dialog splits tokens differently from Python's `TOKEN` regex (`{foo bar}` shows as a chip, stays literal) (07).
- Sorting a column with mixed numbers and text can raise `TypeError`; `filter_remove` with a non-numeric index returns 500 (09).
- Source letters are assigned twice (build order in `apply_edits`, display order in `wb_ctx`); `/wb/preview` and `commit` see the first set (09).
- `remove_source` leaves memberless groups, proposals, decisions and thumbnails behind (02).
- Header ⋯ `openMenu` puts the column label in a single-quoted JS string: a label with `'` breaks it (11).

## 4. Docs out of date with the code

- `docs/schema.sql` vs `db.SCHEMA`; schema comments miss `cancelled` (jobs) and `failed` (plans) (02).
- ADR-016 still lists undo of view changes as "not yet" and omits Author grouping; it says `RAW_VERSION` 2 (now 3) (09, 01).
- WORKFLOW step 4 describes a per-file list with *Remove others…*; the code shows the step 1–2 grouped table (05). WORKFLOW's step 3 *Confirm* button for low-confidence equal values does not exist (06).
- UI_SPEC: funnel colour (orange, not accent), stale action names (*Remove selected copies…*, *Review rename plan…*), "kept copy" label (09, 11).

## 5. Clean-ups

Dead CSS (`.wbt__thumb--small`, `.wbmenu`), a raw `black` in `.wbsheet__act--primary:hover`, a stale CSS comment
above the dashed edges, the unused filename branch in the edit-rule dialog, the unused `wb-filter` listener,
`hashes.cache_key` written but never read, `group_width` stored but not used, unused `exif_keys` parameter of
`field_for` (12, 11, 09, 03).
