"""Generate a small demo disk that exercises all four workflow steps."""
from __future__ import annotations

import os
import random
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import piexif
from PIL import Image, ImageDraw, ImageFilter

PLACES = {
    "bratislava": (48.1440, 17.1101),
    "vienna": (48.2082, 16.3738),
    "prague": (50.0875, 14.4213),
    "split": (43.5081, 16.4402),
}


def _scene(seed: int, size=(1600, 1200), shift: int = 0) -> Image.Image:
    rnd = random.Random(seed)
    w, h = size
    img = Image.new("RGB", size)
    top = tuple(rnd.randrange(40, 220) for _ in range(3))
    bottom = tuple(rnd.randrange(20, 200) for _ in range(3))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        draw.line([(0, y), (w, y)], fill=tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    for _ in range(14):
        x, y = rnd.randrange(-100, w), rnd.randrange(-100, h)
        r = rnd.randrange(60, 380)
        color = tuple(rnd.randrange(0, 256) for _ in range(3))
        if rnd.random() < 0.5:
            draw.ellipse([x + shift, y, x + shift + r, y + r * 0.8], fill=color)
        else:
            draw.rectangle([x + shift, y, x + shift + r * 1.3, y + r * 0.6], fill=color)
    return img.filter(ImageFilter.GaussianBlur(1.2))


def _gps(lat: float, lon: float) -> dict:
    def dms(v: float):
        v = abs(v)
        d, m = int(v), int((v - int(v)) * 60)
        s = round(((v - d) * 60 - m) * 60 * 100)
        return ((d, 1), (m, 1), (s, 100))

    return {
        piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S", piexif.GPSIFD.GPSLatitude: dms(lat),
        piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W", piexif.GPSIFD.GPSLongitude: dms(lon),
    }


def _exif(make: str, model: str, when: str, place: str | None = None, software: str = "") -> bytes:
    t = when.replace("-", ":").encode()
    zeroth = {piexif.ImageIFD.Make: make.encode(), piexif.ImageIFD.Model: model.encode(), piexif.ImageIFD.DateTime: t,
              piexif.ImageIFD.Artist: b"Demo Family" if make == "Apple" else b"Demo Photographer"}
    if make == "Canon":
        zeroth[piexif.ImageIFD.Copyright] = b"(c) Demo Photographer"
    if software:
        zeroth[piexif.ImageIFD.Software] = software.encode()
    exif = {"0th": zeroth, "Exif": {piexif.ExifIFD.DateTimeOriginal: t, piexif.ExifIFD.DateTimeDigitized: t}}
    if place:
        exif["GPS"] = _gps(*PLACES[place])
        # demo GPS clock: UTC, i.e. local summer time minus 2 h
        utc = datetime.strptime(when, "%Y-%m-%d %H:%M:%S").timestamp() - 7200
        u = datetime.fromtimestamp(utc)
        exif["GPS"][piexif.GPSIFD.GPSDateStamp] = u.strftime("%Y:%m:%d").encode()
        exif["GPS"][piexif.GPSIFD.GPSTimeStamp] = ((u.hour, 1), (u.minute, 1), (u.second, 1))
    return piexif.dump(exif)


def _touch(path: Path, when: str) -> None:
    ts = datetime.strptime(when, "%Y-%m-%d %H:%M:%S").timestamp()
    os.utime(path, (ts, ts))


def make_demo(root: str | Path, extra: int = 0) -> Path:
    root = Path(root).expanduser().resolve()
    if root.exists():
        shutil.rmtree(root)
    dcim = root / "Camera" / "DCIM"
    dcim.mkdir(parents=True)
    originals = [
        # name, seed, make, model, time, place
        ("IMG_0001.JPG", 1, "Apple", "iPhone 12", "2019-05-14 18:22:03", "bratislava"),
        ("IMG_0002.JPG", 2, "Apple", "iPhone 12", "2019-05-14 19:05:41", "bratislava"),
        ("IMG_0003.JPG", 3, "Apple", "iPhone 12", "2019-05-18 11:47:10", "vienna"),
        ("IMG_0004.JPG", 4, "Apple", "iPhone 12", "2019-06-02 10:15:30", "prague"),
        ("IMG_0005.JPG", 5, "Apple", "iPhone 12", "2019-06-02 10:42:12", "prague"),
        ("IMG_0010.JPG", 10, "Apple", "iPhone 12", "2019-07-20 16:01:05", "split"),
        ("IMG_0011.JPG", 10, "Apple", "iPhone 12", "2019-07-20 16:01:06", "split"),  # burst neighbour
    ]
    canon = root / "Canon" / "100CANON"
    canon.mkdir(parents=True)
    for name, seed, make, model, when, place in originals:
        shift = 18 if name == "IMG_0011.JPG" else 0
        p = dcim / name
        _scene(seed, shift=shift).save(p, "JPEG", quality=92, exif=_exif(make, model, when, place))
        _touch(p, when)
    for i, (seed, when) in enumerate([(20, "2018-12-24 17:30:00"), (21, "2018-12-24 17:31:12")], start=1):
        p = canon / f"IMG_{4100 + i}.JPG"
        _scene(seed, size=(1800, 1200)).save(p, "JPEG", quality=94,
                                             exif=_exif("Canon", "Canon EOS 5D Mark III", when))
        _touch(p, when)

    # step 1: byte-identical backup copies, one renamed
    backup = root / "Backup 2019"
    backup.mkdir()
    for name in ("IMG_0001.JPG", "IMG_0002.JPG", "IMG_0003.JPG"):
        shutil.copy2(dcim / name, backup / name)
    shutil.copy2(dcim / "IMG_0003.JPG", backup / "Copy of IMG_0003.JPG")
    shutil.copy2(canon / "IMG_4101.JPG", backup / "christmas.jpg")

    # step 2: messenger copy (resized, recompressed, no metadata, receive date in the name)
    wa = root / "WhatsApp Images"
    wa.mkdir()
    with Image.open(dcim / "IMG_0002.JPG") as im:
        small = im.copy()
    small.thumbnail((1024, 1024))
    small.save(wa / "IMG-20190515-WA0003.jpg", "JPEG", quality=70)
    _touch(wa / "IMG-20190515-WA0003.jpg", "2019-05-15 08:12:44")
    # same pixels, different metadata (someone "fixed" the date): only the EXIF block changes
    edited = root / "Edited"
    edited.mkdir()
    shutil.copy2(dcim / "IMG_0004.JPG", edited / "prague castle.jpg")
    piexif.insert(_exif("Apple", "iPhone 12", "2019-06-03 09:00:00", None, software="Photos 5.0"), str(edited / "prague castle.jpg"))
    # export without metadata
    with Image.open(dcim / "IMG_0005.JPG") as im:
        im.convert("RGB").save(edited / "export-0005.jpg", "JPEG", quality=88)
    _touch(edited / "export-0005.jpg", "2020-01-10 21:00:00")
    # step 4: a crop whose only date is in its name; it matches once the date is harmonized
    with Image.open(dcim / "IMG_0003.JPG") as im:
        w, h = im.size
        im.crop((int(w * 0.05), int(h * 0.05), int(w * 0.95), int(h * 0.95))).save(
            edited / "IMG_20190518_114710_crop.jpg", "JPEG", quality=90)
    _touch(edited / "IMG_20190518_114710_crop.jpg", "2021-03-01 12:00:00")

    # videos: original, renamed identical copy, re-encoded copy
    if shutil.which("ffmpeg"):
        vid = dcim / "IMG_0020.MOV"
        common = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=25"]
        subprocess.run(common + ["-metadata", "creation_time=2019-07-20T14:05:00Z",
                                 "-metadata", "com.apple.quicktime.make=Apple",
                                 "-metadata", "com.apple.quicktime.model=iPhone 12",
                                 "-movflags", "use_metadata_tags", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vid)],
                       check=True)
        _touch(vid, "2019-07-20 16:05:00")
        shutil.copy2(vid, backup / "beach video.MOV")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(vid), "-map_metadata", "0",
                        "-movflags", "use_metadata_tags", "-c:v", "libx264", "-crf", "35", str(wa / "VID-20190721-WA0001.mp4")],
                       check=True)
    if extra:
        _make_extra(root, extra)
    return root


