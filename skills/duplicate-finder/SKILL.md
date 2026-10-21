---
name: duplicate-finder
description: Build a local duplicate/similar file finder for photos and videos with a confidence score per match, a Python scan engine and an HTML/CSS web UI served on the Mac. Use when designing, coding or tuning the scan pipeline, the scoring, or the review UI.
---

# Duplicate finder — how to build one

## 1. What a "confidence %" actually is

Almost no duplicate app computes a real probability. The percentage is a
**normalized distance turned upside down**:

| Method | Raw measure | Shown as |
|---|---|---|
| Exact hash (MD5/xxh3/BLAKE3) | equal / not equal | 100 % or nothing |
| Perceptual hash (aHash/dHash/pHash) | Hamming distance d between N-bit hashes | `1 - d/N` |
| dupeGuru picture blocks | sum of RGB differences over a 15×15 grid of average colours | match if score ≤ 100 − threshold |
| dupeGuru filename scan | matched words ×2 / total words in both names | e.g. "a b c d" vs "c d e" = 4/7 = 57 % |
| Video (VDF, viddup) | median/average frame-hash distance over sampled frames | same as pHash |
| Audio fingerprint | sliding-window Hamming similarity of chroma fingerprints | % of matching bits |
| AI embeddings (DINOv2, CLIP) | cosine similarity of vectors | cosine × 100 |

Consequences you must design around:
- 90 % from one algorithm is not 90 % from another. Never mix scales in one column.
- The number is not calibrated: "85 %" does not mean "85 % chance it's a dupe".
  If you want that, calibrate (section 7).
- Better UX than a bare % : a **verdict label + the evidence** that produced it.

## 2. The pipeline (cheap filters first, expensive last)

```
inventory → exact-duplicate pass → decode & fingerprint → candidate search → score → group → choose keeper → review UI → safe action
```

### 2.1 Inventory
Walk the folders once, store in SQLite: path, size, mtime, inode/device,
extension, detected type (by magic bytes, not extension). Skip
`.DS_Store`, `._*`, `.Spotlight-*`, `.fseventsd`, Photos library internals
unless asked.

### 2.2 Exact duplicates (the fdupes/jdupes cascade)
0. Collapse **hard links** (same device + inode) into one entry — they take no
   extra space, so they're not duplicates to remove.
1. Group by **size**; drop every file alone in its size group.
2. **Partial hash** of the first 4 KiB (optionally also last 4 KiB) → split groups.
3. **Full hash** (xxh3-128 or BLAKE3 — fast; MD5 is fine too) → split groups.
4. Optional **byte-by-byte compare** before any destructive action
   (jdupes does this as a failsafe against hash collisions).

Result: confidence 100 %, label **Identical file**. This alone usually finds
most of the waste on backup disks and is I/O-bound, not CPU-bound.

Notes:
- A cryptographic hash isn't required here; nobody is forging your photos.
  xxh3 is several times faster than MD5/SHA-256 with negligible accidental
  collision risk.
- Byte-by-byte verification re-reads both files (doubles I/O on a 2 TB disk).
  Do it **at action time** for the files about to be removed, not during the scan.

### 2.2b Same content, different metadata
Very common on old photo disks: same picture, but one copy had its EXIF date
fixed, tags/GPS added, or was re-saved by an app that rewrote metadata only.
Bytes differ, so the exact pass misses it; pixels are identical.
- **JPEG**: hash only the image data (strip APPn/COM segments, hash from SOS to EOI).
- **Other images**: hash the decoded pixel buffer (after orientation) —
  slower, but exact.
- **Video**: hash the streams, not the container:
  `ffmpeg -i f.mov -map 0:v -map 0:a? -c copy -f streamhash -hash md5 -`
- Label **Same pixels, different metadata**, confidence 99 %. Show a
  metadata diff in the UI; the keeper is usually the copy with the *better*
  metadata, which the person must decide (ties to the date-resolving work).

### 2.3 Fingerprints for near-duplicates (images)
For every image (skip ones already in an identical group except one representative):
1. Decode. HEIC → `pillow-heif`; RAW → use embedded JPEG preview (`rawpy` or
   `exiftool -b -PreviewImage`) — never full RAW demosaic for hashing.
