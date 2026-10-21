---
name: media-confidence-scoring
description: Compute calibrated per-file confidence percentages for resolved capture time, location, device and origin type of photos/videos, combining explicit metadata evidence and implicit heuristic evidence, and decide actions per confidence band. Use after or alongside the media date/location/device/context resolvers.
---

# Media Confidence Scoring

Give every file an honest **percentage**: how likely its resolved values are correct. The
percentage must be **calibrated**: among files labelled "90%", about 90% should really be
right. It combines **explicit** evidence (metadata, sidecars, exports) and **implicit**
evidence (folders, counters, batches, similarity, content) from:
- `media-capture-date-resolver`
- `media-location-resolver`
- `media-device-resolver`
- `media-context-resolver`

## When to use

- After the resolvers have produced candidates and chosen values.
- Before writing anything back, to decide what to apply automatically, what to flag and what
  to send to review.
- When the user asks "how sure are we?" for a file, folder, device or the whole archive.

---

## 1. Define confidence precisely

A percentage only means something relative to a **tolerance**. Always report confidence as
the probability that the true value lies within a stated tolerance of the estimate.

| Dimension | Reported levels (P that the error is within) | Typical use |
|---|---|---|
| Time | `±1 min`, `±1 h`, `same day`, `same month`, `same year` | Exact sort, file naming, day folders, month folders, year folders |
| Location | `≤100 m`, `≤1 km`, `≤10 km` (city), `same region`, `same country` | Exact pin, neighbourhood, city folder, trip/country |
| Device | `exact unit`, `model`, `brand` | Per-owner sort, per-model stats |
| Origin type | the chosen class (capture / screenshot / scan / messenger / edit / download / AI) | Filtering |

Example output: time = 2014-07-12 15:30:12, with confidence **72% ±1 min, 96% same day, 99% same
month**.

Coarser levels are always ≥ finer levels. Enforce this monotonicity.

---

## 2. Two components of every confidence

```
P(correct within tol) = P(evidence is valid) × P(error ≤ tol | evidence valid)
                         └─ validity ─┘         └──── precision ─────┘
```

- **Validity**: is the source telling the truth at all? Was the camera clock set, is the GPS
  fix current, is the folder date really the event date, is it really the same body's counter?
  This comes from reliability tables and the agreement between independent sources
  (sections 3–4).
- **Precision**: given a valid source, how large is the residual error? This comes from the
  source's resolution or uncertainty, or from the width of the heuristic interval (section 5).

Most real errors are **validity failures** (a wrong clock, a wrong timezone, a misfiled file),
not precision. That is why agreement between independent sources matters most.

---

## 3. Base reliabilities (priors; replace with calibrated values)

Starting values for `r` = P(source is valid) **on its own**, before agreement or conflict.

### Time

| Source | r | Precision if valid |
|---|---|---|
| GPS UTC timestamp, fix fresh (≤ 120 s from capture) | 0.97 | ±2 s (+ staleness) |
| Apple `Keys:CreationDate` (with offset), Pixel/OneDrive UTC filenames | 0.97 | ±1 s |
| Phone EXIF DateTimeOriginal with OffsetTime (network-synced clock) | 0.96 | ±1 s |
| Phone EXIF DateTimeOriginal without offset (timezone assumed) | 0.90 (±1 min) / 0.97 (same day) | ±1 s, plus a possible whole-hour offset |
| Camera EXIF DateTimeOriginal, clock **verified** this session | 0.95 | ±drift (est.) |
| Camera EXIF DateTimeOriginal, clock **unverified** | 0.75 (±1 min) / 0.92 (same day) / 0.97 (same month) | ±minutes to hours |
| Google Takeout `photoTakenTime`, `Photos.sqlite` date | 0.93 | ±1 s |
| QuickTime `CreateDate`, convention learned for the model | 0.90 | ±1 s (start/end ± duration) |
| QuickTime `CreateDate`, convention unknown | 0.60 (±1 min) / 0.95 (same day) | ± timezone offset |
| Device filename with full time (`IMG_YYYYMMDD_HHMMSS`) | 0.90 | ±1 s |
| Card-fresh mtime | 0.70 | ±2 s |
| IPTC/XMP DateCreated (human-entered) | 0.70 | as entered (often day only) |
| Date-only filename (WhatsApp etc.) as capture date | 0.45 (same day) | upper bound only |
| Context heuristics | see section 5 | interval-derived |

