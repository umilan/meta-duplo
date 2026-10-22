---
name: metadata-extraction
description: What meta_duplo/metadata.py and meta_duplo/geo.py read from photos and videos (Pillow + pillow-heif for images, ffprobe for videos, optional reverse_geocoder for places), how every field is parsed, validated and stored in the meta table and raw_json, plus file-kind mapping, filename dates and device names. Use when rebuilding or changing extraction, date parsing, device naming or place lookup.
---

# Metadata extraction (`meta_duplo/metadata.py`, `meta_duplo/geo.py`)

Called by the scan's `extract` stage (skill 03) once per image/video file:

```python
m = metadata.read(path, r["kind"], home_tz)        # home_tz = lib.setting("home_timezone"), IANA name
# → INSERT OR REPLACE INTO meta(...) ; raw_json = json.dumps(m.raw, default=str)
```

and its helpers (`kind_of`, `TIME_FMT`, `time_from_filename`, `device_name`) are used by inventory,
`harmonize.py`, `match.py`, `plans.py` and the web views. `geo.place()` is **not** called during the scan:
place names are looked up when proposals and pages are built.

## Tools: implemented vs specified

| | Implemented (ADR-013 POC) | Specified, not implemented (`docs/ARCHITECTURE.md`, `skills/media-capture-date-resolver`) |
|---|---|---|
| Images | **Pillow** (`PIL.Image`, `ExifTags`, `IptcImagePlugin`) + **pillow-heif** (`register_heif_opener()`, wrapped in `try/except` so a missing plugin is tolerated) | ExifTool `-stay_open` pool via `pyexiftool`, `-a -G1 -s -ee3 -json -api LargeFileSupport=1 -api RequestAll=3` |
| Videos | **ffprobe** subprocess (Homebrew `ffmpeg`) | ExifTool `-ee3` (embedded streams, GPMF, MDPM) + ffprobe |
| Raw storage | plain JSON in `meta.raw_json` | zstd JSON in `meta_raw.exiftool_zst` / `ffprobe_zst` with `extract_pass`, `extract_ok`, `stage_version` |
| Integrity | none needed: one process per file, result attached to that file's row | key by `SourceFile`, input count = output count, re-read empties, 1 % sample re-check |
| Geo | `reverse_geocoder` (offline GeoNames towns), optional extra `geo` | timezonefinder, admin polygons, gpx tracks, text geocoding |

CLAUDE.md rule 2 ("key ExifTool results by `SourceFile`; keep the integrity checks") binds the ExifTool
adapter planned for milestone M1. Today there is no batching: `read()` handles exactly one path and returns a
`Meta` for it, so results cannot be misattributed. A rebuild that introduces ExifTool must add the checks.

Dependencies (`pyproject.toml`): `pillow>=10.4`, `pillow-heif>=0.18`, `piexif>=1.1.3` (demo/plans only);
optional `[project.optional-dependencies] geo = ["reverse-geocoder>=1.5"]` (`uv sync --extra geo`). External:
`ffprobe` on `PATH` (checked with `shutil.which`).

## File kinds

```python
IMAGE_EXT   = {"jpg", "jpeg", "png", "heic", "heif", "tif", "tiff", "webp", "gif", "bmp", "dng"}
VIDEO_EXT   = {"mov", "mp4", "m4v", "avi", "mts", "3gp", "mkv"}
SIDECAR_EXT = {"xmp", "aae", "json", "thm"}

def kind_of(ext: str) -> str:      # ext without the dot; lower-cased inside
    "image" | "video" | "sidecar" | "other"
```

Stored in `files.kind` at inventory. Only `image` and `video` are extracted and hashed; only `image` is
fingerprinted. Camera RAW formats other than DNG (`cr2`, `cr3`, `nef`, `arw`, `raf`, `orf`, `rw2`…) are
`other` and therefore ignored by every stage (`docs/schema.sql` has a `raw` kind: specified, not implemented).

## `Meta` record and the `meta` table

