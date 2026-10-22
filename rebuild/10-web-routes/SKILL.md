---
name: web-routes
description: Every HTTP route of meta_duplo/web/app.py (FastAPI + Jinja2 + htmx) — params, what each reads and writes, and its response (HTML partial, JSON, 204 with HX-Trigger / HX-Refresh / HX-Redirect / X-Wb-History) — plus the session-token/CSRF middleware, template filters and globals, the job stripe, the Workbench context `wb_ctx()` and the htmx conventions. Use it when rebuilding or changing the web layer; the table logic itself is in 09-workbench-model.
---

# Web routes (`meta_duplo/web/app.py`)

`create_app(lib: Library, token: str | None = None) -> FastAPI` builds everything inside one closure
(`FastAPI(title="meta-duplo", docs_url=None, redoc_url=None)`). Server-rendered pages; htmx swaps partials;
Alpine.js for local UI state; `static/js/workbench.js` for the Workbench. No build step.

Module constants: `HERE = Path(__file__).parent`, `PAGE = 100` (results groups), `GRID_PAGE = 200` (rename
grid rows), `WB_PAGE = 60` (Workbench groups per chunk). Imports used: `geo, harmonize, match, plans`,
`rename_grid, results, workbench as wb`, `add_source, remove_source` (from `meta_duplo.__main__`),
`device_name`, `Runner, scan_all`.

## 1. App plumbing

### Templates, filters, globals, static

```python
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters.update(size=human_size, band=band,
                             pct=lambda p: "–" if p is None else f"{p * 100:.0f} %", wbr=soft_breaks)
templates.env.globals.update(token=token or "", field_labels=harmonize.FIELD_LABELS)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
```

| filter | behaviour |
|---|---|
| `size` (`human_size`) | bytes → `"512 B"`, `"1.5 KB"`, … `"x.x TB"` (1024 steps; B without decimals) |
| `band` | `None`→unknown; ≥0.98 verified; ≥0.9 high; ≥0.7 medium; ≥0.4 low; else unknown |
| `pct` | `None` → `–`, else `"NN %"` |
| `wbr` (`soft_breaks`) | escapes, then inserts `<wbr>` between lower→Upper (CamelCase) and before `" ("`; returns `Markup` |

Globals: `token` (put in `<body hx-headers>`), `field_labels = {"capture_time": "Date & time",
"location_name": "Place", "device_name": "Device"}`.

`render(request, name, **ctx)` = `TemplateResponse` with defaults `counts=counts()` and `job=last_job()`.
`counts()` (nav badges): `files` (present, not removed, image/video), `removed`, `step1` (open step-1
groups), `step1_bytes` (sum `reclaimable`), `step2` (open step-2 non-`similar_shot`), `bursts` (open
step-2 `similar_shot`), `step3` (distinct captures with a `suggested` proposal), `captures`, `step4` (open
step-4 groups), `drafts` (draft plans). `last_job()` = newest `jobs` row as dict or None.
`start(name, fn)` → `runner.start` (one job at a time; ignored while busy).

### Session token + CSRF middleware

```python
@app.middleware("http")
async def session_token(request, call_next):
    if token is None or request.url.path.startswith("/static"): return await call_next(request)
    if request.query_params.get("token") == token:              # the link printed by `serve`
        resp = RedirectResponse(request.url.path)                # 307, other query params dropped
        resp.set_cookie("meta-duplo", token, httponly=True, samesite="strict"); return resp
    if not compare_digest(request.cookies.get("meta-duplo", ""), token):
        return HTMLResponse("Open meta-duplo with the link printed in the terminal.", 403)
    if request.method == "POST" and not compare_digest(request.headers.get("x-meta-duplo-token", ""), token):
        return HTMLResponse("Missing request token.", 403)
    return await call_next(request)
```
`serve` makes the token from `META_DUPLO_TOKEN` or `secrets.token_urlsafe(16)` (`--no-token` → None, checks
off) and prints `http://host:port/?token=…`. Every POST must send header `X-Meta-Duplo-Token`: htmx gets it
from `<body hx-headers='{"X-Meta-Duplo-Token": "{{ token }}"}'>`; `workbench.js` `fetch` calls read it from
that attribute (`token()`).

### htmx conventions

