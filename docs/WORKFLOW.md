# Workflow: four steps from a messy disk to named, de-duplicated files

This is the user-facing flow of the merged app (meta-duplo + duplicate finder, Proposal A, htmx UI;
see ADR-010 and ADR-011). It sits on top of the 16-stage pipeline in `ARCHITECTURE.md`:
the steps are what the user does, the stages are what the engine runs.

| Step | User goal | Engine stages | Writes to disk? |
|---|---|---|---|
| 1 · Identical files | Remove 100 % identical copies | 1 inventory, 2 extract, 3 hash, 6a match | Only through an approved cleanup plan |
| 2 · Near duplicates | Resolve almost-identical files (other name and/or metadata) | 4 fingerprint, 5 thumbs, 6a match | Only through an approved cleanup plan |
| 3 · Harmonize & rename | One date, place and device per photo; new names from an editable rule | 7–12 context, resolve, score; 13 review; 14 plan | Only through an approved rename plan |
| 4 · Re-check | New duplicate candidates found with the harmonized metadata, sortable | 6b confirm pairs on harmonized values | Only through an approved cleanup plan |

The order matters. Removing identical copies first makes step 2 smaller. Step 2 groups the
near-identical copies so step 3 can use **every copy as evidence** for one capture.
Step 3 then fixes the dates and devices that step 4 needs to find the matches
that were invisible before.

---

## Step 1 · Identical files (100 %)

**Finds:** byte-identical files, whatever their names and folders.

**How:** size groups → partial BLAKE3 (first + last 1 MB, only for size collisions) →
full BLAKE3 (only for partial collisions). A file with a unique size is never read.
Hardlinks (same volume + inode, APFS/HFS+) are one file on disk and are never offered as duplicates.

**Keeper rules** (default order, editable later):
1. In a *reference* (protected) source.
2. Original over re-encoded (messenger, social media software tags).
3. Has a capture time; more pixels; larger file; has GPS.
4. Not in a backup / copy / export / download / messenger folder, and not named "Copy of …".
5. Older file date, shorter path.

**Actions** (results table, see UI_SPEC): tick the copies to remove, or use *Smart select* (all except the
kept copy / keep oldest / keep largest / keep the copy in a chosen folder); per group: *Keep this one*,
*Not duplicates*, *Ignore*. *Review removal plan…* turns the selection into a plan. Removed copies go to
`<source>/_duplicates/group-<id>/<original relative path>` with their sidecars. Right before each
move, the file's size and date are re-checked and identical copies are compared byte by byte
with the kept file.

**Removed copies stay evidence.** Their metadata stays in the library. Step 3 still uses their dates,
GPS and folder names.

## Step 2 · Near duplicates (different names and/or metadata)

**Finds**, with a verdict label and a confidence:

| Verdict | Means | Typical cause |
|---|---|---|
| Same pixels, different metadata (99 %) | Decoded pixels identical | Date "fixed", tags or GPS added, renamed |
| Same photo, different file | Picture matches; time equal or unknown | Re-saved, resized, HEIC→JPG, messenger copy, export without EXIF |
| Edited version | Picture matches, same time, other crop/aspect | Crop, rotate |
| Same video | Same duration, same time, not a different device | Re-encoded or shared video |
| Similar shot (burst) | Same device, camera originals, 1–10 s apart | Burst, bracket. **Review only, never removed automatically** |

**How:** one decode per image gives the pixel hash, dHash, pHash and the thumbnail
(EXIF orientation applied first). The score is a weighted mean of the signals that are known
(picture 0.6, time 0.2, device 0.1, geometry 0.1), so a missing date neither helps nor hurts.
Hard rules come before the score:
- Two camera-original frames from one device at different seconds are a burst, never a duplicate.
- Different device and more than 60 s apart: at most 70 %, no group (two people, same subject).
- Without a date on one side, the picture match must be very close (dHash/pHash distance ≤ 3 of 64 bits).

Groups are built around an anchor: the best keeper, with members that match the anchor itself,
so A≈B≈C chains are never formed. Files from step-1 groups take part through their kept copy only.

**UI:** the same results table as step 1; values that differ from the kept copy (date, device, place, size, name) are highlighted.
*Compare* shows a swipe view and a metadata table. *Not duplicates* records every pair as a labelled
non-duplicate: it is never proposed again and later feeds calibration.