```python
@dataclass
class Meta:
    width: int | None = None; height: int | None = None; duration: float | None = None
    make: str | None = None; model: str | None = None; software: str | None = None
    dto: str | None = None; dto_source: str | None = None; modify_date: str | None = None
    gps_lat: float | None = None; gps_lon: float | None = None
    unique_id: str | None = None; orientation: int | None = None
    raw: dict = field(default_factory=dict)
```

```sql
CREATE TABLE IF NOT EXISTS meta (                 -- meta_duplo/db.py
  file_id INTEGER PRIMARY KEY REFERENCES files(id),
  width INTEGER, height INTEGER, duration REAL,
  make TEXT, model TEXT, software TEXT,
  dto TEXT,                                   -- capture time as naive local 'YYYY-MM-DD HH:MM:SS'
  dto_source TEXT,
  modify_date TEXT,
  gps_lat REAL, gps_lon REAL,
  unique_id TEXT,
  orientation INTEGER,
  raw_json TEXT
);
```

`TIME_FMT = "%Y-%m-%d %H:%M:%S"`: every stored time (`dto`, `modify_date`, `DateTimeDigitized`) is a **naive
local wall-clock** string in this format. UTC values (`GPSDateTime`) are kept in separate keys and never mixed
into `dto`.

## Images: `read_image(path) -> Meta`

```python
with Image.open(path) as img:
    exif = img.getexif()                         # IFD0
    sub  = exif.get_ifd(0x8769)                  # ExifIFD
    gps  = exif.get_ifd(0x8825)                  # GPS IFD
```

| `Meta` field / column | Source | Rule |
|---|---|---|
| `width`, `height` | `img.size` | swapped when `orientation in (5, 6, 7, 8)` (display size) |
| `orientation` | IFD0 `0x0112` | int or `None` |
| `make`, `model`, `software` | IFD0 `0x010F`, `0x0110`, `0x0131` | `(v or None) and str(v).strip("\x00 ")` |
| `modify_date` | IFD0 `0x0132` DateTime | `parse_exif_time` → `TIME_FMT` |
| `dto`, `dto_source` | ExifIFD `0x9003` DateTimeOriginal, else `0x9004` DateTimeDigitized | `parse_exif_time`; `dto_source = "EXIF DateTimeOriginal"` (also when the value came from 0x9004) |
| `unique_id` | ExifIFD `0xA420` ImageUniqueID | as returned |
| `gps_lat`, `gps_lon` | GPS IFD | `_gps()` below |

Not used for the time: `OffsetTimeOriginal` (0x9011) is stored in `raw` only; `dto` stays naive.

### GPS (`_gps(gps: dict)`)

```python
lat = sum(_ratio(x) / 60**i for i, x in enumerate(gps[2]))   # GPSLatitude (deg, min, sec)
lon = sum(_ratio(x) / 60**i for i, x in enumerate(gps[4]))   # GPSLongitude
# KeyError, TypeError, ZeroDivisionError, IndexError → (None, None)
if str(gps.get(1, "N")).upper().startswith("S"): lat = -lat    # GPSLatitudeRef
if str(gps.get(3, "E")).upper().startswith("W"): lon = -lon    # GPSLongitudeRef
if lat == 0 and lon == 0: return None, None                    # "null island" = missing fix
return round(lat, 6), round(lon, 6)
```

`_ratio(v)`: `float(v)`, or `float(v[0]) / float(v[1])` for a `(num, den)` tuple.

`_gps_time(gps) -> str | None`: `GPSDateStamp` (tag 29, `"YYYY:MM:DD"` → `-`) + `GPSTimeStamp` (tag 7,
three rationals truncated with `int(float(x))`) → `"2019-05-14 16:22:03"` (**UTC**). Errors → `None`.

### Every EXIF tag: `all_tags(exif, sub, gps) -> dict`

Keys are `"Group:Name"` with groups `IFD0`, `ExifIFD`, `GPS`; names from `ExifTags.TAGS` (IFD0, ExifIFD)
or `ExifTags.GPSTAGS` (GPS), unknown tags as `f"0x{tag:04X}"`. IFD0 pointer tags `0x8769`, `0x8825`, `0xA005`
are skipped. Values go through `_tag_value(tag, v)`:

| Input | Output |
|---|---|
| `bytes`, tag in `_XP_TAGS = {0x9C9B, 0x9C9C, 0x9C9D, 0x9C9E, 0x9C9F}` (Windows XPTitle/Comment/Author/Keywords/Subject) | `v.decode("utf-16-le", "ignore").strip("\x00 ")` |
| other `bytes` longer than 64 | `f"<{len(v)} bytes>"` (MakerNote and other blobs summarised) |
| other `bytes` | UTF-8 (`ignore`) stripped of `"\x00 "`; if not `isprintable()`, `v.hex()` |
| `tuple` | `", ".join(str(_tag_value(tag, x)) for x in v)` (e.g. GPS `"48.0, 8.0, 38.4"`) |
| rational (`numerator`, not `int`) | `round(float(v), 6)`; `ZeroDivisionError`/`ValueError` → `None` |
| `str` | `.strip("\x00 ")` |
| anything else | unchanged |

Entries whose value is `None` or `""` are dropped.

### XMP and IPTC dates: `xmp_iptc_dates(img) -> dict`

- XMP packet from `img.info["xmp"]` or `img.info["XML:com.adobe.xmp"]` (str is UTF-8 encoded), searched with
  `_XMP_DATE = re.compile(rb'photoshop:DateCreated(?:="([^"]*)"|>([^<]*)<)')` (attribute or element form) →
  `"XMP:DateCreated"`, value as written (e.g. `"2019-05-18T11:47:10"`).
- IPTC via `IptcImagePlugin.getiptcinfo(img)` (`OSError, SyntaxError, ValueError` → `{}`): record `(2, 55)`
  DateCreated `YYYYMMDD` (≥ 8 bytes) → `"YYYY-MM-DD"`, plus `(2, 60)` TimeCreated `HHMMSS±HHMM` (≥ 6 bytes) →
  `" HH:MM:SS" + rest` (offset kept as written) → `"IPTC:DateCreated"`.
- Shown only; weighing them as capture-time candidates belongs to the date resolver (not implemented).

### `raw` for images (`raw_json`)

```python
m.raw = {
    "_v": RAW_VERSION,
    "Make": m.make, "Model": m.model, "Software": m.software,
    "DateTimeOriginal": sub.get(0x9003),            # raw string as in the file, e.g. "2019:05:14 18:22:03"
    "DateTime": exif.get(0x0132),                   # raw string
    "OffsetTimeOriginal": sub.get(0x9011),          # e.g. "+02:00" or None
    "Orientation": m.orientation,
    "GPS": [m.gps_lat, m.gps_lon] if m.gps_lat is not None else None,
    "Format": img.format,                           # "JPEG", "PNG", "HEIF", "TIFF", …
    # the fields the Workbench shows by name; everything else is in "tags"
    "DateTimeDigitized": <parse_exif_time(sub[0x9004]) in TIME_FMT> or None,
    "GPSDateTime": _gps_time(gps),                  # UTC
    "Artist": tags.get("IFD0:Artist") or tags.get("IFD0:XPAuthor"),
    "Copyright": tags.get("IFD0:Copyright"),
    "OwnerName": tags.get("ExifIFD:CameraOwnerName"),
    "SerialNumber": tags.get("ExifIFD:BodySerialNumber"),
    "LensModel": tags.get("ExifIFD:LensModel"),
    "DateCreated": tags.get("XMP:DateCreated") or tags.get("IPTC:DateCreated"),
    "tags": all_tags(exif, sub, gps) | xmp_iptc_dates(img),
}
```

Readers: `web/workbench.py` (`raw["tags"]` feeds the column catalog, `meta_counts`, the `key_dto` /
`key_created` columns and the full-metadata preview; named keys `DateTimeDigitized`, `GPSDateTime`,
`SerialNumber`, `LensModel`, `Artist`, `Copyright`, `OwnerName`), and `harmonize.py` (`{tag:KEY}` name tokens
read `tags`). MakerNotes, IFD1, XMP fields other than DateCreated and ICC are not read.

