---
name: media-context-resolver
description: Derive implicit time, location, device and event evidence for photos/videos from their context — folder structure and names, neighbouring files, counters, import batches, sidecars, derivation chains, archives and content cues — and feed it as constraints to the date, location and device resolvers. Use when files lack or contradict metadata.
---

# Media Context Resolver

Files rarely stand alone. Their **folder**, **neighbours**, **counters**, **copy history**,
**sidecars** and **visible content** carry evidence about when, where and with what device
they were made. This skill extracts that implicit evidence and turns it into **constraints**
for the other resolvers.

It does not decide final values itself. It emits evidence rows; the resolvers score them:
- `media-capture-date-resolver` (time)
- `media-location-resolver` (place)
- `media-device-resolver` (device / unit / owner / origin type)

## When to use

- Files are missing metadata, or their metadata is untrustworthy (stripped, rewritten, re-encoded).
- An archive has a meaningful folder structure: events, trips, years, devices, people.
- Cameras produced mixed stills and videos where only one kind kept its metadata.
- Before the gap-filling steps of the three resolvers. Re-run after each resolver pass, because
  new anchors unlock new inferences.

## Core model: intervals, constraints, propagation

1. **Every unknown is a set, not a guess.**
   - Time: an interval `[lo, hi]` plus an optional point estimate.
   - Place: a disc (centre + radius), or the intersection of several discs.
   - Device: a set of candidate units.
2. **Each heuristic adds a constraint.** Examples:
   - unary bound: `t ≥ model release date`;
   - binary order: `t(file A) ≤ t(file B)`;
   - equality: siblings share a time;
   - membership: the file belongs to an event or batch.
3. **Propagate until nothing changes.** Use interval narrowing over a constraint graph, and
   disc intersection for space.
   - If a constraint makes an interval empty, a conflict exists. Do not force a value:
     report the minimal set of conflicting constraints.
4. **Weight and record every constraint.** Each one stores its `heuristic`, the source
   files, its strength (hard/soft) and the reliability learned for its context. Soft
   constraints may be dropped to resolve conflicts, weakest first.
5. **Confidence = width.** A narrow interval or small disc after propagation means high
   confidence. A wide one stays honest about uncertainty.

### Evidence row (output of this skill)

```sql
CREATE TABLE context_evidence(
  id INTEGER PRIMARY KEY,
  target TEXT,          -- time|place|device|event|origin
  file_id INT,          -- subject file (or NULL for group-level)
  group_id TEXT,        -- folder/batch/sequence/event id when group-level
  kind TEXT,            -- bound_lo|bound_hi|interval|order|equal|disc|candidate_set|member_of
  value TEXT,           -- JSON: {"lo":..,"hi":..} / {"lat":..,"lon":..,"r_m":..} / {"units":[..]}
  related_file_id INT,  -- for order/equal constraints
  heuristic TEXT,       -- e.g. counter_sequence, folder_event_date, import_batch, derivation
  strength TEXT,        -- hard|soft
  reliability REAL,     -- 0..1, learned per context (coherence tests below)
  sources TEXT,         -- JSON list of file ids / paths used
  notes TEXT
);
```

---

## A. Folder structure context

### A1. Parse every path component
Extract the following from **each level** of the path, not only the parent folder.

| Token type | Examples | Feeds |
|---|---|---|
| Full date / date range | `2014-07-12`, `20140712`, `12.7.2014`, `2014-07-10 – 2014-07-17`, `10-17.7.2014` | time interval |
| Month / year / season | `2014-07`, `July 2014`, `Júl 2014`, `2014`, `Leto 2014`, `Winter 13/14` | time interval (coarse) |
| Ordinal / relative | `Day 3`, `Den 2`, `Part 2`, `Week 1`, `#04` | offset from a dated sibling or parent |
| Place | `Croatia`, `Hvar`, `Bratislava`, `Tatry`, `Grandma's house` | place prior (geocode, or user-labelled place) |
| Event | `Wedding`, `Svadba`, `Birthday Jana 5`, `Christmas`, `Vianoce`, `Graduation` | event membership; holiday date windows; age-based years |
| Person | `Jana`, `Dad`, `Kids` | owner/device hints; event grouping |
| Device / source | `DCIM`, `100CANON`, `iPhone Jana`, `GoPro`, `Drone`, `SD card 2`, `Camera Uploads` | device candidates; origin type |
| Pipeline / tool | `Lightroom`, `Export`, `Edited`, `Originals`, `Takeout`, `Google Photos`, `iCloud Photos`, `Backup`, `Recovered`, `found.000` | origin type; reliability of the folder's dates |
| Messenger / web | `WhatsApp Images`, `Sent`, `Telegram`, `Messenger`, `Downloads`, `Screenshots` | origin type; dates are receive/save times |

