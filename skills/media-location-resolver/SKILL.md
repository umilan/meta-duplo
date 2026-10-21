---
name: media-location-resolver
description: Restore, verify and fill in GPS location for photos and videos in a large messy archive using embedded metadata, sidecars, track logs, location-history exports, neighbouring files, place-name text and visual cues. Use for geotagging, location recovery or location cleanup of photo/video libraries.
---

# Media Location Resolver

Determine **where each photo or video was taken**: the camera position, with an honest
accuracy radius. It combines every available location source, repairs broken coordinates,
and fills gaps from time-matched evidence. Every result is auditable, confidence-scored, and
written back only after review.

## When to use

- Photos and videos lack GPS: cameras without GPS, messenger or social copies with stripped
  metadata, scans, recovered files.
- Existing coordinates look wrong: 0,0, wrong hemisphere, stale fixes, China offset, a
  "teleport" between consecutive shots.
- The user has track logs or location-history exports and wants them matched to media.
- Organizing or mapping an archive by place.

## Dependency: capture time first

Location matching is **time matching**. Run (or reuse the output of) the
`media-capture-date-resolver` skill first, so that every file has a resolved **UTC**
capture time, a timezone status and a clock correction. Its duplicate clusters and sibling
groups are also reused here.

The link works in both directions. A resolved location yields a timezone (via
`timezonefinder`), which can turn naive EXIF local times into UTC. Iterate once:
dates → locations → timezones → re-check dates.

## Core principles

1. **Never touch originals.** Use a read-only source, dry runs, logs and an undo script.
2. **Every location is a point plus an accuracy radius** (metres). A city name is a point
   with a 5–20 km radius, not an exact coordinate.
3. **Camera position ≠ subject position.** IPTC/XMP `LocationShown` and visual landmark
   detection describe the subject. `LocationCreated`, EXIF GPS and track logs describe the
   camera. Keep them separate.
4. **Never overwrite a good original camera fix.** Only fill gaps, or repair provably broken
   values (sign, datum, China offset). Record each repair.
5. **Independent evidence beats repeated evidence.** A file's GPS and its own duplicate's GPS
   are one vote. A phone track and a camera GPS fix are two.
6. **Location is sensitive data.** Default to keeping results in sidecars and the DB, and
   offer to strip GPS on export or sharing.

---

## Workflow overview

```
1. Load dates     → resolved UTC time, tz status, clusters, siblings, camera bodies, sessions
2. Extract        → all embedded location tags incl. video telemetry streams (-ee)
3. Collect tracks → GPX/KML/NMEA/FIT, Google Timeline, other exports → one tracks table
4. Collect hints  → catalogs, cloud exports, chat-shared locations, bookings, folder/album names
5. Validate       → range, 0,0, hemisphere/sign, datum/GCJ-02, stale fix, teleport checks
6. Propagate      → duplicates and siblings share camera-position evidence
7. Match tracks   → time-interpolate every file without a strong fix against all tracks
8. Session fill   → neighbours from other devices in the same session or event
9. Text geocode   → IPTC city/country, folder or album names → bounded geocode
10. Visual        → landmark / visual geolocation for what remains (low confidence)
11. Score/pick    → per cluster: best candidate, accuracy radius, confidence
12. Review        → map report: confidence colours, teleports, conflicts
13. Apply         → write GPS (+ accuracy, + source note), optional reverse-geocoded IPTC names
```

Track these steps with the task list. On big archives, run extraction in the background.

---

## Step 2: Embedded location sources

### Extraction

```bash
exiftool -r -a -G1 -s -n -ee3 -json -charset filename=utf8 \
  -api LargeFileSupport=1 \
  "-GPS*" "-*GPS*" "-*Location*" "-*ISO6709*" "-*City*" "-*Country*" \
  "-*State*" "-*Sublocation*" "-XMP-iptcExt:all" "-XMP-photoshop:all" \
  "-IPTC:all" "-DateTimeOriginal" "-SubSecDateTimeOriginal" \
  -Make -Model -SerialNumber -Software \
  /path/to/archive > loc_meta.json
```

