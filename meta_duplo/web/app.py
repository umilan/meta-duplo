"""FastAPI + Jinja + htmx. Every page is server-rendered; htmx swaps partials."""
from __future__ import annotations

import json
import secrets
from urllib.parse import urlencode
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import geo, harmonize, match, plans
from . import rename_grid, results, workbench as wb
from ..__main__ import add_source
from ..db import Library
from ..metadata import device_name
from ..scan import Runner, scan_all

HERE = Path(__file__).parent
PAGE = 100
GRID_PAGE = 200
WB_PAGE = 60  # groups per chunk in the workbench


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
    templates.env.filters.update(size=human_size, band=band, pct=lambda p: "–" if p is None else f"{p * 100:.0f} %")
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
        return RedirectResponse("/sources")

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
        if watch and j and j["status"] == "done":
            return Response(headers={"HX-Refresh": "true"})  # reload the page to show the new results
        return templates.TemplateResponse(request, "partials/job.html", {"job": j})

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
        tree = None
        if view["group"] in wb.GROUP_COLUMN:
            label, replaces = wb.GROUP_COLUMN[view["group"]]
            tree = {"label": label, "replaces": replaces, "width": int(view.get("group_width", 230))}
        cols = []
        for key, width in view["columns"]:
            if tree and key == tree["replaces"]:
                continue  # the grouping column already shows it
            f = wb.field_for(key)
            if f:
                target = key[5:] if key.startswith("edit:") else f.editable
                cols.append({"key": key, "label": f.label, "kind": f.kind, "width": int(width),
                             "target": target, "edit": key.startswith("edit:"),
                             "groupable": {"path": "folder", "date": "day", "device": "device", "place": "place",
                                           "dup_count": "dups", "dup_conf": "dups"}.get(key, "")})
        return {"view": view, "groups": groups[offset:offset + WB_PAGE], "ngroups": len(groups), "nrows": nrows,
                "offset": offset, "next_offset": offset + WB_PAGE, "cols": cols,
                "tree": tree,
                "table_width": 36 + 40 + sum(c["width"] for c in cols) + (tree["width"] if tree else 0),
                "catalog": wb.catalog(exif_keys), "group_names": wb.GROUPS, "ops": wb.OPS,
                "no_value_ops": wb.NO_VALUE_OPS, "date_sources": wb.DATE_SOURCES,
                "field_label": lambda k: (wb.field_for(k).label if wb.field_for(k) else k),
                "conf_label": wb.conf_label, "value": wb.value,
                "tokens": harmonize.TOKENS_HELP, "pattern": lib.setting("filename_pattern")}

    @app.get("/workbench", response_class=HTMLResponse)
    def workbench(request: Request):
        return render(request, "workbench.html", **wb_ctx())

    @app.get("/wb/table", response_class=HTMLResponse)
    def wb_table(request: Request):
        return templates.TemplateResponse(request, "partials/wb_table.html", wb_ctx())

    @app.get("/wb/more", response_class=HTMLResponse)
    def wb_more(request: Request, offset: int = 0):
        return templates.TemplateResponse(request, "partials/wb_groups.html", wb_ctx(offset))

    @app.post("/wb/view")
    async def wb_view(request: Request):
        """Every change to the view (sort, group, filters, columns, edit rules) goes through here."""
        form = await request.form()
        f = {k: str(v) for k, v in form.items()}
        view = wb.load_view(lib)
        act = f.get("action", "")
        cols = view["columns"]
        keys = [c[0] for c in cols]
        reload = True
        if act == "sort":
            view["sort"] = [f.get("key", ""), f.get("dir", "asc")]
        elif act == "group":
            view["group"] = f.get("group", "dups")
        elif act == "q":
            view["q"] = f.get("q", "")
        elif act == "colors":
            view["colors"] = not view.get("colors", True)
        elif act == "filter_add" and f.get("key") and f.get("op") in wb.OPS:
            view["filters"].append({"key": f["key"], "op": f["op"], "value": f.get("value", "")})
        elif act == "filter_remove":
            idx = int(f.get("index", -1))
            if 0 <= idx < len(view["filters"]):
                view["filters"].pop(idx)
        elif act == "col_add" and f.get("key") and f["key"] not in keys and wb.field_for(f["key"]):
            cols.append([f["key"], wb.field_for(f["key"]).width])
        elif act == "col_remove":
            view["columns"] = [c for c in cols if c[0] != f.get("key")]
        elif act == "col_move" and f.get("key") in keys and f.get("key") != f.get("before"):
            moving = next(c for c in cols if c[0] == f["key"])
            rest = [c for c in cols if c[0] != f["key"]]
            at = next((i for i, c in enumerate(rest) if c[0] == f.get("before")), len(rest))
            view["columns"] = rest[:at] + [moving] + rest[at:]
        elif act == "col_width" and f.get("key") == "__group":
            view["group_width"] = max(80, min(900, int(float(f.get("width", 230)))))
            reload = False
        elif act == "col_width" and f.get("key") in keys:
            for c in cols:
                if c[0] == f["key"]:
                    c[1] = max(40, min(900, int(float(f.get("width", c[1])))))
            reload = False  # the browser already shows the new width
        elif act == "edit_set" and f.get("target") in wb.EDIT_LABELS:
            t, kind = f["target"], f.get("kind", "recommended")
            rule: dict = {"kind": kind}
            if t == "filename":
                rule = {"kind": "pattern", "pattern": f.get("pattern") or harmonize.DEFAULT_PATTERN}
            elif kind == "column":
                rule["column"] = f.get("column", "")
            elif kind == "fixed":
                rule["value"] = f.get("value", "").strip()
            elif kind == "shift":
                secs = int(f.get("hh") or 0) * 3600 + int(f.get("mm") or 0) * 60 + int(f.get("ss") or 0)
                rule.update(column=f.get("shift_column") or "date", seconds=-secs if f.get("sign") == "-" else secs)
            view["edits"][t] = rule
            ekey = f"edit:{t}"
            if ekey not in keys:  # the edit column goes right after its source column
                base = wb.EDIT_BASE[t]
                at = keys.index(base) + 1 if base in keys else len(cols)
                cols.insert(at, [ekey, 300 if t == "filename" else 170])
        elif act == "edit_remove" and f.get("target") in wb.EDIT_LABELS:
            view["edits"].pop(f["target"], None)
            view["columns"] = [c for c in cols if c[0] != f"edit:{f['target']}"]
        elif act == "reset":
            view = json.loads(json.dumps(wb.DEFAULT_VIEW))
        wb.save_view(lib, view)
        return Response(status_code=204, headers={"HX-Trigger": "wb-reload"} if reload else {})

    @app.get("/wb/preview/{fid}", response_class=HTMLResponse)
    def wb_preview(request: Request, fid: int):
        view = wb.load_view(lib)
        rows, _ = wb.build_rows(lib)
        wb.apply_edits(rows, view["edits"])
        r = next((x for x in rows if x["id"] == fid), None)
        mates = [x for x in rows if r and r["cluster"] is not None and x["cluster"] == r["cluster"]] if r else []
        return templates.TemplateResponse(request, "partials/wb_preview.html",
                                          {"r": r, "mates": mates, "conf_label": wb.conf_label, "edit_labels": wb.EDIT_LABELS})

    @app.get("/wb/ids")
    def wb_ids(which: str = "nonkeepers"):
        view = wb.load_view(lib)
        if which == "all":  # every row of the current view, also the groups not loaded yet
            groups, _, _ = wb.table(lib, view)
            return {"ids": [r["id"] for g in groups for r in g.rows]}
        return {"ids": wb.nonkeeper_ids(lib, view)}

    def _ids(raw: str) -> set[int]:
        return {int(x) for x in raw.split(",") if x.strip().isdigit()}

    @app.post("/wb/commit")
    def wb_commit(ids: str = Form("")):
        n = wb.commit(lib, wb.load_view(lib), _ids(ids))
        return Response(status_code=204, headers={"HX-Trigger": json.dumps(
            {"wb-reload": True, "toast": f"Saved {n} values as decisions" if n else "Nothing to save for the selection"})})

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