- Handle multiple languages and date orders: DMY (Europe) vs MDY (US) vs YMD. Resolve an
  ambiguous order per folder tree, using its coherent members.
- Watch for stop words ("New folder", "Misc", "Photos", "Pics", "Unsorted").

### A2. Classify the folder type
Each folder gets one type, which sets how much its tokens can be trusted.

| Type | Recognition | Date meaning | Place meaning |
|---|---|---|---|
| **Camera card / DCIM** | `DCIM/1xxXXXXX`, camera filename schemes | none in name; order and counter are gold | none |
| **Event / trip** | Date and/or place and event words; members cluster tightly in time | event dates (trust after coherence test) | event place |
| **Capture-date import** | `YYYY/YYYY-MM-DD` trees made by Lightroom, Photos, digiKam, Synology | capture date (usually reliable) | none |
| **Import-date / backup dump** | Folder date mostly **after** its members' dates; names like `Backup`, `Import` | upper bound only | none |
| **Device dump** | `iPhone Jana`, `Galaxy S10`, `Camera Roll` | none (spans years) | none |
| **Messenger / downloads / screenshots** | Folder names, filename patterns | receive/save time = upper bound | none |
| **Export / edited** | `Export`, `Edited`, `Final`, `For print` | export time ≥ capture time | inherited from originals |
| **Recovered / carved** | `found.000`, `recup_dir.N`, `FILE0001.CHK` | none | none |

### A3. Coherence test: learn whether to trust a folder
For each folder and each token-derived claim (date interval, place area):
- `coverage` = the share of members with **independent** resolved values (from metadata, not
  from this folder).
- `agreement` = the share of those whose value lies inside the claim.

| Result | Action |
|---|---|
| agreement ≥ 0.8, coverage ≥ 3 files | Claim is **reliable** (reliability = agreement). Undated or unlocated members inherit it as a soft interval or disc |
| Members mostly **before** the folder date | Reclassify as an import/backup date → `bound_hi` only |
| agreement < 0.5 | Claim is **unreliable** for this folder; emit nothing |
| coverage = 0 | Inherit with low reliability (0.3); flag for review |

### A4. Hierarchy and siblings
- **Intersect** claims down the tree (`2014/` ∩ `2014-07 Croatia/` ∩ `Day 3/`). The deepest
  reliable level wins on precision.
- **Ordinals:** `Day N` folders form a sequence. With one dated sibling (`Day 1` = 10 July),
  derive the others. Unknown siblings are ordered by their member times.
- **Sibling folder order** (natural sort, or by member medians) gives order constraints
  between their undated files when the series is clearly sequential.
- **Parent place + child event:** the child inherits the parent's place claim only if the child
  passes the coherence test, or has no geotagged members.

### A5. Folder filesystem metadata
- Directory **birth time** ≈ when the folder was created (import/organize time) → `bound_hi`
  for members' capture time. Weak if the tree was copied as a whole.
- Directory **mtime** = last add/remove → `bound_hi`; weakest.
- **Desktop.ini, `.DS_Store`, `.picasa.ini`, `Thumbs.db`, `.nomedia`, `@eaDir`** reveal which
  tools touched the folder (and sometimes album names or stars). Use them for origin type and
  pipeline classification, not for dates.

---

## B. Neighbourhood and sequence context

### B1. Counter sequences (strongest neighbourhood heuristic)
- Group by **body/unit** first, using the device resolver. Never mix bodies that share a prefix.
- Monotonic index = `folder_number × 10000 + counter`. Split into segments at resets (an index
  drop with a time jump forward, a card format, a numbering reset).
- **Shared counters**: Canon `IMG_`/`MVI_`, Nikon `DSC_` stills/video, iPhone `IMG_`
  (photos, videos, screenshots). Some vendors number videos separately (e.g. Sony `C0001`);
  for those, cross-type constraints come only from time interleaving.
- Constraints emitted:
  - `order` between consecutive indices (hard within a segment);
  - `interval [t_prev, t_next]` for unknowns between dated neighbours;
  - video-specific: the counter is taken at record start; the next file
    `≥ video_start + duration`. Learn per model whether the container date is the start or
    the end of recording.
  - **place:** interpolate between same-body neighbours that are close in time and space.
    Otherwise use the nearer neighbour with radius = neighbour distance or speed × gap.
- The inverted case (videos known, photos unknown) is symmetric. Apply the model's QuickTime
  UTC/local convention first.
- **Long range:** a per-body regression of index → time gives weeks- or months-level bounds for
  files far from any anchor.
- **Counter in MakerNotes** (`ShutterCount`, `FileNumber`, `ImageNumber`) survives renaming. A
  renamed file can still be placed in its sequence.

