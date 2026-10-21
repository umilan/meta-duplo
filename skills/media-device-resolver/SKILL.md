---
name: media-device-resolver
description: Identify the device of origin (brand, model, individual unit, owner) and origin type (camera, phone, screenshot, scan, messenger copy, edit, AI) of photos and videos in a messy archive, including files with stripped or rewritten metadata. Use for sorting by device/owner, attribution, or provenance checks.
---

# Media Device Resolver

Work out **which device produced each photo or video**:
- **Level 1 (brand + model):** e.g. "Canon EOS 600D".
- **Level 2 (individual unit):** which physical body or phone produced it, e.g. "serial 123…",
  or "iPhone 13 #2".
- **Level 3 (owner):** e.g. "Dad's camera".
- **Origin type:** original capture, screenshot, scan, messenger copy, edited export,
  download, or AI-generated.

Results are confidence-scored, and checked for consistency so that rewritten metadata can be
detected.

Out of scope: exposure and lens optics parameters (aperture, ISO, focal length…), except where
a field happens to identify hardware (lens serial, camera-module name).

## When to use

- Sorting or filtering an archive by device or by person.
- Separating family members' phones of the **same model**.
- Attributing stripped copies (WhatsApp, Facebook, recovered files) to their source device.
- Spotting screenshots, scans, downloads, edits and AI images inside a "photos" archive.
- Checking whether metadata was faked or rewritten.

## Relationship to other skills

- `media-capture-date-resolver`: provides clusters, siblings, resolved times and camera counters.
  This skill feeds back the device timelines and per-body clock corrections it needs.
- `media-location-resolver`: device identity tells which track belongs to which person, and
  session fill must not mix different people's devices.

Run order: dates (pass 1) → devices → locations → dates (pass 2: refine).

## Core principles

1. **Never modify originals.** Device results live in the DB and in XMP sidecars or keywords.
   Never invent EXIF `Make`/`Model` values in files.
2. **Model ≠ unit.** Make/Model identifies a product. Serial numbers, a sensor fingerprint or
   context identify a **unit**. Many phones write no serial.
3. **Consistency beats a single tag.** Make/Model can be edited in one command. The structural
   fingerprint (quantization tables, MakerNote layout, tag set, dimensions, encoder strings)
   is hard to fake consistently. Agreement means trust; disagreement means "rewritten" or
   "re-saved".
4. **Copies inherit, they don't testify.** A messenger copy's own fingerprint identifies the
   *messenger*, not the camera. Device identity flows from the duplicate cluster's original.
5. **Owners are user-labelled.** Infer device units automatically, but let the user name them.

---

## Workflow overview

```
1. Load        → files, clusters, siblings, resolved dates, counters (from the date resolver)
2. Extract     → identity tags, software/firmware, structural fingerprint, video encoder strings
3. Context     → folders, filenames, Takeout/Photos.sqlite/catalog origin fields, chat exports
4. Classify    → origin type per file (capture / screenshot / scan / messenger / edit / download / AI)
5. Normalize   → brand/model alias tables, Android model codes → marketing names
6. Units       → split models into physical units (serial, counters, PRNU, timelines, context)
7. Validate    → consistency checks; flag rewritten or spoofed metadata
8. Propagate   → duplicates, siblings, sessions → stripped files
9. Visual      → PRNU for stripped files; watermark/overlay OCR; screenshot UI cues
10. Score      → per cluster: device unit, model, origin type, confidence
11. Review     → device list: merge/split units, assign owners
12. Apply      → XMP sidecar/keywords, folder layout by device/owner, logs
```

---

## Step 2: Embedded identity sources

### Extraction

