"""Plans: the only code that writes to source folders.

A plan is a dry-run list of actions. Applying it writes a journal first
(undo/<plan_id>.jsonl), re-verifies each file, then acts group by group: if one action
in a group fails, the group's done actions are rolled back. Undo replays the journal
in reverse.
"""
from __future__ import annotations

import filecmp
import json
import os
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path, PurePosixPath

from .db import Library, now, tx
from .harmonize import effective_values, rename_preview
from .metadata import TIME_FMT
from .scan import HOLD_DIR

SIDECAR_EXTS = (".xmp", ".XMP", ".aae", ".AAE", ".json", ".JSON")


class PlanError(Exception):
    pass


def _sidecars(path: Path) -> list[Path]:
    """Sidecars that travel with a file: IMG_1.xmp, IMG_1.JPG.xmp, IMG_1.aae, IMG_1.JPG.json (Takeout)."""
    out = []
    for ext in SIDECAR_EXTS:
        for cand in (path.with_suffix(ext), path.with_name(path.name + ext)):
            if cand.exists() and cand != path and cand not in out:
                out.append(cand)
    return out


def _new_plan(con, kind: str, summary: dict) -> int:
    return con.execute(
        "INSERT INTO plans(kind, status, created_at, summary) VALUES (?, 'draft', ?, ?)",
        (kind, now(), json.dumps(summary)),
    ).lastrowid


def _writable_sources(con) -> dict[int, dict]:
    return {r["id"]: dict(r) for r in con.execute("SELECT * FROM sources")}


# ───────────────────────── cleanup (steps 1, 2, 4) ─────────────────────────

def build_cleanup_plan(lib: Library, group_ids: list[int], remove_file_ids: list[int] | None = None) -> int:
    """Move every non-keeper of the given groups (or only `remove_file_ids`) to the holding folder
    `<source>/_duplicates/<group>/<original relative path>`."""
    with lib.connect() as con:
        sources = _writable_sources(con)
        actions, blocked, total = [], [], 0
        for gid in group_ids:
            g = con.execute("SELECT * FROM groups WHERE id = ?", (gid,)).fetchone()
            if not g or g["status"] != "open":
                continue
            members = con.execute(
                "SELECT f.*, gm.role FROM group_members gm JOIN files f ON f.id = gm.file_id WHERE gm.group_id = ?",
                (gid,),
            ).fetchall()
            if remove_file_ids is None:  # default: everything except the keeper
                to_remove = [m for m in members if m["role"] != "keeper"]
            else:  # explicit selection: any copy may go, as long as one stays
                to_remove = [m for m in members if m["id"] in remove_file_ids]
            removing = {m["id"] for m in to_remove}
            kept = [m for m in members if m["id"] not in removing]
            if not kept:
                blocked.append((gid, "would leave no copy"))
                continue
            keeper = next((m for m in kept if m["role"] == "keeper"), kept[0])
            for m in to_remove:
                src = sources[m["source_id"]]
                if not src["writable"] or src["role"] == "reference":
                    blocked.append((gid, f"{src['root']} is read-only"))
                    continue
                root = Path(src["root"])
                path = root / m["rel_path"]
                dst_rel = str(PurePosixPath(HOLD_DIR) / f"group-{gid}" / m["rel_path"])
                payload = {"keeper_id": keeper["id"], "verdict": g["verdict"], "step": g["step"],
                           "size": m["size"], "mtime": m["mtime"], "rel_src": m["rel_path"], "rel_dst": dst_rel}
                gkey = f"file-{m['id']}"
                actions.append((gkey, m["id"], "hold", str(path), str(root / dst_rel), json.dumps(payload)))
                for sc in _sidecars(path):
                    rel = sc.relative_to(root)
                    actions.append((gkey, None, "move", str(sc), str(root / HOLD_DIR / f"group-{gid}" / rel), "{}"))
                total += m["size"] or 0
        with tx(con):
            pid = _new_plan(con, "cleanup", {"files": sum(1 for a in actions if a[2] == "hold"), "bytes": total,
                                              "blocked": blocked, "groups": group_ids})
            con.executemany(
                "INSERT INTO actions(plan_id, group_key, file_id, op, src, dst, payload) VALUES (?,?,?,?,?,?,?)",
                [(pid, *a) for a in actions])
    return pid