- `-n` gives signed decimal degrees, so you never parse DMS strings.
- Check `GPSLatitudeRef` / `GPSLongitudeRef` separately in the non-`-n` output. Files that
  have a value but are missing the ref, or have an inconsistent ref, are a classic bug.
- `-ee3` extracts per-sample telemetry: GoPro GPMF GPS5/GPS9, DJI, Sony/Panasonic AVCHD
  MDPM GPS, many dashcams (Novatek `freeGPS`, Viofo, Garmin, BlackVue), Insta360, timed
  `camm` / `mebx` tracks, and subtitle-track NMEA.
- To export embedded video tracks as GPX:
  `exiftool -ee3 -p gpx.fmt -api GeoMaxIntSecs=0 video.mp4 > video.gpx`. The `gpx.fmt`
  file ships with ExifTool in `fmt_files/`.
- **Coverage audit per device model.** Video GPS is stored in vendor-specific MP4/MOV boxes, and
  renamers and other tools often miss some of them (Samsung phone MP4s are a known example).
  - Compare, per `make+model`: the percentage of photos with GPS vs the percentage of videos
    with GPS.
  - If photos have it and videos don't, dump a sample with `exiftool -v3 -ee3` and look for
    `©xyz`, `Keys`, `udta` vendor atoms or timed-metadata tracks before concluding that the
    videos have no location.
- **Extraction integrity**: key every record by `SourceFile`, re-read files with empty
  results in small batches, and spot-check a random sample individually (as in the date
  resolver).

### Embedded source table

| Source (group:tag) | What it is | Kind | Tier |
|---|---|---|---|
| `GPS:GPSLatitude/Longitude(+Ref)`, `GPSAltitude(+Ref)` | Camera/phone fix | camera | 1 |
| `GPS:GPSHPositioningError`, `GPSDOP`, `GPSSatellites`, `GPSMeasureMode` | Accuracy indicators | quality | — |
| `GPS:GPSProcessingMethod` | `GPS`, `NETWORK`, `CELLID`, `WLAN`, `fused`. Network/cell is coarse (radius 100 m–3 km) | quality | — |
| `GPS:GPSDateStamp/TimeStamp` | UTC time of the **fix** (not necessarily of the shot) | quality / stale check | — |
| `GPS:GPSMapDatum` | `WGS-84` expected. `TOKYO` or others need a datum shift | quality | — |
| `GPS:GPSImgDirection(+Ref)`, `GPSSpeed`, `GPSTrack` | Heading and speed (useful for subject projection and teleport checks) | context | — |
| `XMP-exif:GPSLatitude/Longitude` | Often written by Lightroom, digiKam or darktable geotagging (may be manual) | camera / human | 1–2 |
| `XMP-iptcExt:LocationCreated` (City, CountryCode, GPS…) | Where the camera was | camera / human | 2 |
| `XMP-iptcExt:LocationShown` | Where the **subject** is | subject | 3 |
| `XMP-photoshop:City/State/Country`, `XMP-iptcCore:Location`, `CountryCode` | Place text | text | 4 |
| `IPTC:City` (2:90), `Sub-location` (2:92), `Province-State` (2:95), `Country-PrimaryLocationCode` (2:100), `Country-PrimaryLocationName` (2:101) | Place text | text | 4 |
| `IFD1` / MPF preview EXIF GPS | May survive edits of the main EXIF | camera | 1 |
| `MakerNotes` location text (e.g. Panasonic `City`, `Landmark`, `Country`) | Camera-side place names from its built-in database | text | 3 |
| `Keys:GPSCoordinates` (`com.apple.quicktime.location.ISO6709`) + `location.accuracy.horizontal` | iPhone/iPad video, with accuracy | camera | 1 |
| `UserData:GPSCoordinates` (`©xyz`), e.g. `+48.3774+017.5872/` | Android/Samsung/many camera videos | camera | 1 |
| 3GP `loci` atom | Location name + coordinates | camera | 1–2 |
| Telemetry streams via `-ee` (GPMF, DJI `djmd`, MDPM, dashcam, `camm`) | Per-sample track inside the video | track | 1 |
| DJI aircraft vs home point; relative vs absolute altitude | For drones, use the aircraft position for the shot | camera | 1 |

