---
name: architecture-and-stack
description: How meta-duplo is built today (v0.1 POC) - purpose, stack and dependency pins, every module with its public functions, library folder layout, CLI, server start-up, session token and CSRF, the single-job runner, stage idempotency and version constants, hard rules and a build order. Use it first when rebuilding the app from scratch or when deciding where new code belongs.
---

# meta-duplo: architecture and stack (as implemented)

The code is the source of truth. `docs/ARCHITECTURE.md` describes the **target** design (ExifTool pool,
16 stages, SSE, process pools, migrations). The code is the **v0.1 proof of concept** (ADR-013). Where the two
differ, this file follows the code and adds a short note marked **Doc vs code**.

## 1. Purpose and non-goals

**Purpose.** meta-duplo is a local, single-user tool for macOS (Linux works too) for large photo and video disks:

1. **Identical files** (step 1): find byte-identical copies, keep one and move the others to a holding folder.
2. **Near duplicates** (step 2): same pixels with different metadata, re-encoded or resized copies, edits, same
   video. Bursts ("similar shots") are listed but never removed automatically.
3. **Harmonize and rename** (step 3): recommend one capture time, place and device per *capture*, pooling
   evidence from every copy (removed copies too). The user accepts, edits or rejects each one. Files are renamed
   by a pattern and can get XMP sidecars and a new mtime.
4. **Re-check** (step 4): find new duplicate candidates using the harmonized values.
5. **Workbench**, shown on screen as **"Filomena"** (URL `/workbench`, the start page): one table for all of the
   above, with grouping, red/green differences, any EXIF column, and edit-rule columns.

Nothing on disk changes except through a **plan** (a dry-run list of actions) that the user reviews and applies.
Every applied plan can be undone.

**Non-goals (v0.1):** multi-user use, remote access, cloud; photo editing or a viewer; ExifTool and MakerNotes;
calibrated confidence; clock-offset learning, context and propagation stages; in-file metadata writes; SSE;
watching folders.

## 2. Stack and dependencies

| Layer | Implemented with |
|---|---|
| Language / packaging | Python `>=3.12`, `uv`, build backend `hatchling` |
| Web | FastAPI + Uvicorn (`uvicorn.run(app, host, port, log_level="warning")`) |
| Templates | Jinja2 via `fastapi.templating.Jinja2Templates` |
| Interactivity | htmx **2.0.4** and Alpine.js **3.14.8**, vendored in `meta_duplo/web/static/js/` (no CDN, no build step) |
| CSS | plain CSS: `tokens.css`, `base.css`, `layout.css`, `components.css` |
| DB | stdlib `sqlite3`, WAL, one file per library |
| Image metadata | Pillow `getexif()` + `get_ifd`, XMP regex, `IptcImagePlugin`; HEIC via `pillow-heif` |
| Video metadata | `ffprobe` subprocess (optional; skipped if not on PATH) |
| Hashing | `blake3` (bytes and decoded pixels), `imagehash` (dHash and pHash, 64-bit) |
| Demo data | `piexif` (EXIF writing), `ffmpeg` (demo videos, optional) |
| Geo | optional `reverse_geocoder` (offline GeoNames towns) |

`pyproject.toml`:

```toml
[project]
name = "meta-duplo"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastapi>=0.115", "uvicorn>=0.30", "jinja2>=3.1", "python-multipart>=0.0.9",
                "pillow>=10.4", "pillow-heif>=0.18", "imagehash>=4.3", "blake3>=0.4", "piexif>=1.1.3"]
[project.optional-dependencies]
geo = ["reverse-geocoder>=1.5"]          # uv sync --extra geo
[project.scripts]
meta-duplo = "meta_duplo.__main__:main"
[dependency-groups]
dev = ["pytest>=8", "httpx>=0.27"]
```

Versions in `uv.lock` at the time of writing: fastapi 0.156.8, uvicorn 0.54.0, jinja2 3.1.6, python-multipart
0.0.32, pillow 13.0.0, pillow-heif 1.8.1, imagehash 4.3.2, blake3 1.0.11, piexif 1.1.3, reverse-geocoder 1.5.1,
pytest 9.1.1, httpx 0.28.1.

Without the `geo` extra, `geo.place()` returns `{}`: places stay empty and the Sources page shows a hint.
Without `ffprobe`, videos get an empty `Meta()`.

**Doc vs code:** ARCHITECTURE §2 lists ExifTool/pyexiftool, Quick Look/sips, rawpy, timezonefinder, shapely,
MapLibre, Datasette. None of these are used. ExifTool is planned for milestone M1.