2. **Apply EXIF orientation** first, convert to greyscale.
3. Compute and store several hashes (`imagehash` lib):
   - **dHash 64-bit** — fast, few false positives, default workhorse.
   - **pHash 64-bit** (DCT) — more tolerant to edits/recompression.
   - aHash only as a cheap pre-filter (many false positives).
   - Optional: 256-bit hash (hash_size=16) for big libraries — fewer collisions.
4. Also store: width, height, aspect ratio, colour histogram (small, 3×16 bins),
   EXIF `DateTimeOriginal`, `SubSecTimeOriginal`, camera make/model,
   GPS, burst/ContentIdentifier (iPhone), file-format.
5. Reject degenerate hashes (all 0 / all 1 — solid black/white images); flag
   instead of matching.

### 2.4 Videos
1. `ffprobe`: duration, resolution, fps, codec, creation_time, rotation.
2. Pre-filter candidates by **duration within ±1 s or ±2 %**.
3. Sample ~10 frames at 5 %, 15 % … 95 % of duration with `ffmpeg`.
   Skip low-variance frames (black/fades) and retry a nearby timestamp.
4. pHash each frame; similarity = 1 − median(Hamming)/64 across aligned frames.
5. Optional later: audio fingerprint (`fpcalc` / Chromaprint) for trimmed clips;
   embeddings for cropped/mirrored copies.

### 2.5 Candidate search (avoid N²)
Comparing every image to every other is quadratic (dupeGuru admits this).
For 100k+ files use one of:
- **BK-tree** over Hamming distance (simple, good to ~1M).
- **Multi-index hashing**: split 64-bit hash into 4×16-bit chunks; any pair
  within distance ≤3 shares at least one exact chunk → bucket by chunk.
- Bucketing by aspect ratio (rounded) and capture date first cuts work a lot.
- For embeddings: FAISS / hnswlib.

## 3. Scoring: turning evidence into confidence

Compute each signal as 0–1, then combine. Suggested starting weights
(tune with your own labelled pairs):

```
visual   = 1 - hamming(dhash)/64        (also pHash; take max or mean)
time     = 1 if same DateTimeOriginal(+subsec) else decay over |Δt| (e.g. exp(-Δt/5s))
device   = 1 if same make+model, 0.5 unknown, 0 different
geometry = 1 if same aspect ratio, penalise rotations/crops
size_rel = smaller/larger pixel count (tells you "resized copy")
name     = 1 if normalized stems match (see below), else 0

score = 0.60*visual + 0.20*time + 0.10*device + 0.10*geometry
        (+ small bonus, e.g. +0.03, when name = 1)
```

Filename is **weak evidence only**, never a match on its own: unrelated files
share names like `IMG_0001.JPG` (camera counters reset) or `document.pdf`.
Normalize before comparing: lowercase, drop extension, strip copy suffixes
(` (1)`, ` copy`, `-edited`, `_1`). Name + size can be offered as a quick
"preview scan" mode, but it misses renamed files and must not drive actions.

Hard rules override the weighted score:
- identical full hash → 100 %, **Identical file**
- identical pixel/stream hash → 99 %, **Same pixels, different metadata**
- visual ≥ 0.97 and same capture time → **Same photo, different file**
  (re-export, resize, HEIC↔JPG, metadata stripped)
- visual ≥ 0.90, same capture time, different size/crop → **Edited version**
- visual ≥ 0.80 and Δt < 10 s, same device → **Similar shot (burst)** — never auto-select
- different device + different time → cap score at 0.7 even if visually close
  (two people photographing the same thing)

Rough Hamming bands for a 64-bit dHash/pHash:
0–2 near-identical, 3–5 very similar (resize/recompress), 6–10 edited or
similar shot, 11+ loose (many false positives).

Always store the **evidence vector** with the pair, so the UI can say
"same time · same camera · 98 % visual".

## 4. Grouping

- Union-find on pairs above threshold is easy but **chains**: A≈B, B≈C, but A≉C.
- Safer: pick an anchor (best-quality file), add members only if they match
  the anchor; or require every member to match ≥50 % of the group.
- Exact-hash groups first, then near-dup groups built from one representative
  per exact group.
