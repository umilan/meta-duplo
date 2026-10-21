# Data model

The full DDL is in `schema.sql`. This file explains **why** the tables look the way they do.

## Layers

| Layer | Tables | Rewritten when |
|---|---|---|
| **Physical** (what is on disk) | `sources`, `folders`, `files`, `sidecars` | Inventory re-runs |
| **Raw facts** (expensive to get) | `meta_raw`, `hashes`, `fingerprints` | Rarely: only if extraction logic changes |
| **Grouping** | `clusters`, `cluster_members`, `sibling_groups`, `sibling_members`, `device_units` | Cluster / device stages |
| **Evidence** | `candidates`, `context_evidence`, `tracks` | Resolver / context stages (cheap) |
| **Results** | `resolution`, `confidence`, `cluster_band`, `calibration` | Resolve / propagate / score stages |
| **Human** | `overrides` | Only by the user |
| **Actions** | `plans`, `actions` | Planner / apply |
| **Ops** | `runs`, `jobs`, `file_errors`, `settings` | Runner |

Re-running cheap layers never requires touching the disk again. All disk-derived facts live in
the raw-facts layer.

## Key decisions

1. **Resolution is per cluster, not per file.** Duplicates and copies share one truth. Siblings
   are separate clusters linked by `sibling_groups`; their resolutions are kept equal by
   `equal` constraints.
2. **One `candidates` table for all dimensions.** It is filtered by `dim`. The columns are
   sparse on purpose: one table keeps the scoring code generic, and lets the UI show "all
   evidence for this file" with one query.
3. **`origin` = independence group.** This column is what the confidence math groups by. It
   must be filled carefully: two fields written by the same camera clock share an origin.
4. **Time storage.** `t_utc` is used only when the zone is known or derived. `t_local` keeps
   the naive value. `tz_status` ∈ `known|derived|assumed|unknown`. Intervals use `t_lo`/`t_hi`.
5. **Place storage.** Every place is `lat, lon, radius_m`. Text-only places become a disc
   (centroid + feature radius).
6. **Overrides are separate and revocable.** Overrides are not applied by editing results.
   They enter the pipeline as tier-1 evidence with `origin=human`, so they propagate to
   dependent files and can be undone.
7. **Plans store before/after** for every action, so undo needs nothing but the plan itself.
8. **Raw metadata is compressed JSON blobs**, around 5–20 KB per file. That is 3–10 GB for
   500k files, acceptable and far faster than re-reading the disk. Hot fields are copied into
   `fingerprints` and `candidates` for indexing.

## Size estimates (500k files)

| Table | Rows | Approx. size |
|---|---|---|
| files | 500k | 150 MB |
| meta_raw | 500k | 3–10 GB |
| candidates | 5–10 M | 1–2 GB |
| context_evidence | 2–5 M | 0.5–1 GB |
| tracks | 1–5 M | 0.3–1 GB |
| thumbs/ (outside DB) | 500k × 2 sizes | 8–15 GB |

Keep the library folder on the Mac's internal SSD, not on the source HDD.

## Read models for the UI (views.sql, planned)

- `v_file_card`: file + cluster + chosen time/place/device + band, for grids.
- `v_review_queue`: clusters in the low/unknown bands, or with conflicts, ordered by impact
  (how many dependent files a fix would lift).
- `v_folder_report`: folder type, tokens, coherence, member counts per band.
- `v_device_units`: units with counts, active periods, owner labels.
- `v_sequence`: per body, index vs time points, for the counter chart.
- `v_conflicts`: candidate disagreements above threshold.
