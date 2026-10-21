---
name: media-capture-date-resolver
description: Determine the real capture/creation time of photos and videos in a large, messy archive (mixed sources, corrupted files, overwritten metadata, dates in filenames), deduplicate copies, and organize files by resolved date. Use for photo/video library cleanup, dating, deduplication or sorting tasks.
---

# Media Capture Date Resolver

Determine **when each photo or video was actually taken**, using every available source of
date/time evidence. The skill handles duplicates whose metadata disagree and produces an
auditable, confidence-scored result that can drive sorting and renaming.

## When to use

- A user has a large disk or NAS of photos and videos from many sources: cameras, phones,
  messengers, cloud exports, scans and recovered files.
- Files need to be sorted, renamed or deduplicated by real capture time.
- Metadata is missing, stripped, overwritten, or contradicts the filename or filesystem dates.

## Core principles

1. **Never touch originals.** Work on a read-only mount or a copy. Every write is a dry run
   first, logged, and reversible.
2. **Extract everything once into a database, then reason over it.** Don't re-read files per
   decision.
3. **Group by clock, not by field.** Fields written by the same clock (camera EXIF, card mtime)
   count as one vote. Agreement only means something across independent clocks: camera, GPS,
   phone network, server, human.
4. **Metadata degrades forward in time.** Copying, editing, uploading and messaging only add
   *later* timestamps. Within a duplicate cluster, the earliest *plausible* high-tier value
   usually wins.
5. **Bounds are not answers.** Upload, receive, export and backup times are upper limits.
   Device or format release dates are lower limits. Use them to reject candidates, never to
   pick one.
6. **Every resolved date carries its source, timezone status, confidence and the rejected
   alternatives.**

---

## Workflow overview

```
1. Inventory      → walk the tree, record path, size, filesystem times, sidecars
2. Extract        → ExifTool/ffprobe dump of all tags (incl. embedded streams) + hashes
3. Parse names    → filename + folder date candidates (regex table below)
4. Import exports → Google Takeout JSON, Apple Photos.sqlite, chat logs, catalogs
5. Cluster        → exact dupes → same-pixels → perceptual near-dupes → identity keys
6. Candidates     → normalize every timestamp into the candidate table
7. Filter         → drop garbage/default/out-of-bounds values
8. Score          → tier weights × independence × agreement → pick winner per cluster
9. Correct clocks → detect timezone offsets and camera clock errors per body/time window
10. Fill gaps     → interpolate by counter/sequence/similarity, mark low confidence
11. Review        → conflicts and low confidence into a human review report
12. Apply         → write dates (sidecar or in-file), set mtime, rename/move, keep logs
```

Track these steps with the task list. On large archives, run steps 1–4 as a background job
and check progress.

---

## Step 1–2: Extraction

### Tools to check and install

```bash
which exiftool ffprobe || true          # ExifTool ≥ 12.60 recommended (ImageDataHash)
pip install --break-system-packages imagehash pillow pillow-heif blake3 tqdm
# optional: rawpy (RAW previews), opencv-python (video keyframes), pytesseract (OCR)
```

### Full metadata dump (keep raw values, decide later)

```bash
exiftool -r -a -G1 -s -ee3 -json -charset filename=utf8 \
  -api LargeFileSupport=1 -api RequestAll=3 \
  -ext '*' --ext json --ext txt --ext db \
  /path/to/archive > meta.json
```

- `-a` keeps duplicate tags, so you can see conflicting copies of the same tag.
- `-G1` keeps the group (e.g. `ExifIFD`, `XMP-exif`, `QuickTime`, `Keys`, `MakerNotes`,
  `IFD1`). The group is essential for reliability scoring.
- `-ee3` extracts embedded streams: AVCHD MDPM, GoPro GPMF, DJI, timed GPS.
- Do **not** pass `-api QuickTimeUTC` during extraction. Store the raw QuickTime values and
  decide on the UTC vs local interpretation in the scoring step.
- For huge trees, split by top-level folder and run in parallel, or use `-stay_open` with an
  argfile from Python.

### Extraction integrity (do not skip)

Batch metadata readers can silently attach one file's metadata to another file, or return
empty values under load. This has been reported with large batches of `.MTS` videos and with
ExifTool `-stay_open` pipelines.

- **Key every record by the `SourceFile` that ExifTool returns.** Never map results by position
  or order.
- After each batch, check that input count = output count, and that every returned path exists
  in the inventory.
- **Re-read files that return empty or partial date groups** in a second, small-batch or
  single-file pass. Empty results under load are not proof that a tag is absent.
- **Spot-check**: re-extract a random 1% sample individually and compare. Any mismatch → re-run
  that whole batch with a smaller batch size (e.g. ≤ 500 videos or ≤ 2000 images).
- Store `extract_pass` and `extract_ok` per file, so the review report can show files whose
  metadata was never confirmed.

### Coverage audit per device model