### Location

| Source | r | Precision (σ, metres) |
|---|---|---|
| Embedded GNSS fix, fresh, `GPSHPositioningError` given | 0.97 | the stated error |
| Embedded fix, no error stated, method GPS | 0.94 | 15–30 |
| Network/cell/Wi-Fi fix | 0.90 | 100–3000 |
| Stale fix (≥ 2 min old) | 0.6–0.8 | speed × age |
| Track interpolation, Δt ≤ 2 min | 0.93 | track accuracy + movement |
| Track interpolation, Δt ≤ 10 min | 0.80 | grows with movement |
| Timeline visit / booking window | 0.75 | place radius |
| Manual pin (catalog) | 0.85 | 50–500 |
| Text geocode (city/region) | 0.70 | city/region radius |
| Visual landmark | 0.65 | 300–2000 (camera vs subject) |

### Device

| Source | r (correct unit) | r (correct model) |
|---|---|---|
| Body serial, consistent structure | 0.99 | 0.99 |
| Valid C2PA device signature | 0.99 | 0.99 |
| PRNU match above the calibrated threshold | 0.95–0.99 (by PCE) | 0.99 |
| Make/Model, structure consistent | — | 0.97 |
| Make/Model, structure suspect | — | 0.60 |
| Counter / firmware / context unit split | 0.80 | — |
| Filename/DCIM scheme only | — | 0.60 (brand 0.85) |

---

## 4. Combining explicit evidence: log-odds with dependence control

### 4.1 Group by independent origin first
Sources that share an origin are **one** vote: take the **max** reliability inside the group,
never the sum. Origins include:
- the camera clock;
- the GPS satellite clock;
- the phone network;
- the cloud server;
- a human pin;
- the filesystem copy;
- a gazetteer;
- PRNU;
- the counter sequence.

Duplicate copies of a file are never independent evidence for each other.

### 4.2 Weighted agreement in log-odds

For the chosen value `v`, each independent group `g` has reliability `r_g` and either **agrees**
(within tolerance) or **disagrees**:

```
L(v) = L_prior
     + Σ_agree     κ_g · ln( r_g / (1 − r_g) )
     − Σ_disagree  κ_g · ln( r_g / (1 − r_g) ) · d_g
P(valid) = 1 / (1 + e^(−L))
```

- `L_prior`: the log-odds of the base rate. For example, the share of files in this archive or
  device whose time is correct to the day; start with `ln(0.8/0.2)`.
- `κ_g` (dependence discount): 1.0 for the strongest group, 0.7 for further groups that might
  share failure modes (e.g. a phone EXIF time and a phone filename time both come from the
  same phone OS clock), 1.0 for truly independent ones (GPS vs camera clock).
- `d_g` scales how decisive a disagreement is:
  - 1.0 if the disagreeing value is outside tolerance and from a strong source;
  - 0.5 if the disagreement is explainable (an exact whole-hour offset = a timezone question,
    which affects the `±1 h` level but not the `same day` level).
- **Cap** the result at 99.5% for anything not verified by a human or by two independent
  tier-1 sources, and at 99.9% even then. Avoid false certainty.

### 4.3 Evaluate per tolerance level
Run 4.2 separately for each tolerance level. A source can agree at "same day" yet disagree at
"±1 min". For example, camera EXIF 3 h off from GPS: it disagrees at ±1 min, agrees at the
year level, and is treated as a clock-offset question at the day level.

### 4.4 Systematic-error guard
If **all** agreeing sources share one origin (e.g. only the camera clock: DateTimeOriginal,
CreateDate, mtime), add no agreement bonus beyond the group max. Also apply the **clock-trust
factor** of that origin:
- the body's clock verified by any cross-device or GPS comparison this session → 1.0;
- unverified → the reliability from the table;
- known-bad and corrected → the correction's own confidence (see 5.4).