def _make_extra(root: Path, n: int) -> None:
    """n more photos, each with a backup copy; every third also as a messenger copy.
    Gives n identical groups and about n/3 near-duplicate groups, to try the UI at scale."""
    places = list(PLACES)
    for i in range(n):
        when = f"2020-{1 + i % 12:02d}-{1 + i % 28:02d} {8 + i % 12:02d}:{i % 60:02d}:{(i * 7) % 60:02d}"
        year_dir = root / "Camera" / "2020" / f"{1 + i % 12:02d}"
        year_dir.mkdir(parents=True, exist_ok=True)
        p = year_dir / f"IMG_{2000 + i}.JPG"
        _scene(1000 + i, size=(800, 600)).save(p, "JPEG", quality=88,
                                               exif=_exif("Apple", "iPhone 12", when, places[i % len(places)]))
        _touch(p, when)
        backup = root / f"Backup {2020 + i % 3}" / f"{1 + i % 12:02d}"
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, backup / p.name)
        if i % 3 == 0:
            wa = root / "WhatsApp Images"
            wa.mkdir(exist_ok=True)
            with Image.open(p) as im:
                small = im.copy()
            small.thumbnail((480, 480))
            small.save(wa / f"IMG-{when[:10].replace('-', '')}-WA{i:04d}.jpg", "JPEG", quality=70)