## 3. Package and module map

```
meta_duplo/
  __init__.py      docstring only
  __main__.py      CLI; add_source / remove_source (also used by the web app)
  db.py            SCHEMA, DEFAULT_SETTINGS, Library, tx(), now()
  metadata.py      extraction (Pillow, ffprobe), time validation, file-name dates, device names
  scan.py          job runner + stages: inventory, extract, hash, fingerprint (+ thumbnails), scan_all
  match.py         steps 1, 2, 4: pair scoring, anchor grouping, captures
  harmonize.py     step 3: recommendations, decisions, file-name pattern rendering, rename preview
  plans.py         the ONLY writer to source folders: cleanup and rename plans, apply, undo, discard
  geo.py           offline reverse geocoding
  demo.py          demo-disk generator
  web/
    app.py         create_app(): middleware, every route
    workbench.py   Workbench read model: catalog, rows, edit rules, grouping, filters, view state, undo stacks
    results.py     read model + server-side selection for the steps 1/2/4 results table
    rename_grid.py read model for the step-3 rename grid
    templates/     base, workbench, results, step3, compare, plan, plans, settings, sources + partials/
    static/css/    tokens.css base.css layout.css components.css
    static/js/     htmx.min.js alpine.min.js theme.js workbench.js
    static/img/    placeholder.svg
```