---

## 5. Implicit (heuristic) evidence

### 5.1 Inherited values decay along the chain
```
P(child) = P(anchor) × r_link₁ × r_link₂ × …
```
`r_link` is the reliability of each heuristic hop (from the context resolver's trust table,
or calibrated, section 7). Two hops via a coherent folder (0.85) from a 0.95 anchor give
0.95 × 0.85 = 0.81 at the folder's precision level.

### 5.2 Precision from interval width (time)
With final interval `[lo, hi]` (width `W`) and point estimate `t̂`:
- **Uniform assumption** (no information inside the interval):
  `P(|t − t̂| ≤ T) = min(1, 2T / W)` when `t̂` is the midpoint.
- **Interpolated estimate** (counter interpolation in a regular sequence): use a triangular
  distribution centred on `t̂`, or better, the **empirical error distribution** from masking
  tests (section 7). Typically much tighter than uniform.
- **Day/month/year levels**: integrate the distribution over the calendar bucket that contains
  `t̂`. Use the bucket mass, not the ±T window.

`P(level) = P(constraints valid) × P(precision within level)`. Here `P(constraints valid)` is
the product of the `r_link` values of the constraints that actually bound the interval, i.e.
the active (binding) ones, not all of them.

### 5.3 Precision from radius (location)
For an estimate with 1-σ accuracy `σ` (circular Gaussian, Rayleigh distance):
```
P(d ≤ R) = 1 − exp( −R² / (2σ²) )
```
For a uniform disc of radius `ρ` (e.g. a city centroid): `P(d ≤ R) = min(1, R² / ρ²)`.
For reachability intersections: use the intersection area `A` and `P ≈ min(1, πR² / A)`.
Multiply by `P(valid)`.

### 5.4 Corrections carry their own confidence
- A clock-offset correction estimated from `n` samples with spread `s`: the precision of the
  correction is `s/√n` plus residual drift. Its validity is high if `n ≥ 3` and the samples are
  consistent (the spread is small relative to the offset); low if `n = 1`.
- A timezone derived from GPS vs local time gets 0.98. One assumed from the "home timezone"
  gets 0.7 for `±1 h` (the user might have travelled) but 0.97 for `same day`, unless the hour
  is close to midnight (see 5.5).

### 5.5 Boundary effects
A time estimate within the possible-offset range of a calendar boundary has a lower
`same day` confidence. Example: 23:20 with an unknown timezone. Compute the bucket mass while
allowing for the plausible timezone offsets (±1–2 h), weighted by their probabilities.

---

## 6. Combining explicit and implicit evidence for one file

1. Collect the candidates from all resolvers, **explicit and implicit**, into one table per
   dimension.
2. Group them by independent origin. A heuristic hop's origin is its **anchor's** origin chain
   plus the heuristic type. Example: a counter-interpolated time from photos with camera EXIF
   shares the camera-clock origin, so it must not count as independent confirmation of
   that camera's other files.
3. Score with section 4 for explicit agreement; use section 5 for inherited-only values.
4. **If both exist**: implicit evidence acts as an independent group, with `r = P(child)`,
   when its origin chain is independent. It **confirms** (raises `L`) or **contradicts**
   (lowers `L`, and flags the file).
   - A file whose own EXIF says 2019 while all its counter neighbours say 2014 → large
     contradiction → low confidence + review. Usually the EXIF was rewritten or the counter
     segment was mis-assigned.
5. Produce the per-level confidences; enforce monotonicity across levels.

### Worked examples