| convention | how |
|---|---|
| Boosted navigation | `<body hx-boost="true">` |
| Token on POSTs | `hx-headers` on body (above) |
| Toast | `HX-Trigger: {"toast": "msg"}` → htmx dispatches `toast` (detail `{value}`); `<body x-data="{toast:''}" @toast.window=…>` shows `.toast` for 3.5 s. JS uses `window.dispatchEvent(new CustomEvent("toast", {detail: {value}}))` |
| Workbench reload | `HX-Trigger: wb-reload` (or JSON with `"wb-reload": true`); `#wb-table` has `hx-get="/wb/table" hx-trigger="wb-reload from:body" hx-swap="innerHTML" hx-disinherit="*"`. `postView()` (fetch) re-triggers it with `htmx.trigger(document.body, "wb-reload")` when the header contains `wb-reload` |
| View history | `X-Wb-History: <action>` on `/wb/view` → page pushes a view step (`undoHistory.view(act)`), from `postView` and from `htmx:afterRequest` for htmx forms |
| Full reload | `HX-Refresh: true` (job finished, folders added/removed) |
| Redirect | `HX-Redirect: /plans/<id>` (plan builders), `/sources`, `/settings` |
| Other events | `selection-changed`, `results-reload`, `grid-reload`, `wb-pickpath` |
| Infinite scroll | Workbench `<tbody class="more" hx-get="/wb/more?offset=N" hx-trigger="intersect once" hx-swap="outerHTML">` |

### Job stripe (`partials/job.html`)

Rendered into `<div id="job">` in `base.html` only while `job.status == "running"` on page load.
- `running`: `<div class="job" hx-get="/job?watch=1" hx-trigger="every 1s" hx-swap="outerHTML" role="status">`
  with name (capitalized), stage, `<progress value=done max=total>` (indeterminate without total),
  `done / total`, and either *Cancelling…* (`cancelling`) or a **Cancel** button
  `hx-post="/job/cancel" hx-target="closest .job" hx-swap="outerHTML"`.
- `failed`: `.job.job--failed` alert with the first 300 chars of `message` (polling stops).
- `done` / `cancelled` / no job: renders nothing.

---

## 2. Sources and jobs

| method | path | params | does | response |
|---|---|---|---|---|
| GET | `/` | — | — | `RedirectResponse("/workbench")` (Workbench = start page) |
| GET | `/sources` | — | sources with file count `n` and `bytes` (present, not removed) | `sources.html` (`sources`, `geo_ok=geo.available()`) |
| POST | `/sources` | form `path` (req), `writable` | `Path(path).expanduser()` must be a dir → `add_source(lib, str(p), writable=bool)` | 204-like `Response` with `HX-Redirect: /sources`; not a dir → 422 HTML `<p class="error-banner" role="alert">Folder not found: …</p>` (target `#add-error`) |
| POST | `/sources/{sid}/writable` | — | `writable = 1 - writable` | `HX-Redirect: /sources` |
| POST | `/sources/{sid}/role` | — | toggles `role` `reference` ↔ `scan` | `HX-Redirect: /sources` |
| POST | `/scan` | — | `start("scan", scan_all)` | `partials/job.html` with `last_job()` (target `#job`) |
| GET | `/job` | query `watch:int=0` | — | `watch` and last job `done`/`cancelled` → empty `Response` with `HX-Refresh: true`; else `partials/job.html` (`job`, `cancelling=runner._cancel.is_set()`) |
| POST | `/job/cancel` | — | `runner.cancel()` (stops at next progress step; job becomes `cancelled`, work done kept) | `partials/job.html` with `cancelling=True` |

## 3. Steps 1–4 (guided screens)