---

## Step 3: Track and history sources (time series)

Normalize every track into one table:
`tracks(track_id, source, device, utc, lat, lon, alt, accuracy_m, speed, kind)`,
where `kind` is `raw | path | visit`.

| Source | Format notes | Accuracy / use |
|---|---|---|
| **GPS loggers, sport watches, bike computers, apps** (Garmin, Strava, Komoot, Polar, Suunto, Wikiloc…) | GPX/FIT/TCX/KML. FIT → GPX with `gpsbabel` or `fitparse` | 3–15 m, excellent |
| **Embedded video telemetry** (GoPro, DJI, dashcams, 360 cams) | Extracted per sample with `-ee3` | Excellent; also usable as a track for *other* files shot nearby |
| **DJI `.SRT` subtitle sidecars, BlackVue `.gps`, `.NMEA` files, Garmin VIRB `.fit`** | Parse NMEA `$GPRMC` / `$GPGGA`; SRT per frame | Excellent |
| **Google Location History — Takeout `Records.json`** (older exports) | `latitudeE7` / `longitudeE7` (÷1e7), `accuracy`, `timestamp`, `source` (GPS/WIFI/CELL), `deviceTag` | 5 m – 2 km; filter on `accuracy` |
| **Google Semantic Location History** (`Semantic Location History/YYYY/YYYY_MONTH.json`) | `placeVisit` (with location and duration) and `activitySegment` (with waypoints) | Visits are good for "stayed here"; paths are sparse |
| **Google Timeline on-device export** (`Timeline.json`, localized filename) | `semanticSegments` (visits, activities, `timelinePath` points as `"geo:lat,lon"` strings), `rawSignals`, `userLocationProfile`. Exported from the phone (Android: Settings → Location → Location services → Timeline → Export; iOS: Google Maps → Settings → Personal content) | As above. Times are ISO with an offset |
| **Google Photos Takeout JSON** (per photo) | `geoData` (possibly **estimated** or user-edited), `geoDataExif` (original EXIF). `0.0,0.0` means none | `geoDataExif` is tier 1. `geoData` alone is tier 3 (may be an estimate) |
| **Apple Photos `Photos.sqlite`** | `ZASSET.ZLATITUDE` / `ZLONGITUDE` (−180 means none); can include user edits | Tier 1–2 |
| **Snapchat memories export** (`memories_history.json`) | Location string "lat, lon" per memory | Tier 2 |
| **Facebook / Instagram exports** | Post or check-in places, sometimes coordinates | Tier 3–4 (often a place, not the camera) |
| **Swarm / Foursquare, Yelp, Google Maps reviews and contributions** | Check-in = place + time | Tier 3 visit |
| **Chat exports** (WhatsApp / Telegram / Signal shared location) | e.g. `location: https://maps.google.com/?q=48.37,17.58` with the message time | Tier 2 point at that time |
| **Travel records**: flights, train tickets, hotel/Airbnb bookings, car rental, Uber/Bolt trips, toll/parking, card transactions with merchant address, calendar events with location, emailed itineraries | Address/place + time window | Tier 3–4 visits; strong for "which city or country on which day" |
| **Car / navigation**: Google Maps navigation history, car-maker app trips (Tesla etc.), OBD loggers | Tracks or trips | Tier 2 |
| **Own trackers**: OwnTracks, Arc, Life360, Overland, Dawarich/Traccar servers | Raw points | Tier 1–2 |
| **Other photos with GPS** (phone shots during a camera session) | Build a pseudo-track from geotagged files, sorted by resolved UTC | Tier 2 (accuracy = theirs + movement) |

