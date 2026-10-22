"""FastAPI + Jinja + htmx. Every page is server-rendered; htmx swaps partials."""
from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
from urllib.parse import urlencode
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from markupsafe import Markup, escape
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import geo, harmonize, match, plans
from . import rename_grid, results, workbench as wb
from ..__main__ import add_source, remove_source
from ..db import Library
from ..metadata import device_name
from ..scan import Runner, scan_all

HERE = Path(__file__).parent
PAGE = 100
GRID_PAGE = 200
WB_PAGE = 60  # groups per chunk in the workbench


def soft_breaks(text: str) -> Markup:
    """Line-break opportunities for header labels: between CamelCase words and before "(…)"."""
    return Markup(re.sub(r"(?<=[a-z])(?=[A-Z])|(?= \()", "<wbr>", str(escape(text))))


def human_size(n: int | None) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def band(p: float | None) -> str:
    if p is None:
        return "unknown"
    return "verified" if p >= 0.98 else "high" if p >= 0.9 else "medium" if p >= 0.7 else "low" if p >= 0.4 else "unknown"


def create_app(lib: Library, token: str | None = None) -> FastAPI:
    app = FastAPI(title="meta-duplo", docs_url=None, redoc_url=None)
    runner = Runner(lib)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters.update(size=human_size, band=band, pct=lambda p: "–" if p is None else f"{p * 100:.0f} %",
                                 wbr=soft_breaks)
    templates.env.globals.update(token=token or "", field_labels=harmonize.FIELD_LABELS)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.middleware("http")
    async def session_token(request: Request, call_next):
        """Localhost only, plus a random token: other local web pages cannot drive the app."""
        if token is None or request.url.path.startswith("/static"):
            return await call_next(request)
        if request.query_params.get("token") == token:
            resp = RedirectResponse(request.url.path)
            resp.set_cookie("meta-duplo", token, httponly=True, samesite="strict")
            return resp
        if not secrets.compare_digest(request.cookies.get("meta-duplo", ""), token):
            return HTMLResponse("Open meta-duplo with the link printed in the terminal.", status_code=403)
        if request.method == "POST" and not secrets.compare_digest(request.headers.get("x-meta-duplo-token", ""), token):
            return HTMLResponse("Missing request token.", status_code=403)
        return await call_next(request)

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        ctx.setdefault("counts", counts())
        ctx.setdefault("job", last_job())
        return templates.TemplateResponse(request, name, ctx)

    def counts() -> dict:
        with lib.connect() as con:
            q = lambda sql, *a: con.execute(sql, a).fetchone()[0]  # noqa: E731
            return {
                "files": q("SELECT COUNT(*) FROM files WHERE present = 1 AND removed = 0 AND kind IN ('image','video')"),
                "removed": q("SELECT COUNT(*) FROM files WHERE removed = 1"),
                "step1": q("SELECT COUNT(*) FROM groups WHERE step = 1 AND status = 'open'"),
                "step1_bytes": q("SELECT COALESCE(SUM(reclaimable), 0) FROM groups WHERE step = 1 AND status = 'open'"),
                "step2": q("SELECT COUNT(*) FROM groups WHERE step = 2 AND status = 'open' AND verdict != 'similar_shot'"),
                "bursts": q("SELECT COUNT(*) FROM groups WHERE step = 2 AND status = 'open' AND verdict = 'similar_shot'"),
                "step3": q("SELECT COUNT(DISTINCT capture_id) FROM proposals WHERE status = 'suggested'"),
                "captures": q("SELECT COUNT(DISTINCT capture_id) FROM proposals"),
                "step4": q("SELECT COUNT(*) FROM groups WHERE step = 4 AND status = 'open'"),
                "drafts": q("SELECT COUNT(*) FROM plans WHERE status = 'draft'"),
            }

    def last_job() -> dict | None:
        with lib.connect() as con:
            r = con.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(r) if r else None

    def start(name: str, fn) -> None:
        runner.start(name, fn)

    # ───────────────────────── sources + jobs ─────────────────────────

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return RedirectResponse("/workbench")  # the Workbench is the start screen; the steps stay in the nav

    @app.get("/sources", response_class=HTMLResponse)
    def sources(request: Request):
        with lib.connect() as con:
            rows = con.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM files f WHERE f.source_id = s.id AND f.present = 1 AND f.removed = 0) n, "
                "(SELECT COALESCE(SUM(size), 0) FROM files f WHERE f.source_id = s.id AND f.present = 1 AND f.removed = 0) bytes "
                "FROM sources s ORDER BY s.id").fetchall()
        return render(request, "sources.html", sources=rows, geo_ok=geo.available())

    @app.post("/sources")
    def add(path: str = Form(...), writable: str | None = Form(None)):
        p = Path(path).expanduser()
        if not p.is_dir():
            return HTMLResponse(f'<p class="error-banner" role="alert">Folder not found: {p}</p>', status_code=422)
        add_source(lib, str(p), writable=bool(writable))
        return Response(headers={"HX-Redirect": "/sources"})

    @app.post("/sources/{sid}/writable")
    def toggle_writable(sid: int):
        with lib.connect() as con:
            con.execute("UPDATE sources SET writable = 1 - writable WHERE id = ?", (sid,))
        return Response(headers={"HX-Redirect": "/sources"})

    @app.post("/sources/{sid}/role")
    def toggle_role(sid: int):
        with lib.connect() as con:
            con.execute("UPDATE sources SET role = CASE role WHEN 'reference' THEN 'scan' ELSE 'reference' END WHERE id = ?", (sid,))
        return Response(headers={"HX-Redirect": "/sources"})

    @app.post("/scan", response_class=HTMLResponse)
    def scan(request: Request):
        start("scan", lambda job: scan_all(lib, job))
        return templates.TemplateResponse(request, "partials/job.html", {"job": last_job()})

    @app.get("/job", response_class=HTMLResponse)
    def job(request: Request, watch: int = 0):
        j = last_job()
        if watch and j and j["status"] in ("done", "cancelled"):
            return Response(headers={"HX-Refresh": "true"})  # reload the page to show the new (or partial) results
        return templates.TemplateResponse(request, "partials/job.html", {"job": j, "cancelling": runner._cancel.is_set()})

    @app.post("/job/cancel", response_class=HTMLResponse)
    def job_cancel(request: Request):
        runner.cancel()
        return templates.TemplateResponse(request, "partials/job.html", {"job": last_job(), "cancelling": True})

    # ───────────────────────── steps 1 and 2: duplicate groups ─────────────────────────

    def load_groups(step: int, status: str, verdict: str | None = None, exclude_verdict: str | None = None,
                    after: int = 0) -> list[dict]:
        with lib.connect() as con:
            sql = "SELECT * FROM groups WHERE step = ? AND status = ? AND id > ?"
            args: list = [step, status, after]
            if verdict:
                sql += " AND verdict = ?"
                args.append(verdict)
            if exclude_verdict:
                sql += " AND verdict != ?"
                args.append(exclude_verdict)
            groups = [dict(g) for g in con.execute(sql + " ORDER BY id LIMIT ?", (*args, PAGE))]
            for g in groups:
                g["members"] = [dict(m) for m in con.execute(
                    "SELECT f.id, f.name, f.rel_path, f.size, f.kind, f.removed, s.root, s.writable, gm.role, gm.score, gm.evidence, "
                    "m.dto, m.dto_source, m.make, m.model, m.software, m.width, m.height, m.duration, m.gps_lat, m.gps_lon "
                    "FROM group_members gm JOIN files f ON f.id = gm.file_id JOIN sources s ON s.id = f.source_id "
                    "LEFT JOIN meta m ON m.file_id = f.id WHERE gm.group_id = ? "
                    "ORDER BY gm.role = 'keeper' DESC, f.id", (g["id"],))]
                for m in g["members"]:
                    m["evidence"] = json.loads(m["evidence"] or "{}")
                    m["device"] = device_name(m["make"], m["model"])
                    m["place"] = geo.place(m["gps_lat"], m["gps_lon"]).get("city", "") if m["gps_lat"] is not None else ""
                g["differs"] = {
                    k: len({(m[k] or "") for m in g["members"]}) > 1 for k in ("dto", "device", "place", "size", "name")
                }
        return groups

    # ───────────────────────── steps 1, 2 and 4: results table ─────────────────────────

    def results_ctx(step: int, kind: str, status: str, q: str, sort: str, dir: str, min_conf: float,
                    offset: int = 0) -> dict:
        groups, total = results.load(lib, step, status=status, kind=kind, q=q.strip(), sort=sort,
                                     desc=dir != "asc", min_conf=min_conf, offset=offset, limit=PAGE)
        params = {"status": status, "kind": kind, "q": q, "sort": sort, "dir": dir, "min_conf": min_conf}
        return {"groups": groups, "total": total, "step": step, "offset": offset,
                "next_offset": offset + len(groups), "params": params, "query": urlencode(params)}

    def results_page(request: Request, step: int, kind: str, status: str, q: str, sort: str, dir: str,
                     min_conf: float = 0.0):
        ctx = results_ctx(step, kind, status, q, sort, dir, min_conf)
        return render(request, "results.html", folders=results.folders(lib, step, kind), **ctx)

    @app.get("/step/1", response_class=HTMLResponse)
    def step1(request: Request, status: str = "open", q: str = "", sort: str = "size", dir: str = "desc"):
        return results_page(request, 1, "", status, q, sort, dir)

    @app.get("/step/2", response_class=HTMLResponse)
    def step2(request: Request, kind: str = "dups", status: str = "open", q: str = "", sort: str = "size",
              dir: str = "desc"):
        return results_page(request, 2, kind if kind in ("dups", "bursts") else "dups", status, q, sort, dir)

    @app.get("/step/4", response_class=HTMLResponse)
    def step4(request: Request, status: str = "open", q: str = "", sort: str = "confidence", dir: str = "desc",
              min_conf: float = 0.5):
        return results_page(request, 4, "", status, q, sort, dir, min_conf)

    @app.get("/step/{step}/rows", response_class=HTMLResponse)
    def result_rows(request: Request, step: int, offset: int = 0, kind: str = "", status: str = "open", q: str = "",
                    sort: str = "size", dir: str = "desc", min_conf: float = 0.0):
        ctx = results_ctx(step, kind, status, q, sort, dir, min_conf, offset)
        return templates.TemplateResponse(request, "partials/results_rows.html", ctx)

    @app.post("/groups/{gid}/keeper/{fid}", response_class=HTMLResponse)
    def set_keeper(request: Request, gid: int, fid: int):
        results.set_keeper(lib, gid, fid)
        step = _step_of(gid)[0]
        groups, _ = results.load(lib, step, group_ids=[gid])
        resp = templates.TemplateResponse(request, "partials/results_group.html",
                                          {"g": groups[0] if groups else None, "step": step, "open": True})
        resp.headers["HX-Trigger"] = "selection-changed"
        return resp

    @app.post("/select")
    def select(file_id: int = Form(...), group_id: int = Form(...), step: int = Form(...), on: str | None = Form(None)):
        results.toggle(lib, file_id, group_id, step, bool(on))
        return Response(status_code=204, headers={"HX-Trigger": "selection-changed"})

    @app.post("/select/smart")
    def smart(step: int = Form(...), mode: str = Form(...), kind: str = Form("")):
        folder = ""
        if mode.startswith("folder:"):
            mode, folder = "folder", mode[len("folder:"):]
        n = results.smart_select(lib, step, mode, kind, folder)
        msg = "Selection cleared" if mode == "none" else f"Selected {n} copies"
        return Response(status_code=204, headers={"HX-Trigger": json.dumps(
            {"selection-changed": True, "results-reload": True, "toast": msg})})

    @app.get("/selection/bar", response_class=HTMLResponse)
    def selection_bar(request: Request, step: int):
        return templates.TemplateResponse(request, "partials/selection_bar.html",
                                          {"sel": results.summary(lib, step), "step": step})

    @app.post("/plans/cleanup-selection")
    def cleanup_selection(step: int = Form(...)):
        chosen = results.selected_by_group(lib, step)
        if not chosen:
            return Response(status_code=204, headers={"HX-Trigger": json.dumps({"toast": "Nothing selected"})})
        pid = plans.build_cleanup_plan(lib, list(chosen), remove_file_ids={f for fs in chosen.values() for f in fs})
        results.clear(lib, step)  # the plan holds the selection now
        return Response(headers={"HX-Redirect": f"/plans/{pid}"})

    @app.get("/preview/{fid}", response_class=HTMLResponse)
    def preview(request: Request, fid: int, group: int):
        groups, _ = results.load(lib, _step_of(group)[0], group_ids=[group])
        g = groups[0] if groups else None
        m = next((x for x in g["members"] if x["id"] == fid), None) if g else None
        return templates.TemplateResponse(request, "partials/preview.html", {"g": g, "m": m})

    @app.post("/groups/{gid}/decide")
    def decide(gid: int, decision: str = Form(...)):
        with lib.connect() as con:
            g = con.execute("SELECT * FROM groups WHERE id = ?", (gid,)).fetchone()
            files = {f.id: f for f in match.load_files(con)}
            ids = [r["file_id"] for r in con.execute("SELECT file_id FROM group_members WHERE group_id = ?", (gid,))]
            if decision == "not_duplicate":  # every pair in the group is a labelled non-duplicate
                for i, a in enumerate(ids):
                    for b in ids[i + 1:]:
                        if a in files and b in files:
                            con.execute("INSERT INTO decisions(scope, subject, decision, decided_at) VALUES ('pair', ?, ?, datetime('now'))",
                                        (match.pair_subject(files[a], files[b]), "not_duplicate"))
                con.execute("UPDATE groups SET status = 'not_duplicate' WHERE id = ?", (gid,))
            elif decision == "ignore":
                con.execute("INSERT INTO decisions(scope, subject, decision, decided_at) VALUES ('group', ?, 'ignore', datetime('now'))",
                            (g["signature"],))
                con.execute("UPDATE groups SET status = 'ignored' WHERE id = ?", (gid,))
            elif decision == "reopen":
                con.execute("DELETE FROM decisions WHERE scope = 'group' AND subject = ?", (g["signature"],))
                con.execute("UPDATE groups SET status = 'open' WHERE id = ?", (gid,))
            con.execute("DELETE FROM selection WHERE group_id = ?", (gid,))
        # the group leaves the current list: swap it out and refresh the selection bar
        return HTMLResponse("", headers={"HX-Trigger": "selection-changed"})

    @app.post("/plans/cleanup")
    def cleanup(step: int = Form(...), group_ids: list[int] = Form(default=[])):
        pid = plans.build_cleanup_plan(lib, group_ids)
        return Response(headers={"HX-Redirect": f"/plans/{pid}"})

    @app.get("/compare", response_class=HTMLResponse)
    def compare(request: Request, group: int):
        g = next((x for x in load_groups(*_step_of(group), after=group - 1) if x["id"] == group), None)
        return render(request, "compare.html", g=g)

    def _step_of(gid: int) -> tuple[int, str]:
        with lib.connect() as con:
            r = con.execute("SELECT step, status FROM groups WHERE id = ?", (gid,)).fetchone()
        return (r["step"], r["status"]) if r else (1, "open")

    # ───────────────────────── step 3: harmonize + rename ─────────────────────────

    def grid_ctx(filt: str, q: str, sort: str, dir: str, offset: int = 0) -> dict:
        rows, total, fcounts = rename_grid.query(lib, filt, q.strip(), sort, dir == "desc", offset, GRID_PAGE)
        params = {"filter": filt, "q": q, "sort": sort, "dir": dir}
        return {"rows": rows, "total": total, "fcounts": fcounts, "params": params, "query": urlencode(params),
                "offset": offset, "next_offset": offset + len(rows), "cols": rename_grid.visible_columns(lib),
                "all_cols": rename_grid.COLUMNS, "filters": rename_grid.FILTERS,
                "status_labels": rename_grid.STATUS_LABELS}

    @app.get("/step/3", response_class=HTMLResponse)
    def step3(request: Request, filter: str = "all", q: str = "", sort: str = "folder", dir: str = "asc"):
        return render(request, "step3.html", pattern=lib.setting("filename_pattern"),
                      default_pattern=harmonize.DEFAULT_PATTERN, tokens=harmonize.TOKENS_HELP,
                      **grid_ctx(filter, q, sort, dir))

    @app.get("/rename/rows", response_class=HTMLResponse)
    def step3_rows(request: Request, filter: str = "all", q: str = "", sort: str = "folder", dir: str = "asc",
                   offset: int = 0):
        return templates.TemplateResponse(request, "partials/grid_rows.html", grid_ctx(filter, q, sort, dir, offset))

    @app.post("/step/3/columns")
    def step3_columns(cols: list[str] = Form(default=[])):
        rename_grid.save_columns(lib, cols)
        return Response(status_code=204, headers={"HX-Trigger": "grid-reload"})

    def grid_oob(request: Request, keep) -> HTMLResponse:
        """Re-render only the affected rows (out-of-band), so the grid keeps its scroll position."""
        rows = [r for r in rename_grid.build_rows(lib) if keep(r)]
        return templates.TemplateResponse(request, "partials/grid_oob.html", {
            "rows": rows, "cols": rename_grid.visible_columns(lib), "status_labels": rename_grid.STATUS_LABELS})

    @app.post("/rename/exclude", response_class=HTMLResponse)
    def rename_exclude(request: Request, file_id: int = Form(...), on: str | None = Form(None)):
        with lib.connect() as con:
            if on:  # checkbox ticked = include in renaming
                con.execute("DELETE FROM rename_exclude WHERE file_id = ?", (file_id,))
            else:
                con.execute("INSERT OR IGNORE INTO rename_exclude(file_id) VALUES (?)", (file_id,))
        return grid_oob(request, lambda r: r["id"] == file_id)

    @app.post("/harmonize", response_class=HTMLResponse)
    def run_harmonize(request: Request):
        start("recommend", lambda job: harmonize.build_proposals(lib, job))
        return templates.TemplateResponse(request, "partials/job.html", {"job": last_job()})

    @app.post("/proposals/{cid}/{field}", response_class=HTMLResponse)
    def proposal(request: Request, cid: int, field: str, action: str = Form(...), value: str | None = Form(None)):
        if action == "edit":
            harmonize.set_status(lib, cid, field, "edited", (value or "").strip())
        else:
            harmonize.set_status(lib, cid, field, {"accept": "accepted", "reject": "rejected", "reset": "suggested"}[action])
        return grid_oob(request, lambda r: r["capture"] == cid)  # every copy of the photo changes together

    @app.post("/proposals/accept")
    def accept_bulk(min_conf: float = Form(...), field: str | None = Form(None)):
        n = harmonize.accept_above(lib, min_conf, field or None)
        return Response(status_code=204, headers={"HX-Trigger": json.dumps(
            {"grid-reload": True, "toast": f"Accepted {n} recommendations"})})

    @app.post("/settings/pattern")
    def pattern(pattern: str = Form(...)):
        lib.set_setting("filename_pattern", pattern.strip() or harmonize.DEFAULT_PATTERN)
        return Response(status_code=204, headers={"HX-Trigger": "grid-reload"})

    @app.post("/plans/rename")
    def rename(write_xmp: str | None = Form(None), set_mtime: str | None = Form(None)):
        pid = plans.build_rename_plan(lib, write_xmp=bool(write_xmp), set_mtime=bool(set_mtime))
        return Response(headers={"HX-Redirect": f"/plans/{pid}"})

    # ───────────────────────── step 4: re-check ─────────────────────────

    @app.post("/recheck", response_class=HTMLResponse)
    def recheck(request: Request):
        def fn(job):
            harmonize.build_proposals(lib, job)  # keep captures in sync with steps 1-2
            match.step4_recheck(lib, job)

        start("re-check", fn)
        return templates.TemplateResponse(request, "partials/job.html", {"job": last_job()})

    # ───────────────────────── workbench (experimental): one table for every task ─────────────────────────

    def wb_ctx(offset: int = 0) -> dict:
        view = wb.load_view(lib)
        groups, exif_keys, nrows = wb.table(lib, view)
        if wb.show_new_dates(view, wb.catalog(exif_keys)):  # every date column is on by default (empty ones slim)
            wb.save_view(lib, view)
            groups, exif_keys, nrows = wb.table(lib, view)
        tree = None
        if view["group"] in wb.GROUP_COLUMN:
            label, name_col = wb.GROUP_COLUMN[view["group"]]
            tree = {"label": label, "name_col": name_col, "width": wb.TREE_WIDTH}
        # a column nobody has a value in is never dropped, only collapsed to a slim one (double-click toggles)
        rows = [r for g in groups for r in g.rows]
        letters: dict[str, dict[str, str]] = {}  # source links lettered A, B, C… in the order they show
        for r in rows:
            for t, src in r.get("src", {}).items():
                seen = letters.setdefault(t, {})
                src["letter"] = seen.setdefault(src["id"], chr(65 + len(seen)) if len(seen) < 26 else "*")
        # cells that values were copied from (Apply this value to …) get the same badge, pointing at the edit column
        src_cells: dict[str, dict] = {}
        for r in rows:
            for t, src in r.get("src", {}).items():
                if src.get("rid"):
                    sc = src_cells.setdefault(f"{src['rid']}:{src['key']}", {"letter": src["letter"], "target": t, "n": 0})
                    sc["n"] += 1
        cols, prev_cat = [], None
        # pinned columns stay at the left (the thumbnail too while it is first); they are kept as one block
        pinned = set(view.get("pinned", [])) | ({"thumb"} if view["columns"] and view["columns"][0][0] == "thumb" else set())
        ordered = [c for c in view["columns"] if c[0] in pinned] + [c for c in view["columns"] if c[0] not in pinned]
        for t, base in wb.EDIT_BASE.items():  # an edit column always sits right after the field it edits
            ekey = f"edit:{t}"
            keys_now = [c[0] for c in ordered]
            if ekey in keys_now and base in keys_now and base not in pinned and ekey not in pinned:
                col = ordered.pop(keys_now.index(ekey))
                ordered.insert([c[0] for c in ordered].index(base) + 1, col)
        for key, width in ordered:
            f = wb.field_for(key)
            if f:
                target = key[5:] if key.startswith("edit:") else f.editable
                empty = f.kind != "special" and not any(wb.value(r, key) not in ("", None) for r in rows)
                slim = view.get("slim", {}).get(key, empty)
                cat = (wb.field_for(wb.EDIT_BASE.get(key[5:], "")) or f).group if key.startswith("edit:") else f.group
                cat_start, prev_cat = bool(cols) and cat != prev_cat, cat  # a divider where the category changes
                cols.append({"key": key, "label": f.label, "kind": f.kind, "slim": slim, "empty": empty, "cat_start": cat_start,
                             "abbr": wb.acronym(f.label), "apply_target": wb.edit_target(key) or "",
                             "width": wb.SLIM_WIDTH if slim else wb.snap(width),
                             "target": target, "edit": key.startswith("edit:"), "pinned": key in pinned,
                             "groupable": {"path": "folder", "date": "day", "device": "device", "place": "place",
                                           "dup_count": "dups", "dup_conf": "dups"}.get(key, "")})
                if key.startswith("edit:"):  # the distinct sources (rules) its values come from, A, B, C…
                    links = {r["src"][target]["letter"]: r["src"][target] for r in rows if target in r.get("src", {})}
                    cols[-1]["links"] = [links[k] for k in sorted(links)]
        return {"view": view, "src_cells": src_cells, "edit_labels": wb.EDIT_LABELS, "groups": groups[offset:offset + WB_PAGE], "ngroups": len(groups), "nrows": nrows,
                "offset": offset, "next_offset": offset + WB_PAGE, "cols": cols,
                "col_labels": [[c["key"], c["label"], c["kind"]] for c in cols if c["kind"] != "special"],  # Sort by choices
                "tree": tree,
                "table_width": 40 + 40 + sum(c["width"] for c in cols) + (tree["width"] if tree else 0),
                "catalog": (catalog := wb.catalog(exif_keys)),
                "catalog_pairs": {g: [[f.key, f.label] for f in fs] for g, fs in catalog.items()},  # column picker JS
                "group_names": wb.GROUPS, "ops": wb.OPS,
                "no_value_ops": wb.NO_VALUE_OPS, "date_sources": wb.DATE_SOURCES,
                "field_label": lambda k: (wb.field_for(k).label if wb.field_for(k) else k),
                "conf_label": wb.conf_label, "conf_parts": wb.conf_parts, "value": wb.value, "checks_meta": wb.CHECKS, "key_fields": wb.KEY_FIELDS,
                "tokens": harmonize.TOKENS_HELP, "name_tokens": harmonize.NAME_TOKENS,
                "name_fields": (name_fields := wb.name_fields(catalog, [k for k, _w in view["columns"]])),
                "name_labels": {tok: label for _g, label, tok in harmonize.NAME_TOKENS + name_fields},
                # the picker: the recommended values first (they follow the New date / place / device edits)
                "name_options": [("Recommended values", label, tok) for _g, label, tok in harmonize.NAME_TOKENS] + name_fields, "pattern": lib.setting("filename_pattern"),
                "name_pattern": (view["edits"].get("filename", {}).get("pattern")
                                 or lib.setting("filename_pattern") or harmonize.DEFAULT_PATTERN)}

    @app.get("/workbench", response_class=HTMLResponse)
    def workbench(request: Request):
        with lib.connect() as con:
            srcs = con.execute(
                "SELECT s.id, s.label, s.root, s.writable, s.role, (SELECT COUNT(*) FROM files f WHERE f.source_id = s.id "
                "AND f.present = 1 AND f.removed = 0) AS n FROM sources s ORDER BY s.id").fetchall()
        ctx = wb_ctx()
        off = set(ctx["view"].get("sources_off") or [])
        return render(request, "workbench.html", sources=srcs, sources_off=off,
                      nfiles=sum(r["n"] for r in srcs if r["id"] not in off), **ctx)

    @app.get("/wb/table", response_class=HTMLResponse)
    def wb_table(request: Request):
        return templates.TemplateResponse(request, "partials/wb_table.html", wb_ctx())

    @app.get("/wb/more", response_class=HTMLResponse)
    def wb_more(request: Request, offset: int = 0):
        return templates.TemplateResponse(request, "partials/wb_groups.html", wb_ctx(offset))

    # Naming-pattern dialog: preview values for one file, and the user's saved patterns (presets)
    @app.post("/wb/name-preview")
    def wb_name_preview(pattern: str = Form(""), fid: str = Form(""), options: str = Form("")):
        view = wb.load_view(lib)
        rows, _ = wb.build_rows(lib)
        wb.apply_edits(rows, {k: v for k, v in view["edits"].items() if k != "filename"})
        r = next((x for x in rows if str(x["id"]) == fid), None) or (rows[0] if rows else None)
        if r is None:
            return {"name": "", "values": {}, "file": ""}
        e, v, eff = r["e"], r["v"], r["effective"]
        values = {"capture_time": e.get("date", eff.get("capture_time") or v["date"]),
                  "location_name": e.get("place", eff.get("location_name") or v["place"]),
                  "device_name": e.get("device", eff.get("device_name") or v["device"]),
                  "country": eff.get("country") or v["country"], "tags": v}
        def alone(tok: str) -> str:  # a token on its own, as it would read in the name
            out = harmonize.render_name(tok + ".x", values, r["original_name"], r["ext_raw"])
            return "" if out is None else out[:-2] if out.endswith(".x") else out

        each = {m.group(0): alone(m.group(0)) for m in harmonize.TOKEN.finditer(pattern)}
        name = harmonize.render_name(pattern, values, r["original_name"], r["ext_raw"])
        out = {"name": name if name is not None else "(no date for this file: it keeps its name)", "values": each,
               "file": r["v"]["filename"]}
        if options:  # the parameter list: every recommended value and every column of this file
            toks = [t for _g, _l, t in harmonize.NAME_TOKENS] + [f"{{tag:{k}}}" for k in v]
            out["options"] = {t: alone(t) for t in toks}
        return out

    def _presets() -> list[dict]:
        try:
            saved = json.loads(lib.setting("name_presets") or "[]")
        except ValueError:
            saved = []
        return [{"name": "Default", "pattern": harmonize.DEFAULT_PATTERN, "builtin": True}] + saved

    @app.get("/wb/presets")
    def wb_presets():
        return {"presets": _presets()}

    @app.post("/wb/presets")
    def wb_presets_change(action: str = Form(""), name: str = Form(""), pattern: str = Form(""), new: str = Form("")):
        saved = [p for p in _presets() if not p.get("builtin")]
        name, new = name.strip(), new.strip()
        if action == "save" and name and name != "Default":
            saved = [p for p in saved if p["name"] != name] + [{"name": name, "pattern": pattern}]
        elif action == "delete":
            saved = [p for p in saved if p["name"] != name]
        elif action == "rename" and new and new != "Default" and not any(p["name"] == new for p in saved):
            saved = [{**p, "name": new} if p["name"] == name else p for p in saved]
        lib.set_setting("name_presets", json.dumps(saved))
        return {"presets": _presets()}

    @app.get("/wb/values")
    def wb_values(key: str = ""):
        """Distinct values of a column in the current view, most frequent first (Filter by value)."""
        if not wb.field_for(key):
            return {"values": []}
        groups, _, _ = wb.table(lib, wb.load_view(lib))
        count: dict[str, int] = {}
        for g in groups:
            for r in g.rows:
                v = wb.value(r, key)
                s = "" if v in (None, "") else str(v)
                count[s] = count.get(s, 0) + 1
        top = sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))[:300]
        return {"values": [{"value": v, "count": n} for v, n in top]}

    @app.post("/wb/view")
    async def wb_view(request: Request):
        """Every change to the view (sort, group, filters, columns, edit rules) goes through here."""
        form = await request.form()
        f = {k: str(v) for k, v in form.items()}
        view = wb.load_view(lib)
        before = json.dumps(view)  # for undo
        act = f.get("action", "")
        cols = view["columns"]
        keys = [c[0] for c in cols]
        reload = True

        def add_edit_column(t: str, after: str = "") -> None:
            """The purple column of an edit rule: right after the field it edits (New date after Date taken), even
            when its value was copied from another column; `after` is only the fallback when that field is hidden."""
            ekey, ks = f"edit:{t}", [c[0] for c in cols]
            if ekey in ks:
                return
            base = wb.EDIT_BASE[t] if wb.EDIT_BASE[t] in ks else after
            at = ks.index(base) + 1 if base in ks else len(cols)
            cols.insert(at, [ekey, 304 if t == "filename" else 176])

        if act == "sort":
            view["sort"] = [f.get("key", ""), f.get("dir", "asc")]
        elif act == "group":
            wb.set_group(view, f.get("group", "dups") if f.get("group") in wb.GROUPS else "dups")
        elif act == "source_toggle" and f.get("id", "").isdigit():
            off = set(view.get("sources_off") or [])
            off ^= {int(f["id"])}
            view["sources_off"] = sorted(off)
        elif act == "q":
            view["q"] = f.get("q", "")
        elif act == "colors":
            view["colors"] = not view.get("colors", True)
        elif act == "filter_add" and f.get("key") and f.get("op") in wb.OPS:
            flt = {"key": f["key"], "op": f["op"], "value": f.get("value", "")}
            idx = int(f["index"]) if f.get("index", "").isdigit() else -1
            if 0 <= idx < len(view["filters"]):  # edited from its chip: replace it in place
                view["filters"][idx] = flt
            else:
                view["filters"].append(flt)
        elif act == "filter_remove":
            idx = int(f.get("index", -1))
            if 0 <= idx < len(view["filters"]):
                view["filters"].pop(idx)
        elif act == "col_add" and f.get("key") and f["key"] not in keys and wb.field_for(f["key"]):
            wb.insert_column(cols, f["key"], wb.field_for(f["key"]).width)
        elif act == "cols_set":  # column picker: show or hide many fields at once (a category, or all found)
            wanted = [k for k in f.get("keys", "").split(",") if k]
            if f.get("on"):
                for k in wanted:
                    if k not in keys and wb.field_for(k):
                        wb.insert_column(cols, k, wb.field_for(k).width)
            else:
                view["columns"] = [c for c in cols if c[0] not in wanted]
        elif act == "cols_order":  # column picker: order the shown columns like the list
            cols.sort(key=lambda c: wb.column_rank(c[0]))
        elif act == "col_remove":
            view["columns"] = [c for c in cols if c[0] != f.get("key")]
        elif act == "col_move" and f.get("key") in keys and f.get("key") != f.get("before"):
            moving = next(c for c in cols if c[0] == f["key"])
            rest = [c for c in cols if c[0] != f["key"]]
            at = next((i for i, c in enumerate(rest) if c[0] == f.get("before")), len(rest))
            view["columns"] = rest[:at] + [moving] + rest[at:]
        elif act == "col_pin" and f.get("key") in keys:  # header menu: keep the column at the left while scrolling
            pinned = [k for k in view.get("pinned", []) if k in keys and k != f["key"]]
            if f.get("on"):
                lead = [k for k in keys if k == "thumb" or k in pinned]  # the pinned block, in table order
                col = next(c for c in cols if c[0] == f["key"])
                rest = [c for c in cols if c[0] != f["key"]]
                at = max((i for i, c in enumerate(rest) if c[0] in lead), default=-1) + 1
                view["columns"] = rest[:at] + [col] + rest[at:]  # joins the end of the pinned block
                pinned.append(f["key"])
            view["pinned"] = pinned
        elif act == "col_slim" and f.get("key") in keys:  # header double-click: collapse / expand
            view.setdefault("slim", {})[f["key"]] = f.get("on") == "1"
        elif act == "col_width" and f.get("key") == "__group":
            view["group_width"] = max(80, wb.snap(f.get("width", 232)))
            reload = False
        elif act == "col_width" and f.get("key") in keys:
            for c in cols:
                if c[0] == f["key"]:
                    c[1] = wb.snap(f.get("width", c[1]))
            reload = False  # the browser already shows the new width
        elif act == "edit_set" and f.get("target") in wb.EDIT_LABELS:
            t, kind = f["target"], f.get("kind", "recommended")
            rule: dict = {"kind": kind}
            if t == "filename":
                rule = {"kind": "pattern", "pattern": f.get("pattern") or harmonize.DEFAULT_PATTERN}
            elif kind == "column":
                rule["column"] = f.get("column", "")
                if t not in wb.apply_targets(rule["column"]):  # "Use this for another column…": a date only into the date
                    return Response(status_code=204, headers={"HX-Trigger": json.dumps({"toast": "That column cannot feed this field"})})
            elif kind == "fixed":
                rule["value"] = f.get("value", "").strip()
            elif kind == "shift":
                secs = int(f.get("hh") or 0) * 3600 + int(f.get("mm") or 0) * 60 + int(f.get("ss") or 0)
                rule.update(column=f.get("shift_column") or "date", seconds=-secs if f.get("sign") == "-" else secs)
            view["edits"][t] = {**rule, "overrides": view["edits"].get(t, {}).get("overrides", {}),
                                        "override_from": view["edits"].get(t, {}).get("override_from", {})}
            add_edit_column(t)
        elif act in ("cell_filter", "cell_ref", "cell_apply", "cell_set") and f.get("fid", "").isdigit():
            fid, key = int(f["fid"]), f.get("key", "")
            if act == "cell_ref":  # use this row as the reference of its group (or stop)
                refs = set(view.get("ref_ids") or [])
                view["ref_ids"] = sorted(refs ^ {fid})
            elif wb.field_for(key):
                rows, _ = wb.build_rows(lib)
                wb.apply_edits(rows, view["edits"])
                r = next((x for x in rows if x["id"] == fid), None)
                t = wb.edit_target(key)
                if act == "cell_apply" and f.get("target"):  # "Apply to column…": the user picks the field it feeds
                    t = f["target"] if f["target"] in wb.apply_targets(key) else None
                if r is None:
                    pass
                elif act == "cell_filter":  # only rows with this value in this column
                    val = wb.value(r, key)
                    empty = val in ("", None)
                    view["filters"].append({"key": key, "op": "empty" if empty else "equals", "value": "" if empty else str(val)})
                elif act == "cell_set" and t and t != "filename":  # a value typed for this one file
                    rule = view["edits"].setdefault(t, {"kind": "keep"} if t != "filename" else {"kind": "keep", "pattern": ""})
                    rule.setdefault("overrides", {})[str(fid)] = f.get("value", "").strip()
                    add_edit_column(t, key)
                elif act == "cell_apply" and t and t != "filename":  # this value for the group / selection / all
                    scope = f.get("scope")
                    if scope == "group":
                        groups, _, _ = wb.table(lib, view)
                        ids = next(([x["id"] for x in g.rows] for g in groups if any(x["id"] == fid for x in g.rows)), [fid])
                    elif scope == "selection":
                        ids = sorted(_ids(f.get("ids", "")) | {fid})
                    else:
                        ids = None
                    view["edits"][t] = {"kind": "fixed", "value": wb.cell_value(r, key), "ids": ids, "from": {"fid": fid, "key": key},
                                        "overrides": view["edits"].get(t, {}).get("overrides", {}),
                                        "override_from": view["edits"].get(t, {}).get("override_from", {})}
                    add_edit_column(t, key)
        elif act == "cells":  # the cell sheet for several selected cells: the same actions, for each cell
            try:
                cells = [(int(c[0]), str(c[1])) for c in json.loads(f.get("cells", "[]")) if wb.field_for(str(c[1]))]
            except (ValueError, TypeError, IndexError):
                cells = []
            op = f.get("op", "")
            rows, _ = wb.build_rows(lib)
            wb.apply_edits(rows, view["edits"])
            by_id = {r["id"]: r for r in rows}
            cells = [(fid, key) for fid, key in cells if fid in by_id]
            if op == "filter":  # one filter per column: "is" / "is one of" / "is empty"
                per: dict[str, set] = {}
                for fid, key in cells:
                    v = wb.value(by_id[fid], key)
                    per.setdefault(key, set()).add("" if v in ("", None) else str(v))
                for key, vals in per.items():
                    filled_vals = sorted(v for v in vals if v)
                    if not filled_vals:
                        view["filters"].append({"key": key, "op": "empty", "value": ""})
                    elif len(filled_vals) == 1 and len(vals) == 1:
                        view["filters"].append({"key": key, "op": "equals", "value": filled_vals[0]})
                    else:
                        view["filters"].append({"key": key, "op": "in", "value": wb.IN_SEP.join(filled_vals)})
            elif op == "ref":  # these rows become references (or stop being, when all already are)
                refs, fids = set(view.get("ref_ids") or []), {fid for fid, _ in cells}
                view["ref_ids"] = sorted(refs - fids if fids <= refs else refs | fids)
            elif op in ("set", "apply"):
                groups = None
                for fid, key in cells:
                    t = f.get("target") if op == "apply" and f.get("target") else wb.edit_target(key)
                    if op == "apply" and f.get("target") and t not in wb.apply_targets(key):
                        continue
                    if not t or t == "filename":
                        continue
                    rule = view["edits"].setdefault(t, {"kind": "keep"})
                    over, frm = rule.setdefault("overrides", {}), rule.setdefault("override_from", {})
                    if op == "set":
                        over[str(fid)], _ = f.get("value", "").strip(), frm.pop(str(fid), None)
                    else:  # this cell's value for its group / the ticked rows / every row
                        scope = f.get("scope")
                        if scope == "group":
                            if groups is None:
                                groups, _, _ = wb.table(lib, view)
                            ids = next(([x["id"] for x in g.rows] for g in groups if any(x["id"] == fid for x in g.rows)), [fid])
                        elif scope == "selection":
                            ids = sorted(_ids(f.get("ids", "")) | {fid})
                        else:
                            ids = list(by_id)
                        val = wb.cell_value(by_id[fid], key)
                        for i in ids:
                            over[str(i)] = val
                            frm[str(i)] = {"fid": fid, "key": key}
                    add_edit_column(t, key)
        elif act == "edit_clear" and f.get("target") in wb.EDIT_LABELS:  # every row back to "= current value"
            view["edits"][f["target"]] = {"kind": "keep"} | ({"pattern": ""} if f["target"] == "filename" else {})
        elif act == "edit_remove" and f.get("target") in wb.EDIT_LABELS:
            view["edits"].pop(f["target"], None)
            view["columns"] = [c for c in cols if c[0] != f"edit:{f['target']}"]
        elif act == "reset":
            view = {**json.loads(json.dumps(wb.DEFAULT_VIEW)), "colors": view.get("colors", True)}  # colours: appearance
        wb.save_view(lib, view)
        headers = {"HX-Trigger": "wb-reload"} if reload else {}
        if json.dumps(view) != before and wb.history_push(lib, json.loads(before), act):
            headers["X-Wb-History"] = act  # the page records one view step in its undo history
        return Response(status_code=204, headers=headers)

    @app.post("/wb/history")
    def wb_history(direction: str = Form(...)):
        """Undo / redo one view change (the page keeps the order with its selection steps)."""
        r = wb.history_step(lib, direction)
        return r or {"ok": False}

    # Data input: manage folders without leaving the Workbench. Each change rescans and reloads the page.
    def _rescan() -> None:
        def fn(job):
            scan_all(lib, job)
            harmonize.build_proposals(lib, job)
        start("scan", fn)

    def _toast(msg: str) -> Response:
        return Response(status_code=204, headers={"HX-Trigger": json.dumps({"toast": msg})})

    def _choose_folder() -> str | None:
        """Open the macOS folder dialog (this server runs on the user's Mac). Returns a path, "" when the user
        cancels, or None when no native dialog is available (another OS, no GUI session)."""
        if sys.platform != "darwin":
            return None
        script = ('tell application "System Events"\n activate\n'
                  ' set f to choose folder with prompt "Add a folder to meta-duplo"\nend tell\nPOSIX path of f')
        try:
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if r.returncode != 0:
            return "" if "-128" in r.stderr else None  # -128: the user pressed Cancel
        return r.stdout.strip()

    @app.post("/wb/sources/pick")
    def wb_source_pick(writable: str | None = Form(None)):
        path = _choose_folder()
        if path is None:  # no native dialog: the page asks for a typed path instead
            return Response(status_code=204, headers={"HX-Trigger": json.dumps({"wb-pickpath": {"writable": bool(writable)}})})
        if not path:
            return Response(status_code=204)
        add_source(lib, path, writable=bool(writable))
        _rescan()
        return Response(headers={"HX-Refresh": "true"})

    @app.post("/wb/sources")
    def wb_source_add(path: str = Form(""), writable: str | None = Form(None)):
        p = Path(path.strip()).expanduser()
        if not path.strip() or not p.is_dir():
            return _toast(f"Folder not found: {p}")
        add_source(lib, str(p), writable=bool(writable))
        _rescan()
        return Response(headers={"HX-Refresh": "true"})

    @app.post("/wb/sources/demo")
    def wb_source_demo():
        from ..demo import make_demo

        root = Path.home() / "meta-duplo-demo"
        if not root.exists():  # never recreate an existing folder: plans may have changed it
            make_demo(root)
        add_source(lib, str(root), writable=True)
        _rescan()
        return Response(headers={"HX-Refresh": "true"})

    @app.post("/wb/sources/remove-all")
    def wb_source_remove_all():
        with lib.connect() as con:
            ids = [r["id"] for r in con.execute("SELECT id FROM sources")]
        kept = [sid for sid in ids if not remove_source(lib, sid)]  # a folder a plan uses stays (undo needs it)
        if len(kept) == len(ids):
            return _toast("These folders are used by plans. Keep them so the plans can still be undone.")
        view = wb.load_view(lib)
        view["sources_off"] = [s for s in view.get("sources_off", []) if s in kept]
        wb.save_view(lib, view)
        _rescan()  # rebuild duplicate groups and proposals without them, like a single remove
        return Response(headers={"HX-Refresh": "true"})

    @app.post("/wb/sources/{sid}/remove")
    def wb_source_remove(sid: int):
        if not remove_source(lib, sid):
            return _toast("This folder is used by a plan. Keep it so the plan can still be undone.")
        view = wb.load_view(lib)
        view["sources_off"] = [s for s in view.get("sources_off", []) if s != sid]
        wb.save_view(lib, view)
        _rescan()
        return Response(headers={"HX-Refresh": "true"})

    @app.get("/wb/preview/{fid}", response_class=HTMLResponse)
    def wb_preview(request: Request, fid: int):
        view = wb.load_view(lib)
        rows, _ = wb.build_rows(lib)
        wb.apply_edits(rows, view["edits"])
        r = next((x for x in rows if x["id"] == fid), None)
        mates = [x for x in rows if r and r["cluster"] is not None and x["cluster"] == r["cluster"]] if r else []
        return templates.TemplateResponse(request, "partials/wb_preview.html",
                                          {"r": r, "mates": mates, "conf_label": wb.conf_label, "edit_labels": wb.EDIT_LABELS})

    @app.get("/wb/mates/{fid}")
    def wb_mates(fid: int):
        """The file and the other copies of its duplicate set, for the large viewer (one / grid)."""
        rows, _ = wb.build_rows(lib)
        r = next((x for x in rows if x["id"] == fid), None)
        if not r:
            return {"items": []}
        mates = [x for x in rows if r["cluster"] is not None and x["cluster"] == r["cluster"]] or [r]
        mates.sort(key=lambda x: (x["id"] != fid, not x["keeper"], x["v"]["path"]))
        return {"items": [{"id": x["id"], "name": x["v"]["filename"], "path": x["v"]["path"], "kind": x["v"]["kind"],
                           "conf": wb.conf_label(x), "kept": x["keeper"]} for x in mates]}

    @app.get("/wb/ids")
    def wb_ids(which: str = "nonkeepers", key: str = "", filled: int = 1):
        """Selection presets over the whole current view, also the groups not loaded yet;
        which=filled: the files with (filled=1) or without (0) a value in column `key`."""
        if which == "filled":  # key: one column, or several joined by newlines (a multi-cell selection)
            keys = [k for k in key.split("\n") if k]
            if not keys or not all(wb.field_for(k) for k in keys):
                raise HTTPException(400, "unknown column")
            return {"ids": wb.filled_ids(lib, wb.load_view(lib), keys, bool(filled))}
        if which not in wb.SELECTION_PRESETS:
            raise HTTPException(400, "unknown selection preset")
        return {"ids": wb.preset_ids(lib, wb.load_view(lib), which)}

    def _ids(raw: str) -> set[int]:
        return {int(x) for x in raw.split(",") if x.strip().isdigit()}

    @app.post("/wb/commit")
    def wb_commit(ids: str = Form(""), target: str = Form(""), all: str = Form("")):
        view = wb.load_view(lib)
        chosen = set(wb.preset_ids(lib, view, "all")) if all else _ids(ids)
        n = wb.commit(lib, view, chosen, only=target if target in wb.EDIT_LABELS else None)
        return Response(status_code=204, headers={"HX-Trigger": json.dumps(
            {"wb-reload": True, "toast": "Saved the filename pattern; review the rename plan to apply it" if target == "filename"
             else f"Saved {n} value{'' if n == 1 else 's'} as decisions" if n else "Nothing to save for these files"})})

    @app.post("/wb/rename-plan")
    def wb_rename_plan(ids: str = Form(""), write_xmp: str | None = Form(None)):
        chosen = _ids(ids)
        if not chosen:
            return Response(status_code=204, headers={"HX-Trigger": json.dumps({"toast": "Select files first"})})
        wb.commit(lib, wb.load_view(lib), chosen)  # the plan uses exactly what the table shows
        pid = plans.build_rename_plan(lib, write_xmp=bool(write_xmp), only_ids=chosen)
        return Response(headers={"HX-Redirect": f"/plans/{pid}"})

    @app.post("/wb/remove-plan")
    def wb_remove_plan(ids: str = Form("")):
        chosen = _ids(ids)
        gids = wb.groups_for(lib, chosen)
        if not gids:
            return Response(status_code=204, headers={"HX-Trigger": json.dumps({"toast": "No duplicates in the selection"})})
        pid = plans.build_cleanup_plan(lib, gids, remove_file_ids=chosen)
        return Response(headers={"HX-Redirect": f"/plans/{pid}"})

    # ───────────────────────── plans ─────────────────────────

    @app.get("/plans", response_class=HTMLResponse)
    def plan_list(request: Request):
        with lib.connect() as con:
            rows = [dict(r) | {"summary": json.loads(r["summary"] or "{}")}
                    for r in con.execute("SELECT * FROM plans ORDER BY id DESC")]
        return render(request, "plans.html", plans=rows)

    @app.get("/plans/{pid}", response_class=HTMLResponse)
    def plan_detail(request: Request, pid: int):
        with lib.connect() as con:
            p = con.execute("SELECT * FROM plans WHERE id = ?", (pid,)).fetchone()
            if not p:
                return RedirectResponse("/plans")
            acts = [dict(a) for a in con.execute("SELECT * FROM actions WHERE plan_id = ? ORDER BY id LIMIT 1000", (pid,))]
            roots = sorted((r["root"] for r in con.execute("SELECT root FROM sources")), key=len, reverse=True)
        for a in acts:
            for key in ("src", "dst"):
                for root in roots:
                    if a[key] and a[key].startswith(root + "/"):
                        a[key] = a[key][len(root) + 1:]
                        break
        return render(request, "plan.html", plan=dict(p) | {"summary": json.loads(p["summary"] or "{}")}, actions=acts,
                      roots=roots)

    @app.post("/plans/{pid}/{what}")
    def plan_action(pid: int, what: str):
        msg = ""
        try:
            if what == "apply":
                r = plans.apply_plan(lib, pid)
                msg = f"Applied: {r['done']} done, {r['failed']} failed"
            elif what == "undo":
                r = plans.undo_plan(lib, pid)
                msg = f"Undone: {r['undone']} actions"
            elif what == "discard":
                plans.discard_plan(lib, pid)
                return Response(headers={"HX-Redirect": "/plans"})
        except plans.PlanError as exc:
            msg = str(exc)
        return Response(headers={"HX-Redirect": f"/plans/{pid}", "HX-Trigger": json.dumps({"toast": msg})})

    # ───────────────────────── settings + media ─────────────────────────

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request):
        with lib.connect() as con:
            rows = {r["key"]: r["value"] for r in con.execute("SELECT * FROM settings")}
        return render(request, "settings.html", settings=rows, library=str(lib.path))

    @app.post("/settings")
    def save_settings(home_timezone: str = Form(...), near_threshold: str = Form(...),
                      recheck_min_confidence: str = Form(...)):
        for k, v in (("home_timezone", home_timezone), ("near_threshold", near_threshold),
                     ("recheck_min_confidence", recheck_min_confidence)):
            lib.set_setting(k, v.strip())
        return Response(headers={"HX-Redirect": "/settings", "HX-Trigger": json.dumps({"toast": "Settings saved"})})

    @app.get("/thumb/{fid}")
    def thumb(fid: int):
        p = lib.path / "thumbs" / f"{fid}.jpg"
        if p.exists():
            return FileResponse(p, headers={"Cache-Control": "max-age=86400"})
        return FileResponse(HERE / "static" / "img" / "placeholder.svg")

    @app.get("/media/{fid}")
    def media(fid: int):
        with lib.connect() as con:
            r = con.execute("SELECT s.root, f.rel_path FROM files f JOIN sources s ON s.id = f.source_id WHERE f.id = ?",
                            (fid,)).fetchone()
        if not r:
            return Response(status_code=404)
        return FileResponse(Path(r["root"]) / r["rel_path"])

    return app