| method | path | params | does / response |
|---|---|---|---|
| GET | `/step/1` | `status=open, q="", sort=size, dir=desc` | `results.html` via `results_page(step=1, kind="")` |
| GET | `/step/2` | `kind=dups` (`dups`/`bursts`, else dups), `status, q, sort=size, dir=desc` | `results.html` |
| GET | `/step/4` | `status=open, q, sort=confidence, dir=desc, min_conf=0.5` | `results.html` |
| GET | `/step/{step}/rows` | `offset, kind, status, q, sort=size, dir, min_conf=0.0` | `partials/results_rows.html` (next page) |
| POST | `/groups/{gid}/keeper/{fid}` | — | `results.set_keeper`; `partials/results_group.html` (`g`, `step`, `open=True`) + `HX-Trigger: selection-changed` |
| POST | `/select` | form `file_id, group_id, step, on?` | `results.toggle(...)`; 204 `HX-Trigger: selection-changed` |
| POST | `/select/smart` | form `step, mode, kind=""` | `mode` `folder:<path>` → folder mode; `results.smart_select`; 204 `HX-Trigger: {"selection-changed": true, "results-reload": true, "toast": "Selected N copies"|"Selection cleared"}` |
| GET | `/selection/bar` | `step` | `partials/selection_bar.html` (`sel=results.summary`) |
| POST | `/plans/cleanup-selection` | form `step` | nothing chosen → 204 toast *Nothing selected*; else `plans.build_cleanup_plan(lib, group_ids, remove_file_ids=…)`, `results.clear`, `HX-Redirect: /plans/<pid>` |
| GET | `/preview/{fid}` | query `group` | `partials/preview.html` (`g`, `m`) |
| POST | `/groups/{gid}/decide` | form `decision` | `not_duplicate`: insert a `pair` decision per member pair (`match.pair_subject`), status `not_duplicate`; `ignore`: `group` decision on signature, status `ignored`; `reopen`: delete that decision, status `open`; always clears `selection` of the group. Response: empty HTML + `HX-Trigger: selection-changed` |
| POST | `/plans/cleanup` | form `step`, `group_ids[]` | `build_cleanup_plan(lib, group_ids)`; `HX-Redirect` |
| GET | `/compare` | `group` | `compare.html` with `load_groups(step, status, after=group-1)` match |
| GET | `/step/3` | `filter=all, q, sort=folder, dir=asc` | `step3.html` (`pattern`, `default_pattern`, `tokens`, `grid_ctx`) |
| GET | `/rename/rows` | same + `offset` | `partials/grid_rows.html` |
| POST | `/step/3/columns` | form `cols[]` | `rename_grid.save_columns`; 204 `HX-Trigger: grid-reload` |
| POST | `/rename/exclude` | form `file_id, on?` | ticked → delete from `rename_exclude`, else insert; `partials/grid_oob.html` for that row (OOB swap) |
| POST | `/harmonize` | — | `start("recommend", build_proposals)`; job partial |
| POST | `/proposals/{cid}/{field}` | form `action` (`edit/accept/reject/reset`), `value?` | `set_status` edited(value)/accepted/rejected/suggested; OOB rows of that capture |
| POST | `/proposals/accept` | form `min_conf`, `field?` | `harmonize.accept_above`; 204 `{"grid-reload": true, "toast": "Accepted N recommendations"}` |
| POST | `/settings/pattern` | form `pattern` | setting `filename_pattern` (blank → `DEFAULT_PATTERN`); 204 `grid-reload` |
| POST | `/plans/rename` | form `write_xmp?, set_mtime?` | `build_rename_plan`; `HX-Redirect` |
| POST | `/recheck` | — | `start("re-check", build_proposals + match.step4_recheck)`; job partial |

`results_ctx` → `{groups, total, step, offset, next_offset, params, query}`; `grid_ctx` → `{rows, total,
fcounts, params, query, offset, next_offset, cols, all_cols, filters, status_labels}`.

## 4. Plans, settings, media

| method | path | does / response |
|---|---|---|
| GET | `/plans` | `plans.html`, plans newest first with parsed `summary` |
| GET | `/plans/{pid}` | `plan.html`: plan, first 1000 actions with `src`/`dst` shown relative to the longest matching source root, `roots`; unknown → redirect `/plans` |
| POST | `/plans/{pid}/{what}` | `apply` → `plans.apply_plan` (toast *Applied: N done, M failed*); `undo` → `undo_plan` (*Undone: N actions*); `discard` → `HX-Redirect: /plans` only; `PlanError` → its message. Response: `HX-Redirect: /plans/<pid>` + `HX-Trigger: {"toast": msg}` |
| GET | `/settings` | `settings.html` (`settings` dict of all rows, `library` path) |
| POST | `/settings` | form `home_timezone, near_threshold, recheck_min_confidence` (stripped) → settings; `HX-Redirect: /settings` + toast *Settings saved* |
| GET | `/thumb/{fid}` | `<library>/thumbs/<fid>.jpg` with `Cache-Control: max-age=86400`; else `static/img/placeholder.svg` |
| GET | `/media/{fid}` | original file (`source.root / rel_path`) as `FileResponse`; unknown id → 404 |