---

## Step 4: Hint sources (non-time-series)

| Source | Notes | Tier |
|---|---|---|
| Catalogs: Lightroom `.lrcat` (`AgHarvestedExifMetadata.gpsLatitude/Longitude`), digiKam (`ImagePositions`), Immich, PhotoPrism, Synology Photos, Shotwell, Apple Photos | May hold manual geotags that were never written to the files | 2 (human) |
| Cloud APIs: Flickr geo, OneDrive `location`, Google Photos | | 2 |
| **Folder and album names** (`2014-07 Croatia Hvar`, `Wedding Bratislava`) | Geocode with constraints (see Step 9) | 4 |
| **Filenames with place names** or coordinates (rare: some GPS cameras, map exports, Street View) | Parse with regex | 2–4 |
| IPTC/XMP place text (city, country) | Geocode to centroid + radius | 4 |
| Panorama/stitching project files (`.pto`, `.pts`), slideshow or album projects | Sometimes titles or places | 4 |

---

## Step 5: Validation rules

Every candidate gets `valid`, `repair` and `reason` fields.

| Check | Rule | Action |
|---|---|---|
| Range | `|lat| ≤ 90`, `|lon| ≤ 180`, not NaN | Reject |
| Null Island | `|lat| < 0.0001 and |lon| < 0.0001` | Reject (unset) |
| Placeholder | Same exact coordinate on more than N files from different devices and dates (a home/default/"last known" value written by an app) | Downgrade to tier 4, flag |
| Missing or wrong ref | Value present but `Ref` missing, or sign contradicts `Ref` | Recompute the sign from other evidence (timezone offset, neighbours, IPTC country); mark `repair=sign` |
| Hemisphere sanity | The resolved timezone offset should roughly match longitude (`offset_h ≈ lon/15`, ±3 h, wider for China, India and Spain-like zones); `CountryCode` should contain the point | Flag mismatches; try a sign flip and accept it if the flipped point matches both |
| Datum | `GPSMapDatum` is not WGS-84 (e.g. `TOKYO`) | Convert to WGS-84; `repair=datum` |
| **China GCJ-02** | Point inside mainland China, and the source is a Chinese-market phone, a camera app, or WeChat/Amap/Baidu-derived (BD-09). Typical shift about 100–700 m | Convert GCJ-02 → WGS-84 (`eviltransform`-style algorithms; BD-09 → GCJ-02 first). Only when verified (e.g. against a track or another device); otherwise flag |
| Stale fix | `|GPS fix time − capture UTC| > 120 s` **and** the device was moving (neighbours/track show movement), or the gap is more than 1 h | Downgrade to tier 3, radius += speed × gap; large gaps → reject |
| Coarse method | `GPSProcessingMethod` ∈ {NETWORK, CELLID, WLAN} or `HPositioningError > 100 m` | Keep, with radius = the stated error (or 500–3000 m if unknown) |
| Teleport | Implied speed from the previous/next resolved file of the same session is above 300 km/h on the ground, or above 1000 km/h | Flag both. The outlier (the one inconsistent with more neighbours) is downgraded; a flight is allowed only if a flight record or airport points exist |
| Altitude | Negative altitudes far below the terrain, or above 9000 m without a flight | Drop the altitude only |
| Precision loss | Coordinates rounded to 2 or fewer decimals (≈1 km) or exact integer degrees | Radius = rounding size; tier down |
| Privacy-blurred | Approximate location (iOS "precise location off" ≈ several km, some apps) | Radius = the documented blur |

Store `accuracy_m` for every valid candidate:
1. stated error;
2. otherwise DOP × 5 m;
3. otherwise method defaults: GPS 15 m, WiFi 50 m, cell 1500 m, manual pin 50 m,
   city centroid = city radius, country = country radius.

---

## Step 6: Propagation through duplicates and siblings