- Let the user regroup live by moving a threshold slider — keep all pair
  scores in SQLite so regrouping needs no rescan.

## 5. Choosing the keeper (auto-selection)

Default order, user-editable:
1. File in a folder marked **reference / protected** (never selectable for removal).
2. Original format over derivative (RAW/HEIC > JPG > PNG screenshot > WhatsApp-style recompress).
3. Higher resolution, then larger file size.
4. Has more metadata (GPS, capture date).
5. Older capture/mtime, shorter path, not in "Downloads"/"export" folders.

Only Identical and Same-photo groups get auto-selection. Similar-shot groups
are review-only.

## 6. Safety rules (non-negotiable)

- Default action = **move to a quarantine folder** (mirroring original paths)
  or macOS Trash, never `rm`.
- Dry-run first; show total bytes to be freed.
- Write an action log (JSON lines) with undo support.
- Re-verify size+mtime (and byte compare for Identical) right before acting —
  the disk may have changed since the scan.
- Never leave a group with zero kept files.
- Never follow symlinks into other trees; treat hardlinks (same inode) as
  already-deduplicated, not duplicates.

## 7. Calibrating real confidence (optional, later)

1. Build a test set: take ~300 photos, generate variants with Pillow
   (resize 50 %, JPEG q=60, crop 10 %, rotate, brightness ±10 %, HEIC↔JPG,
   strip EXIF) plus true non-duplicates (burst neighbours, same scene other day).
2. Score all pairs, label them, plot precision/recall per threshold.
3. Fit logistic regression on the evidence vector → output is now an actual
   probability. Show it as the confidence %.

## 8. Architecture for an HTML/CSS front end

The browser can't read the disk or run ffmpeg, so run a **local server**:

```
Python 3.12
  FastAPI + uvicorn  (bind 127.0.0.1 only)
  SQLite (WAL mode)  — files, hashes, pairs, groups, actions
  Pillow, pillow-heif, imagehash, rawpy, xxhash/blake3
  ffmpeg/ffprobe, exiftool (brew install)
  ProcessPoolExecutor for decoding/hashing
Front end: plain HTML + CSS + small vanilla JS (no build step)
```

Start with `python app.py` → opens `http://127.0.0.1:8765`.

### API sketch
```
POST /api/scan            {roots:[], reference_roots:[], modes:["exact","image","video"]}
GET  /api/scan/progress   Server-Sent Events: phase, files done/total, bytes, ETA
GET  /api/groups?min=0.85&kind=same_photo&page=1
GET  /api/group/{id}      members + evidence + suggested keeper
GET  /api/thumb/{file_id}?size=400   cached JPEG thumbnail (videos: middle frame)
GET  /api/preview/{file_id}           full image / video stream (Range support)
POST /api/group/{id}/keep {file_id}
POST /api/actions/apply   {mode:"quarantine"|"trash", dry_run:true}
POST /api/actions/undo    {action_id}
```

### UI structure
- **Scan page**: folder list (paste paths; mark reference), mode checkboxes,
  progress bar driven by SSE.
- **Results page**: filters by verdict label and confidence slider (regroups
  live), sort by reclaimable bytes.
- **Group card**: thumbnails in a CSS grid, keeper outlined, each member shows
  resolution, size, date, camera, path, and a **confidence badge**.
- **Compare view**: side-by-side or swipe/flicker overlay at 100 % zoom,
  metadata diff table with differing fields highlighted.
- Keyboard: ←/→ next group, K keep, X mark, Space compare.

### CSS notes
- Confidence colour via a custom property:
  `.badge{background:hsl(calc(var(--conf)*1.2) 70% 45%)}` with `--conf: 0..100`
  set inline → red-to-green automatically.
- `grid-template-columns: repeat(auto-fill, minmax(180px,1fr))` for thumbnails;
  `aspect-ratio:1; object-fit:contain` so portrait/landscape align.
- `<img loading="lazy" decoding="async">`; paginate groups (50 per page) or
  use `content-visibility:auto` on group cards — thousands of cards otherwise.
- Light/dark via `prefers-color-scheme` and CSS variables.
- Verdict labels as text, not colour alone.

## 9. Scale: 2 TB+ of media on one Mac