# ───────────────────────── rename + annotate (step 3) ─────────────────────────

def _xmp(values: dict) -> str:
    def gps(v: float, pos: str, neg: str) -> str:
        d = abs(v)
        return f"{int(d)},{(d - int(d)) * 60:.4f}{pos if v >= 0 else neg}"

    t = values.get("capture_time")
    iso = datetime.strptime(t, TIME_FMT).strftime("%Y-%m-%dT%H:%M:%S") if t else ""
    props = []
    if iso:
        props += [f"<exif:DateTimeOriginal>{iso}</exif:DateTimeOriginal>", f"<xmp:CreateDate>{iso}</xmp:CreateDate>",
                  f"<photoshop:DateCreated>{iso}</photoshop:DateCreated>"]
    if values.get("location_name"):
        props.append(f"<photoshop:City>{values['location_name']}</photoshop:City>")
    if values.get("country"):
        props.append(f"<photoshop:Country>{values['country']}</photoshop:Country>")
    if values.get("lat") is not None:
        props += [f"<exif:GPSLatitude>{gps(values['lat'], 'N', 'S')}</exif:GPSLatitude>",
                  f"<exif:GPSLongitude>{gps(values['lon'], 'E', 'W')}</exif:GPSLongitude>"]
    if values.get("device_name"):
        props.append(f"<tiff:Model>{values['device_name']}</tiff:Model>")
    body = "\n      ".join(props)
    return f"""<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="meta-duplo 0.1">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about=""
      xmlns:exif="http://ns.adobe.com/exif/1.0/" xmlns:xmp="http://ns.adobe.com/xap/1.0/"
      xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" xmlns:tiff="http://ns.adobe.com/tiff/1.0/">
      {body}
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
"""


def build_rename_plan(lib: Library, write_xmp: bool = True, set_mtime: bool = False,
                      only_ids: set[int] | None = None) -> int:
    """Rename (and annotate) every file, or only `only_ids` (the workbench selection)."""
    preview = rename_preview(lib)
    with lib.connect() as con:
        sources = _writable_sources(con)
        values = effective_values(con)
        src_of = {r["id"]: r["source_id"] for r in con.execute("SELECT id, source_id FROM files")}
        actions, blocked = [], []
        for p in preview:
            if p["status"] == "excluded" or (only_ids is not None and p["file_id"] not in only_ids):
                continue
            src = sources[src_of[p["file_id"]]]
            if not src["writable"] or src["role"] == "reference":
                if p["status"] not in ("unchanged", "no_date"):
                    blocked.append((p["file_id"], f"{src['root']} is read-only"))
                continue
            root = Path(src["root"])
            path = root / p["rel_path"]
            gkey = f"file-{p['file_id']}"
            vals = values.get(p["capture_id"], {})
            final = path
            if p["status"] in ("ok", "renumbered"):
                final = path.with_name(p["new"])
                actions.append((gkey, p["file_id"], "rename", str(path), str(final), json.dumps({})))
                for sc in _sidecars(path):  # IMG_1.xmp → <new stem>.xmp, IMG_1.JPG.xmp → <new name>.xmp
                    tail = sc.name[len(path.name):] if sc.name.startswith(path.name) else sc.suffix
                    new_sc = final.with_name(final.name + tail) if sc.name.startswith(path.name) else final.with_suffix(tail)
                    actions.append((gkey, None, "move", str(sc), str(new_sc), "{}"))
            if write_xmp and any(vals.get(k) for k in ("capture_time", "location_name", "device_name")):
                xmp = final.with_suffix(".xmp")
                if not xmp.exists() and not _sidecars(path):
                    actions.append((gkey, p["file_id"], "write_xmp", None, str(xmp), json.dumps({"xml": _xmp(vals)})))
            if set_mtime and vals.get("capture_time"):
                ts = datetime.strptime(vals["capture_time"], TIME_FMT).timestamp()
                actions.append((gkey, p["file_id"], "set_mtime", str(final), str(final), json.dumps({"mtime": ts})))
        with tx(con):
            pid = _new_plan(con, "rename", {
                "renames": sum(1 for a in actions if a[2] == "rename"),
                "sidecars": sum(1 for a in actions if a[2] == "write_xmp"),
                "mtimes": sum(1 for a in actions if a[2] == "set_mtime"),
                "blocked": blocked[:50], "blocked_count": len(blocked),
                "pattern": lib.setting("filename_pattern"),
            })
            con.executemany(
                "INSERT INTO actions(plan_id, group_key, file_id, op, src, dst, payload) VALUES (?,?,?,?,?,?,?)",
                [(pid, *a) for a in actions])
    return pid