- **Duplicate clusters** (from the date skill): pool all camera-position candidates from all
  members. A messenger copy inherits the original's fix.
- **Siblings** (RAW+JPEG, Live Photo HEIC+MOV, brackets, bursts, panorama frames): share the
  best fix. For a Live Photo, prefer the MOV `ISO6709` if the HEIC lacks GPS.
- Edited exports (`-edited`, `IMG_E…`) inherit from the original.
- **Conflict inside a cluster**: if two members' fixes differ by more than the sum of their
  radii, one copy was manually geotagged or wrongly edited. Prefer the camera-original
  member, unless the other one is a `human` correction made later (catalog edit). Flag for
  review.

---

## Step 7: Time-matching against tracks

For each file (or cluster) without a valid tier-1 fix, query every track around its capture UTC.

```python
def match(t, track, max_int_s=600, max_ext_s=120, stay_m=150):
    p, n = track.prev(t), track.next(t)          # nearest points before/after
    if p and n and (n.t - p.t) <= max_int_s:
        f = (t - p.t) / (n.t - p.t)
        pos = geodesic_interp(p, n, f)
        move = haversine(p, n)
        radius = max(p.acc, n.acc) + min(move, move * min(f, 1 - f) * 2)
        return pos, radius, "interp"
    if p and n and haversine(p, n) <= stay_m:    # long gap but same place → stayed
        return midpoint(p, n), max(p.acc, n.acc) + stay_m, "stay"
    q = p if p and (t - p.t) <= max_ext_s else n if n and (n.t - t) <= max_ext_s else None
    if q:
        return q.pos, q.acc + q.speed_or(1.4) * abs(t - q.t), "extrap"
    v = track.visit_covering(t)                   # semantic visit / booking window
    if v:
        return v.pos, v.radius, "visit"
    return None
```

Rules:
- **Timezone correctness is critical.** Tracks are UTC (or ISO with an offset). If the file's
  `tz_status` is `assumed`, try candidate offsets (whole hours ±1–2 around the assumption)
  and pick the one that minimises the residual distance across the **session** (geosync).
  Feed the result back to the date resolver.
- **Camera clock offsets**: use the per-body or per-session correction from the date
  resolver. Alternatively, estimate it here by aligning a few camera files that can be
  located (e.g. a GPS-tagged phone shot of the same scene) against the track.
- **Multiple tracks overlapping** (watch + phone + Timeline): take the one with the best
  local accuracy; if they agree within radius, shrink the radius slightly (independent
  confirmation).
- **Device mismatch**: a track from the user's phone may not be where the camera was (a
  family member shooting elsewhere). Only accept it if the session's other evidence agrees.
- ExifTool can do the simple case directly:
  `exiftool -geotag "tracks/*.gpx" "-geotime<${SubSecDateTimeOriginal}+02:00" -api GeoMaxIntSecs=600 -api GeoMaxExtSecs=120 DIR`
  Here we use it only for dry-run validation; the main pipeline writes from the DB.

---

## Step 8: Session and event fill

- A **session** is a set of files from any devices whose resolved times have no gap over 2 h
  (tune this).
- Within a session, geotagged files from *other* devices (typically the phone) become a
  pseudo-track. Apply Step 7 with that track, then radius += 50 m for "same group, different
  person".
- If all valid points of a session lie within 1 km, assign untagged members the medoid, with
  radius = max distance to the medoid + 100 m (`method=session_cluster`).
- For multi-day trips with sparse points, use day-level visits: if a day has located files only
  within one city, untagged files of that day get the city centroid and city radius
  (`method=day_city`, low confidence).

### Counter-sequence siblings (same body)

The camera's file counter is shared by stills and videos on many bodies (see the date resolver,
Step 10.1). For a file between neighbours of the **same body** with known positions:
- **Both neighbours within 10 min and ≤ 500 m apart**: interpolate by time (or by index if
  the time is unknown). Radius = distance between the neighbours + 50 m.
- **Neighbours far apart in space**: take the neighbour nearer in time. Radius = distance to
  the other neighbour, or speed × gap.
