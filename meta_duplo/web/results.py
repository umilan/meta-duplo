"""Read model and selection for the duplicate results table (steps 1, 2 and 4).

Selection is server-side (`selection` table): it survives paging, filtering and reloads,
and a removal plan is built from it in one go.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import PurePosixPath

from .. import geo, harmonize
from ..db import Library, tx
from ..metadata import device_name

SORTS = ("size", "qty", "name", "date", "device", "place", "confidence")
KIND_FILTER = {  # (step, kind) → extra WHERE clause on groups
    "dups": " AND g.verdict != 'similar_shot'",
    "bursts": " AND g.verdict = 'similar_shot'",
}


def _where(step: int, status: str, kind: str, q: str, min_conf: float) -> tuple[str, list]:
    sql = " WHERE g.step = ? AND g.status = ?" + KIND_FILTER.get(kind, "")
    args: list = [step, status]
    if min_conf:
        sql += " AND g.score >= ?"
        args.append(min_conf)
    if q:
        sql += (" AND g.id IN (SELECT gm2.group_id FROM group_members gm2 JOIN files f2 ON f2.id = gm2.file_id "
                "WHERE f2.name LIKE ? OR f2.rel_path LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    return sql, args


def _effective(con) -> tuple[dict, dict]:
    caps = {r["file_id"]: r["capture_id"] for r in con.execute("SELECT file_id, capture_id FROM captures")}
    return caps, harmonize.effective_values(con)


def _member_rows(con, group_ids: list[int], step: int) -> dict[int, list[dict]]:
    if not group_ids:
        return {}
    marks = ",".join("?" * len(group_ids))
    rows = con.execute(
        f"""SELECT gm.group_id, gm.role, gm.score, gm.evidence, f.id, f.name, f.rel_path, f.size, f.kind, f.mtime,
               s.label AS source, s.writable, s.role AS source_role, m.dto, m.make, m.model, m.software,
               m.width, m.height, m.duration, m.gps_lat, m.gps_lon, (sel.file_id IS NOT NULL) AS selected
            FROM group_members gm JOIN files f ON f.id = gm.file_id JOIN sources s ON s.id = f.source_id
            LEFT JOIN meta m ON m.file_id = f.id LEFT JOIN selection sel ON sel.file_id = f.id
            WHERE gm.group_id IN ({marks})
            ORDER BY gm.role = 'keeper' DESC, f.rel_path""",
        group_ids,
    ).fetchall()
    caps, values = _effective(con) if step == 4 else ({}, {})
    out: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        m = dict(r)
        m["evidence"] = json.loads(m["evidence"] or "{}")
        parent = PurePosixPath(m["rel_path"]).parent
        m["crumbs"] = [m["source"]] + [p for p in parent.parts if p not in (".", "")]
        if step == 4:  # step 4 shows the harmonized values it matched on
            v = values.get(caps.get(m["id"]), {})
            m["date"], m["device"], m["place"] = v.get("capture_time", ""), v.get("device_name", ""), v.get("location_name", "")
        else:
            m["date"] = m["dto"] or ""
            m["device"] = device_name(m["make"], m["model"])
            m["place"] = geo.place(m["gps_lat"], m["gps_lon"]).get("city", "") if m["gps_lat"] is not None else ""
        out[m["group_id"]].append(m)
    return out


def load(lib: Library, step: int, *, status: str = "open", kind: str = "", q: str = "", sort: str = "size",
         desc: bool = True, min_conf: float = 0.0, offset: int = 0, limit: int = 100,
         group_ids: list[int] | None = None) -> tuple[list[dict], int]:
    """One page of groups with their members, sorted on group-level keys."""
    with lib.connect() as con:
        if group_ids is not None:
            marks = ",".join("?" * len(group_ids)) or "NULL"
            heads = con.execute(f"SELECT g.* FROM groups g WHERE g.id IN ({marks})", group_ids).fetchall()
        else:
            where, args = _where(step, status, kind, q, min_conf)
            heads = con.execute("SELECT g.* FROM groups g" + where, args).fetchall()
        ids = [h["id"] for h in heads]
        members = _member_rows(con, ids, step)
    groups = []
    for h in heads:
        g = dict(h)
        g["members"] = members.get(g["id"], [])
        if not g["members"]:
            continue
        keeper = next((m for m in g["members"] if m["role"] == "keeper"), g["members"][0])
        g["keeper"] = keeper
        g["total"] = sum(m["size"] or 0 for m in g["members"])
        g["selected"] = sum(1 for m in g["members"] if m["selected"])
        for m in g["members"]:  # highlight only what differs from the kept copy
            m["diff"] = set() if m is keeper else {
                k for k in ("date", "device", "place", "size", "name") if (m[k] or "") != (keeper[k] or "")}
        groups.append(g)
    key = {
        "size": lambda g: g["total"],
        "qty": lambda g: len(g["members"]),
        "name": lambda g: g["keeper"]["name"].lower(),
        "date": lambda g: g["keeper"]["date"] or "",
        "device": lambda g: (g["keeper"]["device"] or "").lower(),
        "place": lambda g: (g["keeper"]["place"] or "").lower(),
        "confidence": lambda g: g["score"] or 0,
    }.get(sort, lambda g: g["total"])
    groups.sort(key=lambda g: (key(g), -g["id"]), reverse=desc)
    return groups[offset:offset + limit], len(groups)


def folders(lib: Library, step: int, kind: str = "") -> list[tuple[str, int]]:
    """Folders that hold copies in open groups, most copies first: choices for 'keep copies in'."""
    with lib.connect() as con:
        rows = con.execute(
            "SELECT s.label, f.rel_path FROM groups g JOIN group_members gm ON gm.group_id = g.id "
            "JOIN files f ON f.id = gm.file_id JOIN sources s ON s.id = f.source_id"
            " WHERE g.step = ? AND g.status = 'open'" + KIND_FILTER.get(kind, ""), (step,)).fetchall()
    count: dict[str, int] = defaultdict(int)
    for r in rows:
        count[_folder_key(r["label"], r["rel_path"])] += 1
    return sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))[:60]


def _folder_key(label: str, rel_path: str) -> str:
    parent = str(PurePosixPath(rel_path).parent)
    return f"{label}/{parent}" if parent != "." else label


def _in_folder(label: str, rel_path: str, folder: str) -> bool:
    key = _folder_key(label, rel_path)
    return key == folder or key.startswith(folder + "/")


# ───────────────────────── selection ─────────────────────────

def toggle(lib: Library, file_id: int, group_id: int, step: int, on: bool) -> None:
    with lib.connect() as con:
        if on:
            con.execute("INSERT OR REPLACE INTO selection(file_id, group_id, step) VALUES (?, ?, ?)", (file_id, group_id, step))
        else:
            con.execute("DELETE FROM selection WHERE file_id = ?", (file_id,))


def _set_keeper(con, gid: int, fid: int) -> None:
    con.execute("UPDATE group_members SET role = CASE file_id WHEN ? THEN 'keeper' ELSE 'member' END WHERE group_id = ?",
                (fid, gid))
    con.execute("UPDATE groups SET keeper_file_id = ?, reclaimable = (SELECT COALESCE(SUM(f.size), 0) FROM group_members gm "
                "JOIN files f ON f.id = gm.file_id WHERE gm.group_id = ? AND gm.file_id != ?) WHERE id = ?",
                (fid, gid, fid, gid))


def set_keeper(lib: Library, gid: int, fid: int) -> None:
    with lib.connect() as con, tx(con):
        _set_keeper(con, gid, fid)
        con.execute("DELETE FROM selection WHERE file_id = ?", (fid,))  # the kept copy is never selected


def smart_select(lib: Library, step: int, mode: str, kind: str = "", folder: str = "") -> int:
    """Pick a keeper per open group by a rule, then select every other copy.
    Similar shots (bursts) are never selected automatically."""
    with lib.connect() as con, tx(con):
        con.execute("DELETE FROM selection WHERE step = ?", (step,))
        if mode == "none":
            return 0
        rows = con.execute(
            "SELECT g.id AS gid, gm.role, f.id, f.rel_path, f.size, f.mtime, s.label, m.width, m.height "
            "FROM groups g JOIN group_members gm ON gm.group_id = g.id JOIN files f ON f.id = gm.file_id "
            "JOIN sources s ON s.id = f.source_id LEFT JOIN meta m ON m.file_id = f.id "
            "WHERE g.step = ? AND g.status = 'open' AND g.verdict != 'similar_shot'" + KIND_FILTER.get(kind, ""),
            (step,)).fetchall()
        groups: dict[int, list] = defaultdict(list)
        for r in rows:
            groups[r["gid"]].append(r)
        n = 0
        for gid, ms in groups.items():
            keeper = next((m for m in ms if m["role"] == "keeper"), ms[0])
            if mode == "oldest":
                keeper = min(ms, key=lambda m: (m["mtime"] or 0, m["id"]))
            elif mode == "largest":
                keeper = max(ms, key=lambda m: ((m["width"] or 0) * (m["height"] or 0), m["size"] or 0, -m["id"]))
            elif mode == "folder":
                inside = [m for m in ms if _in_folder(m["label"], m["rel_path"], folder)]
                if not inside:
                    continue  # no copy in that folder: leave the group for a manual decision
                keeper = next((m for m in inside if m["role"] == "keeper"), inside[0])
            if keeper["role"] != "keeper":
                _set_keeper(con, gid, keeper["id"])
            for m in ms:
                if m["id"] != keeper["id"]:
                    con.execute("INSERT OR REPLACE INTO selection(file_id, group_id, step) VALUES (?, ?, ?)", (m["id"], gid, step))
                    n += 1
        return n


def summary(lib: Library, step: int) -> dict:
    with lib.connect() as con:
        r = con.execute(
            "SELECT COUNT(*) AS files, COALESCE(SUM(f.size), 0) AS bytes, COUNT(DISTINCT sel.group_id) AS groups "
            "FROM selection sel JOIN files f ON f.id = sel.file_id WHERE sel.step = ?", (step,)).fetchone()
        whole = con.execute(
            "SELECT COUNT(*) FROM (SELECT sel.group_id FROM selection sel WHERE sel.step = ? GROUP BY sel.group_id "
            "HAVING COUNT(*) >= (SELECT COUNT(*) FROM group_members gm WHERE gm.group_id = sel.group_id))", (step,)).fetchone()[0]
    return {"files": r["files"], "bytes": r["bytes"], "groups": r["groups"], "whole_groups": whole}


def selected_by_group(lib: Library, step: int) -> dict[int, list[int]]:
    with lib.connect() as con:
        out: dict[int, list[int]] = defaultdict(list)
        for r in con.execute("SELECT group_id, file_id FROM selection WHERE step = ?", (step,)):
            out[r["group_id"]].append(r["file_id"])
    return out


def clear(lib: Library, step: int) -> None:
    with lib.connect() as con:
        con.execute("DELETE FROM selection WHERE step = ?", (step,))