### 9.1 Budget (rough, Apple Silicon, ~400k files at ~5 MB average)
| Stage | Bound by | Rough time | Notes |
|---|---|---|---|
| Inventory (walk + stat) | metadata I/O | 2–10 min | `os.scandir`, batch inserts |
| Metadata read (EXIF/QuickTime) | I/O + exiftool | 20–60 min | `exiftool -stay_open` batch mode, or read headers only |
| Exact pass | disk | worst case all bytes: ~3.7 h on USB HDD at 150 MB/s, ~35 min on SSD at 1 GB/s | only same-size candidates are read |
| Image fingerprints | CPU | ~10 min with embedded thumbnails; ~40+ min with full decode | see 9.2 |
| Video fingerprints | CPU + seeks | 1–3 s per video | keyframe-only seeking, hardware decode |
| Candidate search + scoring | CPU, RAM | minutes | BK-tree / buckets, never all-pairs |

Show these as estimates before the scan and actual times after (UI section 2).

### 9.2 Make the expensive steps cheap
- **Hash from embedded thumbnails.** dHash needs only a 9×8 image. Most JPEGs
  and HEICs carry a ~160 px EXIF thumbnail and RAW files carry a JPEG preview;
  decoding those is 10–50× faster than the full image. Fall back to full
  decode only when the thumbnail is missing or for the final pixel-exact check.
- **Use macOS ImageIO for decode** (`CGImageSourceCreateThumbnailAtIndex` via
  `pyobjc`): hardware HEIC decoding, applies orientation, returns small images fast.
- **Video**: `ffmpeg -hwaccel videotoolbox -skip_frame nokey -ss <t>` — seek to
  keyframes only, decode a small frame, never the whole stream.
- **Exact pass on HDD**: one reader thread per physical disk, sort reads by
  path/inode to reduce seeking; on SSD use 4–8 readers.
- **Process pool** = number of performance cores; keep efficiency cores for the UI.
- **Lower priority** (`os.nice`, or `taskpolicy -b`) so the Mac stays usable.

### 9.3 Memory
- Stream everything; never hold the file list in RAM. SQLite is the working set.
- What lives in RAM: 64-bit hashes (400k × 8 B ≈ 3 MB) and the BK-tree — tiny.
- Store only pairs above the lowest threshold the UI slider allows; a 400k-file
  library should produce thousands of pairs, not billions.
- Thumbnails only for files that end up in a group, generated on demand,
  256 px WebP (~10 KB each), cached on disk.

### 9.4 Resumable and pausable
- A `jobs` table: one row per (file, stage) with status `pending/done/failed`.
  Workers pull pending rows; a crash or quit loses at most the current batch.
- Commit every few hundred files (WAL mode). Pause = stop pulling jobs.
- Unplugged disk → its jobs pause automatically and resume when it reappears
  (match by volume UUID, not mount path).
- Cache identity: (volume UUID, inode, size, mtime) → unchanged files are never
  re-hashed on later runs.

## 9b. Projects, reopening results and the decisions store

### Projects
- A **project** = one SQLite file in `~/Library/Application Support/<app>/projects/`.
  It holds inventory, fingerprints, pairs, groups, decisions and scan history.
- Reopening a project needs **no disk access** for review: metadata, scores and
  cached thumbnails are inside. Disks that are not connected show as *offline*;
  you can review and decide, but actions wait until the disk is back.
- Each scan is a **run** inside the project; later runs add or update files and
  keep earlier decisions.

### Decisions live outside the media files
Never write decision state into the photos (no EXIF edits, no xattrs by default).
A separate `decisions` table:

| Field | Meaning |
|---|---|
| `subject` | a file content hash, a pair (hash A + hash B), a group signature, or a folder pair |
| `decision` | `keep`, `remove`, `not_duplicate`, `ignore`, `resolved`, `deferred` |
| `scope` | file / pair / group / folder-pair / rule |
| `note`, `decided_at`, `run_id` | context and history |

- **Group signature** = sorted list of member content hashes. If a later scan
  finds the same members, the group stays *resolved* and is hidden.
- If a **new member** appears in a resolved group, reopen it with a
  "changed since you resolved it" badge.
- Key by **content hash**, not path, so decisions survive renames, moves and
  copying the library to another disk.