## Step 3 · Harmonize metadata and rename

### Recommendations (one per capture and field)

A **capture** is a set of files that are the same photo or video: step-1 groups plus step-2
duplicate groups the user did not reject, **including removed copies**. For each capture
there are three recommendations:

| Field | Evidence, best first | Confidence (POC) |
|---|---|---|
| Date & time | Tier 1: EXIF DateTimeOriginal / QuickTime date → tier 2: date+time in a file name (`IMG_20190514_182203`, `PXL_…`, `2019-05-14 18.22.03`) → tier 3: date only (`IMG-20190515-WA0003`) → tier 4: file system date | 93 % tier 1; +5 % when another origin agrees (e.g. the file name); max 70 % when copies disagree; 75 / 50 / 25 % for tiers 2–4 |
| Place | GPS from the kept copy, else from another copy → nearest town (offline GeoNames) | 90 % own GPS, 85 % from another copy |
| Device | Make + model, normalized (`NIKON CORPORATION` + `NIKON D750` → `Nikon D750`; `Canon` + `Canon EOS 5D` → `Canon EOS 5D`) | 90 %; 60 % when copies disagree |

Rules from the resolver skills that the POC already follows:
- The earliest plausible tier-1 value wins: copies and edits only add later timestamps.
- Copies are not independent. Agreement counts only between different origins (camera clock,
  file name, file system), never between two copies of the same field.
- Invalid values are rejected, not fatal: epoch artefacts (1970-01-01 …), factory defaults
  (2000-01-01 00:00), calendar-impossible dates in file names, future dates.
- Video UTC times are converted with the home time zone **per date** (IANA, never a fixed offset).

Every recommendation shows **where it came from** and **why**, e.g. *"From metadata of IMG_0004.JPG.
Copies disagree by 81870 s; the earliest is used."*

### Decisions (up to the user)

| Status | Meaning | Used for renaming and step 4 |
|---|---|---|
| suggested | Not decided yet | No, the current value is used |
| accepted | User accepted the recommendation | Yes |
| edited | User typed a value | Yes |
| rejected | "Keep current" | No |

A value that equals the current one is auto-accepted only when it is confident (≥ 90 %). Otherwise
it is shown as *keep as is?* with a **Confirm** button, e.g. when copies disagree.
Bulk actions: *Accept all ≥ 90 %*, *Accept all ≥ 70 %*. Filter: *Needs a decision* / *All captures*.
Rebuilding recommendations keeps accepted, edited and rejected decisions.

### File name rule (editable)

Default, as requested:

```
{date:%Y-%m-%d %H-%M-%S} ({location_name}, {device_name}) - {original_filename}.{ext}
```

renders `IMG_0001.JPG` as `2019-05-14 18-22-03 (Bratislava, Apple iPhone 12) - IMG_0001.jpg`.

| Token | Value |
|---|---|
| `{date:FORMAT}` | Capture date/time; FORMAT uses strftime codes (`%Y %m %d %H %M %S`) |
| `{location_name}` | Town (editable per capture) |
| `{country}` | Country code |
| `{device_name}` | Camera or phone |
| `{original_filename}` | The **first** name the file was seen with, without extension. Renaming twice never nests names |
| `{ext}` / `{EXT}` | Extension in lower / upper case |

Tidying rules: empty tokens drop their separators: `( , Apple iPhone 12)` → `(Apple iPhone 12)`,
`()` disappears. Illegal characters (`/ \ : * ? " < > |`) become `-`. Names are NFC and
at most 200 characters before the extension. Files without a date keep their name and are
flagged. Collisions get ` (2)`, ` (3)`, so names are collision-free by construction.
The rule has a live preview while typing (htmx, 300 ms debounce) and is saved per library.

### Rename plan

*Review rename plan…* builds a dry-run plan:
- **rename** each file in place, with its sidecars (`IMG_1.xmp`, `IMG_1.JPG.xmp`, `.aae`, Takeout `.json`);
- optionally **write an XMP sidecar** with the harmonized date, place, GPS and device (never over an existing sidecar);
- optionally **set the file date** to the capture date.