```bash
exiftool -r -a -G1 -s -u -json -charset filename=utf8 -api LargeFileSupport=1 \
  -Make -Model -UniqueCameraModel -LocalizedCameraModel -HostComputer \
  -SerialNumber -BodySerialNumber -InternalSerialNumber -CameraSerialNumber \
  -LensMake -LensModel -LensSerialNumber -LensID \
  -CameraOwnerName -OwnerName -Artist -Copyright -By-line -Credit -Source \
  -Software -FirmwareVersion -CreatorTool -OriginatingProgram -ProgramVersion \
  -HistorySoftwareAgent -ImageUniqueID -ModelID -CameraID \
  "-*ContentIdentifier*" "-*MediaGroupUUID*" "-*BurstUUID*" \
  -MakerNoteType -ExifByteOrder -ExifVersion -ImageWidth -ImageHeight \
  -ExifImageWidth -ExifImageHeight -ThumbnailLength -ProfileDescription \
  -JFIFVersion -JPEGQualityEstimate -EncodingProcess -YCbCrSubSampling \
  -MajorBrand -CompatibleBrands -HandlerDescription -HandlerVendorID \
  -CompressorName -Encoder -VideoCodec "-Keys:all" "-UserData:all" \
  -FileName -Directory /archive > dev_meta.json

# structure fingerprint helpers
exiftool -htmlDump file.jpg > structure.html         # manual inspection
exiftool -v3 file.jpg | grep -E "DQT|APP[0-9]+|SOF"  # segment order and tables
ffprobe -v error -show_format -show_streams -of json video.mp4
c2patool file.jpg                                     # C2PA manifest, if present
```

`-u` includes unknown tags. Unknown MakerNote tag sets are themselves a fingerprint.

**Extraction integrity**:
- Key every record by `SourceFile`, never by order; batch tools have been seen attaching one
  video's metadata to another in large batches.
- Re-read files with empty identity fields in small batches.
- Spot-check a random sample individually.

A wrong Make/Model caused by the extractor would poison the unit clustering.

### Identity table

| Source (group:tag) | Identifies | Notes | Tier |
|---|---|---|---|
| `IFD0:Make`, `IFD0:Model` | Brand + model | Editable. Normalize (Step 5) | 2 |
| `DNG:UniqueCameraModel`, `LocalizedCameraModel` | Model (survives DNG conversion) | | 2 |
| `ExifIFD:BodySerialNumber` (0xA431), `MakerNotes:SerialNumber` / `InternalSerialNumber` / `CameraSerialNumber`, `XMP-aux:SerialNumber`, `XMP-exifEX:BodySerialNumber` | **Unit** | Cameras mostly; phones rarely. Some vendors encode the factory or date in the internal serial | 1 |
| `ExifIFD:LensSerialNumber`, `XMP-aux:LensSerialNumber` | Lens unit, which links bodies that share a lens collection (same owner) | | 2 (owner link) |
| `ExifIFD:LensMake/LensModel` on phones (e.g. "iPhone 13 Pro back triple camera 5.7mm f/1.5") | Model + camera module (main/ultra-wide/front) | Front camera = selfie | 2 |
| `ExifIFD:CameraOwnerName`, `MakerNotes:OwnerName`, `IFD0:Artist`, `IFD0:Copyright` set in camera | Owner | Set once in camera menus; strong owner signal | 1 (owner) |
| `IFD0:Software`, `MakerNotes:FirmwareVersion` | Firmware or OS version, or the editing software | Firmware progression over time = one unit's timeline. Editor names = a re-save | 2 / origin type |
| `IFD0:HostComputer` | Apple device or computer that wrote it | | 2 |
| `ExifIFD:ImageUniqueID` | Per-image ID; format is vendor-specific | Some encode device info | 3 |
| `MakerNotes:ModelID` / `CameraID` / `InternalModelID` | Exact model variant (e.g. a regional variant) | | 2 |
| `MakerNotes:ShutterCount` / `ImageCount` / `FileNumber` | Counter sequence per unit | Overlapping sequences at the same time = two units | 1 (unit split) |
| `Apple:ContentIdentifier`, `MediaGroupUUID`, `BurstUUID` | Sibling linking | | — |
| `XMP-xmp:CreatorTool`, `XMP-xmpMM:History softwareAgent` | Software chain | Origin type and edits | 3 |
| `IPTC:OriginatingProgram` / `ProgramVersion`, `By-line`, `Credit`, `Source` | Software / person / agency | | 3 |
| `IPTC:SpecialInstructions` beginning with `FBMD…` | Facebook-processed copy | | origin type |
| `XMP-iptcExt:DigitalSourceType` | `digitalCapture`, `trainedAlgorithmicMedia` (AI), `compositeWithTrainedAlgorithmicMedia`, `screenCapture`, `negativeFilm` / `positiveFilm` / `print` (scans), etc. | Self-declared | 2 (origin type) |
| **C2PA manifest** (JUMBF in APP11 / `c2pa` box) | Signed claim: device or app (`claim_generator`), signing certificate subject (vendor/device), actions (captured, edited, AI) | Cryptographically verifiable; validate with `c2patool`. Present on some newer cameras and phones and on many AI generators | 1 (if the signature is valid) |
| `XMP-GCamera:*`, Samsung trailer `MotionPhoto_Data`, Google `MotionPhoto` / Ultra HDR gain-map (`hdrgm`) | Vendor camera app | Pixel / Samsung / Android 14+ camera apps | 2 |
| HEIC auxiliary images (depth map, portrait/semantic mattes), Apple `HDRGainMap` | Apple camera features | Implies capable model range | 3 |
| ICC `ProfileDescription` (e.g. "Display P3", vendor-named profiles) | Pipeline family | Apple = Display P3; many cameras sRGB/Adobe RGB | 3 |