## Videos: `read_video(path, home_tz) -> Meta`

If `shutil.which("ffprobe")` is falsy → return an empty `Meta()` (`raw == {}`, so extract retries on the next
scan). Otherwise:

```python
subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
               capture_output=True, text=True, timeout=60)
data = json.loads(out.stdout or "{}")
fmt  = data.get("format", {})
tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}   # FORMAT (container) tags only
```

| Field | Source |
|---|---|
| `width`, `height` | first stream with `codec_type == "video"` (`width`, `height`; rotation / display matrix ignored) |
| `duration` | `round(float(fmt["duration"]), 2)`; `TypeError/ValueError` → `None` |
| `make` | `com.apple.quicktime.make` or `make` |
| `model` | `com.apple.quicktime.model` or `model` |
| `software` | `com.apple.quicktime.software` or `encoder` (e.g. `Lavf60.16.100`, which `match.F.reencoded()` uses) |
| `dto`, `dto_source` | see time rules below |
| `gps_lat`, `gps_lon` | `com.apple.quicktime.location.iso6709` or `location`, `re.match(r"([+-]\d+\.?\d*)([+-]\d+\.?\d*)", loc)` → two floats (no rounding, no null-island check) |
| `orientation`, `modify_date`, `unique_id` | not set |

Video time rules (CLAUDE.md rule 3: never fixed offsets):

1. `local = tags["com.apple.quicktime.creationdate"]` (ISO 8601 **with** offset, e.g.
   `2019-07-20T16:05:00+0200`): `datetime.fromisoformat(local).replace(tzinfo=None)` — keep the wall-clock time
   of the capture place; `dto_source = "QuickTime CreationDate"`.
2. Else `utc = tags["creation_time"]` (QuickTime stores UTC): `datetime.fromisoformat(utc.replace("Z",
   "+00:00")).astimezone(ZoneInfo(home_tz)).replace(tzinfo=None)`; `dto_source = "QuickTime creation_time
   (UTC)"`. The conversion uses the IANA zone **for that instant**, so DST is right per timestamp.
3. A `ValueError` in either parse → try the next / give up. `dt = valid_time(dt)`; if `None`, `dto_source =
   None` too.

An unknown `home_tz` raises `ZoneInfoNotFoundError` (a `KeyError`, not caught here) → the whole read fails and
`extract` records `files.error = "extract: …"`. `subprocess.TimeoutExpired` and bad JSON likewise become
per-file errors. Stream-level tags (e.g. per-track `creation_time`) are not read.

Raw for videos:

```python
m.raw = {"_v": RAW_VERSION, "format": fmt.get("format_name"),
         "tags": {f"QuickTime:{k}": v for k, v in tags.items()}}     # keys lower-cased
```

Example (demo `IMG_0020.MOV`): `{"_v": 3, "format": "mov,mp4,m4a,3gp,3g2,mj2", "tags": {"QuickTime:major_brand":
"qt  ", …, "QuickTime:creation_time": "2019-07-20T14:05:00Z", "QuickTime:com.apple.quicktime.make": "Apple",
"QuickTime:com.apple.quicktime.model": "iPhone 12", "QuickTime:encoder": "Lavf60.16.100"}}` →
`dto = "2019-07-20 16:05:00"` with `home_timezone = Europe/Bratislava` (CEST, UTC+2 in July).

**Specified, not implemented**: storing raw QuickTime values and learning per model whether `CreateDate` is UTC
or local; per-file timezone from GPS (timezonefinder); `tz_status`/UTC columns from `docs/schema.sql`.

## Dispatcher

```python
def read(path: Path, kind: str, home_tz: str) -> Meta:
    "image" → read_image(path); "video" → read_video(path, home_tz); else Meta()
```

## `RAW_VERSION = 3`