After extraction, compute per `make+model` and file type: the percentage of files with each
date group (EXIF original, QuickTime, Keys, GPS, MakerNotes, embedded streams).
- If a model's photos have a field but its videos don't (or one firmware's do and another's
  don't), suspect a vendor-specific storage location the extractor missed, not missing data.
- Inspect one sample with `exiftool -v3 -ee3` and add the location to the source table.

### Content hashes

```bash
# byte-level (fast prefilter by size first)
b3sum file
# pixel/stream-level: identical image data despite different metadata
exiftool -ImageDataHash -api ImageHashType=SHA256 -json -r /path
# video stream hash (metadata-independent)
ffmpeg -v error -i in.mp4 -map 0:v -c copy -f streamhash -hash sha256 -
```

Perceptual hash (Python, `imagehash`): compute `phash` and `dhash` on an EXIF-orientation-
corrected, downscaled image. For HEIC use `pillow-heif`. For RAW, hash the embedded preview.
For video, hash 5–10 keyframes taken at fixed percentages of the duration.

### Database schema (SQLite)

```sql
CREATE TABLE files(
  id INTEGER PRIMARY KEY, path TEXT UNIQUE, size INT, ext TEXT, mime TEXT,
  fs_mtime TEXT, fs_btime TEXT, fs_ctime TEXT,
  blake3 TEXT, data_hash TEXT, phash TEXT, dhash TEXT,
  make TEXT, model TEXT, serial TEXT, shutter_count INT, image_unique_id TEXT,
  content_identifier TEXT, document_id TEXT, original_document_id TEXT,
  software TEXT, width INT, height INT, duration REAL,
  is_reencoded INT, corrupt INT, cluster_id INT, sibling_group TEXT
);
CREATE TABLE candidates(
  file_id INT, source TEXT, raw_value TEXT,
  utc TEXT,            -- ISO UTC if the timezone is known, else NULL
  local_naive TEXT,    -- ISO local time if naive
  tz_offset_min INT,   -- if known
  clock TEXT,          -- camera|gps|phone_net|server|fs_copy|human|derived
  tier INT, weight REAL, precision_s REAL,
  kind TEXT,           -- point|upper_bound|lower_bound
  valid INT, reject_reason TEXT
);
CREATE TABLE resolved(
  cluster_id INT PRIMARY KEY, master_file_id INT,
  utc TEXT, local TEXT, tz_offset_min INT, tz_status TEXT,  -- known|derived|assumed|unknown
  confidence TEXT,     -- high|medium|low|none
  score REAL, winning_source TEXT, method TEXT, notes TEXT
);
```

---

## Step 3–4: Source catalogue

Every source becomes a row in `candidates`. "Clock" is the clock that originally produced the
value.

### A. EXIF (JPEG, HEIC/HEIF, TIFF, RAW: CR2/CR3/NEF/ARW/DNG/ORF/RW2/RAF)

| Tag (group) | Meaning | Clock | Tier | Kind |
|---|---|---|---|---|
| `ExifIFD:DateTimeOriginal` + `SubSecTimeOriginal` + `OffsetTimeOriginal` | Shutter moment | camera | 1 | point |
| `ExifIFD:CreateDate` (DateTimeDigitized) | Digitized. For scans this is the **scan** time | camera / scanner | 2 | point |
| `IFD0:ModifyDate` | Last software edit | software | 4 | upper_bound |
| `GPS:GPSDateStamp` + `GPSTimeStamp` | UTC from satellites; may be the stale last fix | gps | 1 | point (±staleness) |
| `MakerNotes:*` (Canon TimeInfo/TimeZone, Nikon WorldTime, Sony/Panasonic/Olympus/Fuji dates, Samsung trailer) | Vendor copies of time and timezone, usually untouched by editors | camera | 1 | point |
| `MakerNotes:ShutterCount` / `ImageNumber` / `FileNumber` | Monotonic frame counter | camera | — | ordering only |
| `IFD1` / MPF preview EXIF | Embedded thumbnail's copy of EXIF; may hold pre-edit values | camera | 2 | point |
| `ImageUniqueID`, `BodySerialNumber`, `Apple:ContentIdentifier` | Identity | — | — | clustering |
| `Software`, `ExifVersion`, `Model`, `LensModel` | Context | — | — | lower_bound / reliability |

### B. XMP (embedded or `.xmp` sidecar)

| Field | Tier | Kind |
|---|---|---|
| `XMP-exif:DateTimeOriginal`, `XMP-xmp:CreateDate` | 2 | point |
| `XMP-photoshop:DateCreated` (often set manually for scans/historic photos) | 2 (human) | point |
| `XMP-xmp:ModifyDate`, `XMP-xmp:MetadataDate` | 4 | upper_bound |
| `XMP-xmpMM:History` `stEvt:when` (earliest event) | 4 | upper_bound |
| `xmpMM:DocumentID` / `OriginalDocumentID` / `DerivedFrom` | — | clustering (lineage) |

### C. IPTC-IIM
`DateCreated`+`TimeCreated` (2:55/2:60) and `DigitalCreationDate`+`Time` (2:62/2:63).
Tier 2, clock `human` (often entered by hand).

### D. Video

| Source | Notes | Tier |
|---|---|---|
| `Keys:CreationDate` (`com.apple.quicktime.creationdate`) | iPhone; includes the timezone offset | 1 |
| `QuickTime:CreateDate` / `MediaCreateDate` / `TrackCreateDate` (mvhd/mdhd/tkhd) | Spec says UTC since 1904, but many cameras write **local** time. Value 0 means unset | 2 |
| `UserData:DateTimeOriginal` / `©day` | | 2 |
| `XMP` in `uuid` box (Sony, Adobe) | | 2 |
| AVCHD `.MTS/.M2TS` H.264 MDPM `DateTimeOriginal` (`-ee`) | Per-frame camera time with timezone | 1 |
| AVCHD `PRIVATE/AVCHD/BDMV/PLAYLIST/*.MPL`, `CLIPINF/*.CPI` | Clip dates | 2 |
| Sony XAVC `C0001M01.XML` sidecar `CreationDate` | Has timezone | 1 |
| GoPro GPMF `GPSU` (`-ee`), `.THM`/`.LRV` sidecars | GPS UTC | 1 |
| DJI `.SRT` sidecar: per-frame time and GPS | | 1 |
| AVI RIFF `IDIT`; Canon/Nikon `.THM` (a JPEG with full EXIF!) | | 1–2 |
| DV stream recording date per frame | | 1 |
| MKV/WebM `DateUTC` | | 2 |
| Encoder `Lavf*`, HandBrake, WhatsApp | Re-encoded: mark `is_reencoded`, and downgrade container dates to bound or tier 4 | — |

### E. Other image formats
- **PNG:** `tIME` (last modification, tier 4), `tEXt Creation Time` (tier 3), `eXIf` chunk,
  XMP in `iTXt`.
- **WebP:** EXIF and XMP chunks, usually stripped.
- **GIF / BMP:** essentially no dates.
- **PSD / TIFF:** XMP, IPTC, Photoshop IRB.
- **JPEG `COM` segment:** old software sometimes wrote dates here (tier 3, parse with regex).
- **DNG:** `OriginalRawFileName` links the DNG to its source RAW (clustering).

### F. Filenames and folders

Apply to the basename first, then to each path component. Store the pattern id as `source`.

| id | Regex (Python, case-insensitive) | Meaning | TZ | Tier/Kind |
|---|---|---|---|---|
| `fn_pixel` | `^PXL_(\d{8})_(\d{6})(\d{3})` | Google Pixel | **UTC** | 2 point |
| `fn_onedrive` | `^(\d{8})_(\d{6})(\d{3})_(iOS\|Android)` | OneDrive camera upload | usually **UTC**; verify against EXIF | 2 point |
| `fn_android` | `^(IMG\|VID\|PANO\|MVIMG\|BURST\|PORTRAIT)_?(\d{8})_(\d{6})` | Android camera | local | 2 point |
| `fn_samsung` | `^(\d{8})_(\d{6})(?:\(\d+\))?\.` | Samsung | local | 2 point |
| `fn_dropbox` | `^(\d{4}-\d{2}-\d{2}) (\d{2})\.(\d{2})\.(\d{2})` | Dropbox upload (from EXIF) | local | 2 point |
| `fn_whatsapp` | `^(IMG\|VID\|AUD\|PTT\|STK)-(\d{8})-WA\d{4}` | Date **received/saved**; EXIF stripped | local | 3 **upper_bound** (date + 23:59:59) |
| `fn_signal` | `^signal-(\d{4}-\d{2}-\d{2})-(\d{6})` | Save time | local | 4 upper_bound |
| `fn_telegram` | `^(photo\|video)_(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})` | Export time | local | 4 upper_bound |
| `fn_insta` | `(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})_UTC` | Post time | UTC | 4 upper_bound |
| `fn_android_shot` | `^Screenshot_(\d{8})[-_](\d{6})` | Screenshot | local | 1 point |
| `fn_mac_shot` | `^Screen ?[Ss]hot (\d{4}-\d{2}-\d{2}) at (\d{1,2})\.(\d{2})\.(\d{2})(?: ?([AP]M))?` | macOS screenshot | local | 1 point |
| `fn_win_shot` | `^Screenshot (\d{4}-\d{2}-\d{2}) (\d{6})` | Windows screenshot | local | 1 point |
| `fn_epoch` | `(?<!\d)(1\d{9})(\d{3})?(?!\d)` | Unix seconds/ms; accept only if within 2000…now | UTC | 3 point |
| `fn_generic_dt` | `(?<!\d)((?:19\|20)\d{2})[-_.]?(0[1-9]\|1[0-2])[-_.]?(0[1-9]\|[12]\d\|3[01])[ _T.-]?([01]\d\|2[0-3])[-_.:h]?([0-5]\d)[-_.:m]?([0-5]\d)(?!\d)` | Any YYYYMMDD-HHMMSS | unknown | 3 point |
| `fn_generic_d` | `(?<!\d)((?:19\|20)\d{2})[-_.]?(0[1-9]\|1[0-2])[-_.]?(0[1-9]\|[12]\d\|3[01])(?!\d)` | Date only | — | 3 point (day precision) |
| `dir_date` | same as `fn_generic_d`, or `(?:19\|20)\d{2}[-_ ](0[1-9]\|1[0-2])`, or a bare `(?:19\|20)\d{2}` | Folder names (`2014-07 Croatia`, Lightroom `2014/2014-07-12`) | — | 3 point (day/month/year precision) |
| `fn_counter` | `^(IMG\|DSC\|DSCF\|DSCN\|_MG\|P\d{3}\|GOPR\|GH\d{2}\|DJI)_?(\d{4,})` | Camera counter, no date | — | ordering only |

Filename pitfalls:
- **DD-MM vs MM-DD:** if both readings are valid, check against other sources. If nothing
  helps, mark ambiguous.
- **2-digit years** (`'98`): pivot around 70.
- **Suffixes:** `(1)`, `-1`, `_edited`, `-edited`, `IMG_E1234` (iOS edited copy) are
  duplicate or edit markers, not dates.
- **Recovered-file names:** PhotoRec (`f12345678.jpg`) and Facebook export names are
  meaningless. Ignore them.

### G. Sidecars, exports, catalogs, logs

| Source | Field | Notes | Tier/Kind |
|---|---|---|---|
| Google Takeout `*.json` / `*.supplemental-metadata.json` | `photoTakenTime.timestamp` | UTC epoch; survives EXIF stripping | 1 point |
| | `creationTime` | Upload time | upper_bound |
| Apple Photos `Photos.sqlite` | `ZASSET.ZDATECREATED` | Core Data epoch (+978307200 to get Unix); may contain the user-adjusted date | 1 point |
| | `ZADDEDDATE` | Date added to the library | upper_bound |
| Apple `.AAE` | `adjustmentTimestamp` | Edit time; also pairs the edit with its original | upper_bound |
| Facebook / Instagram export JSON | `creation_timestamp` | Upload time | upper_bound |
| | `taken_timestamp` | If present | 2 point |
| Lightroom `.lrcat` | `Adobe_images.captureTime` | May hold a manual correction | 2 point (human) |
| digiKam / Shotwell / Immich / PhotoPrism / Synology Photos DB | capture date columns | | 2 point |
| WhatsApp `_chat.txt` | line `DD/MM/YYYY, HH:MM - Name: IMG-…-WA0007.jpg (file attached)` | Exact send time; locale-dependent format | 3 upper_bound (tight) |
| Email `Date:` header of the message carrying the attachment | | | upper_bound |
| Cloud APIs: OneDrive `takenDateTime`, Dropbox `time_taken`, Google Photos `creationTime`, Flickr `date_taken`/`date_posted` | | | 2 point / upper_bound |

Takeout quirks:
- JSON names are truncated at about 46–51 characters.
- `IMG(1).jpg` pairs with `IMG.jpg(1).json`.
- Edited files `*-edited.jpg` share the original's JSON.
- File mtime in the export is the export date.

### H. Filesystem and archives

| Source | Meaning | Tier/Kind |
|---|---|---|
| mtime | Preserved by `cp -p`, `rsync -t`, robocopy and camera card copies. If it is within a few seconds of the EXIF time, it confirms the camera clock (same clock: no extra vote) | 3 point / corroboration |
| Birth time (NTFS, APFS/HFS+, ext4 `crtime` via `statx`, Btrfs `otime`) | Usually the copy time | 4 upper_bound |
| ctime (Unix) | Inode change, **not** creation | ignore |
| atime | | ignore |
| NTFS `$FILE_NAME` timestamps, USN journal | Forensic; harder to fake | 4 upper_bound |
| FAT / exFAT on the original card | Camera-written, local time. exFAT stores a UTC offset; FAT has 2 s mtime resolution and a 1980 epoch | 3 point |
| ZIP / TAR / 7z entry times | Freeze the original mtime even after many copies. ZIP is DOS local time (2 s); its extended-timestamp field is UTC | 3 point / upper_bound |
| macOS xattrs `kMDItemContentCreationDate`, `kMDItemDownloadedDate`, quarantine | | 3–4 |
| Optical disc (ISO9660) dates; burn date | | upper_bound |

### I. Content inference (tier 5, last resort)
- OCR of burned-in dates (film imprints like `'98 5 14`, dashcam or CCTV overlays, the
  screenshot status-bar clock). Use `pytesseract` on the corners or status bar.
- Print backs and lab backprint codes (scans).
- Visual dating: age of known people, season, devices, cars. Only gives a range; record it as
  a lower+upper bound pair.

### J. Bounds
- **Lower bounds:**
  - camera, lens and OS release date (keep a small lookup table, or treat "the first date this
    model appears anywhere in the archive" as a soft lower bound);
  - format introduction: HEIC 2017-09, EXIF 2.31 offsets 2016, Live Photos 2015-09.
- **Upper bounds:** upload, receive, export, backup and archive dates; the earliest birth time
  across all copies; the earliest XMP history event; WhatsApp filename date.

---

## Step 5: Duplicate clustering

Run the levels in order. Union-find the results into `cluster_id`.

| Level | Key | Catches |
|---|---|---|
| L1 exact | `size` → `blake3` | Plain copies |
| L2 same data | `ImageDataHash` / video `streamhash` | Same pixels, edited metadata, renamed |
| L3 identity | `serial + shutter_count`; `ImageUniqueID`; `OriginalDocumentID`; Apple `ContentIdentifier` (+ `MediaGroupUUID`) | Same exposure across edits and exports |
| L4 near-dup | `phash` Hamming ≤ 6 **and** `dhash` ≤ 8 (tune on a sample); aspect ratio within 2% or a crop check; video: ≥ 70% of keyframe hashes match | Recompressed, resized, messenger copies, HEIC→JPG |

Guard rails for L4:
- Bursts and near-identical sequential shots can collide. Do not merge L4 matches whose
  tier-1 timestamps differ by more than 2 s **and** both look camera-original.
- Require a second signal for merges across different cameras.

### Siblings (same moment, different products: keep all, share the date)
- RAW + JPEG: same basename **and** same DateTimeOriginal+SubSec. Never pair on time alone: fast
  bursts (10+ fps) can give consecutive frames an identical `SubSecTimeOriginal`. Use the
  basename/counter or the shutter count as the tie-breaker.
- Live Photo: HEIC/JPG + MOV with the same `ContentIdentifier`.
- HDR / AEB brackets, bursts (`BurstUUID`), panorama source frames, Motion Photos.
- iOS `IMG_1234.HEIC` + `IMG_E1234.HEIC` + `.AAE`.

Store these in `sibling_group`. They resolve to the same time but are never deduplicated
away.

### Master selection within a cluster
Score each member, highest wins:
1. Original format (RAW, original HEIC or MOV) over transcoded JPEG/MP4.
2. Not re-encoded: `Software` is camera firmware, not WhatsApp, Lavf, Photoshop or Facebook.
3. Highest pixel count, then highest bitrate or file size.
4. Most complete metadata (count of tier-1 fields present, GPS present).
5. Earliest internal timestamps.
6. Not marked corrupt.

---

## Step 6–8: Candidate filtering and scoring

### Reject (`valid=0`, with `reject_reason`)
- Empty, unparseable, `0000:00:00 00:00:00`, `    :  :     :  :  `.
- **Calendar-invalid** values (e.g. 2026-09-31, Feb 29 in non-leap years, hour 24, minute 60),
  above all those parsed from filenames. Reject the candidate, log it, and keep processing the
  rest of the batch: one bad value must never abort a batch.
- Epoch artefacts: 1904-01-01, 1970-01-01, 1980-01-01 (any time).
- Factory defaults: Jan 1 00:00–00:10 of 2000–2012 when the value comes from the camera
  clock. Also any timestamp shared by more than 20 files from different devices (batch
  overwrite or import time).
- Future (after the extraction date) or before 1826. Below the lower bound or above the
  tightest upper bound for that cluster.
- Container dates on re-encoded videos (downgrade to upper_bound instead of rejecting).

### Tier weights

| Tier | Weight | Typical sources |
|---|---|---|
| 1 | 10 | DateTimeOriginal (camera firmware), GPS UTC, Apple Keys:CreationDate, MDPM, MakerNotes, Takeout photoTakenTime, Photos.sqlite ZDATECREATED, screenshot filenames |
| 2 | 6 | CreateDate, QuickTime mvhd, XMP/IPTC created, precise device filenames, catalogs, preview EXIF |
| 3 | 3 | Sidecar THM/XML/SRT, card mtime, date-only names, folders, chat-log times, PNG Creation Time |
| 4 | bound | ModifyDate, MetadataDate, PNG tIME, birth time, upload/export/receive |
| 5 | 1 | Interpolation, OCR, content inference |

Modifiers:
- ×0.5 if `Software` is an editor or messenger.
- ×0.7 if the timezone is unknown and the value is used for absolute ordering.
- ×1.2 if SubSec is present.
- ×0 if the value violates a bound.

### Scoring algorithm (per cluster)

```python
def resolve(cluster):
    C = [c for f in cluster.files for c in f.candidates if c.valid and c.kind == "point"]
    lo = max(b.value for b in cluster.lower_bounds, default=MIN)
    hi = min(b.value for b in cluster.upper_bounds, default=NOW)
    C = [c for c in C if lo <= c.instant_or_local <= hi]

    # 1. timezone reconciliation: pair naive-local with UTC sources
    for naive in [c for c in C if c.utc is None]:
        for ref in [c for c in C if c.utc is not None]:
            d = naive.local_naive - ref.utc
            if abs(d - round_to_15min(d)) <= max(5 * 60, ref.precision_s):
                naive.tz_offset = round_to_15min(d)
                naive.utc = naive.local_naive - naive.tz_offset
                naive.tz_status = "derived"
    # unresolved naive values: assume the archive's home timezone, tz_status="assumed"

    # 2. group candidates that agree (±2 s, or ±GPS staleness up to 120 s, or ± filesystem
    #    timestamp resolution: FAT/exFAT copies can round mtime to 2 s, HFS+ 1 s)
    groups = agglomerate(C, tolerance=lambda a, b: max(2, a.precision_s, b.precision_s))

    # 3. score each group: one vote per independent clock (max weight per clock)
    for g in groups:
        g.score = sum(max(c.weight for c in g if c.clock == k) for k in {c.clock for c in g})
        g.earliest = min(c.utc for c in g)

    # 4. choose: highest score; tie → earliest (metadata degrades forward)
    best = max(groups, key=lambda g: (g.score, -g.earliest.timestamp()))
    runner = second_best(groups)
    margin = best.score - (runner.score if runner else 0)
    clocks = {c.clock for c in best}

    conf = ("high"   if len(clocks) >= 2 and margin >= 6 else
            "medium" if best.score >= 10 and margin >= 3 else
            "low"    if best.score > 0 else "none")
    return best, conf
```

For a cluster with no tier-1/2 point value but a tight upper bound (e.g. a WhatsApp copy with
nothing else), use the bound as the date, `method="upper_bound"`, `confidence="low"`.

---

## Step 9: Camera clock correction

Cameras often run with a wrong clock: never set, not switched to DST, or not changed after
travelling.

1. For every camera body (`make + model + serial`, or `model` alone if no serial), split its
   files into sessions (a gap of more than 6 h).
2. In each session, collect **offset samples**:
   - GPS UTC vs EXIF local within the same file;
   - the same scene shot by a GPS-enabled phone (L3/L4 match or same GPS cell ±2 min), compared
     with the camera EXIF;
   - a photo of a clock or screen (OCR).
3. If the median offset is a multiple of 15 min (±5 min), it is a **timezone**. Set the
   offset for the session, `tz_status="derived"`.
4. If it is anything else (e.g. +3 h 17 min, or +1 y −2 d), it is **clock error**. Apply the
   median correction to every file of that body in the session, including files without GPS.
   Record `method="clock_corrected"` and the delta.
5. Drift: if samples within a long session show a trend (seconds per day), fit a line and
   correct per file.
6. Default dates (factory epoch) with a working counter: re-date using the
   counter-interpolation in Step 10, anchored on the nearest correctly dated frames of the
   same body.
7. **Learn the QuickTime date convention per device model and firmware.** Whether a model's
   QuickTime `CreateDate` is true UTC or local time varies by vendor. A typical symptom is
   action-cam or phone videos landing exactly 1 or 2 h off.
   - Decide from evidence: compare against GPS UTC (GoPro GPMF `GPSU`, `Keys:CreationDate`
     with offset), filename times, or the same body's photos shot minutes apart.
   - Store the result in a `qt_convention(model, firmware) = utc|local` table and apply it to
     every video from that model.
8. **Convert between UTC and local time with the IANA timezone database per timestamp
   (DST-aware), never with a fixed offset.** A constant "+1 h" fix is right in summer and wrong
   in winter, or the reverse. Use the location-derived zone (`timezonefinder`) where available,
   otherwise the archive's home zone, e.g. `Europe/Bratislava`.

---

## Step 10: Gap filling: context heuristics and constraint propagation

Represent every file's time as an **interval `[lo, hi]` plus an optional point estimate**,
never as a bare guess. A resolved file has a narrow interval (seconds); a folder-name date
gives a day, month or year interval; an upload date gives `hi` only. The heuristics below
add **constraints** between files. Propagate them until nothing changes (interval constraint
propagation over a partial-order graph), then pick a point inside each interval. The
interval width is the confidence.

### 10.1 Counter-sequence siblings (strongest heuristic)

Files from **one camera body** share a counter: e.g. `DSC0001.JPG`, `DSC0002.MPG`,
`DSC0003.JPG`; Canon `IMG_` and `MVI_` share numbering; iPhone `IMG_` covers photos, videos
and screenshots. The counter is assigned in capture order, so within a body,
**counter order ⇒ time order**.

1. **Build the body sequence.** Group by body first (serial or device unit from the device
   resolver, plus prefix, DCIM folder scheme and structural fingerprint). Never mix two
   bodies with the same prefix.
2. **Make one monotonic index.** `index = folder_number × 10000 + file_counter`
   (`100CANON/IMG_9999` → `101CANON/IMG_0001`). Detect resets (card format, "reset numbering"):
   a drop in index with a forward jump in time starts a new segment.
3. **Exclude separate counters.** Some vendors number videos separately (Sony `C0001.MP4` vs
   `DSC00001.JPG`). Don't cross-interpolate between different counters; for those, use time
   interleaving only (10.3).
4. **Constraint.** For a file with index `n` between dated neighbours `(n1, t1)` and
   `(n2, t2)`: `t ∈ [t1, t2]`. That is a hard bound when the counter segment is continuous.
5. **Video specifics.** The counter is taken at **record start**. Container dates may be start
   or end of recording, depending on the model. Learn which per model, using cases where the
   next photo is known: next photo time ≥ video start + duration.
   - A video's own interval is `[t_prev, t_next − duration]`.
   - A photo right after a video gets `lo = video_start + duration`.
6. **Point estimate.**
   - If `t2 − t1` ≤ 10 min: interpolate linearly by index (`method=counter_interp`, medium
     confidence; high if the gap is ≤ 1 min).
   - If larger: take the nearer neighbour's time with an interval of the whole gap
     (`method=counter_bound`, low confidence).
   - Location gets the same treatment (see the location resolver).
7. **Inverted case** (videos dated, photos not) works the same way. First apply the model's
   QuickTime UTC/local convention (Step 9), or the photos inherit an hour-shifted time.
8. **Long-range fallback.** For files far from any dated neighbour, fit index → time per body
   (piecewise-monotonic regression over all dated files). This gives a weeks-to-months
   estimate, low confidence.

### 10.2 Folder semantics: learn what each folder's date means

A folder name date can mean the **event date**, the **import date** or the **backup/export
date**. Learn which per folder before using it.

- **Coherence test.** Of the folder's files that already have resolved dates, what share falls
  inside the folder's date interval?
  - ≥ 80% → the folder is an **event/capture folder**. Undated files inherit the folder
    interval (day/month/year precision); the point estimate is the median of its dated files.
  - Mostly *before* the folder date → it is an **import/backup date**: use it as an upper
    bound (`hi`) for undated files only.
  - Incoherent → ignore the folder's date.
- **Hierarchy.** Intersect intervals from all levels (`2014/` ∩ `2014-07 Croatia/` ∩
  `Day 3/`). The deepest coherent level wins.
- **Ordinal names** ("Day 3", "Part 2") are offsets from a dated sibling folder or parent.
- **Folder timestamps.** Directory birth time ≈ import time → `hi`. Directory mtime = last
  change: weak.
- **Folder type classifier.** DCIM-style, event, date-import (Lightroom/Photos-style, usually
  capture date = reliable), backup dump, messenger, downloads, screenshots. Messenger and
  download folders never pass dates to their files as capture time.
- **Place words** in the folder name feed the location resolver (with the same coherence test
  against geotagged members).

### 10.3 Other context constraints

| Heuristic | Constraint / estimate |
|---|---|
| **Import batch.** Files with near-identical birth time or copy order (one card dump) | Batch interval = [min, max] of its dated members, widened a little; `hi` = import time |
| **Card directory order.** On an original card or its image, FAT directory entries are in write order | Order ⇒ time order (like a counter) |
| **Visual near-duplicates / same scene** (perceptual hash, CLIP embedding) with dated shots | Same event → their interval |
| **Burst / bracket / panorama frames** | Same time ± seconds |
| **Derived files** (edits, exports, messenger copies, uploads, emails) | Original's time ≤ derivative's time |
| **Device ownership period** (first/last seen dates of the unit, firmware/OS versions present) | `[first_seen, last_seen]` for files with that unit's fingerprint |
| **Model / format / lens release dates** | `lo` |
| **Lens or accessory first use** (lens serial first seen) | `lo` for later files with that lens |
| **Sensor dust pattern** evolution (interchangeable-lens cameras) | Ordering between cleanings |
| **Location visited once** (from track history) | An undated photo geolocated (visually or by text) to a place the user visited only once → that visit's date |
| **People and age.** Face clustering + age estimation for children; a known birthday | Year-level interval |
| **Text in the image.** Birthday candles or number balloons, cake text, calendars, newspapers, screens, chat timestamps, status-bar clock | Day or exact time |
| **Season and holidays.** Snow, foliage, Christmas tree, Easter, beach | Month window (hemisphere-aware) |
| **Daylight sanity check.** Exposure/brightness (EV), and sun elevation from time + location | Catches AM/PM swaps, 12 h shifts and wrong timezones: a "14:00" night shot is wrong |
| **Calendar / event data** (user's calendar export, known trips) | Interval for event-named folders |

### 10.4 Order of trust for heuristic estimates

1. Counter neighbours within minutes, and burst/sibling inheritance.
2. Visual near-duplicates of dated shots; import batch with a tight range.
3. Coherent folder date (day).
4. Counter neighbours hours or days away; coherent folder date (month/year).
5. Device timeline; long-range counter regression.
6. Content cues (age, season, OCR).

Every inherited value records `method`, the source files and the interval width. If nothing
narrows a file below a year, set `confidence="none"`; the file goes to `unknown_date/`
(sub-sorted by any known bounds).

---

## Step 11: Review report

Generate an HTML or CSV report (an artifact works well) listing:
- clusters with `confidence in (low, none)`;
- clusters where `runner.score ≥ 0.6 × best.score` and the two dates differ by more than 1 h;
- clock corrections applied (body, session, delta, sample count);
- ambiguous DD-MM / MM-DD parses;
- L4 near-duplicate merges with a borderline distance (show thumbnails side by side);
- files marked corrupt.

Each row shows the thumbnail, the chosen date and source, all candidates with tier and clock,
and the rejected values with their reasons. Let the user override; overrides become
`clock="human"`, tier 1.

---

## Step 12: Apply

Always dry run first: produce an action plan CSV (`src, dst, writes`) and show a sample.

### Writing dates
Prefer XMP sidecars for RAW and for anything the user hasn't approved for in-file writes.

```bash
# images: write resolved local time + offset
exiftool -P -overwrite_original_in_place \
  "-DateTimeOriginal=2023:05:14 15:30:12" "-CreateDate=2023:05:14 15:30:12" \
  "-OffsetTimeOriginal=+02:00" "-OffsetTime=+02:00" \
  "-XMP-exif:DateTimeOriginal=2023:05:14 15:30:12+02:00" file.jpg

# videos: QuickTime dates must be UTC
exiftool -P -overwrite_original_in_place -api QuickTimeUTC \
  "-QuickTime:CreateDate=2023:05:14 15:30:12+02:00" \
  "-QuickTime:MediaCreateDate=2023:05:14 15:30:12+02:00" \
  "-Keys:CreationDate=2023:05:14 15:30:12+02:00" file.mp4

# filesystem mtime + creation time (FileCreateDate works on Windows and macOS)
exiftool "-FileModifyDate=2023:05:14 15:30:12+02:00" "-FileCreateDate=2023:05:14 15:30:12+02:00" file
```

- **Copy/move tools often preserve only the modified time.** On Windows the standard copy keeps
  mtime but sets the creation time to the copy time. After any copy or move, set both
  timestamps explicitly, per file.
- **Verify after writing**: re-read the file's metadata dates and filesystem times, compare
  with the planned values (within the target filesystem's resolution), and log any mismatch.

For thousands of files, generate an ExifTool argfile and run `exiftool -@ args.txt` once,
using `-execute` between files.

Never write to files marked corrupt. Copy them as-is, and list them in the report.

### Layout and naming (suggested)

```
YYYY/YYYY-MM-DD[ event]/YYYYMMDD_HHMMSS[_mmm]_<model>_<orig-basename>.<ext>
_duplicates/<cluster_id>/<original relative path>
unknown_date/<original relative path>
```

- Keep the original basename in the new name, so sidecars, chat logs and the user's memory
  still match.
- Move siblings together; sidecars follow their master.
- Make names collision-free by construction. `datetime + subsec` is **not** unique (bursts
  share subsec values), so always include the original basename/counter or the shutter count.
  Use `_1`, `_2` only as a last resort.
- Generate names and paths in one deterministic function: one pass, with no chained
  rule/script stages that can each apply their own collision handling.
- **Every path component must exist even when a value is missing.** Use explicit placeholders
  (`unknown-date`, `unknown-place`), never empty strings that leave double separators or
  trailing spaces.
- **Sanitize for the target filesystem**:
  - Windows: no trailing spaces or dots; no `<>:"/\|?*`; avoid reserved names (`CON`, `NUL`…);
    watch path length.
  - Normalize Unicode to NFC (macOS NFD names otherwise create look-alike duplicates).
- **Move siblings and sidecars as a unit**: RAW+JPEG, Live Photo HEIC+MOV, `.xmp`, `.THM`,
  `.AAE`, `.SRT`, `.LRV`, Takeout `.json`. The whole group gets the same new base name; a
  partial failure rolls back the whole group.

### Logs to keep (next to the output)
- `resolution.sqlite` (the full DB);
- `actions.csv` (every move, rename and write, with the original path);
- `undo.sh` / `undo.ps1` generated from `actions.csv`.

---

## Corrupted and recovered files

- **JPEG:** EXIF sits at the start, so truncated files usually keep their dates. ExifTool still
  reads them; report `Warning` tags.
- **MP4/MOV:** the `moov` atom is often at the end, so a truncated file loses its metadata and
  index. Try `untrunc` with a healthy reference file from the same device. Dates may then come
  only from the filename, sidecars or the filesystem.
- **Carved files** (PhotoRec): names and filesystem dates are meaningless; internal metadata is
  usually intact.
- Mark `corrupt=1` if ExifTool reports `Error`, or the decoder fails (Pillow `verify()`, or
  `ffprobe` fails or reports no streams).

---

## Pitfalls checklist

- [ ] EXIF times are local and naive unless `OffsetTime*` is present. GPS is UTC. QuickTime is
      "UTC" but often actually local time.
- [ ] DST transitions: an offset that changes mid-trip is real, not an error.
- [ ] Travelling with an unchanged camera clock: an offset that is whole hours, for one whole
      session.
- [ ] Windows Explorer / Photos rotation, Google Photos edits and Lightroom exports rewrite
      `ModifyDate` and sometimes `CreateDate`.
- [ ] Messengers and social networks strip EXIF; their filename dates are receipt or upload
      times.
- [ ] Takeout and cloud downloads set mtime to the export date.
- [ ] Scans: `CreateDate` = scan date. The real date is in IPTC/XMP or the folder, or it is
      unknown.
- [ ] Same `DateTimeOriginal` on many different files usually means a batch "fix" by some tool;
      treat it as suspicious.
- [ ] 12 h/24 h and AM/PM parsing in macOS screenshot names and chat logs; locale-specific date
      order.
- [ ] HEIC/HEIF orientation must be applied before perceptual hashing.

---

## Reference tools

| Purpose | Tool |
|---|---|
| Metadata read/write | ExifTool (core), ffprobe, MediaInfo |
| Exact duplicates | fclones, rmlint, jdupes |
| Near duplicates | imagehash / imagededup (Python), czkawka, dupeGuru, digiKam |
| Apple Photos library | osxphotos |
| Google Takeout | GooglePhotosTakeoutHelper (gpth), or the JSON parsing described here |
| Broken MP4/MOV | untrunc |
| Recovery | PhotoRec |
| OCR | Tesseract (pytesseract) |
| Simple sorters, for comparison | Phockup, elodie (no arbitration logic) |

## Deliverables to the user

1. A short summary: totals, duplicates removed or grouped, confidence distribution, clock
   corrections applied.
2. The review report (artifact) for low-confidence and conflicting items.
3. The action plan CSV and undo script, for approval **before** any write or move.
4. After approval: the organized tree plus `resolution.sqlite`.
