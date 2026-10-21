# Duplicate Finder — UI Design Notes

Oct 21, 2026 · @umilan

The UI splits into three screens: **Setup** (what to scan and how), **Results** (several views of the same data) and an **Action basket** (what will actually happen).

## 1. Setup: sources and exclusions

Use a folder tree, not just a folder list, plus a rule builder for everything the tree can't express.

**Folder tree.** After a root is added, a quick inventory pass (seconds, even on 2 TB) lists every subfolder with its file count and size. Untick subfolders to exclude them. Each root is marked **Scan** or **Reference** (never offered for removal).

**Rule builder.** Each rule is a row: `[Include/Exclude] [field] [operator] [value]`.

- **Fields:** path, name, extension, size, modified date, detected type; any metadata tag (Make, Model, DateTimeOriginal, Software, GPS, duration, resolution)
- **Operators:** is, contains, matches regex, >, <, exists, missing
- **Examples:**
  - `Exclude path matches regex /\.(git|thumbnails)/`
  - `Exclude Software contains "WhatsApp"`
  - `Exclude size < 50 KB`
- **Presets:** macOS junk, Photos library internals, thumbnail caches
- **Live counter:** each rule shows "matches 12 340 files / 41 GB" before scanning; a regex tester shows sample paths that match and don't match

Metadata rules cost something because tags must be read. The engine evaluates path and size rules first and reads metadata only for files that survive. The UI shows that cost the same way as the methods below.

## 2. Setup: method picker with cost

Each method is a row showing what it finds, what it misses, what it reads, and its cost.

| Method | Reads | Finds |
| --- | --- | --- |
| Name + size | inventory only | copies with kept names |
| Exact (size → partial → full hash) | whole file, only for same-size candidates | byte-identical files |
| Same pixels / streams | decode | metadata-only differences |
| Perceptual (dHash/pHash) | decode | resized, recompressed |
| Video frames | ffmpeg seeks | re-encoded videos |
| Embeddings (later) | decode + model | crops, mirrors |

**Making the cost honest:**

- **Measure, don't guess.** Before the scan, run a 10-second benchmark: disk read speed, plus decode time for \~30 files of each type (JPEG, HEIC, RAW, MOV). Multiply by inventory counts to get the estimate.
- **Show marginal cost, not standalone cost.** Decoding is the expensive part and it's shared. Once "Same pixels" is on, adding pHash costs almost nothing ("+40 s"). Standalone totals would make perceptual hashing look as expensive as decoding everything.
- **Show the bottleneck type.** Mark each method disk-bound or CPU-bound. On an external HDD the full-hash pass is limited by the disk: 2 TB at \~150 MB/s is roughly 3.7 h if everything were a candidate. Partial hashing on an HDD is limited by seeks, not bytes.
- **Show cache hits.** "80 % already cached" turns a 2 h estimate into 20 min.
- **Display:** a relative bar per method, the absolute time, and a total at the bottom. During the scan, show per-phase actual vs. estimated times so estimates improve next time.

## 3. Results: several views of the same data

The Folders view is probably the most useful for backup disks; the others serve different questions.

- **Groups:** cards with thumbnails, keeper outlined, a verdict label and confidence badge, and "found by" chips (`exact` `pHash`). A pair can be found by several methods; the strongest one sets the confidence.
- **Table:** one row per file, sortable and multi-sort: verdict, method, confidence, reclaimable size, group size, capture date, folder, type. The power-user view.
- **Folders:** for each folder pair, compute the overlap and classify it:
  - "A ≡ B": identical contents
  - "A ⊂ B": everything in A exists in B
  - "A ∩ B 63 %": partial overlap

  Show it as a tree with a bar per folder for the duplicated fraction. Clicking a folder lists where its copies live, so a whole folder is resolved at once instead of 4 000 files.
- **Overlap heatmap:** folders × folders, coloured by shared size. Reveals "these five backup folders are all the same 2019 export" at a glance.
- **Treemap of reclaimable space:** answers "where is the waste?"
- **Timeline (photos):** duplicates plotted by capture date, showing which import or backup created them.

**Grouping and sorting controls:**

- **Group by:** verdict, method, folder pair, type
- **Sort by:** reclaimable bytes, confidence, method strength, count, date
- **Confidence slider:** regroups live, which is why all pair scores are stored

## 4. Ignoring findings

Offer several levels, because "ignore" means different things:

- **This pair:** isn't a duplicate
- **This file:** never flag it
- **This group**
- **Folder pair:** never compare A with B (e.g. an intentional backup)
- **Rule:** "hide Similar-shot groups", "hide anything under 70 %"

Two design choices matter:

- **Key ignores by content hash, not path.** They then survive renames, moves and re-scans.
- **Separate "Ignore" from "Not a duplicate".** "Not a duplicate" clicks are labelled examples that feed threshold calibration, so review work improves the scoring.

Keep an **Ignored** tab with a counter ("124 ignored") and restore buttons.

## 5. Action basket

Nothing is acted on from the results views. Marking files adds them to a basket that shows:

- total space freed
- files per verdict
- a warning if any group would be left with no copy
- the chosen action (quarantine or Trash)

Apply, then undo from the log if needed.