# ───────────────────────── apply / undo ─────────────────────────

def _verify(con, a) -> None:
    """Re-check right before acting: the disk may have changed since the scan."""
    if a["op"] not in ("hold", "rename") or not a["file_id"]:
        return
    f = con.execute("SELECT size, mtime FROM files WHERE id = ?", (a["file_id"],)).fetchone()
    st = os.stat(a["src"])
    if st.st_size != f["size"] or abs(st.st_mtime - f["mtime"]) > 1e-3:
        raise PlanError("file changed since the scan; rescan first")
    if a["op"] == "hold":
        p = json.loads(a["payload"])
        if p.get("verdict") == "identical":  # byte compare before removing an "identical" copy
            k = con.execute(
                "SELECT s.root, f.rel_path FROM files f JOIN sources s ON s.id = f.source_id WHERE f.id = ?",
                (p["keeper_id"],)).fetchone()
            if not filecmp.cmp(a["src"], Path(k["root"]) / k["rel_path"], shallow=False):
                raise PlanError("bytes differ from the kept copy")


def _do(a) -> dict:
    """Perform one action; return what undo needs."""
    op, src, dst = a["op"], a["src"], a["dst"]
    if op in ("hold", "move", "rename"):
        if Path(dst).exists():
            raise PlanError(f"target exists: {dst}")
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, dst)  # same volume: an atomic rename
        return {}
    if op == "write_xmp":
        if Path(dst).exists():
            raise PlanError(f"sidecar exists: {dst}")
        Path(dst).write_text(json.loads(a["payload"])["xml"], encoding="utf-8")
        return {}
    if op == "set_mtime":
        st = os.stat(dst)
        ts = json.loads(a["payload"])["mtime"]
        os.utime(dst, (st.st_atime, ts))
        return {"old_atime": st.st_atime, "old_mtime": st.st_mtime}
    raise PlanError(f"unknown op {op}")


def _undo_one(a, info: dict) -> None:
    op, src, dst = a["op"], a["src"], a["dst"]
    if op in ("hold", "move", "rename"):
        Path(src).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(dst, src)
    elif op == "write_xmp":
        Path(dst).unlink(missing_ok=True)
    elif op == "set_mtime":
        os.utime(dst, (info["old_atime"], info["old_mtime"]))


def _sync_db(con, a, undo: bool = False) -> None:
    if not a["file_id"]:
        return
    if a["op"] == "hold":
        p = json.loads(a["payload"])
        if undo:
            con.execute("UPDATE files SET removed = 0, rel_path = ? WHERE id = ?", (p["rel_src"], a["file_id"]))
        else:
            con.execute("UPDATE files SET removed = 1, rel_path = ? WHERE id = ?", (p["rel_dst"], a["file_id"]))
    elif a["op"] == "rename":
        path = Path(a["src"] if undo else a["dst"])
        f = con.execute("SELECT rel_path FROM files WHERE id = ?", (a["file_id"],)).fetchone()
        rel = str(PurePosixPath(f["rel_path"]).with_name(path.name))
        con.execute("UPDATE files SET rel_path = ?, name = ? WHERE id = ?", (rel, path.name, a["file_id"]))
    elif a["op"] == "set_mtime":
        con.execute("UPDATE files SET mtime = ? WHERE id = ?", (os.stat(a["dst"]).st_mtime, a["file_id"]))