| Module | Responsibility | Main public names (signatures) |
|---|---|---|
| `__main__` | CLI and source management | `DEFAULT_LIBRARY: str`; `add_source(lib, root: str, writable=False, role="scan") -> int`; `remove_source(lib, sid: int) -> bool`; `main(argv: list[str] \| None = None) -> None` |
| `db` | schema, connections, settings | `SCHEMA: str`; `DEFAULT_SETTINGS: dict[str,str]`; `tx(con)` (context manager: `BEGIN IMMEDIATE` … `COMMIT`/`ROLLBACK`); `now() -> str` (UTC `%Y-%m-%dT%H:%M:%SZ`); `class Library(path)` with `.path`, `.db_path`, `.connect()`, `.setting(key) -> str`, `.set_setting(key, value) -> None` |
| `metadata` | read one file | `IMAGE_EXT`, `VIDEO_EXT`, `SIDECAR_EXT`, `TIME_FMT = "%Y-%m-%d %H:%M:%S"`, `RAW_VERSION = 3`; `kind_of(ext) -> str`; `@dataclass Meta`; `valid_time(dt)`; `parse_exif_time(value)`; `time_from_filename(name) -> (datetime \| None, "second" \| "day" \| "")`; `device_name(make, model) -> str`; `all_tags(exif, sub, gps) -> dict`; `xmp_iptc_dates(img) -> dict`; `read_image(path) -> Meta`; `read_video(path, home_tz) -> Meta`; `read(path, kind, home_tz) -> Meta` |
| `scan` | runner and scan stages | `HOLD_DIR = "_duplicates"`; `JUNK = {".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r"}`; `PARTIAL_BYTES = 1 MiB`; `class Cancelled(BaseException)`; `class Job(lib, job_id, cancel)` with `.update(stage, done=0, total=0, message="")`; `class Runner(lib)` with `.busy()`, `.cancel() -> bool`, `.start(name, fn) -> int \| None`, `.run_sync(name, fn)`; `inventory(lib, job)`; `abs_path(con, file_id) -> Path`; `extract(lib, job)`; `hash_stage(lib, job)`; `fingerprint(lib, job)`; `scan_all(lib, job)` |
| `match` | duplicate matching | `DUP_VERDICTS = {"identical","same_pixels","same_photo","edited","same_video"}`; `REENCODERS`; `COPY_WORDS`; `@dataclass F` (one file + meta + hashes); `FILE_SQL`; `load_files(con, include_removed=False) -> list[F]`; `keeper_key(f) -> tuple`; `pair_subject(a, b) -> str`; `compare(a, b) -> (score, verdict \| None, evidence)`; `step1_exact(lib, job=None) -> int`; `step2_near(lib, job=None) -> int`; `build_captures(lib)`; `step4_recheck(lib, job=None) -> int` |
| `harmonize` | step 3 | `FIELDS = ("capture_time","location_name","device_name")`; `FIELD_LABELS`; `recommend_time(members)`, `recommend_place(members, keeper)`, `recommend_device(members, keeper)` (each returns `{value, confidence, source, explanation[, extra]}`); `build_proposals(lib, job=None) -> int`; `effective_values(con) -> dict[capture_id, dict]`; `set_status(lib, capture_id, field, status, value=None)`; `accept_above(lib, min_conf, field=None) -> int`; `TOKEN`, `ILLEGAL`, `DEFAULT_PATTERN`, `TOKENS_HELP`, `NAME_TOKENS`; `set_tag_source(fn)`, `has_tags(pattern)`, `tag_values(lib)`; `render_name(pattern, values, original_name, ext) -> str \| None`; `rename_preview(lib, pattern=None, limit=None) -> list[dict]` |
| `plans` | plan engine | `SIDECAR_EXTS`; `class PlanError(Exception)`; `build_cleanup_plan(lib, group_ids, remove_file_ids=None) -> int`; `build_rename_plan(lib, write_xmp=True, set_mtime=False, only_ids=None) -> int`; `apply_plan(lib, plan_id) -> {"done","failed","status"}`; `undo_plan(lib, plan_id) -> {"undone","errors"}`; `discard_plan(lib, plan_id)` |
| `geo` | towns | `place(lat, lon) -> {"city","country","region"}` or `{}` (lru_cache 50 000); `available() -> bool` |
| `demo` | demo disk | `PLACES`; `make_demo(root, extra=0) -> Path` (**deletes `root` first**, then writes 21 files: 18 images, 3 videos if ffmpeg exists) |
| `web.app` | HTTP layer | `create_app(lib, token: str \| None = None) -> FastAPI`; helpers `soft_breaks`, `human_size`, `band(p)`; `PAGE = 100`, `GRID_PAGE = 200`, `WB_PAGE = 60` |
| `web.workbench` | Workbench model | `Field`, `CORE`, `EDIT_LABELS`, `EDIT_BASE`, `GROUPS`, `OPS`, `NO_VALUE_OPS`, `DATE_SOURCES`, `VIEW_VERSION = 5`, `DEFAULT_VIEW`, `DUP_FILTER`, `SINGLES`, `GROUP_COLUMN`, `SLIM_WIDTH = 40`, `TREE_WIDTH = 40`, `HISTORY_MAX = 100`, `CHECKS`, `KEY_FIELDS`, `SELECTION_PRESETS`; `snap(width)`, `load_view(lib)`, `load_view_from(v)`, `set_group(view, group)`, `save_view(lib, view)`, `history_push(lib, previous, act)`, `history_step(lib, direction)`, `field_for(key)`, `exif_category(tag)`, `column_rank(key)`, `insert_column(cols, key, width)`, `catalog(exif_keys)`, `name_fields(catalog, shown)`, `build_rows(lib) -> (rows, exif_keys)`, `apply_edits(rows, edits)`, `edit_target(key)`, `cell_value(r, key)`, `value(r, key)`, `table(lib, view) -> (groups, exif_keys, nrows)`, `explain_diff`, `conf_parts`, `conf_label`, `acronym`, `commit(lib, view, ids, only=None) -> int`, `preset_ids(lib, view, preset)`, `groups_for(lib, ids)`. At import it calls `harmonize.set_tag_source(...)` so `{tag:KEY}` patterns can render. |
| `web.results` | results table | `SORTS`, `KIND_FILTER`; `load(lib, step, *, status, kind, q, sort, desc, min_conf, offset, limit, group_ids) -> (groups, total)`; `folders(lib, step, kind)`; `toggle(lib, file_id, group_id, step, on)`; `set_keeper(lib, gid, fid)`; `smart_select(lib, step, mode, kind="", folder="") -> int` (modes `none`, `keeper`, `oldest`, `largest`, `folder`); `summary(lib, step)`; `selected_by_group(lib, step)`; `clear(lib, step)` |
| `web.rename_grid` | step-3 grid | `Column`, `COLUMNS`, `DEFAULT_COLUMNS`, `FILTERS`, `STATUS_LABELS`; `visible_columns(lib)`, `save_columns(lib, keys)`, `build_rows(lib)`, `query(lib, filt, q, sort, desc, offset, limit) -> (rows, total, counts)` |

**Dependency direction:** `db` ← `metadata` ← `scan` ← `match` ← `harmonize` ← `plans` ← `web.*`. `web.app`
imports `add_source`/`remove_source` from `__main__`. `harmonize` gets Workbench values only through the
registered tag source, so the core never imports `web`.

**Doc vs code:** ARCHITECTURE §14 plans `config.py`, `db/repo.py`, `jobs/`, `adapters/`, `stages/`, `resolvers/`,
`scoring/`, `plan/`. None of these exist. The flat modules above are the whole engine.

