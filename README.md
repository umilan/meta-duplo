# meta-duplo: media organizer + duplicate finder (v0.1 proof of concept)

A local tool for big photo/video disks. It works in four steps:

1. **Identical files:** find byte-identical copies, keep one, move the rest to a holding folder.
2. **Near duplicates:** the same photo with another name or metadata: re-saved, resized, messenger copies, edited dates. Bursts are shown separately and never removed automatically.
3. **Harmonize & rename:** one date, place and device per photo, recommended from every copy (removed ones too). You accept, edit or reject each one. Files are then renamed by an editable rule:
   `{date:%Y-%m-%d %H-%M-%S} ({location_name}, {device_name}) - {original_filename}.{ext}`
   → `2019-05-14 18-22-03 (Bratislava, Apple iPhone 12) - IMG_0001.jpg`
4. **Re-check:** with the harmonized metadata, new duplicate candidates appear in a list you can sort by name, size, date, place, device, confidence or bindings.

Nothing on disk changes outside a **plan** you review and apply. Every plan is journaled and can be undone.

## Try it (macOS or Linux)

```bash
brew install ffmpeg uv          # ffprobe for videos; uv for Python
uv sync --extra geo             # dependencies + offline place names
uv run meta-duplo demo              # creates ~/meta-duplo-demo (21 files with every duplicate case) and adds it as a writable source
uv run meta-duplo serve             # prints http://127.0.0.1:8765/?token=…  — open it
```

In the browser: **Scan all sources** → step 1 *Review plan…* → *Apply plan* → step 2 → step 3 *Build recommendations*,
edit the name rule, accept → step 4 *Run re-check* → step 3 *Review rename plan…* → apply → undo from *Plans & undo*.

Your own folder: `uv run meta-duplo add-source "/Volumes/Photos/Pictures"`. It is read-only until you add
`--writable` or flip it on the Sources page. Libraries live in `~/MetaDuploLibraries/default`
(change it with `--library`).

Tests: `uv run pytest` runs the whole workflow on a generated disk and checks that apply + undo restore every byte.

## Layout

```
meta_duplo/
  db.py          schema (subset of docs/schema.sql) and settings
  metadata.py    EXIF/QuickTime reading, file-name dates, device names
  scan.py        inventory → extract → hash → fingerprint/thumbnails; job runner
  match.py       steps 1, 2 and 4: scoring, anchor grouping, captures
  harmonize.py   step 3: recommendations, decisions, file name rule
  plans.py       the only writer: cleanup and rename plans, journal, apply, undo
  geo.py         offline reverse geocoding
  demo.py        demo disk generator
  web/           FastAPI app, Jinja templates, htmx/Alpine (vendored), CSS tokens
docs/            WORKFLOW (the four steps), UI_SPEC, ARCHITECTURE, DATA_MODEL, schema.sql, DECISIONS, ROADMAP
skills/          domain logic: date, place, device, context, confidence, duplicate finder
```

## What the POC does not do yet

See ADR-013 and `docs/ROADMAP.md`. In short:
- Metadata comes from Pillow + ffprobe, not ExifTool, so RAW MakerNotes and QuickTime Keys are partial.
- Confidence is tier-based, not calibrated.
- No camera-clock correction, context or propagation.
- No in-file metadata writes.
- Videos match by duration and time, not by keyframes.
- Polling instead of SSE.
- Matching is all-pairs below 2 500 images and multi-index buckets above; it is not tuned for 500k files yet.