In-file metadata writes are deliberately out of the POC. They come later through ExifTool, opt-in,
never for videos by default (ARCHITECTURE §9.5).

## Step 4 · Re-check with harmonized metadata

Runs the matching again **between captures**, using the effective (accepted or edited) values:

| Signal | Weight | Note |
|---|---|---|
| Picture similarity (dHash/pHash) | 0.45 | images only |
| Harmonized time | 0.20 | 1 within 2 s, then decays |
| Device | 0.10 | equal / different / unknown |
| Place | 0.10 | same town or within 200 m |
| Name similarity | 0.10 | of the original names |
| Size ratio | 0.05 | |

A pair is listed only with a strong signal: picture ≥ 80 %, or the same time with the same
device or place. Without a picture comparison, confidence is capped at 90 % of the score.
Bursts (same device, camera originals, different seconds) and pairs marked *Not duplicates*
are excluded.

**List** (one row per file, rows of a group share a shade). Sortable by name, folder, size, date, place,
device, confidence, **bindings** (how many other files this one is linked to) and group. Filter
by minimum confidence. Sort and filter live in the URL, so a view can be bookmarked.
Actions per group: *Compare*, *Remove others…* (cleanup plan), *Not duplicates*.

The demo disk contains the case this step exists for: a crop with no metadata, dated only by
its file name. Step 2 cannot group it, because there is no date and the crop changes the picture.
After step 3 accepts the file-name date, step 4 matches it to the original at 85 %.

---

## Safety (all steps)

- Sources are read-only until marked *writable*. *Reference* sources are never changed.
- Every change is a **plan**: a dry run first, then a journal (`undo/<plan>.jsonl`), then apply.
  Actions are grouped per file with its sidecars and roll back together.
- Re-verification right before acting; the scan may be hours old.
- **Undo** per plan, in reverse order. The test suite checks that apply + undo leaves every byte
  where it was.
- Decisions live in the library, never inside the media files.
- Localhost only, session token, token header on every POST.

---

## Further ideas (not in the POC yet)

Ordered by expected value for this workflow:

0. **Step 4 feeds back into step 3.** A step-4 match the user confirms (*Same capture*, next to
   *Remove others*) merges the two captures. The crop then inherits the original's place and device
   in its recommendations. In the POC a confirmed match can only be removed, not merged.
1. **Camera clock correction** (step 3). Learn a camera's clock offset from pairs taken at
   the same moment as phone photos (or one photo of a clock), then shift every photo of that
   camera unit in one decision. This is the biggest bulk fix in real archives (meta-duplo device + time skills).
2. **Session fill for place** (step 3). A photo with no GPS, taken minutes before or after geotagged photos
   from the same session or device, gets their place as a medium-confidence recommendation.
3. **Folder overlap view** (step 1): `A ≡ B`, `A ⊂ B`, `A ∩ B 63 %` from the duplicate-finder
   research. Whole backup folders get resolved in one decision instead of 4 000.
4. **Bulk edit by folder or event** (step 3): "everything in `2019 Chorvátsko/` is in Split".
5. **Folder layout rule** next to the file name rule: `{date:%Y}/{date:%Y-%m-%d} {location_name}/`.
   This is the reorganize mode of meta-duplo's plan engine.
6. **Impact preview** for every decision: "Accepting this lifts 312 files to high confidence"
   (meta-duplo UI_SPEC principle 3).
7. **Keyboard review** for steps 2–4: `j/k` or `←/→` to move, `a` accept, `Enter` keep, `x` mark,
   `n` not duplicates, `i` ignore, `Space` compare, `?` help.
8. **CSV export** of the step-4 list and of every plan (before → after) for an audit trail.
9. **Calibration from your decisions**: *Not duplicates* and accepted/rejected recommendations become
   labelled examples that tune the weights and turn scores into calibrated probabilities.
10. **Cost estimates before a scan**: a 10-second benchmark gives time per method and cache hits (duplicate-finder UI-DESIGN §2).
11. **Video keyframe hashing** for re-encoded videos with stripped metadata. The POC matches videos by duration + time only.
12. **ExifTool adapter** for full metadata coverage (MakerNotes, QuickTime Keys, RAW formats) and opt-in in-file writes.