## 4. Library directory

```
~/MetaDuploLibraries/default/          # Library(path): created with parents on first use
  library.sqlite                       # + library.sqlite-wal, library.sqlite-shm (WAL)
  thumbs/<file_id>.jpg                 # 360 px max side, JPEG quality 82, images only, written by fingerprint
  undo/<plan_id>.jsonl                 # journal written by apply_plan before acting
```

- The path comes from `--library`, else the env var `META_DUPLO_LIBRARY`, else `~/MetaDuploLibraries/default`.
  `DEFAULT_LIBRARY` is read **once at import** of `__main__`.
- `Library.__init__` resolves the path, creates the folder plus `thumbs/` and `undo/`, runs `SCHEMA`
  (`CREATE … IF NOT EXISTS`), and inserts `DEFAULT_SETTINGS` with `INSERT OR IGNORE`.
- Journal lines: `{"id", "op", "src", "dst", "file_id"}`, one per action in plan order.
  **Doc vs code:** undo does **not** read the journal. It replays `actions` rows from the DB (`status='done'`,
  `ORDER BY id DESC`, using `payload.undo`). The journal is only a crash trail.
- On the source disks: the holding folder is `<source root>/_duplicates/group-<gid>/<original rel_path>`.
  Inventory skips it, together with dot-folders and `*.photoslibrary`.
- `/thumb/{fid}` serves `thumbs/{fid}.jpg` (`Cache-Control: max-age=86400`), else `static/img/placeholder.svg`.
  `/media/{fid}` serves the original file read-only.
- **Doc vs code:** there is no `config.toml`, `exports/` or `logs/`. Configuration is the `settings` table
  (see the data-model skill).

## 5. CLI (`meta_duplo/__main__.py`, entry point `meta-duplo`)

Global option: `--library PATH` (default `DEFAULT_LIBRARY`). A subcommand is required.

| Command | Arguments / options | Effect |
|---|---|---|
| `demo [path]` | `path` default `~/meta-duplo-demo`; `--extra N` (int, default 0): N more photos, each with a backup copy, and every third also as a WhatsApp copy | `make_demo(path, extra)` (**removes an existing folder first**), then `add_source(lib, root, writable=True)`. Prints `Demo disk created at … and added as a writable source to …` |
| `add-source path` | `--writable` (flag) | `add_source(lib, path, writable)`. Upsert on `root` (resolved absolute path): a second call updates `writable` and `role`. Prints `Added <path>` |
| `scan` | none | `Runner(lib).run_sync("scan", lambda job: scan_all(lib, job))`, then prints the last job row as a dict, e.g. `{'name': 'scan', 'status': 'done', 'message': 'Scan complete'}` |
| `serve` | `--host` (default env `META_DUPLO_HOST` or `127.0.0.1`), `--port` (int, default env `META_DUPLO_PORT` or `8765`), `--no-token` (development only) | Builds the token, calls `create_app(lib, token)`, prints `meta-duplo is running: http://HOST:PORT/?token=TOKEN`, runs Uvicorn |

`scan` runs inventory → extract → hash → fingerprint → step 1 → step 2. It does **not** build step-3 proposals.
The web "rescan" from the Workbench data-input actions also runs `harmonize.build_proposals`.

**Doc vs code:** ARCHITECTURE §14 names `init` and `run` commands. They do not exist. The source table has no
`volume_uuid`: a source is identified by its absolute `root` path.

## 6. Server start-up, session token, CSRF

```python
token = None if args.no_token else os.environ.get("META_DUPLO_TOKEN") or secrets.token_urlsafe(16)
app = create_app(lib, token)
print(f"meta-duplo is running: http://{host}:{port}/" + (f"?token={token}" if token else ""), flush=True)
if parent := os.environ.get("META_DUPLO_PARENT_PID"):
    stop_with_parent(int(parent))   # thread: every 2 s os.kill(pid, 0); once gone, SIGTERM to itself
uvicorn.run(app, host=host, port=port, log_level="warning")
```

`create_app` builds one `Runner(lib)`, mounts `/static`, registers Jinja filters (`size`, `band`, `pct`, `wbr`)
and the globals `token` and `field_labels`, and disables `/docs` and `/redoc`.

**Middleware `session_token`** (HTTP, runs on every request):