- **Inverted case** (videos geotagged, photos not, or the reverse) works the same. Video GPS is
  the position at record start, or a per-frame track if telemetry exists; use the telemetry
  sample nearest to the photo's time.

### Reachability constraint (spatial interval propagation)

For any file with time `t` and unknown position, every located file of the same person/session
at time `t_k` constrains it to a disc of radius `r_k + v_max × |t − t_k|`. Use v_max ≈ 5 km/h
on foot, 120 km/h by road, 900 km/h for flights. The feasible area is the **intersection** of
these discs.
- If the intersection is small, assign its centre with that radius.
- If it is empty, there is a time error or a wrong fix somewhere: flag it.

### Folder place names

Place words in folder names are a location prior only if the folder passes a **coherence
test**: at least 80% of its geotagged files lie within the geocoded area. A folder whose
geotagged files are elsewhere must not pass its name on to untagged files.

---

## Step 9: Text geocoding (place names)

Sources, in order: IPTC/XMP `LocationCreated` text, IPTC City/State/Country, MakerNote place
text, album names, folder names, filename words.

- Geocode with an offline gazetteer (GeoNames `cities500` / `allCountries`) or Nominatim, with
  rate limiting and caching.
- **Disambiguate using constraints**: the session's or day's other points, the
  country/timezone from the date resolver, and the user's frequent locations. "Paris" next to
  Texas points is Paris, TX.
- Result = feature centroid; radius by feature class:
  - POI: 200 m
  - village: 2 km
  - city: sqrt(area) or 5–15 km
  - region: 50–150 km
  - country: country radius
- Never upgrade text to tier 1–2, even if the place is precise.
- Stop words in folder names (`Holiday`, `Trip`, `Photos`, `New folder`, month names) must not
  be geocoded; require a gazetteer hit with population above a threshold, or an exact POI.

---

## Step 10: Visual inference (last resort)

- **Landmark recognition** (e.g. Google Cloud Vision landmark detection, or similar APIs):
  gives a subject location. Convert it to a camera location with a radius of 0.3–2 km
  depending on the landmark's size and visibility distance.
- **Visual geolocation models** (GeoCLIP, StreetCLIP, PIGEON-style): country/region level.
  Use only to choose between text candidates, or with a ≥ 25 km radius.
- **OCR**: street signs, shop names, license-plate country codes, language detection, map or
  navigation screenshots (coordinates or addresses), dashcam overlays with burned-in
  coordinates (exact).
- Sun position, shadows and terrain matching: only for single high-value images, manually.
- Everything here is `kind=subject` or low confidence, and always goes to review.

Get user consent before sending images to any external API (privacy).

---

## Step 11: Scoring and selection

### Tier weights

| Tier | Weight | Sources |
|---|---|---|
| 1 | 10 | Fresh embedded GPS (fix within 120 s of capture, error < 100 m); iPhone ISO6709 with accuracy; telemetry streams; Takeout `geoDataExif`; preview-EXIF GPS |
| 2 | 7 | Track interpolation (Δ ≤ 120 s or stay); catalog/manual geotags (`human`); chat-shared location at time; Snapchat; own trackers |
| 3 | 4 | Interpolation with Δ up to 10 min; Timeline visits; session pseudo-track from other devices; Google Photos `geoData` without `geoDataExif`; stale or coarse embedded fixes; MakerNote place text |
| 4 | 2 | Text geocodes (city/region), bookings and transactions covering the time, day-city fill, check-ins |
| 5 | 1 | Visual/landmark inference |

### Algorithm (per cluster)