| Case | Evidence | Result |
|---|---|---|
| iPhone photo | EXIF DTO + OffsetTime (0.96), GPS UTC agrees (0.97, independent) | L = ln(0.8/0.2) + ln(0.96/0.04) + 0.7·ln(0.97/0.03) ≈ 1.39 + 3.18 + 2.43 = 7.0 → 99.9% → **cap 99.5% ±1 min** |
| Camera JPEG, no GPS, clock verified via the phone at the same event | DTO (0.95), clock offset of the session from 4 phone pairs, spread 20 s | 1.39 + 2.94 = 4.33 → 98.7% valid; precision ±1 min ~ 0.95 → **~94% ±1 min, 99% same day** |
| Same camera, clock never verified | DTO (r = 0.75 at ±1 min, 0.92 at day level) | ±1 min: valid = 1/(1+e^−(1.39+1.10)) ≈ 92%, × precision (unknown drift) ≈ 0.87 → **~80% ±1 min**. Day: 1/(1+e^−(1.39+2.44)) ≈ 98%, minus the midnight-boundary effect → **~95% same day** |
| Video between counter neighbours 3 min apart, photos verified | Anchors 0.95 × link 0.9; interval 3 min, empirical P(±1 min) = 0.7 | **~60% ±1 min, ~85% same day** (the same-day level drops slightly near midnight) |
| WhatsApp copy, no original, filename date 2019-05-14 | `bound_hi` only; prior of the gap between capture and receipt (from the archive: 45% same day, 85% within 30 days) | **~45% same day, ~80% same month** (point = filename date) |
| File in a coherent event folder (agreement 0.88, day precision), nothing else | Folder link 0.88 × anchor 0.95 | **~84% same day**, ±1 min unknown (≤ 5%) |
| Location: track interpolation Δt 40 s, track σ 10 m, moving 1 m/s | valid 0.93; σ ≈ 10 + 40·0.5 = 30 m | ≤100 m: 0.93 × (1 − e^(−100²/(2·30²))) ≈ **93%**; ≤1 km: **93%** |
| Location: city from folder name, coherent | valid 0.85; city radius 8 km | ≤1 km: 0.85 × (1/64) ≈ **1%**; ≤10 km: **85%** |

(Example numbers are illustrative; real values come from calibration.)

---

## 7. Calibration (turn priors into measured percentages)

This step is what makes the percentages true.

### 7.1 Masking tests (self-supervised, no human needed)
Use files where the truth is known with high certainty: two independent tier-1 sources agree,
e.g. GPS UTC + phone EXIF, or a geotagged phone shot.
1. **Hide** the strong evidence (drop its EXIF time or GPS).
2. Run the heuristics and resolvers as if the file were unknown.
3. Record the error and the confidence the scorer would have reported.
4. Bucket by heuristic and context: counter gap size, folder type, Δt to the track, device
   model, etc.

This gives an **empirical error CDF** per heuristic and bucket. `P(error ≤ tol)` can be read
directly from it, replacing the uniform/triangular assumptions.

### 7.2 Source reliability per device and context
Estimate `r` per model or body from the archive itself:
- the share of a body's files whose EXIF agrees with independent GPS or phone references;
- the share of a folder type's files that pass coherence;
- the QuickTime convention hit-rate per model.

Shrink small samples toward the global prior (Bayesian beta-binomial:
`r = (agree + a) / (n + a + b)` with prior `a, b` from the table).

### 7.3 Score calibration
Fit an **isotonic regression** (or Platt scaling) from raw scores to observed correctness on
the masked and human-reviewed sets, per dimension and tolerance level.
- Plot **reliability diagrams**.
- Report the **Brier score** and **expected calibration error (ECE)**; target ECE < 3 points.
- Re-calibrate after major pipeline changes.

### 7.4 Human audit sample
Review a stratified random sample: about 20 files per confidence band per dimension. Check
that the observed accuracy matches the band. Feed the results into 7.3.

---

## 8. Overall per-file confidence

There is no single universal number. Report the dimensions separately, and compute
**purpose-specific** overall scores:

| Purpose | Formula |
|---|---|
| Rename `YYYYMMDD_HHMMSS…` | `P_time(±1 min)` |
| Sort into `YYYY/YYYY-MM-DD` folders | `P_time(same day)` |
| Sort into `YYYY/YYYY-MM-DD Place` | `P_time(same day) × P_loc(≤10 km)` (assume independence unless they share an origin chain, then use `min`) |
| Per-owner album | `P_device(unit)` × the owner-label confidence |
| "Is this an original photo?" | `P_origin(capture)` |