1. If `token is None` or the path starts with `/static`, pass through.
2. If the query has `token=<token>`: answer `200` with a tiny page,
   `<meta http-equiv="refresh" content="0;url=<path>">` plus a *Continue* link (query dropped, path escaped), and
   set the cookie `meta-duplo=<token>`, `httponly`, `samesite="strict"`. **Not a redirect:** the desktop app
   opens this link from its own origin, and a redirect would keep the chain cross-site, so WebKit would hold the
   new strict cookie back (ADR-019).
3. If the cookie does not match (`secrets.compare_digest`): `403` with "Open meta-duplo with the link printed in
   the terminal."
4. If the method is `POST` and the header `X-Meta-Duplo-Token` does not match: `403` "Missing request token."

**CSRF approach:** the same session token is sent as a header on every POST. `base.html` puts it on the body,
so htmx sends it on all requests:

```html
<body hx-boost="true" hx-headers='{"X-Meta-Duplo-Token": "{{ token }}"}' ...>
```

Plain `fetch()` calls in `workbench.js` read it back with
`JSON.parse(document.body.getAttribute("hx-headers"))["X-Meta-Duplo-Token"]`. There is no separate CSRF token,
and there is no CORS middleware.

**Localhost:** the default bind is `127.0.0.1`. **Doc vs code:** `--host`/`META_DUPLO_HOST` can bind any
address, and nothing enforces loopback. A rebuild should refuse non-loopback hosts or warn.

**Tauri prerequisites:** host, port and token come from the environment or flags. **Doc vs code:** asset URLs are
root-absolute (`/static/...`, `/thumb/...`), not relative.

### Route inventory (`web/app.py`)

| Method | Path | Purpose / response |
|---|---|---|
| GET | `/` | 307 → `/workbench` |
| GET | `/workbench`, `/wb/table`, `/wb/more?offset=` | page, table partial, next 60 groups |
| POST | `/wb/view` | every view change (form `action=`…); 204 + `HX-Trigger: wb-reload`, optional `X-Wb-History: <act>` |
| POST | `/wb/history` | `direction=undo\|redo` → JSON `{"ok", "label", "reload"}` or `{"ok": false}` |
| POST | `/wb/name-preview`; GET/POST `/wb/presets`; GET `/wb/values?key=` | naming dialog JSON; presets; distinct values (top 300) |
| GET | `/wb/preview/{fid}`, `/wb/mates/{fid}`, `/wb/ids?which=` | preview partial; copies JSON; selection-preset ids |
| POST | `/wb/commit`, `/wb/rename-plan`, `/wb/remove-plan` | save edit columns as decisions; build a rename or cleanup plan → `HX-Redirect /plans/{pid}` |
| POST | `/wb/sources`, `/wb/sources/pick` (macOS `osascript` folder dialog), `/wb/sources/demo`, `/wb/sources/{sid}/remove`, `/wb/sources/remove-all` | data input; each one rescans (scan_all + build_proposals) |
| GET/POST | `/sources`, `/sources/{sid}/writable`, `/sources/{sid}/role` | sources page; toggles |
| POST | `/scan`; GET `/job?watch=1`; POST `/job/cancel` | start a scan; poll the job partial (1 s); cancel |
| GET | `/step/1`, `/step/2?kind=dups\|bursts`, `/step/4`, `/step/{n}/rows` | results table and paging |
| POST | `/groups/{gid}/keeper/{fid}`, `/groups/{gid}/decide`, `/select`, `/select/smart`, `/plans/cleanup-selection`, `/plans/cleanup` | keeper, decisions (`not_duplicate`/`ignore`/`reopen`), selection, cleanup plans |
| GET | `/selection/bar?step=`, `/preview/{fid}?group=`, `/compare?group=` | partials |
| GET/POST | `/step/3`, `/rename/rows`, `/step/3/columns`, `/rename/exclude`, `/harmonize`, `/proposals/{cid}/{field}`, `/proposals/accept`, `/settings/pattern`, `/plans/rename` | step 3 |
| POST | `/recheck` | job "re-check": build_proposals + step4_recheck |
| GET/POST | `/plans`, `/plans/{pid}`, `/plans/{pid}/apply\|undo\|discard` | plans; outcome as `HX-Trigger` toast |
| GET/POST | `/settings` | `home_timezone`, `near_threshold`, `recheck_min_confidence` |
| GET | `/thumb/{fid}`, `/media/{fid}` | thumbnail, original file |

htmx conventions: responses use the `HX-Redirect`, `HX-Refresh: true` and `HX-Trigger` headers (JSON with
`toast`, `selection-changed`, `results-reload`, `grid-reload`, `wb-reload`, `wb-pickpath`). Every page uses
`render()`, which adds `counts` (nav badges) and `job` (the last job row).