"Bump when the stored raw metadata changes; extract re-reads older rows". Scan's extract selects files whose
`raw_json` lacks the substring `"_v": <RAW_VERSION>` (relies on default `json.dumps` separators).

| Version | Change |
|---|---|
| 1 | initial: the named keys `Make` … `Format` only (inferred from the ADR-016 note; any row without the current marker is re-read either way) |
| 2 | ADR-016: every EXIF tag in `raw_json.tags`, plus `DateTimeDigitized`, `GPSDateTime` (UTC), `Artist`, `Copyright`, `OwnerName`, `SerialNumber`, `LensModel` |
| 3 | `XMP:DateCreated` / `IPTC:DateCreated` in `tags` and the `DateCreated` key (UI_SPEC: "Extraction reads DateCreated since `RAW_VERSION = 3`") |

## Date validation and parsing

```python
_EPOCH_ARTEFACTS = {(1904, 1, 1), (1970, 1, 1), (1980, 1, 1)}

def valid_time(dt: datetime | None) -> datetime | None:
    if dt is None: return None
    if dt.year < 1826 or dt > datetime.now(): return None          # naive local "now"
    if (dt.year, dt.month, dt.day) in _EPOCH_ARTEFACTS: return None  # any time of day
    if 2000 <= dt.year <= 2012 and dt.month == 1 and dt.day == 1 and dt.hour == 0 and dt.minute <= 10:
        return None                                                  # camera factory defaults
    return dt
```

The factory-default rule applies to every source here (the date-resolver skill limits it to camera-clock
values). The "> 20 files from different devices share a timestamp" rule is specified, not implemented.

`parse_exif_time(value) -> datetime | None`: falsy → `None`; `s = str(value).strip().replace("\x00", "")`;
try `"%Y:%m:%d %H:%M:%S"`, `"%Y-%m-%d %H:%M:%S"`, `"%Y:%m:%d %H:%M"` on `s[:19]`; first success goes through
`valid_time`. `"0000:00:00 00:00:00"`, blanks and ISO `T` forms (`2019-05-14T18:22:03`) return `None`.
`"2019:05:14 18:22"` → `18:22:00`. Sub-seconds and offsets are dropped by the `[:19]` slice.

### `time_from_filename(name) -> tuple[datetime | None, str]`

Precision is `"second"`, `"day"` or `""`. Patterns tried in order (first match wins per pattern):

```python
_FN_WHATSAPP = re.compile(r"(?:IMG|VID|AUD|PTT)-((?:19|20)\d{6})-WA\d+", re.I)
_FN_FULL = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})[ _\-T.]{0,3}(\d{2})[-_.:h]?(\d{2})[-_.:m]?(\d{2})(?!\d{3})")
_FN_DATE = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")
```

1. WhatsApp → `strptime(group, "%Y%m%d")` → `(valid_time(dt), "day")`; `ValueError` → `(None, "")` (does not
   fall through).
2. Full date-time → `datetime(*groups)` → `(valid_time(dt), "second")`; a calendar-invalid value
   (`2026-09-31`) raises `ValueError` and falls through to 3. Note: a valid but rejected value
   (`2000-01-01 00-05-00`) returns `(None, "second")`; callers test the datetime, not the precision.
3. Date only → `(valid_time(dt), "day")`; `ValueError` → continue. Nothing → `(None, "")`.

Filename times are naive local. Observed results:

| Name | Result |
|---|---|
| `IMG_20190518_114710.jpg`, `IMG_20190518_114710_crop.jpg` | 2019-05-18 11:47:10, second |
| `20190514_182203.jpg`, `2019-05-14 18.22.03.jpg` | 2019-05-14 18:22:03, second |
| `IMG-20190515-WA0003.jpg` | 2019-05-15 00:00:00, day |
| `PXL_20230101_120000123.jpg` | 2023-01-01, day (the `(?!\d{3})` guard rejects the ms suffix) |
| `Screenshot 2021-03-04 at 10.11.12.png` | 2021-03-04, day |
| `photo 2019.05.14.jpg` | 2019-05-14, day |
| `IMG_0001.JPG`, `1556000000.jpg`, `20260931_101010.jpg` | None, "" |