---

## 5. Workbench

### `wb_ctx(offset=0) -> dict` (shared by `/workbench`, `/wb/table`, `/wb/more`)

1. `view = wb.load_view(lib)`; `groups, exif_keys, nrows = wb.table(lib, view)`.
2. `tree = {"label", "name_col", "width": wb.TREE_WIDTH}` from `GROUP_COLUMN[view.group]`; `None` for `none`.
3. `rows` = all rows of all (filtered) groups — not just this page.
4. **Re-letter sources** in display order: per target, source id → `A`, `B`, … (`*` after 26), written into
   `r["src"][t]["letter"]`; they only order the header's "Values from: …" list now (no badges are shown).
5. (removed: `src_cells`; a source cell is shown on hover by `data-src` + the arrow, see 11 §6)
6. Pinned ordering: `pinned = set(view.pinned) | {"thumb"} if the first column is thumb`; columns =
   pinned ones (in stored order) followed by the rest.
7. For each column whose `field_for(key)` exists, a `cols` entry:

| key | value |
|---|---|
| `key`, `label`, `kind` | from the field |
| `empty` | `kind != "special"` and no row of the view has a value |
| `slim` | `view.slim.get(key, empty)` |
| `width` | `SLIM_WIDTH` if slim else `snap(width)` |
| `cat_start` | category differs from the previous column (not for the first); edit columns use their base field's category |
| `abbr` | `wb.acronym(label)` |
| `apply_target` | `wb.edit_target(key) or ""` |
| `target` | `key[5:]` for edit columns, else `field.editable` |
| `edit` | key starts `edit:` |
| `pinned` | key in `pinned` |
| `groupable` | `path→folder, date→day, device→device, place→place, dup_count→dups, dup_conf→dups`, else "" |
| `base` | edit columns: the column they edit (`EDIT_BASE`), for `= value` |
| `links` | edit columns only: distinct `r["src"][target]` dicts, sorted by letter (the header tooltip's "Values from: …") |

8. Returned keys:

| key | value |
|---|---|
| `view` | the view dict |
| `edit_labels` | `wb.EDIT_LABELS` |
| `groups` | `groups[offset:offset + WB_PAGE]` |
| `ngroups`, `nrows`, `offset`, `next_offset` (`offset + 60`) | paging |
| `cols` | above |
| `col_labels` | `[[key, label, kind]]` for non-special columns (Sort-by picker, `$store.wb.colLabels`) |
| `tree` | above |
| `table_width` | `40 (checkbox) + 40 (“+” column) + Σ col widths + tree width` (inline style) |
| `catalog` | `wb.catalog(exif_keys)` |
| `catalog_pairs` | `{group: [[key, label]]}` (column picker JS) |
| `group_names`, `ops`, `no_value_ops`, `date_sources` | `wb.GROUPS`, `wb.OPS`, `wb.NO_VALUE_OPS`, `wb.DATE_SOURCES` |
| `field_label` | `lambda k: field_for(k).label or k` |
| `conf_label`, `conf_parts`, `value` | the model functions |
| `checks_meta`, `key_fields` | `wb.CHECKS`, `wb.KEY_FIELDS` |
| `tokens`, `name_tokens` | `harmonize.TOKENS_HELP`, `harmonize.NAME_TOKENS` |
| `name_fields` | `wb.name_fields(catalog, shown keys)` |
| `name_labels` | `{token: label}` over `NAME_TOKENS + name_fields` |
| `name_options` | `[("Recommended values", label, tok) for NAME_TOKENS] + name_fields` |
| `pattern` | setting `filename_pattern` |
| `name_pattern` | filename rule pattern, else setting, else `harmonize.DEFAULT_PATTERN` |

### Workbench pages and partials

| method | path | params | response |
|---|---|---|---|
| GET | `/workbench` | — | `workbench.html` via `render`, ctx = `wb_ctx()` + `sources` (`id,label,root,writable,role,n`), `sources_off` (set), `nfiles` (files in sources not off) |
| GET | `/wb/table` | — | `partials/wb_table.html` (filters bar, Selection/Actions pickers, table; includes `wb_groups.html`) |
| GET | `/wb/more` | `offset:int=0` | `partials/wb_groups.html` for the next 60 groups (+ a new `more` tbody while `next_offset < ngroups`) |

### `POST /wb/view` — every view change

Form fields as strings. Steps: load view, `before = json.dumps(view)`, apply `action`, `wb.save_view`;
headers `{"HX-Trigger": "wb-reload"}` unless `reload = False`; if `json.dumps(view) != before`,
`wb.history_push(lib, before_view, act)` and add `X-Wb-History: <act>`. Response: **204** with those
headers. Unknown actions or failed guards save the view unchanged (still `wb-reload`, no history).
`cols` is `view["columns"]` (mutated in place unless replaced), `keys` its keys.

| action | fields | effect |
|---|---|---|
| `sort` | `key` (""=default order), `dir` (`asc` default) | `view.sort = [key, dir]` |
| `group` | `group` | `wb.set_group(view, group if in GROUPS else "dups")` (adds/drops the auto `Dup. ≥ 2` chip). Page reloads itself |
| `source_toggle` | `id` (digits) | XOR id into `sources_off` (sorted). Page reloads itself |
| `q` | `q` | `view.q = q` (page sends it debounced 400 ms) |
| `colors` | — | `view.colors = not colors` |
| `filter_add` | `key`, `op ∈ OPS`, `value`, `index?` | `{key, op, value}`; `index` digits and in range → replace that filter (edit from its chip); else append. Key not validated |
| `filter_remove` | `index` | pop if in range (`int()` of non-digits raises → 500) |
| `col_add` | `key` | if not shown and known: `wb.insert_column(cols, key, field.width)` (catalog position) |
| `cols_set` | `keys` (comma list), `on` | `on` → insert every unknown-and-valid key at its catalog position; else remove all of them |
| `cols_order` | — | `cols.sort(key=column_rank)` (“Order like this list”) |
| `col_remove` | `key` | drop that column (its edit rule stays) |
| `col_move` | `key` (shown, ≠ `before`), `before` | move before `before`; missing/"" → to the end |
| `col_pin` | `key` (shown), `on` | `pinned` minus key (and minus keys not shown); `on` → move the column right after the pinned block (thumb counts) and append to `pinned`; off → only unpinned |
| `col_slim` | `key` (shown), `on` | `view.slim[key] = (on == "1")` |
| `col_width` | `key="__group"`, `width` | `group_width = max(80, snap(width))`; `reload = False` |
| `col_width` | `key` (shown), `width` | `c[1] = snap(width)`; `reload = False` (browser already shows it) |
| `edit_set` | `target ∈ EDIT_LABELS`, `kind` (default `recommended`), `pattern`, `column`, `value`, `shift_column`, `sign`, `hh`, `mm`, `ss` | filename → `{"kind": "pattern", "pattern": pattern or DEFAULT_PATTERN}`; `column` → `{kind, column}`; `fixed` → `{kind, value.strip()}`; `shift` → `{kind, column: shift_column or "date", seconds: ±(hh*3600+mm*60+ss)}` (`sign == "-"` negates). Existing `overrides` are kept; `ids`/`from` are dropped. Then `add_edit_column(target)` |
| `cell_ref` | `fid` | XOR fid into `ref_ids` (sorted); key not needed |
| `cell_filter` | `fid`, `key` (known) | append `{key, op: "empty", value: ""}` if the cell is empty, else `{key, op: "equals", value: str(val)}` (value from rows with edits applied) |
| `cell_set` | `fid`, `key` (target date/place/device), `value` | rule = existing or `{"kind": "keep"}`; `overrides[str(fid)] = value.strip()`; `add_edit_column(t, after=key)` |
| `cell_apply` | `fid`, `key` (target date/place/device), optional `target` ("To column…": must be in `wb.apply_targets(key)`: `date` for date fields, else `place`/`device`), `scope` (`group`/`selection`/`all`), `ids` | value = `wb.cell_value(r, key)`; ids: `group` → ids of the group (from `wb.table` with the current view) holding fid, else `[fid]`; `selection` → `_ids(ids) ∪ {fid}` sorted; otherwise `None` (all). Rule `{"kind": "fixed", "value", "ids", "from": {"fid", "key"}, "overrides": existing}`; `add_edit_column(t, after=key)` |
| `edit_clear` | `target` | rule → `{"kind": "keep"}` (+ `"pattern": ""` for filename): overrides dropped, column kept (`= current`) |
| `edit_remove` | `target` | drop the rule and the `edit:<target>` column |
| `reset` | — | `DEFAULT_VIEW` deep copy, keeping `colors` |

`cell_*` require `fid` digits; `cell_filter/set/apply` require `field_for(key)` and a row with that id;
`cell_set`/`cell_apply` are ignored for non-editable keys and for filename.

`add_edit_column(t, after="")`: no-op if `edit:t` is shown; insert after `EDIT_BASE[t]` if shown (DateTimeOriginal (EXIF) ✎ after
DateTimeOriginal (EXIF), even when copied from another date column), else after `after` if shown, else at the end. `wb_ctx` also
moves every shown edit column right after its base when rendering (unless either is pinned), so older views are
corrected; width `304` for filename, `176` otherwise.

Senders: `postView()` in `workbench.js` (sort, group, source_toggle, q, colors via `wb-colors` event,
filter_remove, filter by value, cols_set, cols_order, col_add/col_remove from picker checkboxes, col_move on
drop, col_pin, col_slim on header double-click, col_width after a drag, edit_set from the naming dialog,
edit_clear, edit_remove, cell_* from the cell menu, reset via `wb-reset`); htmx forms `hx-post="/wb/view"
hx-swap="none"` for the filter dialog (`filter_add` + `index`) and the edit-rule dialog (`edit_set`).

### Other Workbench endpoints

| method | path | params | does | response |
|---|---|---|---|---|
| POST | `/wb/history` | form `direction` (`undo`/`redo`) | `wb.history_step` | JSON `{"ok": true, "label": "...", "reload": bool}` or `{"ok": false}`. Page toasts *Undone: label*, then `location.reload()` if `reload` else triggers `wb-reload` |
| GET | `/wb/values` | `key` | distinct values of a column over the current view (`wb.table` rows; None/"" → ""), most frequent first, then by value, top 300 | `{"values": [{"value": str, "count": n}]}`; unknown key → `{"values": []}` |
| GET | `/wb/ids` | `which=nonkeepers` | `wb.preset_ids(lib, view, which)` over the whole view | `{"ids": [...]}`; unknown preset → 400 *unknown selection preset* |
| GET | `/wb/preview/{fid}` | — | rows with edits applied; `mates` = rows of the same cluster | `partials/wb_preview.html` (`r`, `mates`, `conf_label`, `edit_labels`): image (`/media/` for images, `/thumb/` for videos, click opens the lightbox), pending edits, Dates/Location/Device/Author/File facts, duplicate set (✓ kept), all tags sorted; *File not found.* when missing. Requested by file rows: `hx-get hx-target="#wb-preview" hx-trigger="click, wb-show"` |
| GET | `/wb/mates/{fid}` | — | the file plus its cluster mates (or itself), sorted `(id != fid, not keeper, path)` | `{"items": [{"id", "name", "path", "kind", "conf": conf_label, "kept": bool}]}`; unknown → `{"items": []}` (lightbox *Duplicate set* mode) |
| POST | `/wb/name-preview` | form `pattern`, `fid`, `options` | rows with every edit **except filename** applied; row `fid` else the first; values as in `apply_edits` (`capture_time/location_name/device_name` from New columns, else effective, else current; `country`; `tags = v`); `alone(tok)` renders `tok + ".x"` and strips `.x` | `{"name": str, "values": {token: text}, "file": filename}`; `name` = `"(no date for this file: it keeps its name)"` when `render_name` gives None; `options` set → also `"options": {token: text}` for every `NAME_TOKENS` token and `{tag:<k>}` for every key of `v`. No rows → `{"name": "", "values": {}, "file": ""}` |
| GET | `/wb/presets` | — | — | `{"presets": [{"name": "Default", "pattern": DEFAULT_PATTERN, "builtin": true}, ...saved]}` |
| POST | `/wb/presets` | form `action` (`save`/`delete`/`rename`), `name`, `pattern`, `new` | `save`: name ≠ "" and ≠ "Default" → replace same name or append; `delete`: by name; `rename`: `new` non-empty, ≠ Default, unique → rename. Stored in setting `name_presets` (JSON list, builtin excluded) | same JSON as GET |
| POST | `/wb/commit` | form `ids` (csv), `target`, `all` | `chosen` = all view rows (`preset_ids "all"`) if `all`, else `_ids(ids)`; `wb.commit(..., only=target if target in EDIT_LABELS else None)` | 204 `HX-Trigger: {"wb-reload": true, "toast": …}`: filename → *Saved the filename pattern; review the rename plan to apply it*; n > 0 → *Saved N value(s) as decisions*; else *Nothing to save for these files* |
| POST | `/wb/rename-plan` | form `ids`, `write_xmp?` | empty → toast *Select files first*; else `wb.commit(all targets)` then `plans.build_rename_plan(lib, write_xmp=bool, only_ids=chosen)` | `HX-Redirect: /plans/<pid>` |
| POST | `/wb/remove-plan` | form `ids` | `gids = wb.groups_for(lib, ids)`; none → 204 toast *No duplicates in the selection*; else `plans.build_cleanup_plan(lib, gids, remove_file_ids=chosen)` (a group never loses its last copy) | `HX-Redirect: /plans/<pid>` |

`_ids(raw)` = set of ints from a comma list, ignoring non-digits.

### Data input (folders) from the Workbench

`_rescan()` = `start("scan", scan_all + harmonize.build_proposals)`. `_toast(msg)` = 204 with
`HX-Trigger: {"toast": msg}`.

| method | path | params | does | response |
|---|---|---|---|---|
| POST | `/wb/sources/pick` | form `writable?` | `_choose_folder()`: on macOS runs `osascript` (*System Events* `choose folder with prompt "Add a folder to meta-duplo"`, timeout 600 s) → POSIX path; Cancel (`-128`) → ""; not macOS / error → None | None → 204 `HX-Trigger: {"wb-pickpath": {"writable": bool}}` (page prompts for a typed path, then POSTs `/wb/sources`); "" → 204; path → `add_source` + `_rescan` + `HX-Refresh: true` |
| POST | `/wb/sources` | form `path`, `writable?` | expand `~`; empty or not a dir → toast *Folder not found: <p>*; else `add_source`, `_rescan` | `HX-Refresh: true` |
| POST | `/wb/sources/demo` | — | `~/meta-duplo-demo`: `make_demo` only if it does not exist; `add_source(writable=True)`; `_rescan` | `HX-Refresh: true` |
| POST | `/wb/sources/remove-all` | — | `remove_source` each (False when a plan's actions refer to its files); all kept → toast *These folders are used by plans. Keep them so the plans can still be undone.*; else prune `sources_off` to kept ids (direct `save_view`, no history), `_rescan` | `HX-Refresh: true` |
| POST | `/wb/sources/{sid}/remove` | — | `remove_source`; refused → toast *This folder is used by a plan. Keep it so the plan can still be undone.*; else drop sid from `sources_off`, `_rescan` | `HX-Refresh: true` |

Removing a source deletes library rows only (`selection, rename_exclude, captures, group_members, meta,
hashes, fingerprints, files, sources`); nothing on disk. The job stripe then polls `/job?watch=1` and
`HX-Refresh`es when the scan ends.

### Page-side flow (for orientation)

- Selection lives only in the page (`Alpine.store("wb").sel`); presets call `/wb/ids`; actions pass
  `ids` as CSV (`runAction`: commit / remove-plan / rename-plan with `write_xmp: "1"`; edit header *Apply* →
  `/wb/commit` with `target` and `ids`, or `all: "1"` without a selection).
- Undo list: sessionStorage key `meta-duplo-history` (max 100) holds selection steps (`{t:"sel", before,
  after}`) and view markers (`{t:"view", act}`); a view marker is undone through `/wb/history`.
- `group` and `source_toggle` reload the whole page after posting (`.then(() => location.reload())`)
  because the header pickers are outside `#wb-table`.

---

## Rebuild checklist

1. `create_app(lib, token)`: FastAPI without docs, `Runner(lib)`, Jinja templates with filters `size`,
   `band`, `pct`, `wbr` and globals `token`, `field_labels`; mount `/static`.
2. Middleware exactly as above; verify: no cookie → 403; `/?token=T` → 307 to `/` with cookie
   `meta-duplo=T` (httponly, samesite strict); POST without `X-Meta-Duplo-Token` → 403; `/static/*` open.
3. `render`, `counts`, `last_job`, `start`; `base.html` with `hx-boost`, `hx-headers`, toast listener and
   `#job` slot; `partials/job.html` with 1 s polling and Cancel.
4. Sources/jobs routes; `/job?watch=1` returns `HX-Refresh` after `done`/`cancelled`.
5. Steps 1–4, plans, settings, `/thumb`, `/media` routes per the tables.
6. `wb_ctx` with every key listed (re-lettering, pinned-first order, slim/empty, `cat_start`,
   `links`, `table_width`, `name_*`).
7. `/workbench`, `/wb/table`, `/wb/more` (60 groups per chunk, `intersect once`).
8. `/wb/view` with all 22 actions, `add_edit_column`, `reload=False` for widths, history push +
   `X-Wb-History` only when the view changed; 204.
9. `/wb/history`, `/wb/values`, `/wb/ids` (400 on unknown), `/wb/preview/{fid}`, `/wb/mates/{fid}`,
   `/wb/name-preview`, `/wb/presets` GET/POST, `/wb/commit`, `/wb/rename-plan`, `/wb/remove-plan`.
10. Folder routes `/wb/sources/pick`, `/wb/sources`, `/wb/sources/demo`, `/wb/sources/remove-all`,
    `/wb/sources/{sid}/remove` with `_rescan` and `HX-Refresh`.
11. Run `uv run pytest` and a manual smoke test (below).

## Verify

There are no HTTP-level tests; the routes are thin wrappers, verified through the model tests and a smoke run.

- `uv run pytest` — `tests/test_workbench.py` (12 tests: grouping, filters, edit rules + `commit` +
  `build_rename_plan(only_ids)` used by `/wb/rename-plan`, view migrations, presets used by `/wb/ids`,
  duplicates chip used by `group`, column order used by `col_add`/`cols_order`, cell scopes/overrides/reference
  used by `cell_apply`/`cell_set`/`cell_ref`, undo/redo used by `/wb/history`, tag patterns used by
  `/wb/name-preview`), `tests/test_workflow.py` (steps 1–4, plans apply + undo, read-only sources),
  `tests/test_results.py` (results paging, smart select, cleanup plan, job cancel) — all pass.
- Smoke with `fastapi.testclient.TestClient(create_app(lib, "T"))`:
  - `GET /workbench` → 403; `GET /?token=T` → 307 + cookie; then `GET /` → 307 to `/workbench` → 200.
  - `POST /wb/view action=sort key=size dir=desc` with header → 204, `HX-Trigger: wb-reload`,
    `X-Wb-History: sort`; repeating the same post → 204 without `X-Wb-History`.
  - `POST /wb/view action=col_width key=path width=203` → 204 with no `HX-Trigger`, stored width 200.
  - `POST /wb/history direction=undo` → `{"ok": true, "label": "column width", "reload": false}`.
  - `GET /wb/ids?which=bogus` → 400; `GET /wb/values?key=nope` → `{"values": []}`.
  - `POST /wb/remove-plan ids=` → 204 with toast *No duplicates in the selection*.
  - `POST /wb/presets action=save name=Default` → unchanged list (Default is protected).


## Use this for another column (update)

Header menu → *Use this for another column…* (`openUseFor(menu)`, disabled for edit and special columns) reuses the `applycol` dialog in `mode: "column"` (no scope) and posts `edit_set target=<date|place|device> kind=column column=<key>`; `edit_set` refuses a `column` whose `wb.apply_targets(column)` does not contain the target (toast "That column cannot feed this field"). `apply_edits` tidies a copied date with `_readable_date(raw)[:19]`.


## `cells` action (update)

`POST /wb/view action=cells`, `cells` = JSON `[[fid, key], …]`: `op=filter` adds one filter per column (`equals`, `in` with values joined by `wb.IN_SEP` " | ", or `empty`); `op=set` writes `value` into `overrides` for each editable cell (clears its `override_from`); `op=apply` with `scope=group` (each cell's own group), `selection` (`ids` ∪ the cell) or `all`, optional `target` (checked by `apply_targets`), writes the cell's value into `overrides` and `override_from = {fid, key}` for every target file (sources then read "Copied from <column> of <file>"); `op=ref` adds the rows to `ref_ids` (removes them when all already are). `GET /wb/ids?which=filled&key=` accepts several keys joined by newlines (filled = a value in each; empty = in none). New filter op `in` ("is one of").