### Desktop app (update)

The macOS app (ADR-019) has three parts. Build them after the web app works.

- **`packaging/server.py` + `packaging/meta-duplo-server.spec`** (PyInstaller, one-file, console, no UPX,
  `TARGET_ARCH` from the environment). `server.py` calls `multiprocessing.freeze_support()` then `main()`. The
  spec bundles `meta_duplo/web/templates` and `static`, all submodules of `uvicorn` and `meta_duplo`, all of
  `pillow_heif`, and the data files of `reverse_geocoder`. Run it with
  `uv sync --extra geo --group package` (the `package` group is `pyinstaller>=6.10`), then
  `uv run pyinstaller packaging/meta-duplo-server.spec --noconfirm --distpath packaging/dist --workpath packaging/build`.
  The result is `packaging/dist/meta-duplo-server` (about 78 MB). It takes the same commands as `meta-duplo`.
- **`desktop/`** (Tauri v2, `@tauri-apps/cli` 2.5.0, Rust crates `tauri` 2, `tauri-plugin-shell` 2, `libc` on
  unix):
  - `tauri.conf.json`: productName `Filomena`, identifier `app.metaduplo.filomena`, `frontendDist ../dist`,
    window `main` 1440×900 (min 960×600), `externalBin ["binaries/meta-duplo-server"]`, macOS minimum 11.0,
    `signingIdentity "-"` (ad-hoc).
  - `dist/index.html` is the "Starting the library…" page.
  - `src/main.rs` does the following:
    1. Picks a free port (bind `127.0.0.1:0`) and a 128-bit hex token from `/dev/urandom`.
    2. Spawns the sidecar `serve` with `META_DUPLO_HOST/PORT/TOKEN/PARENT_PID`, and prints its output prefixed
       `server:`.
    3. A thread polls the TCP port for up to 90 s, then calls `run_on_main_thread` → `window.navigate(http://127.0.0.1:<port>/?token=<token>)`.
    4. `RunEvent::Exit` sends SIGTERM to the sidecar (`libc::kill`). Do not use `CommandChild::kill`: its
       SIGKILL leaves the one-file binary's Python child running.
  - The sidecar file must be named `src-tauri/binaries/meta-duplo-server-<target triple>`.
  - Icons come from `npm run icons` (`app-icon.png`, 1024 px).
- **`.github/workflows/desktop.yml`** runs on `macos-15` (aarch64) and `macos-15-intel` (x86_64). It builds the
  server, then smoke-tests the binary (demo, scan, serve; a token link and a cookie request must reach a page
  containing "Filomena"). Then it runs `npm ci` and `npx tauri build --target <triple>`, and uploads
  `Filomena-<arch>.dmg` and `.app.zip`. On `v*` tags it attaches them to a release.

## 7. Job runner (`scan.Runner`)

- **One background job at a time** per `Runner` instance. The web app has one, created in `create_app`. A `threading.Lock`
  guards `start`. If a job is already running, `start` returns `None` and the request is silently ignored.
- `start(name, fn)` inserts a `jobs` row (`stage='starting'`, `status='running'`, `started_at=now()`), then runs
  `fn(job)` in a **daemon thread** `job-<id>`. Afterwards it sets `status` to `done`, `failed` (message =
  exception + `traceback.format_exc(limit=3)`) or `cancelled` (message `Cancelled`), and sets `finished_at`.
- `Job.update(stage, done, total, message)` writes progress. It is also the **cancel point**: if the cancel
  event is set, it raises `Cancelled(BaseException)`, so per-file `except Exception` handlers never swallow it.
- `Runner.cancel()` sets the event and returns `False` if nothing is running.
- `run_sync` (CLI and tests) is `start` plus `join`.
- Job names used: `scan`, `recommend` (`/harmonize`), `re-check`. Stage strings: `starting`, `inventory`,
  `extract`, `hash: partial`, `hash: full`, `hash`, `fingerprint`, `step 1: identical`, `step 2: comparing`,
  `step 2: near`, `step 3: recommendations`, `step 4: re-check`, `done`.
- UI progress: `partials/job.html` polls `GET /job?watch=1` every 1 s (`hx-trigger="every 1s"`). On `done` or
  `cancelled` the server answers `HX-Refresh: true`. On `failed` it shows the first 300 characters of the message.
- Each stage opens short connections and commits per file or per batch (`tx` for inventory, hash and grouping).
  A crash loses at most the file in progress.