Consumers: `harmonize._time_candidates` (second → tier 2, day → tier 3, origin `filename`, for both
`original_name` and `name`) and the Workbench `name_date` column. The per-app regex table of the
date-resolver skill (Pixel UTC, Signal, Telegram, epoch, screenshot tiers, upper bounds) is specified, not
implemented.

## Device names: `device_name(make, model) -> str`

```python
_BRANDS = {
    "nikon corporation": "Nikon", "nikon": "Nikon", "canon": "Canon", "samsung": "Samsung",
    "apple": "Apple", "olympus imaging corp.": "Olympus", "olympus corporation": "Olympus",
    "sony": "Sony", "google": "Google", "huawei": "Huawei", "xiaomi": "Xiaomi",
    "fujifilm": "Fujifilm", "panasonic": "Panasonic", "gopro": "GoPro", "dji": "DJI",
    "lg electronics": "LG", "motorola": "Motorola", "oneplus": "OnePlus", "pentax": "Pentax",
    "ricoh imaging company, ltd.": "Ricoh", "leica camera ag": "Leica",
}
def device_name(make, model):
    make = (make or "").strip().strip("\x00"); model = (model or "").strip().strip("\x00")
    if not make and not model: return ""
    brand = _BRANDS.get(make.lower(), make.title() if make.isupper() else make)
    if brand and model.lower().startswith(brand.lower()): return model        # model already carries the brand
    if make and model.lower().startswith(make.lower()): model = model[len(make):].strip()
    return f"{brand} {model}".strip()
```

| make, model | result |
|---|---|
| Apple, iPhone 12 | `Apple iPhone 12` |
| Canon, Canon EOS 5D Mark III | `Canon EOS 5D Mark III` |
| NIKON CORPORATION, NIKON D750 | `NIKON D750` (model returned as is, case kept) |
| SAMSUNG, SM-G960F | `Samsung SM-G960F` |
| OLYMPUS IMAGING CORP., E-M5 | `Olympus E-M5` |
| LG Electronics, LG-H850 | `LG-H850` |
| None, iPhone | `iPhone` |

Computed on read (never stored): `match.F.device`, `harmonize.recommend_device`, results and Workbench rows.
Model-code → marketing-name tables and unit/serial resolution (device-resolver skill) are specified, not
implemented.

## Reverse geocoding: `meta_duplo/geo.py`

```python
@lru_cache(maxsize=1)
def _rg():                       # import reverse_geocoder lazily; any exception → None
@lru_cache(maxsize=50_000)
def place(lat: float, lon: float) -> dict:
    """{'city': 'Bratislava', 'country': 'SK', 'region': 'Bratislavsky'} or {} when unavailable."""
    rg = _rg()
    if rg is None: return {}
    try:
        r = rg.search([(round(lat, 4), round(lon, 4))], mode=1, verbose=False)[0]
    except TypeError:            # older versions have no `verbose`
        r = rg.search([(round(lat, 4), round(lon, 4))], mode=1)[0]
    return {"city": r.get("name", ""), "country": r.get("cc", ""), "region": r.get("admin1", "")}
def available() -> bool: return _rg() is not None
```

- Data: the `reverse_geocoder` package's bundled offline GeoNames towns (nearest populated place, K-d tree,
  `mode=1` = single process). No network. No distance limit: a point at sea still gets the nearest town.
- Coordinates are rounded to 4 decimals (~11 m) for the query; the cache key is the unrounded pair.
- Missing extra → `{}` everywhere. Effects: `harmonize.recommend_place` returns value `""`, confidence `0.3`,
  explanation "GPS found, but offline place names are not installed (`uv sync --extra geo`)."; the Sources page
  gets `geo_ok = geo.available()` to show a hint; place columns stay empty. GPS itself is still stored.