```python
def resolve_location(cluster):
    C = [c for c in cluster.candidates if c.valid and c.kind in ("camera", "track", "text", "visual")]
    if not C: return None, "none"
    # independent agreement: candidates whose circles overlap (distance <= r1 + r2)
    groups = overlap_cluster(C)
    for g in groups:
        g.score = sum(max(c.weight for c in g if c.origin == o) for o in {c.origin for c in g})
        g.best = min(g, key=lambda c: c.accuracy_m)             # most precise member
        g.radius = g.best.accuracy_m * (0.8 if len({c.origin for c in g}) >= 2 else 1.0)
    best = max(groups, key=lambda g: (g.score, -g.radius))
    runner = second_best(groups)
    conflict = runner and runner.score >= 0.6 * best.score and \
               haversine(best.best, runner.best) > best.radius + runner.radius
    conf = ("high"   if best.score >= 10 and best.radius <= 100 and not conflict else
            "medium" if best.radius <= 1000 and not conflict else
            "low")
    return best, conf, conflict
```

`origin` is the independent evidence source: camera GNSS, phone GNSS, Google server fusion,
human pin, gazetteer, vision model. Copies and duplicates of the same fix share one origin.

Prefer precision **within the winning group** (pick the smallest-radius member), but choose
the group by agreement and weight, not by precision alone. A precise but stale fix must not
beat three agreeing fresh sources.

---

## Step 12: Review report

Build an interactive map artifact (Leaflet/MapLibre from an allowed CDN) or a CSV:
- points coloured by confidence, with radius circles;
- a timeline slider; sessions as polylines;
- flags: teleports, repaired signs/datums/GCJ, conflicts within clusters, stale fixes,
  placeholder coordinates, text geocodes with several plausible matches;
- per item: thumbnail, chosen point and source, all candidates (tier, origin, radius, reason
  rejected);
- user overrides (drag the pin, choose a candidate, mark "unknown"). Store overrides as
  `origin=human`, tier 2, and re-run the session fill.

---

## Step 13: Apply

Dry run → action CSV → user approval → write with an argfile → undo script.

```bash
# images (EXIF + XMP), signed decimals with explicit refs
exiftool -P -overwrite_original_in_place \
  -GPSLatitude=48.377412 -GPSLatitudeRef=N \
  -GPSLongitude=17.587201 -GPSLongitudeRef=E \
  -GPSHPositioningError=35 -GPSMapDatum=WGS-84 \
  "-GPSProcessingMethod=restored:track_interp" \
  -XMP-exif:GPSLatitude=48.377412 -XMP-exif:GPSLongitude=17.587201 \
  file.jpg

# videos
exiftool -P -overwrite_original_in_place \
  "-Keys:GPSCoordinates=48.377412, 17.587201, 150" \
  "-UserData:GPSCoordinates=48.377412, 17.587201, 150" file.mov

# RAW / don't-touch files → XMP sidecar
exiftool -o %d%f.xmp -GPSLatitude=... -GPSLongitude=... file.CR3
```

- Write `GPSHPositioningError` so viewers and later runs know the uncertainty. Don't write
  coordinates with more decimals than the accuracy justifies; round to the radius.
- Do not write low-confidence or text-only locations into EXIF GPS by default. Put them in
  sidecars or the DB only, or into IPTC City/Country text fields.
- Optional reverse geocoding to fill `IPTC:City / Country` and
  `XMP-iptcExt:LocationCreated`, for search without a map. Do it in two layers:
  - **Country/region/municipality by point-in-polygon** against admin boundaries (Natural
    Earth, GADM or OSM, offline). "Nearest city with population ≥ N" can return a town across
    a border or the wrong municipality near borders, coasts and lakes.
  - **City/place label** from the nearest populated place (GeoNames) only if it lies inside the
    same admin polygon and within a sensible distance (e.g. 15 km). Otherwise use the
    municipality or region name.
  - Keep a user-editable **place override table** (raw lookup → preferred name, spelling,
    language), and apply it before writing or naming. Record which language the names are in.
- Files without a resolved location still get a valid destination: use an explicit
  `unknown-place` placeholder, never an empty path component. Do not route them into a
  separate manual pass.
- Never modify files flagged corrupt.
- Keep `location.sqlite`, `actions.csv` and an undo script next to the output.