### Video identity

| Source | Notes |
|---|---|
| `Keys:Make` / `Model` / `Software` (`com.apple.quicktime.*`) | iPhone/iPad; the OS version gives a timeline |
| `Keys`/`UserData` Android keys (`com.android.version`, `com.android.manufacturer`, `com.android.model`, `com.android.capture.fps`) | Android camera apps, where present |
| `UserData:Make` / `Model` / `©mak` / `©mod` / `©swr`, Samsung `smta`/`sefd` atoms | Camera / Samsung |
| GoPro `udta`: `FIRM` (firmware → model), `CAME`/`CASN` (serial or serial hash), GPMF `DVNM` (device name) | Unit-level for GoPro |
| DJI `djmd`/`dbgi`, `©mdl`, SRT headers | Drone model |
| Sony XAVC XML sidecar: `Device manufacturer`, `modelName`, `serialNo` | Unit |
| Canon/Panasonic/Nikon MakerNotes inside MOV/MP4 (`CNTH`, `PANA`, `NCTG` …) | Model / unit |
| AVCHD MDPM `Make`/`Model` codes | Model |
| `ftyp` major brand (`qt  ` Apple, `mp42`, `isom`, `XAVC`, `CAEP` Canon, `niko`…) | Writer family |
| `HandlerDescription` / `HandlerVendorID` ("Core Media Video" Apple, "VideoHandle"/"SoundHandle" Android MediaRecorder, "Ambarella", "GoPro AVC", "DJI.AVC"…) | Writer family |
| `Encoder` = `Lavf…`, HandBrake, CapCut, Premiere, DaVinci; `CompressorName` | Re-encoded or edited; the device stays unknown unless inherited |
| Timescale (600 Apple, 90000…), track order, audio sample rate/channels, GOP structure, bitrate mode | Fingerprint features |

---

## Step 2b: Structural fingerprint (hard to fake)

Compute per file and store as `fp_*` columns. Hash the combination as `struct_fp`.

| Feature | How | What it reveals |
|---|---|---|
| JPEG quantization tables | Hash of the DQT tables (exiftool `-v3`, or Pillow `img.quantization`) | Camera firmware family vs re-save software (Photoshop, libjpeg quality N, WhatsApp, Facebook). Compare against a learned per-model table set |
| Huffman tables (standard vs optimized), progressive vs baseline | Pillow / exiftool `EncodingProcess` | Cameras are baseline with standard tables; optimized or progressive = re-saved |
| Chroma subsampling | `YCbCrSubSampling` | Per-model constant |
| APP segment set and order | Segment list (APP0 JFIF? APP1 Exif, APP1 XMP, APP2 ICC/MPF, APP11 JUMBF, APP13 IPTC, APP14 Adobe) | Cameras rarely write JFIF; Adobe adds APP13/APP14; phones add MPF and gain maps |
| EXIF byte order, IFD order, tag set and order, MakerNote type/offset, thumbnail size | ExifTool | Per-firmware signature; an edited file with re-ordered IFDs has been rewritten |
| Image dimensions and aspect | | Each sensor/model has a finite set of native resolutions; unusual sizes = cropped or resized |
| HEIC structure: item types, grid tiles, `hvcC` profile | ExifTool / `heif-info` | Apple vs Samsung vs others |
| Trailer data after EOI (Samsung `SEFT`, Google Motion Photo MP4) | Bytes after the final EOI | Vendor |
| File naming + DCIM folder scheme | Step 3 | Vendor |

Learn the **reference fingerprint** for each model from files that are clearly camera-original
(firmware `Software`, native dimensions, consistent tags). Then score every file's similarity
to the fingerprint of its claimed model.

---

## Step 3: Context sources

