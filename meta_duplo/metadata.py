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

from PIL import Image

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
        m.raw = {
            "Make": m.make, "Model": m.model, "Software": m.software,
            "DateTimeOriginal": sub.get(0x9003), "DateTime": exif.get(0x0132),
            "OffsetTimeOriginal": sub.get(0x9011), "Orientation": m.orientation,
            "GPS": [m.gps_lat, m.gps_lon] if m.gps_lat is not None else None,
            "Format": img.format,
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
    m.raw = {"format": fmt.get("format_name"), "tags": tags}
    return m


def read(path: Path, kind: str, home_tz: str) -> Meta:
    if kind == "image":
        return read_image(path)
    if kind == "video":
        return read_video(path, home_tz)
    return Meta()