- Export/import decisions as JSON, so they survive a project rebuild and can be
  version-controlled or backed up.
- Optional, off by default: write a Finder tag (e.g. "Dup: keep") for people who
  want to see decisions in Finder.

### Progress view
- Counters per verdict: open / resolved / ignored / deferred, and bytes reclaimable.
- "Continue where I left off" reopens the last filter, sort and scroll position.
- Saved views (filter + sort + group-by) for repeated analysis.

## 9c. Media focus mode

A separate mode for photo/video disks. It is faster and more accurate than the
generic file mode because a photo's identity is its **capture event**, not its bytes.

| | Files mode | Media mode |
|---|---|---|
| Scope | all files | images, videos, sidecars (XMP, AAE, JSON) |
| Exact match | whole-file hash | container-aware hash (JPEG scan data, video/audio streams) plus whole-file hash |
| Size grouping | first filter | not used as a filter (metadata edits change size) |
| Candidates | size groups, then hashes | **capture-time buckets**: same camera + DateTimeOriginal ± a few seconds, ImageUniqueID, Apple ContentIdentifier |
| Visual check | optional | dHash/pHash within the bucket only; BK-tree only for files without a capture time |
| Units | single files | Live Photo (HEIC+MOV), RAW+JPEG, file+sidecar treated as one unit |
| Keeper rules | size, date, path | original format, resolution, metadata completeness, reference folder |
| Results views | groups, table, folders | adds **timeline** (by capture date) and **event** grouping |

Why it's faster: bucketing by capture time turns an all-pairs search into many
tiny comparisons. Why it's better: two visually similar photos from different
cameras or different days are not grouped, which removes most false positives
(same scene photographed twice, screenshots, dark shots).

Fallback chain for capture time (ties to the date-resolving work):
EXIF DateTimeOriginal+SubSec → QuickTime CreationDate → sidecar JSON (Google
Takeout) / XMP → date in filename → file mtime (lowest trust, flagged).

## 9d. Performance notes (summary)

- The exact pass is disk-bound: read sequentially, one reader thread per
  physical disk; parallelism doesn't help on a single HDD.
- Hashing images is CPU-bound: process pool = number of performance cores.
- Cache keyed by (volume UUID, inode, size, mtime) → re-scans of an unchanged disk
  take seconds, and the same disk mounted elsewhere still hits the cache.
- Make the scan resumable: commit to SQLite every N files.
- Thumbnails: generate on demand, cache in `~/Library/Caches/<app>/thumbs`.

## 10. Common pitfalls

- Hashing before applying EXIF rotation → rotated copies don't match.
- Live Photos: HEIC + MOV pair share a ContentIdentifier — group them as a
  unit, don't flag the MOV as a "duplicate" of anything.
- Screenshots, scanned documents, dark night shots and white backgrounds
  produce near-identical hashes → require time/device agreement.
- Same EXIF time ≠ duplicate (edits keep the timestamp; bursts share the second).
- Files with corrupted data: catch decode errors, mark as "unreadable",
  keep them out of auto-selection.
- macOS Unicode normalization (NFD) in filenames — normalize to NFC for comparison.
- Cloud placeholders (iCloud/Dropbox "online-only") — reading them triggers
  downloads; detect and skip.

## 11. Build order

1. Project file + inventory + jobs table (pause/resume) + exact-duplicate cascade + minimal results page.
2. Decisions store (keep / remove / not duplicate / ignore / resolved) + reopen project.
3. Safe actions (quarantine, log, undo).
4. Media mode: metadata read, capture-time buckets, thumbnail-based dHash, verdict labels.
5. Compare view and keeper rules; timeline view.
6. Video pass (keyframe sampling, hardware decode).
7. Calibration test set; tune weights; "not duplicate" labels feed it.
8. Optional: embeddings, audio fingerprints, generic Files mode similarity.

## References
- dupeGuru scan docs: https://dupeguru.voltaicideas.net/help/en/scan.html
- jdupes man page (size → partial → full → byte compare)
- Czkawka (Rust; hash size, resize algorithm options, caching)
- Video Duplicate Finder (frames, audio fingerprint, DINOv2): https://github.com/0x90d/videoduplicatefinder
- Python `imagehash` library (aHash, dHash, pHash, wHash)