- Callers: `harmonize.recommend_place` / `_current`, `web/app.py` (step pages), `web/results.py`,
  `web/workbench.py` (`place`, `country` columns).
- Specified, not implemented: timezone from coordinates (`timezonefinder`), admin polygons, GPS validation
  beyond null island (hemisphere, GCJ-02, stale fixes), track matching, writing IPTC place names.

## Rebuild checklist

1. Create `kind_of` with the three extension sets; confirm inventory stores it in `files.kind`.
2. Add the `Meta` dataclass and `TIME_FMT`; keep `meta` columns exactly as in `db.py`.
3. Implement `valid_time` and `parse_exif_time` (three formats, `[:19]`, NUL stripping).
4. Implement `_FN_WHATSAPP`, `_FN_FULL`, `_FN_DATE` and `time_from_filename` with the stated fall-through.
5. Implement `_BRANDS` and `device_name`.
6. Implement `_ratio`, `_gps` (refs, null island, 6 decimals) and `_gps_time` (UTC string).
7. Implement `_tag_value`, `all_tags` (groups, pointer skip, unknown-tag hex names) and `xmp_iptc_dates`.
8. Implement `read_image` with the field table and the full `raw` dict including `"_v"` and `"tags"`.
9. Implement `read_video` with the exact ffprobe command, timeout 60, format tags lower-cased, the two-step
   time rule using `ZoneInfo(home_tz)` per timestamp, ISO 6709 GPS and the `QuickTime:` raw tags.
10. Implement `read()` and `RAW_VERSION = 3`; check extract re-reads rows with an older or missing `_v`.
11. Implement `geo.py` with both `lru_cache`s, the lazy import, the `verbose` fallback and `available()`.
12. Register the HEIF opener at import (`try: register_heif_opener() except Exception: pass`).

## Verify

- `tests/test_workbench.py::test_key_date_fields_first_and_xmp_date_read`: a JPEG saved with an XMP packet
  yields `read_image(path).raw["tags"]["XMP:DateCreated"] == "2019-05-18T11:47:10"`.
- `tests/test_workbench.py::test_meta_check_counts` exercises the `Group:Name` tag keys.
- `tests/test_workflow.py::test_steps_3_and_4_and_plans`: depends on extraction being right — the WhatsApp
  copy is dated `2019-05-14 19:05:41` from its original's EXIF, the crop gets `2019-05-18 11:47:10` from its
  file name, and renames read `2019-05-14 18-22-03 (Bratislava, Apple iPhone 12) - IMG_0001.jpg` (needs
  `--extra geo`) and `2018-12-24 17-31-12 (Canon EOS 5D Mark III) - IMG_4102.jpg`.
- After scanning the demo disk, `meta` must hold, for example:
  - `Camera/DCIM/IMG_0001.JPG`: `dto 2019-05-14 18:22:03`, `dto_source "EXIF DateTimeOriginal"`, Apple /
    iPhone 12, GPS `48.144, 17.1101`, 1600×1200; `raw_json` has `"_v": 3`, `"GPSDateTime": "2019-05-14
    16:22:03"`, `"Artist": "Demo Family"`, and `tags` with `GPS:GPSLatitude = "48.0, 8.0, 38.4"`;
  - `Camera/DCIM/IMG_0020.MOV`: `dto 2019-07-20 16:05:00`, `dto_source "QuickTime creation_time (UTC)"`,
    640×360, `duration 3.0`, software `Lavf…`;
  - `WhatsApp Images/IMG-20190515-WA0003.jpg`: no dto, no make, 1024×768.
- Spot checks in a REPL: the filename and device tables above; `parse_exif_time("0000:00:00 00:00:00") is None`;
  `geo.place(48.1486, 17.1077) == {"city": "Bratislava", "country": "SK", "region": "Bratislavsky"}` with the
  extra installed, `{}` without it.
- Change the `home_timezone` setting to `America/New_York`, bump nothing, delete the video's `meta` row and
  rescan: its `dto` becomes `2019-07-20 10:05:00` (EDT), proving per-timestamp IANA conversion.