### B2. Filesystem order and batches
- **Import batch**: files with near-identical birth times, or consecutive NTFS MFT /
  inode numbers, were copied together (usually one card dump). Emit `member_of(batch)`.
  - The batch interval is the dated members' `[min, max]`, widened by about 10%.
  - `bound_hi` = the batch's copy time.
- **FAT/exFAT directory entry order** on an original card or card image = camera write order
  → `order` constraints (as good as a counter).
- **ZIP/TAR entry order and timestamps** in archives: entry mtimes are frozen copies. The
  archive creation date is `bound_hi`.
- **Takeout part numbers / export chunks**: no capture meaning. Use only for derivation
  (export ≥ capture).

### B3. Siblings, sidecars, companions (equality constraints)

| Group | Recognition | Constraint |
|---|---|---|
| RAW + JPEG (+ HEIF) | Same base name **and** same body; time within 1 s | `equal(time, place, device)` |
| Live Photo (HEIC/JPG + MOV) | Same base name, Apple `ContentIdentifier` | `equal` (MOV start ≈ still − ~1.5 s) |
| Bursts, brackets, panorama frames | `BurstUUID`, consecutive counters, sub-second spacing | Same time ± seconds; same place |
| Sidecars | `.xmp`, `.THM`, `.AAE`, `.SRT`, `.LRV`, `.XML`, `.json` (Takeout), `.pp3`, `.dop` | Sidecar evidence applies to its parent; parent and sidecar are moved as a unit |
| Edit derivatives | `IMG_E1234`, `-edited`, `_edit`, `(1)`, `copy`, `Kopie` | `order`: original ≤ derivative; `equal(place, device)` |

### B4. Derivation chains (order constraints)
- `original ≤ edited export ≤ messenger copy ≤ email date ≤ upload date ≤ backup/archive date`.
- Perceptual near-duplicates **across** folders link a stripped copy (WhatsApp, Facebook,
  download) to its original. The copy inherits equality on capture time, place and device,
  and contributes its own receive/upload times as `bound_hi`.
- **Chat exports** give the exact send time of a file (`bound_hi`) and the sender (an
  owner/device hint).

---

## C. Event and session context

- **Sessions**: split each body's (or each person's) timeline at gaps > 2 h (tune this).
  Undated files linked into a session by counter, batch or visual similarity inherit the
  session interval.
- **Events**: cluster all resolved files in time + place (e.g. HDBSCAN on
  `[t_scaled, lat, lon]`). Join undated files to events by:
  - CLIP / perceptual similarity to event members (same scene, outfits, light);
  - folder event tokens;
  - shared people (face clusters).
- **Multi-device events**: phones and cameras at the same event can calibrate each other's
  clocks and locations (see the date and location resolvers). Context links (same event
  folder, same scene) supply the pairs.
- **Recurring places** (home, grandparents, office): learn user-labelled places from frequent
  clusters. Folder or person tokens then map to known coordinates without geocoding.

---

## D. Device and owner context

- **Device lifetime**: the first/last seen dates of each unit, and the firmware/OS versions
  present → `interval` for unassigned files that match that unit's fingerprint.
- **Accessory and lens timeline**: a lens serial's first appearance gives `bound_lo` for later
  files with that lens.
- **Model, format and feature release dates** → `bound_lo`. Examples: HEIC 2017, Live Photos
  2015, Ultra HDR 2023, EXIF offsets 2016; a model's launch date.
- **Owner context**: person tokens in folders, uploader accounts (Takeout, shared libraries),
  messenger "Sent" folders, chat senders → owner and device candidate sets. This is also
  which person's location track applies.
- **Sensor dust** (interchangeable-lens cameras): dust maps change between cleanings →
  ordering within a body.

---

## E. Content cues (weakest; use to break ties or flag errors)

| Cue | Gives |
|---|---|
| OCR of burned-in date stamps, dashcam overlays, status-bar clocks, on-screen dates, chat timestamps | Exact or day-level time |
| Birthday candles, number balloons, cake text plus a known birthday | Day/year |
| Face clusters + age estimation (children) | Year range |
| Season (snow, foliage, blossom), holidays (Christmas tree, Easter), school events | Month window (hemisphere-aware) |
| Landmarks, signs, languages, licence plates | Place (subject location; camera within some radius) |
| Daylight vs claimed time: brightness/EV, sun elevation from time + place, shadow direction | Detects AM/PM swaps, 12 h shifts, wrong timezones, wrong hemisphere |
| Weather vs historical weather for the claimed day and place | Weak consistency check |

Visual and AI cues need user consent before images are sent to any external API.

---

## F. Pipeline