**Doc vs code:** there is no durable queue, no worker processes, no chunking, no pause, no SSE and no `caffeinate`.
A job left `running` by a killed server stays `running` in the DB, and the page keeps polling it. A rebuild should
mark stale `running` rows `failed` at start-up.

## 8. Stage idempotency and versions

| Stage | Picks up | Idempotency rule |
|---|---|---|
| `inventory` | every source whose `root` is a directory | Upsert by `(source_id, rel_path)`. A size change or `abs(mtime diff) > 1e-3` resets derived rows. Unseen and not-removed files become `present = 0` |
| `extract` | `present=1`, kind image/video, no `meta` row **or** `raw_json` without the substring `"_v": <RAW_VERSION>` | `INSERT OR REPLACE INTO meta`; errors go to `files.error`, and the row is still written |
| `hash_stage` | all present, not removed, image/video, `size > 0` | Size → partial → full cascade. Reuses stored `partial`/`full`. Upserts every row (`partial`/`full` become NULL when no collision) and sets `cache_key = "inode:size:mtime"` |
| `fingerprint` | images with no `fingerprints` row | One decode: content hash, dHash, pHash, thumbnail. On a decode error it writes a NULL row (no retry) plus `files.error` |
| `step1_exact`, `step2_near`, `step4_recheck` | in-memory from `load_files` | `_save_groups` **replaces** all groups of the step and carries non-open statuses over by `signature`. It clears `selection` for that step |
| `build_captures`, `build_proposals` | everything | Full rebuild. Proposals keep `accepted`/`edited`/`rejected` by `(capture_id, field)` |

Version constants that exist:

| Constant | Where | Meaning |
|---|---|---|
| `RAW_VERSION = 3` | `metadata.py` | Stored in `meta.raw_json` as `"_v"`. A bump makes `extract` re-read every older row once. 3 = XMP/IPTC DateCreated; ADR-016 still says 2 |
| `VIEW_VERSION = 5` | `web/workbench.py` | Stored as `workbench_view.v`. `load_view_from` upgrades v<2 (drops default edit columns), v<3 (dup filter chip), v<5 (key date columns) |

**Doc vs code:** CLAUDE.md hard rule 6 and ARCHITECTURE §5 require a `STAGE_VERSION` per stage and a `runs`
table. **Neither exists.** Only `RAW_VERSION` triggers re-runs. Hashes, fingerprints and groups carry no
version. A rebuild should add `STAGE_VERSION` constants and store them per output row, or in `runs`.

## 9. Hard rules (CLAUDE.md) as rebuild constraints

| # | Rule | Status in the current code |
|---|---|---|
| 1 | Write to source volumes only via `plans.apply_plan`/`undo_plan` with a plan. Tests use temp copies | Holds for plans. Exceptions that touch the filesystem: `demo.make_demo` (its own folder, `rmtree` first) and `/wb/sources/demo`, which only creates `~/meta-duplo-demo` when it does not exist. Tests use `tmp_path` |
| 2 | Key ExifTool results by `SourceFile`; count, re-read empties, sample re-check | No ExifTool yet. Extraction is one file per call, so there is no ordering risk. Keep this rule for M1 |
| 3 | No fixed UTC offsets; `zoneinfo` per timestamp; keep naive local times apart from UTC | `read_video` converts QuickTime UTC with `ZoneInfo(home_timezone)` per timestamp. `meta.dto` is always naive local. Note: `harmonize._time_candidates` formats mtime with the machine's local zone |
| 4 | Every candidate has an `origin`; copies never add votes | Time candidates carry `origin` ∈ `camera_clock`/`container`/`filename`/`filesystem`. Agreement counts only across different origins |
| 5 | Per-file errors never abort a run; record them | Recorded in `files.error` (there is no `file_errors` table) |
| 6 | Stages idempotent and versioned; bump `STAGE_VERSION` | Idempotent yes; versioned only through `RAW_VERSION` (see §8) |
| 7 | No frontend build; templates in `web/templates`; CSS only with tokens from `tokens.css` (2·4·8·16·24·32·40·48·64); no inline styles; no new JS frameworks | Holds. Allowed inline-style exceptions (ADR-016/017): Workbench column widths, menu position, swipe clip. `snap()` keeps widths on the 8 px grid, 40–904 |
| 8 | Localhost only, session token, CSRF on POSTs | See §6 (the host is overridable) |
| 9 | NFC paths in DB; FS operations use the original bytes | Paths are NFC. **Original bytes are not stored**: FS operations join `root` with the NFC `rel_path`. This is fine on APFS (normalization-insensitive) and can fail on other file systems |
| 10 | Tauri prerequisites: env host/port/token, relative asset URLs, Safari-compatible | Env yes. Asset URLs are absolute (see §6) |

