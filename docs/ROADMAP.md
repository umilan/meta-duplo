# Roadmap (merged)

Each milestone ends with something usable on a real disk. Durations are estimates. In total:
16.5–24 weeks to M10.

## Status

**v0.1 proof of concept** (this repository): the four-step workflow runs end to end on the demo disk:
- scan;
- identical and near duplicates;
- recommendations;
- the rename rule with live preview;
- the re-check list;
- plans with journal and undo;
- tests and a browser walk-through.

See `WORKFLOW.md` and ADR-013 for its scope. The milestones below take it to the full spec.

| # | Milestone | Weeks | POC coverage |
|---|---|---|---|
| M0 | Skeleton: CLI, migrations, FastAPI + Jinja + htmx shell, job runner, fixtures | ½–1 | ✓ (no migrations runner, no SSE) |
| M1 | Inventory + extraction: ExifTool `-stay_open` pool, ffprobe, zstd raw metadata, integrity checks, rules builder, folder tree | 1–2 | partial (Pillow/ffprobe, no rules builder) |
| M2 | Match + group (step 1–2): hash cascade, content hash, fingerprints, thumbs, capture buckets, pairs, anchor groups, verdicts, keeper rules, siblings | 2–3 | ✓ (all-pairs below 2 500 images, multi-index above) |
| M3 | Decisions + cleanup: decisions keyed by content, holding folder, journal, undo, removed copies as evidence | 2–3 | ✓ |
| M4 | Duplicates workspace: folder overlap (≡ ⊂ ∩), heatmap, treemap, timeline, compare with metadata diff, method costs, video keyframes | 2 | compare only |
| M5 | Time resolver (step 3): full candidate set, timezone reconciliation, clock-offset learning; step 4 re-check | 2–3 | partial (tiers, filename dates, re-check) |
| M6 | Context + propagation: folder tokens, sequences, sessions, intervals | 2 | – |
| M7 | Device resolver: normalization tables, units, origin classes | 1–2 | partial (brand normalization) |
| M8 | Place resolver: GPS validation, tracks, session fill, gazetteer | 2–3 | partial (GPS → town) |
| M9 | Confidence + calibration: log-odds, masking calibration, duplicate-pair calibration from "not duplicates" | 1–2 | – |
| M10 | Restore + reorganize: rename rule (✓), XMP (✓), folder layout rule, in-file writes via ExifTool (opt-in), mixed plans | 2 | rename + XMP + file dates |
| M11 | Polish + optional modules (OCR, CLIP, faces, PRNU, C2PA, embeddings, audio fingerprints) | – | – |
| M12 | Tauri desktop app | 1 | started: unsigned macOS builds (arm64, x86_64) in CI, ADR-019 |

**Next three steps after the POC:**
1. ExifTool adapter (M1). With it, purple columns for the EXIF dates (DateTimeOriginal, CreateDate, ModifyDate), written into the file by ExifTool through an approved plan, opt-in (M10). “Use this for another column” then offers them next to *New date*.
2. Camera clock correction (M5).
3. Folder overlap view (M4).

These are the three highest-value items in `WORKFLOW.md` → Further ideas.
