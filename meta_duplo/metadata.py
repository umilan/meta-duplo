"""Metadata extraction (Pillow for images, ffprobe for videos) and time/device parsing.

The full spec uses an ExifTool -stay_open pool; this POC reads the fields the four
workflow steps need without external tools beyond ffprobe.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import ExifTags, Image, IptcImagePlugin

try:  # HEIC/HEIF support is optional
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover
    pass

IMAGE_EXT = {"jpg", "jpeg", "png", "heic", "heif", "tif", "tiff", "webp", "gif", "bmp", "dng"}
VIDEO_EXT = {"mov", "mp4", "m4v", "avi", "mts", "3gp", "mkv"}
SIDECAR_EXT = {"xmp", "aae", "json", "thm"}

TIME_FMT = "%Y-%m-%d %H:%M:%S"


def kind_of(ext: str) -> str:
    ext = ext.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in SIDECAR_EXT:
        return "sidecar"
    return "other"


@dataclass
class Meta:
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    make: str | None = None
    model: str | None = None
    software: str | None = None
    dto: str | None = None
    dto_source: str | None = None
    modify_date: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    unique_id: str | None = None
    orientation: int | None = None
    raw: dict = field(default_factory=dict)


# ───────────────────────── time validation ─────────────────────────

_EPOCH_ARTEFACTS = {(1904, 1, 1), (1970, 1, 1), (1980, 1, 1)}


def valid_time(dt: datetime | None) -> datetime | None:
    """Reject epoch artefacts, factory defaults and impossible years."""
    if dt is None:
        return None
    if dt.year < 1826 or dt > datetime.now():
        return None
    if (dt.year, dt.month, dt.day) in _EPOCH_ARTEFACTS:
        return None
    if 2000 <= dt.year <= 2012 and dt.month == 1 and dt.day == 1 and dt.hour == 0 and dt.minute <= 10:
        return None
    return dt


def parse_exif_time(value) -> datetime | None:
    if not value:
        return None
    s = str(value).strip().replace("\x00", "")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M"):
        try:
            return valid_time(datetime.strptime(s[:19], fmt))
        except ValueError:
            continue
    return None


_FN_FULL = re.compile(
    r"(?<!\d)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})[ _\-T.]{0,3}(\d{2})[-_.:h]?(\d{2})[-_.:m]?(\d{2})(?!\d{3})"
)
_FN_WHATSAPP = re.compile(r"(?:IMG|VID|AUD|PTT)-((?:19|20)\d{6})-WA\d+", re.I)
_FN_DATE = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")


def time_from_filename(name: str) -> tuple[datetime | None, str]:
    """Return (time, precision) where precision is 'second', 'day' or ''."""
    m = _FN_WHATSAPP.search(name)
    if m:
        try:
            return valid_time(datetime.strptime(m.group(1), "%Y%m%d")), "day"
        except ValueError:
            return None, ""
    m = _FN_FULL.search(name)
    if m:
        try:
            return valid_time(datetime(*map(int, m.groups()))), "second"
        except ValueError:  # calendar-invalid values such as 2026-09-31 are rejected, not fatal
            pass
    m = _FN_DATE.search(name)
    if m:
        try:
            return valid_time(datetime(*map(int, m.groups()))), "day"
        except ValueError:
            pass
    return None, ""


# ───────────────────────── device names ─────────────────────────

_BRANDS = {
    "nikon corporation": "Nikon", "nikon": "Nikon", "canon": "Canon", "samsung": "Samsung",
    "apple": "Apple", "olympus imaging corp.": "Olympus", "olympus corporation": "Olympus",
    "sony": "Sony", "google": "Google", "huawei": "Huawei", "xiaomi": "Xiaomi",
    "fujifilm": "Fujifilm", "panasonic": "Panasonic", "gopro": "GoPro", "dji": "DJI",
    "lg electronics": "LG", "motorola": "Motorola", "oneplus": "OnePlus", "pentax": "Pentax",
    "ricoh imaging company, ltd.": "Ricoh", "leica camera ag": "Leica",
}


def device_name(make: str | None, model: str | None) -> str:
    make = (make or "").strip().strip("\x00")
    model = (model or "").strip().strip("\x00")
    if not make and not model:
        return ""
    brand = _BRANDS.get(make.lower(), make.title() if make.isupper() else make)
    if brand and model.lower().startswith(brand.lower()):
        return model
    if make and model.lower().startswith(make.lower()):
        model = model[len(make):].strip()
    return f"{brand} {model}".strip()


# ───────────────────────── extraction ─────────────────────────

def _ratio(v) -> float:
    try:
        return float(v)
    except TypeError:
        return float(v[0]) / float(v[1])


def _gps(gps: dict) -> tuple[float | None, float | None]:
    try:
        lat = sum(_ratio(x) / 60**i for i, x in enumerate(gps[2]))
        lon = sum(_ratio(x) / 60**i for i, x in enumerate(gps[4]))
    except (KeyError, TypeError, ZeroDivisionError, IndexError):
        return None, None
    if str(gps.get(1, "N")).upper().startswith("S"):
        lat = -lat
    if str(gps.get(3, "E")).upper().startswith("W"):
        lon = -lon
    if lat == 0 and lon == 0:  # "null island" is a missing fix, not a place
        return None, None
    return round(lat, 6), round(lon, 6)


RAW_VERSION = 3  # bump when the stored raw metadata changes; extract re-reads older rows (3: XMP / IPTC DateCreated)

_XP_TAGS = {0x9C9B, 0x9C9C, 0x9C9D, 0x9C9E, 0x9C9F}  # Windows XP* tags: UTF-16LE bytes


def _tag_value(tag: int, v):
    if isinstance(v, bytes):
        if tag in _XP_TAGS:
            return v.decode("utf-16-le", "ignore").strip("\x00 ")
        if len(v) > 64:
            return f"<{len(v)} bytes>"
        text = v.decode("utf-8", "ignore").strip("\x00 ")
        return text if text.isprintable() else v.hex()
    if isinstance(v, tuple):
        return ", ".join(str(_tag_value(tag, x)) for x in v)
    if hasattr(v, "numerator") and not isinstance(v, int):
        try:
            return round(float(v), 6)
        except (ZeroDivisionError, ValueError):
            return None
    if isinstance(v, str):
        return v.strip("\x00 ")
    return v


def all_tags(exif, sub: dict, gps: dict) -> dict:
    """Every EXIF tag as 'Group:Name' → readable value (MakerNote and other blobs summarised)."""
    out = {}
    for group, items, names in (("IFD0", exif.items(), ExifTags.TAGS), ("ExifIFD", sub.items(), ExifTags.TAGS),
                                ("GPS", gps.items(), ExifTags.GPSTAGS)):
        for tag, v in items:
            if group == "IFD0" and tag in (0x8769, 0x8825, 0xA005):
                continue  # pointers to sub-IFDs
            name = names.get(tag, f"0x{tag:04X}")
            val = _tag_value(tag, v)
            if val not in (None, ""):
                out[f"{group}:{name}"] = val
    return out


_XMP_DATE = re.compile(rb'photoshop:DateCreated(?:="([^"]*)"|>([^<]*)<)')


def xmp_iptc_dates(img: Image.Image) -> dict:
    """DateCreated from XMP (photoshop:DateCreated) and IPTC (2:55 DateCreated + 2:60 TimeCreated), as written.

    Only read and shown here: weighing them as capture-time candidates belongs to the date resolver skill."""
    out = {}
    xmp = img.info.get("xmp") or img.info.get("XML:com.adobe.xmp") or b""
    if isinstance(xmp, str):
        xmp = xmp.encode("utf-8", "replace")
    if m := _XMP_DATE.search(xmp):
        val = (m.group(1) or m.group(2) or b"").decode("utf-8", "replace").strip()
        if val:
            out["XMP:DateCreated"] = val
    try:
        iptc = IptcImagePlugin.getiptcinfo(img) or {}
    except (OSError, SyntaxError, ValueError):
        iptc = {}
    day, time = iptc.get((2, 55)), iptc.get((2, 60))
    if isinstance(day, bytes) and len(day) >= 8:
        d = day.decode("ascii", "replace")
        val = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        if isinstance(time, bytes) and len(time) >= 6:
            t = time.decode("ascii", "replace")
            val += f" {t[:2]}:{t[2:4]}:{t[4:6]}{t[6:]}"
        out["IPTC:DateCreated"] = val
    return out


def _gps_time(gps: dict) -> str | None:
    """GPSDateStamp + GPSTimeStamp (UTC), e.g. '2019-05-14 16:22:03'."""
    try:
        d = str(gps[29]).strip("\x00 ").replace(":", "-")
        h, mi, s = (int(float(x)) for x in gps[7])
        return f"{d} {h:02d}:{mi:02d}:{s:02d}"
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def read_image(path: Path) -> Meta:
    m = Meta()
    with Image.open(path) as img:
        exif = img.getexif()
        m.width, m.height = img.size
        m.orientation = exif.get(0x0112)
        if m.orientation in (5, 6, 7, 8):
            m.width, m.height = m.height, m.width
        m.make = (exif.get(0x010F) or None) and str(exif.get(0x010F)).strip("\x00 ")
        m.model = (exif.get(0x0110) or None) and str(exif.get(0x0110)).strip("\x00 ")
        m.software = (exif.get(0x0131) or None) and str(exif.get(0x0131)).strip("\x00 ")
        modify = parse_exif_time(exif.get(0x0132))
        m.modify_date = modify.strftime(TIME_FMT) if modify else None
        sub = exif.get_ifd(0x8769)
        dto = parse_exif_time(sub.get(0x9003)) or parse_exif_time(sub.get(0x9004))
        if dto:
            m.dto, m.dto_source = dto.strftime(TIME_FMT), "EXIF DateTimeOriginal"
        m.unique_id = sub.get(0xA420)
        m.gps_lat, m.gps_lon = _gps(exif.get_ifd(0x8825))
        gps = exif.get_ifd(0x8825)
        tags = all_tags(exif, sub, gps) | xmp_iptc_dates(img)
        digitized = parse_exif_time(sub.get(0x9004))
        m.raw = {
            "_v": RAW_VERSION,
            "Make": m.make, "Model": m.model, "Software": m.software,
            "DateTimeOriginal": sub.get(0x9003), "DateTime": exif.get(0x0132),
            "OffsetTimeOriginal": sub.get(0x9011), "Orientation": m.orientation,
            "GPS": [m.gps_lat, m.gps_lon] if m.gps_lat is not None else None,
            "Format": img.format,
            # the fields the workbench shows by name; everything else is in "tags"
            "DateTimeDigitized": digitized.strftime(TIME_FMT) if digitized else None,
            "GPSDateTime": _gps_time(gps),
            "Artist": tags.get("IFD0:Artist") or tags.get("IFD0:XPAuthor"),
            "Copyright": tags.get("IFD0:Copyright"),
            "OwnerName": tags.get("ExifIFD:CameraOwnerName"),
            "SerialNumber": tags.get("ExifIFD:BodySerialNumber"),
            "LensModel": tags.get("ExifIFD:LensModel"),
            "DateCreated": tags.get("XMP:DateCreated") or tags.get("IPTC:DateCreated"),
            "tags": tags,
        }
    return m


def read_video(path: Path, home_tz: str) -> Meta:
    m = Meta()
    if not shutil.which("ffprobe"):
        return m
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    data = json.loads(out.stdout or "{}")
    fmt = data.get("format", {})
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            m.width, m.height = s.get("width"), s.get("height")
            break
    try:
        m.duration = round(float(fmt.get("duration")), 2)
    except (TypeError, ValueError):
        pass
    m.make = tags.get("com.apple.quicktime.make") or tags.get("make")
    m.model = tags.get("com.apple.quicktime.model") or tags.get("model")
    m.software = tags.get("com.apple.quicktime.software") or tags.get("encoder")
    local = tags.get("com.apple.quicktime.creationdate")
    utc = tags.get("creation_time")
    dt = None
    if local:  # local time with offset: keep the wall-clock time of the capture place
        try:
            dt, m.dto_source = datetime.fromisoformat(local).replace(tzinfo=None), "QuickTime CreationDate"
        except ValueError:
            dt = None
    if dt is None and utc:  # QuickTime stores UTC; convert per timestamp with the home zone
        try:
            u = datetime.fromisoformat(utc.replace("Z", "+00:00"))
            dt, m.dto_source = u.astimezone(ZoneInfo(home_tz)).replace(tzinfo=None), "QuickTime creation_time (UTC)"
        except ValueError:
            dt = None
    dt = valid_time(dt)
    if dt:
        m.dto = dt.strftime(TIME_FMT)
    else:
        m.dto_source = None
    loc = tags.get("com.apple.quicktime.location.iso6709") or tags.get("location")
    if loc:
        mm = re.match(r"([+-]\d+\.?\d*)([+-]\d+\.?\d*)", loc)
        if mm:
            m.gps_lat, m.gps_lon = float(mm.group(1)), float(mm.group(2))
    m.raw = {"_v": RAW_VERSION, "format": fmt.get("format_name"),
             "tags": {f"QuickTime:{k}": v for k, v in tags.items()}}
    return m


def read(path: Path, kind: str, home_tz: str) -> Meta:
    if kind == "image":
        return read_image(path)
    if kind == "video":
        return read_video(path, home_tz)
    return Meta()