def apply_plan(lib: Library, plan_id: int) -> dict:
    with lib.connect() as con:
        plan = con.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not plan or plan["status"] != "draft":
            raise PlanError("only a draft plan can be applied")
        actions = [dict(a) for a in con.execute("SELECT * FROM actions WHERE plan_id = ? ORDER BY id", (plan_id,))]
        journal = lib.path / "undo" / f"{plan_id}.jsonl"
        with journal.open("w", encoding="utf-8") as fh:  # journal first, then act
            for a in actions:
                fh.write(json.dumps({k: a[k] for k in ("id", "op", "src", "dst", "file_id")}) + "\n")
        groups: dict[str, list[dict]] = defaultdict(list)
        for a in actions:
            groups[a["group_key"]].append(a)
        done = failed = 0
        for key, acts in groups.items():
            finished: list[tuple[dict, dict]] = []
            try:
                for a in acts:
                    _verify(con, a)
                    finished.append((a, _do(a)))
            except Exception as exc:  # roll the whole group back: sidecars never get separated
                for a, info in reversed(finished):
                    try:
                        _undo_one(a, info)
                    except Exception:
                        pass
                with tx(con):
                    for a in acts:
                        con.execute("UPDATE actions SET status = 'failed', error = ? WHERE id = ?", (str(exc), a["id"]))
                failed += 1
                continue
            with tx(con):
                for a, info in finished:
                    con.execute("UPDATE actions SET status = 'done', payload = ? WHERE id = ?",
                                (json.dumps({**json.loads(a["payload"] or "{}"), "undo": info}), a["id"]))
                    _sync_db(con, a)
            done += 1
        status = "applied" if not failed else ("partially_applied" if done else "failed")
        con.execute("UPDATE plans SET status = ?, applied_at = ? WHERE id = ?", (status, now(), plan_id))
        if plan["kind"] == "cleanup":
            con.execute(
                "UPDATE groups SET status = 'resolved' WHERE id IN (SELECT g.id FROM groups g WHERE g.status = 'open' "
                "AND NOT EXISTS (SELECT 1 FROM group_members gm JOIN files f ON f.id = gm.file_id "
                "WHERE gm.group_id = g.id AND gm.role != 'keeper' AND f.removed = 0))")
    return {"done": done, "failed": failed, "status": status}


def undo_plan(lib: Library, plan_id: int) -> dict:
    with lib.connect() as con:
        plan = con.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not plan or plan["status"] not in ("applied", "partially_applied"):
            raise PlanError("only an applied plan can be undone")
        acts = [dict(a) for a in con.execute(
            "SELECT * FROM actions WHERE plan_id = ? AND status = 'done' ORDER BY id DESC", (plan_id,))]
        undone = errors = 0
        for a in acts:
            info = json.loads(a["payload"] or "{}").get("undo", {})
            try:
                _undo_one(a, info)
            except Exception as exc:
                con.execute("UPDATE actions SET error = ? WHERE id = ?", (f"undo: {exc}", a["id"]))
                errors += 1
                continue
            with tx(con):
                con.execute("UPDATE actions SET status = 'undone' WHERE id = ?", (a["id"],))
                _sync_db(con, a, undo=True)
            undone += 1
        con.execute("UPDATE plans SET status = 'undone', undone_at = ? WHERE id = ?", (now(), plan_id))
        if plan["kind"] == "cleanup":
            for gid in json.loads(plan["summary"] or "{}").get("groups", []):
                con.execute("UPDATE groups SET status = 'open' WHERE id = ? AND status = 'resolved'", (gid,))
    return {"undone": undone, "errors": errors}


def discard_plan(lib: Library, plan_id: int) -> None:
    with lib.connect() as con, tx(con):
        con.execute("DELETE FROM actions WHERE plan_id = ? AND status = 'planned'", (plan_id,))
        con.execute("DELETE FROM plans WHERE id = ? AND status = 'draft'", (plan_id,))