| Source | Field / pattern | Gives |
|---|---|---|
| DCIM folder names | `100CANON`, `100NIKON`/`NCD70`, `100MSDCF` (Sony), `100_FUJI`, `100OLYMP`, `100_PANA`, `100GOPRO`, `100MEDIA` (DJI), `100APPLE`, `Camera` (Android) | Vendor |
| File prefixes | `IMG_` (Canon, Apple…), `_MG_` (Canon, Adobe RGB), `MVI_` (Canon video), `DSC_`/`_DSC` (Nikon/Sony), `DSC0`, `DSCF` (Fuji), `DSCN` (Nikon compact), `P10…` (Panasonic/Olympus), `PXL_` (Pixel), `GOPR`/`GH`/`GX` (GoPro), `DJI_`, `MAH`/`C0001` (Sony video), `MVIMG_` (Google Motion), `YYYYMMDD_HHMMSS` (Samsung), `IMG_YYYYMMDD_HHMMSS` (many Android) | Vendor / OS |
| Screenshot names | `Screenshot_…_<App>.jpg` (Samsung includes the app), `Screenshot_YYYYMMDD-HHMMSS.png` (Android), iOS screenshots are `IMG_xxxx.PNG`, `Screen Shot …` / `Screenshot … at …` (macOS), `Screenshot YYYY-MM-DD HHMMSS.png` (Windows) | Origin type + OS |
| Messenger folders and names | `WhatsApp Images/` (`Sent/` = sent by this phone's owner), `IMG-…-WA….jpg`, `Telegram Images/`, `Signal/`, `Messenger/`, `Snapchat/`, `Instagram/` | Origin type = messenger copy; sender context |
| Backup/sync names | Folder names such as "iPhone Jana", "Galaxy S10 backup", "SD card 2014", `Camera Uploads`, OneDrive `_iOS`/`_Android` filename suffix | Unit/owner hints, OS |
| **Google Photos Takeout JSON** | `googlePhotosOrigin`: `mobileUpload` (`deviceType` e.g. `IOS_PHONE` / `ANDROID_PHONE`, `deviceFolder.localFolderName`), `webUpload`, `driveDesktopUploader`, `fromSharedAlbum`, `fromPartnerSharing`; plus the uploader account | OS, source folder, own-vs-shared |
| **Apple `Photos.sqlite`** | `ZADDITIONALASSETATTRIBUTES.ZIMPORTEDBY` (camera / screenshot / other app), `ZIMPORTEDBYBUNDLEIDENTIFIER` (e.g. `net.whatsapp.WhatsApp`), `ZORIGINALFILENAME`, `ZASSET.ZKIND` / `ZKINDSUBTYPE` (screenshot, Live, panorama…), shared-library contributor fields | Origin type, saving app, owner (shared library) |
| Catalogs (Lightroom `AgHarvestedExifMetadata.cameraRef` / `cameraSNRef`, digiKam, Immich, PhotoPrism) | Camera and serial columns, possibly from before the files were stripped | Model / unit |
| Chat exports | Who sent the file (`_chat.txt` line with the filename) | Sender = likely device owner |
| Email | `From:` of the message carrying the attachment | Sender |
| macOS xattrs `kMDItemWhereFroms`, Windows `Zone.Identifier` ADS | Download URL | Origin type = downloaded |

---

## Step 4: Origin type classification

Rules, evaluated in order. Store `origin_type` and `origin_reason`.

| Origin type | Signals |
|---|---|
| `ai_generated` | C2PA action or `DigitalSourceType` = trainedAlgorithmicMedia; generator software (Firefly, DALL·E, Midjourney params in PNG `parameters`/`prompt` text chunks, Stable Diffusion `parameters`, ComfyUI `workflow` JSON); typical generator sizes (512/768/1024 squares) with no camera tags |
| `screenshot` | Screenshot filename; `Photos.sqlite` subtype; XMP `UserComment`="Screenshot"; PNG with no camera tags at **device screen resolution** (e.g. 1170×2532, 1080×2400, 2880×1800); OCR of a status bar or notch area |
| `screen_recording` | Video at screen resolution, with variable frame rate, no camera keys, and the OS recorder's handler names |
| `scan` | Scanner `Make`/`Model` (Epson, Canon CanoScan, Plustek…), `Software` = scanning app (EPSON Scan, VueScan, SilverFast), DPI 300/600/1200+, `DigitalSourceType` film/print; or film-scan sizes |
| `messenger_copy` | Messenger filename or folder; known messenger re-encode fingerprint (quant tables, max dimension, metadata fully stripped); Facebook `FBMD` |
| `edited_export` | `Software`/`CreatorTool` = editor; xmpMM History; optimized Huffman tables; non-native dimensions; `-edited`, `IMG_E…`; Lightroom/Photoshop export structure |
| `video_edit_export` | Encoder = Lavf / HandBrake / NLE; no camera atoms |
| `downloaded` | Download xattrs/ADS; browser download folders; web-typical sizes/WebP; stripped metadata with no messenger signature |
| `camera_original` | Firmware `Software`, a native resolution, and a structural fingerprint matching the claimed model |
| `unknown` | None of the above |

A file can be `messenger_copy` and still have a known source device (inherited in Step 8).

---

## Step 5: Normalization

- **Brand aliases**:
  - `NIKON CORPORATION` / `NIKON` → Nikon
  - `OLYMPUS IMAGING CORP.` / `OM Digital Solutions` → Olympus/OM System
  - `samsung` / `SAMSUNG` → Samsung
  - `HUAWEI` / `HONOR` (split by date)
  - `Xiaomi` / `Redmi` / `POCO`
  - `LGE` → LG
  - `motorola` → Motorola
  - Trim trailing spaces and NULs.
- **Model aliases**: strip a repeated brand prefix ("Canon Canon EOS…"), unify `Mark III`/`Mk III`,
  and map regional names (e.g. EOS 600D = Rebel T3i = Kiss X5) to one canonical model with
  alias lists.
- **Android model codes → marketing names** (e.g. `SM-G991B` → Galaxy S21 5G). Use Google
  Play's supported-devices list (CSV with manufacturer, marketing name, model code); community
  mirrors exist as JSON. The suffix letters often encode the region/carrier variant; keep the raw
  code as the canonical key.
- **Apple**: EXIF has marketing names ("iPhone 13 Pro"). Some sources (backup manifests, video
  keys) use identifiers like `iPhone14,2`; map these with a lookup table.
- Keep `make_raw`, `model_raw` and `model_code` alongside the normalized values.

---

## Step 6: Unit identification (splitting a model into physical devices)

Start with one unit per `(canonical_model, serial)` when a serial exists. For files without a
serial, split each model into units using:

1. **Counter continuity**: each unit has its own filename counter (`IMG_0001…9999`, `DSC_`) and
   MakerNote shutter/file counter. Plot counter vs time per model.
   - **Two interleaved monotonic sequences over the same period = two units.**
   - A counter reset with a jump in firmware = possibly the same unit after a reset or service,
     or a replacement unit. Flag it.
2. **Firmware / OS timeline**: the same model with different OS versions on the same day =
   different units. Each unit's version only moves forward.
3. **Concurrency**: the same model shooting at the same time at distant places (location
   resolver) = different units.
4. **Context**: Takeout uploader account, Photos.sqlite shared-library contributor, backup
   folder name, messenger "Sent" folder, CameraOwnerName/Copyright.
5. **Sensor fingerprint (PRNU)**:
   - Build a reference fingerprint per candidate unit from about 30–50 originals (flat, bright,
     unedited; same orientation and resolution).
   - Test files by peak-to-correlation energy (PCE). The usual decision threshold is around 60;
     tune it on known pairs.
   - It works on stripped originals and moderate re-compression; it degrades with heavy resizing,
     messengers, and phone computational pipelines (multi-frame fusion, stabilization). Use a
     pre-registered geometry, the same camera module, and the same resolution.
6. **Hardware quirks**: hot/dead pixels and sensor dust spots (interchangeable-lens cameras;
   dust also changes over time, so it can help with dating), lens decentering or vignetting
   signatures, and lens serial co-occurrence.

Union-find these signals into `device_units`. Allow the user to merge or split.

---

## Step 7: Consistency checks (detect rewritten or spoofed metadata)

For each file with a claimed Make/Model:

| Check | Inconsistent if |
|---|---|
| Structural fingerprint vs model reference | Quant tables, APP set, MakerNote type or tag order don't match the model's camera-original reference |
| Dimensions | Not in the model's native resolution set (and not a known crop mode) |
| Firmware/OS vs date | Firmware or OS version released after the capture date; model released after the capture date |
| MakerNote vs IFD0 | MakerNote vendor ≠ Make (e.g. a Canon MakerNote under Make=Nikon) |
| Video atoms | Apple Make with Android handler names, or the reverse |
| C2PA | Manifest signature invalid, or its claim contradicts EXIF |
| Batch artefact | Many files from different fingerprints share an identical Make/Model/Software string written by one tool (ExifTool, digiKam) |

Results:
- `consistent`: tier unchanged.
- `re-saved`: the structure belongs to an editor, but the identity is plausibly inherited. Keep
  the model at tier 2, origin = `edited_export`.
- `suspect`: identity tags downgraded to tier 4; flagged for review.

---

## Step 8: Propagation

- **Duplicate clusters**: the cluster's device = the master/original's device. Stripped copies
  inherit it with `method=inherited`. A messenger copy without an original in the archive stays
  `unknown` unless other signals apply.
- **Siblings**: RAW+JPEG, Live Photo pairs, bursts and brackets share the unit.
- **Sessions**: within a session (time gap < 2 h), a file with an unknown device whose
  structural fingerprint matches exactly one unit active in that session gets that unit
  (`method=session_fp`, medium confidence).
- **Sender context**:
  - A messenger copy sent by person X (chat export or folder) → `owner_hint=X`; device model
    unknown unless inherited.
  - `WhatsApp Images/Sent` → the archive owner's own device of that period (low confidence).

---

## Step 9: Visual and content cues (stripped files)

- **PRNU match** against the unit references (Step 6). This is the strongest signal for stripped
  originals.
- **OCR of visible watermarks**: "Shot on <phone>", "SHOT ON MI…", Huawei/Leica/Hasselblad
  co-branding marks, camera-app date stamps, dashcam and action-cam overlays with the model name.
- **Screenshot UI**: the status-bar layout, notch/Dynamic Island, OEM skin, navigation bar and
  screen resolution identify OS, brand and sometimes model.
- **Front-camera cues** (mirroring, selfie framing) only refine the module, not the unit.
- **AI-detection classifiers**: treat as weak evidence; never the sole basis for `ai_generated`.

---

## Step 10: Scoring

| Tier | Weight | Evidence |
|---|---|---|
| 1 | 10 | Body/internal serial; valid C2PA device signature; PRNU PCE above threshold; CameraOwnerName (owner) |
| 2 | 7 | Make/Model with consistent structural fingerprint; MakerNote ModelID; video vendor atoms; lens serial (owner link); catalog camera/serial |
| 3 | 4 | Filename/DCIM scheme; Takeout `deviceType`/folder; Photos.sqlite importer; screenshot resolution; ICC/HEIC/vendor XMP features; firmware timeline fit |
| 4 | 2 | Inherited via session fingerprint; folder/backup names; sender context; suspect identity tags |
| 5 | 1 | OCR/UI/visual cues alone |

```python
def resolve_device(cluster):
    cands = gather(cluster)                 # (unit_id or model_key, evidence, tier, origin)
    by_unit = group(cands, key="unit_or_model")
    for g in by_unit:
        g.score = sum(max(e.weight for e in g if e.origin == o) for o in {e.origin for e in g})
    best = max(by_unit, key=lambda g: g.score)
    runner = second(by_unit)
    level = "unit" if best.has_unit_evidence else "model" if best.model else "brand" if best.brand else "none"
    conf = ("high"   if best.score >= 14 and (not runner or runner.score < 0.5 * best.score) else
            "medium" if best.score >= 7 else
            "low"    if best.score > 0 else "none")
    return best, level, conf
```

Evidence `origin` means independent provenance: EXIF writer, structural fingerprint, PRNU,
filesystem context, cloud export, user. EXIF Make and EXIF Model from the same file are one
origin.

---

## Step 11: Review

Build an artifact or CSV **device list**, one row per unit, with:
- brand, model, model code, serial (masked);
- first/last seen; file count;
- firmware/OS range; owner label (editable);
- sample thumbnails;
- counter-vs-time chart;
- flags: merged/split suggestions, suspect metadata, counter resets.

Second view: files with `level in (none, brand)` or low confidence, grouped by origin type.

Keep user decisions as `origin=user` evidence at tier 1, and re-run propagation.

---

## Step 12: Apply

- Write to **XMP sidecars or keywords**, not EXIF identity tags:
  - keywords: `device|<brand>|<model>|<unit-label>`, `owner|<name>`, `origin|<type>`;
  - optional `XMP-iptcExt:DigitalSourceType` for screenshots, scans or AI (only when certain).
- Optional layout: `by-owner/<name>/YYYY/…` or `by-device/<model>-<unit>/…`, implemented as
  symlinks or album entries to avoid duplicating data.
- Keep `device.sqlite`, the actions CSV and an undo script.

```bash
exiftool -o %d%f.xmp "-XMP-dc:Subject+=device|Canon|EOS 600D|Dad" "-XMP-dc:Subject+=origin|camera_original" file.CR2
```

---

## Database tables

```sql
CREATE TABLE dev_features(
  file_id INT PRIMARY KEY, make_raw TEXT, model_raw TEXT, model_code TEXT,
  brand TEXT, model TEXT, serial TEXT, internal_serial TEXT, lens_serial TEXT,
  owner_tag TEXT, software TEXT, firmware TEXT, host_computer TEXT,
  makernote_type TEXT, byte_order TEXT, width INT, height INT,
  qt_hash TEXT, huff_optimized INT, progressive INT, subsampling TEXT,
  app_segments TEXT, struct_fp TEXT, icc TEXT, ftyp TEXT, handler TEXT, encoder TEXT,
  c2pa_status TEXT, c2pa_signer TEXT, digital_source_type TEXT,
  counter INT, counter_kind TEXT, prnu_ref_unit INT, prnu_pce REAL,
  origin_type TEXT, origin_reason TEXT, consistency TEXT
);
CREATE TABLE device_units(
  unit_id INT PRIMARY KEY, brand TEXT, model TEXT, model_code TEXT, serial TEXT,
  owner_label TEXT, first_seen TEXT, last_seen TEXT, files INT,
  fw_min TEXT, fw_max TEXT, prnu_ref BLOB, notes TEXT
);
CREATE TABLE dev_resolved(
  cluster_id INT PRIMARY KEY, unit_id INT, brand TEXT, model TEXT,
  level TEXT, confidence TEXT, origin_type TEXT, method TEXT, score REAL, notes TEXT
);
```

---

## Pitfalls checklist

- [ ] Same-model family phones: without serials, use counters, OS timelines, context and PRNU.
- [ ] Phones replaced under warranty or restored from backup: the same "device" in cloud context,
      but a different unit (PRNU changes, counters may continue).
- [ ] Camera counters wrap at 9999 and reset when folders or cards change.
- [ ] One RAW converted by several tools produces several JPEG/DNG/TIFF "devices" in Software.
      Identity is the camera; Software only tells the origin type.
- [ ] ExifTool, digiKam or Lightroom batch edits can stamp identical Make/Model onto unrelated
      files (e.g. scans tagged with a camera). Check the structural consistency.
- [ ] Messenger and social copies carry the platform's fingerprint, not the camera's.
- [ ] Screen resolutions are shared by many models: OS/brand-level only, unless combined with UI
      cues.
- [ ] C2PA is only meaningful when the signature validates. A strip or re-save removes it
      silently; absence proves nothing.
- [ ] PRNU needs the same camera module and resolution; phones with multiple modules need
      separate references per module.
- [ ] Serial numbers and owner names are personal data. Mask them in shared reports.

---

## Reference tools

| Purpose | Tool |
|---|---|
| Tags and structure | ExifTool (`-u`, `-v3`, `-htmlDump`), ffprobe, MediaInfo |
| JPEG fingerprint | JPEGsnoop (signature database), Pillow `quantization`, ImageMagick `identify -verbose` (quality estimate) |
| Provenance | `c2patool` (Content Credentials) |
| Sensor fingerprint | PRNU implementations (e.g. polimi-ispl `prnu-python`), camera-fingerprint toolkits |
| Forensic viewers | Sherloq, Forensically, Ghiro |
| Model codes | Google Play supported devices CSV (and JSON mirrors); Apple identifier tables |
| Library context | osxphotos (Photos.sqlite fields), Google Takeout JSON parsing |
| OCR | Tesseract (watermarks, overlays, status bars) |

## Deliverables to the user

1. A device inventory: units with model, owner label, active period, file counts, confidence.
2. An origin-type breakdown (originals, screenshots, scans, messenger copies, edits, downloads,
   AI).
3. A review artifact for low-confidence and suspect items, and for merge/split suggestions.
4. After approval: XMP keywords or sidecars, optional by-owner/by-device views, `device.sqlite`,
   logs and an undo script.