---

## Database tables (SQLite, alongside the date resolver DB)

```sql
CREATE TABLE loc_candidates(
  file_id INT, cluster_id INT, source TEXT, origin TEXT,
  kind TEXT,                 -- camera|track|subject|text|visual|visit
  lat REAL, lon REAL, alt REAL, accuracy_m REAL,
  fix_utc TEXT, dt_s REAL,   -- capture vs fix time difference
  method TEXT,               -- embedded|interp|stay|extrap|visit|session|text|visual
  tier INT, weight REAL, valid INT, repair TEXT, reason TEXT
);
CREATE TABLE tracks(
  track_id TEXT, source TEXT, device TEXT, utc TEXT,
  lat REAL, lon REAL, alt REAL, accuracy_m REAL, speed REAL, kind TEXT
);
CREATE INDEX tracks_time ON tracks(utc);
CREATE TABLE loc_resolved(
  cluster_id INT PRIMARY KEY, lat REAL, lon REAL, alt REAL, accuracy_m REAL,
  confidence TEXT, method TEXT, winning_source TEXT, conflict INT, notes TEXT,
  tz_from_location TEXT      -- IANA zone from timezonefinder, fed back to the date resolver
);
```

---

## Pitfalls checklist

- [ ] Track logs are UTC, EXIF is local time: a wrong timezone shifts photos along the route by
      hours. Always geosync per session.
- [ ] A camera clock that is minutes off places shots hundreds of metres to kilometres away
      while driving.
- [ ] Lost N/S/E/W refs put Western-hemisphere photos in Asia or Southern-hemisphere photos in
      the north.
- [ ] GCJ-02 (China) and BD-09 (Baidu) offsets; datum `TOKYO` in old Japanese devices.
- [ ] Cold-start GPS writes the last known position, sometimes from a previous trip.
- [ ] Google Photos `geoData` can be an *estimate*; `geoDataExif` is what the camera wrote.
- [ ] Apps that tag the **upload** location (home) rather than the capture location.
- [ ] Drones: aircraft position vs home point; relative vs absolute altitude.
- [ ] Telephoto or landscape shots: the camera may be kilometres from the subject.
- [ ] Indoor, urban-canyon and underground fixes are poor; trust the stated error, not the
      decimals.
- [ ] A shared family album mixes devices of different people: one person's track ≠ another
      person's photos.
- [ ] Privacy: location reveals home and routines. Offer to strip GPS from copies for sharing.

---

## Reference tools

| Purpose | Tool |
|---|---|
| Read/write tags, extract telemetry, geotag, export GPX | ExifTool (`-ee3`, `-geotag`, `-geosync`, `-p gpx.fmt`) |
| Track conversion | GPSBabel, `gpxpy`, `fitparse`, `pynmea2` |
| Google Timeline conversion | Own parser for Records.json, Semantic History and on-device Timeline.json, or converters that output GPX |
| Telemetry | gopro-telemetry / gpmf-extract, DJI SRT parsers |
| Geodesy | `pyproj` / `geographiclib` (geodesic interpolation), `shapely` |
| Geocoding | GeoNames (offline), Nominatim (rate-limited), `reverse_geocoder` (offline) |
| Timezone from coordinates | `timezonefinder` |
| China offsets | eviltransform-style GCJ-02 / BD-09 converters |
| Manual review / visual | GpsPrune, GPXSee, digiKam Geolocation Editor, darktable map, GeoSetter (Win), HoudahGeo (Mac), Immich/PhotoPrism map |
| Visual geolocation | Cloud landmark detection APIs, GeoCLIP / StreetCLIP (local) |

## Deliverables to the user

1. A summary: located / newly located / repaired / unlocated counts, confidence distribution,
   tracks used.
2. A map review artifact covering flagged and low-confidence items.
3. The action CSV and undo script, for approval **before** writing.
4. After approval: updated files or sidecars, plus `location.sqlite`, plus the timezone
   feedback for the date resolver.