Also: domain logic follows `skills/*/SKILL.md`; ADRs live in `docs/DECISIONS.md`; when a domain rule is unclear,
leave a `TODO(spec):` instead of guessing.

## 10. Suggested build order

1. `db.py`: schema, `Library`, `tx`, `now`, settings (data-model skill).
2. `metadata.py`: `kind_of`, time validation, file-name dates, `device_name`, `read_image`, `read_video`.
3. `scan.py`: `Runner`/`Job`/`Cancelled`, then `inventory`, `extract`, `hash_stage`, `fingerprint`, `scan_all`.
4. `__main__.py`: `add-source`, `scan`. `demo.py` comes next so there is test data.
5. `match.py`: `compare`, `_candidate_pairs`, `_anchor_groups`, `_save_groups`, `step1_exact`, `step2_near`.
6. `plans.py`: cleanup plan, `apply_plan` with verify, group rollback, journal, `undo_plan`, `discard_plan`.
7. `harmonize.py`: captures, recommendations, proposals, `render_name`, `rename_preview`; then rename plans.
8. `match.step4_recheck`.
9. `web/app.py` skeleton: middleware, `render`, job partial, sources, plans, settings, thumb, media.
10. `web/results.py` + steps 1/2/4 pages; `web/rename_grid.py` + step 3.
11. `web/workbench.py` + `/workbench` and `/wb/*` (last: it depends on everything above).
12. CSS tokens and components; `theme.js`; `workbench.js`.

## Rebuild checklist

1. Create `pyproject.toml` with the dependencies, the `geo` extra, the `dev` group and the `meta-duplo` script
   above. `uv sync --extra geo` succeeds.
2. Implement `Library` so `Library(tmp)` creates `library.sqlite`, `thumbs/`, `undo/`, and
   `PRAGMA journal_mode` returns `wal`.
3. Implement `Runner`: `start` returns an id, a second `start` while busy returns `None`, and `cancel` produces
   status `cancelled` with `done` below `total`.
4. Implement the four scan stages with the pick-up rules in §8. Re-running `scan` on an unchanged disk does no
   extraction or fingerprint work.
5. Implement `RAW_VERSION` re-read: change the constant, and the next scan re-extracts every `meta` row.
6. Implement the CLI exactly as in §5, with the same defaults and env vars.
7. Implement `create_app` with the token middleware (§6) and `hx-headers` on `<body>`.
8. Implement matching, plans, harmonize, read models and the Workbench in the order of §10.
9. Vendor htmx 2.0.4 and Alpine 3.14.8 under `static/js/`. Use no CDN and no bundler.
10. Add `STAGE_VERSION` per stage and stale-job recovery at start-up (improvements over the POC; record them in an ADR).

## Verify

```bash
uv sync --extra geo
uv run pytest -q                                   # expect: 31 passed (test_workflow, test_results, test_workbench, test_desktop)
L=$(mktemp -d); D=$(mktemp -d)/demo
uv run meta-duplo --library "$L" demo "$D"         # "Demo disk created at … and added as a writable source to …"
uv run meta-duplo --library "$L" scan              # {'name': 'scan', 'status': 'done', 'message': 'Scan complete'}
ls "$L"                                            # library.sqlite  thumbs  undo  (-wal/-shm only while a connection is open)
ls "$L/thumbs" | wc -l                             # 18 (one per demo image)
META_DUPLO_TOKEN=t uv run meta-duplo --library "$L" serve --port 8799 &
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8799/workbench          # 403 (no cookie)
curl -s -c j -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:8799/?token=t'     # 200 (refresh page) + Set-Cookie meta-duplo=t
curl -s -b j -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8799/scan   # 403 (no X-Meta-Duplo-Token header)
curl -s -b j -H 'X-Meta-Duplo-Token: t' -X POST http://127.0.0.1:8799/scan | grep -c job   # job partial
```

With ffmpeg on PATH, the demo scan gives 21 files (18 images, 3 videos). It produces step-1 groups 5 × `identical`
and step-2 groups `same_photo` × 2, `same_pixels` × 1, `same_video` × 1, `similar_shot` × 1. Applying the step-1
cleanup plan moves 6 copies (`done == 6`). Undoing the rename plan and then the cleanup plan restores every byte
(`test_steps_3_and_4_and_plans`).