**Default overall** (when the user asks for one number):
```
overall = weighted geometric mean of P_time(same day), P_loc(≤10 km), P_device(model), P_origin
          (weights 0.4, 0.25, 0.2, 0.15; drop missing-by-design dimensions, e.g. location for scans)
```
Also show the **weakest link** (the dimension with the minimum value).

Round the displayed percentages to the nearest 5% below 90%, and to 1% above. Never show
"100%".

---

## 9. Bands and actions

| Band | Range | Default action |
|---|---|---|
| **Verified** | ≥ 98% (or human-confirmed) | Auto-apply: write metadata, rename, move |
| **High** | 90–98% | Auto-apply; list in the summary |
| **Medium** | 70–90% | Apply to the DB and sidecars; rename/move only at coarse levels where confidence ≥ 90% (e.g. month folder); flag |
| **Low** | 40–70% | Do not write in-file; place in `review/`, or in coarse buckets with an `~` marker (e.g. `2014-07~`) |
| **Unknown** | < 40% | `unknown_date/`, `unknown_place/` … plus the known bounds as sub-folders (e.g. `unknown_date/before-2016/`) |

Choose the folder granularity per file from its own confidences. A file can be confidently
in "2014-07" while unsure about the day. Write the confidence and the basis into XMP sidecars
(or the DB) so later tools and runs can respect them.

---

## 10. Per-file output record

```json
{
  "file": "2014/2014-07 Croatia/DSC0002.MPG",
  "time": {
    "value": "2014-07-12T15:31:40+02:00", "interval": ["2014-07-12T15:30:12+02:00", "2014-07-12T15:33:05+02:00"],
    "conf": {"1min": 0.60, "1h": 0.88, "day": 0.88, "month": 0.95, "year": 0.97},
    "basis": ["counter_sequence(DSC0001,DSC0003)", "anchor: camera clock verified via phone (n=4)"],
    "conflicts": []
  },
  "location": {
    "value": [43.1729, 16.4411], "radius_m": 180,
    "conf": {"100m": 0.55, "1km": 0.90, "10km": 0.92, "country": 0.95},
    "basis": ["counter_neighbours_interp", "phone track Δt=70s"]
  },
  "device": {"unit": "Nikon D7000 #1 (Dad)", "conf": {"unit": 0.90, "model": 0.97, "brand": 0.99},
             "basis": ["shared counter sequence", "folder DCIM/100NCD70", "container fingerprint"]},
  "origin": {"value": "camera_original", "conf": 0.93},
  "overall": {"default": 0.91, "weakest": "location", "purpose": {"day_place_folder": 0.81}},
  "band": "high",
  "action": "apply_coarse_place_fine_time"
}
```

---

## 11. Reports to deliver

1. **Distribution**: per dimension and level, a histogram of files per band. Expected-correct
   count = the sum of confidences, i.e. how many files are probably right.
2. **Weakest links**: top groups (folders, devices, sequences) by number of files in the low
   or unknown bands. Fixing one anchor there often lifts hundreds of files: show the expected
   gain.
3. **Calibration panel**: reliability diagrams, ECE, Brier, masking-test error CDFs per
   heuristic.
4. **Conflict list**: files whose explicit and implicit evidence disagree strongly.
5. **What-if**: the effect of the user confirming one anchor (e.g. "this camera's clock was
   +1 h on this trip") on the confidence of all dependent files. Propagate and show the change.

---

## 12. Pitfalls

- **Double counting**: copies, sidecars and inherited values reuse the same origin. Group by
  origin chain before combining.
- **Systematic errors look confident**: a whole trip with a wrong camera clock is internally
  consistent. Only an independent clock (GPS, phone, known event) reveals it. Keep the
  clock-trust factor until verified.
- **Precision ≠ confidence**: 6-decimal coordinates or millisecond timestamps say nothing about
  validity.
- **Uniform-interval assumptions are pessimistic for regular sequences** and optimistic for
  folder dates. Replace them with masking-test CDFs as soon as there is data.
- **Coarse levels can be wrong while fine levels look fine** only if monotonicity is broken.
  Enforce it.
- **Tiny samples** (a body with 3 files) need shrinkage toward priors.
- **Human confirmations** override everything, but log them, so an accidental confirmation
  can be undone.