```
1. Inventory paths     → tokenize every component (A1), classify folders (A2)
2. Load anchors        → current resolved time/place/device from the three resolvers
3. Coherence tests     → per folder and per claim (A3); keep reliability scores
4. Emit constraints    → folders (A3–A5), sequences (B1), batches (B2), siblings (B3),
                         derivations (B4), sessions/events (C), device timelines (D), content (E)
5. Propagate           → narrow intervals/discs to a fixed point; detect empty sets (conflicts)
6. Resolve conflicts   → drop the weakest soft constraints first; report what was dropped
7. Hand back           → evidence rows into the resolvers' candidate tables (as tier 3–5)
8. Iterate             → re-run 2–7 after the resolvers update (new anchors → new inferences)
9. Report              → per file: interval/disc width, heuristics used, conflicts
```

### Propagation sketch

```python
def propagate(files, constraints, max_iter=50):
    for _ in range(max_iter):
        changed = False
        for c in sorted(constraints, key=lambda c: c.strength_rank):
            if c.kind == "order":                       # t(a) <= t(b)
                a, b = files[c.a], files[c.b]
                new_b_lo = max(b.lo, a.lo); new_a_hi = min(a.hi, b.hi)
                changed |= b.set_lo(new_b_lo) | a.set_hi(new_a_hi)
            elif c.kind == "equal":
                lo = max(files[c.a].lo, files[c.b].lo); hi = min(files[c.a].hi, files[c.b].hi)
                changed |= files[c.a].set(lo, hi) | files[c.b].set(lo, hi)
            elif c.kind in ("interval", "bound_lo", "bound_hi"):
                changed |= files[c.file].intersect(c.lo, c.hi)
            elif c.kind == "disc":
                changed |= files[c.file].intersect_disc(c.lat, c.lon, c.r_m)
        conflicts = [f for f in files.values() if f.empty()]
        if conflicts:
            drop_weakest_soft_constraint(conflicts, constraints)   # log it
            continue
        if not changed:
            break
```

Point estimate inside the final interval, in order of preference:
1. counter interpolation;
2. the median of the folder's or event's coherent members;
3. the midpoint.

Confidence follows the width: ≤ 1 min high; ≤ 1 day medium; ≤ 1 month low; wider → none.
Place: ≤ 100 m high; ≤ 2 km medium; ≤ 25 km low.

---

## G. Trust order of implicit evidence (default soft-constraint weights)

| Rank | Heuristic | Default reliability |
|---|---|---|
| 1 | Siblings / sidecars / Live Photo / burst equality | 0.95 |
| 2 | Same-body counter neighbours ≤ 10 min apart; card directory order | 0.9 |
| 3 | Derivation order (original ≤ copy ≤ upload) | 0.9 (hard for order) |
| 4 | Visual near-duplicate of a dated/located file | 0.8 |
| 5 | Import batch with a tight dated range | 0.75 |
| 6 | Coherent folder date (day) / coherent folder place (city) | the learned agreement |
| 7 | Counter neighbours hours to days apart; event membership by similarity | 0.6 |
| 8 | Coherent folder month/year; capture-date import trees without a test | 0.5 |
| 9 | Device lifetime, firmware or model bounds; long-range counter regression | 0.4 (bounds are hard) |
| 10 | Content cues (OCR exact stamps higher; season, age lower) | 0.2–0.7 |
| 11 | Untested folder tokens, folder filesystem times | 0.3 / bound only |

---

## H. Pitfalls

- **Re-organized archives:** a folder named "2014 Croatia" may contain files someone dragged in
  from other trips. Always run the coherence test; never trust a name blindly.
- **Folder copies reset folder timestamps.** Directory birth times often reflect the last copy
  of the whole tree.
- **Two bodies, one prefix** (two Nikons, `DSC_`): counter constraints across bodies are
  invalid. Split by unit first.
- **Counter resets and wraps** create false order constraints. Segment carefully; treat
  cross-segment order as soft.
- **Separate video counters** on some vendors: no index-based constraints between stills and
  videos there.
- **Video timing:** start vs end of recording, and the UTC/local convention per model.
- **Shared albums and family dumps** mix owners. Owner context must not attach one person's
  track or device to another person's files.
- **Messenger and download folders** look like event folders (dated subfolders) but carry
  receive times.
- **Exports named by export date** ("Export 2020-03-01") are upper bounds, not capture dates.
- **Ordinals and month names in other languages:** keep a multilingual month/weekday/holiday
  table (e.g. sk/cs/de/en).
- **Conflicts are signal.** An empty interval often reveals a wrong camera clock, a wrong
  timezone or a misfiled file. Surface it; don't smooth it away.

## Deliverables

1. `context_evidence` table merged into the resolvers' candidate tables.
2. Folder classification report: type, extracted claims, coherence/reliability, members
   affected.
3. Sequence report: per body, counter-segment plots (index vs time) with inferred files marked.
4. Conflict report: minimal conflicting constraint sets, with the files involved.
5. Per-file explanation: final interval/disc, contributing heuristics, dropped constraints.
