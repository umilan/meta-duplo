"""Duplicate matching for workflow steps 1, 2 and 4.

Step 1  identical files: size → partial → full BLAKE3 (100 %).
Step 2  near duplicates: same pixels, resized/recompressed, edited, similar shots.
Step 4  re-check: the same matching, but on harmonized time / place / device.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import PurePath

from .db import Library, tx

DUP_VERDICTS = {"identical", "same_pixels", "same_photo", "edited", "same_video"}
REENCODERS = ("whatsapp", "facebook", "instagram", "telegram", "messenger", "snapseed")
COPY_WORDS = ("backup", "copy", "export", "download", "whatsapp", "telegram", "edited", "kopie", "zaloha")


@dataclass
class F:
    id: int
    source_id: int
    rel_path: str
    name: str
    original_name: str
    ext: str
    kind: str
    size: int
    mtime: float
    inode: int | None
    role: str
    removed: int
    width: int | None
    height: int | None
    duration: float | None
    make: str | None
    model: str | None
    software: str | None
    dto: str | None
    gps_lat: float | None
    gps_lon: float | None
    full: str | None
    content: str | None
    dhash: int | None
    phash: int | None

    @property
    def device(self) -> str:
        from .metadata import device_name

        return device_name(self.make, self.model)

    @property
    def pixels(self) -> int:
        return (self.width or 0) * (self.height or 0)

    @property
    def key(self) -> str:
        """Content-based identity for decisions: survives renames and moves."""
        return self.content or self.full or f"file:{self.id}"

    def reencoded(self) -> bool:
        sw = (self.software or "").lower()
        return any(r in sw for r in REENCODERS) or "-WA" in self.name.upper()

    def in_copy_folder(self) -> bool:
        """Backups, exports, downloads and renamed copies rank below camera folders."""
        p = self.rel_path.lower()
        return any(w in p for w in COPY_WORDS) or self.name.lower().startswith("copy of")


FILE_SQL = """
SELECT f.id, f.source_id, f.rel_path, f.name, f.original_name, f.ext, f.kind, f.size, f.mtime, f.inode,
       s.role, f.removed, m.width, m.height, m.duration, m.make, m.model, m.software, m.dto,
       m.gps_lat, m.gps_lon, h.full, h.content, p.dhash, p.phash
FROM files f JOIN sources s ON s.id = f.source_id
LEFT JOIN meta m ON m.file_id = f.id LEFT JOIN hashes h ON h.file_id = f.id
LEFT JOIN fingerprints p ON p.file_id = f.id
WHERE f.kind IN ('image', 'video') AND f.present = 1
"""


def load_files(con, include_removed: bool = False) -> list[F]:
    sql = FILE_SQL + ("" if include_removed else " AND f.removed = 0")
    out = []
    for r in con.execute(sql):
        d = dict(r)
        d["dhash"] = int(d["dhash"], 16) if d["dhash"] else None
        d["phash"] = int(d["phash"], 16) if d["phash"] else None
        out.append(F(**d))
    return out


def keeper_key(f: F) -> tuple:
    """Default keeper rules, best first: reference folder, original over re-encoded,
    has a capture time, more pixels, larger file, GPS, not in a backup/export folder,
    older, shorter path."""
    return (
        f.role == "reference",
        not f.reencoded(),
        f.dto is not None,
        f.pixels,
        f.size or 0,
        f.gps_lat is not None,
        not f.in_copy_folder(),
        -(f.mtime or 0),
        -len(f.rel_path),
        -f.id,
    )


def _ts(s: str | None) -> datetime | None:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S") if s else None


def _ham(a: int | None, b: int | None) -> int | None:
    return None if a is None or b is None else (a ^ b).bit_count()


def _stem(name: str) -> str:
    s = PurePath(name).stem.lower()
    for suffix in (" copy", "-edited", "_edited", " (1)", " (2)", " (3)", "_1", "_2", "-1", "-2"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s


def _decided(con) -> tuple[set[str], set[str]]:
    pairs, groups = set(), set()
    for r in con.execute("SELECT scope, subject, decision FROM decisions"):
        if r["scope"] == "pair" and r["decision"] == "not_duplicate":
            pairs.add(r["subject"])
        elif r["scope"] == "group" and r["decision"] == "ignore":
            groups.add(r["subject"])
    return pairs, groups


def pair_subject(a: F, b: F) -> str:
    return "|".join(sorted([a.key, b.key]))


def _known_avg(parts: list[tuple[float, float | None]]) -> float:
    """Weighted mean over the signals that are known; unknown signals neither help nor hurt."""
    known = [(w, s) for w, s in parts if s is not None]
    total = sum(w for w, _ in known)
    return sum(w * s for w, s in known) / total if total else 0.0


# ───────────────────────── pair scoring (step 2) ─────────────────────────

def compare(a: F, b: F) -> tuple[float, str | None, dict]:
    ev: dict = {}
    ta, tb = _ts(a.dto), _ts(b.dto)
    dt = abs((ta - tb).total_seconds()) if ta and tb else None
    ev["time_delta_s"] = dt
    dev = None
    if a.device and b.device:
        dev = 1.0 if a.device == b.device else 0.0
    ev["same_device"] = dev
    if a.kind == "video" or b.kind == "video":
        if a.kind != b.kind or not a.duration or not b.duration:
            return 0.0, None, ev
        same_len = abs(a.duration - b.duration) <= max(1.0, 0.01 * max(a.duration, b.duration))
        ev["duration"] = [a.duration, b.duration]
        if same_len and dt is not None and dt <= 2 and dev != 0.0:
            return 0.9 if dev else 0.85, "same_video", ev
        return 0.0, None, ev
    if a.content and a.content == b.content:
        ev["visual"] = 1.0
        return 0.99, "same_pixels", ev
    dd, pd = _ham(a.dhash, b.dhash), _ham(a.phash, b.phash)
    if dd is None or pd is None:
        return 0.0, None, ev
    dist = min(dd, pd)
    visual = 1 - dist / 64
    ev.update(visual=round(visual, 3), dhash_dist=dd, phash_dist=pd)
    if a.width and b.width and a.height and b.height:
        ra, rb = a.width / a.height, b.width / b.height
        geometry = 1.0 if abs(ra - rb) / max(ra, rb) <= 0.02 else 0.0
        ev["size_ratio"] = round(min(a.pixels, b.pixels) / max(a.pixels, b.pixels), 3)
    else:
        geometry = None
    ev["same_aspect"] = geometry
    tsig = None if dt is None else (1.0 if dt == 0 else math.exp(-dt / 5))
    score = _known_avg([(0.6, visual), (0.2, tsig), (0.1, dev), (0.1, geometry)])
    if _stem(a.name) == _stem(b.name):
        score += 0.03
        ev["same_name"] = True
    score = min(score, 1.0)
    verdict = None
    if dev == 0.0 and dt is not None and dt > 60:
        return min(score, 0.7), None, ev  # two people photographing the same thing
    originals = not a.reencoded() and not b.reencoded()
    if dt is not None and dt > 0 and dev == 1.0 and originals:
        # two camera-original frames from one device at different seconds: a burst, never a duplicate
        return (round(score, 3), "similar_shot", ev) if visual >= 0.80 and dt <= 10 else (round(score, 3), None, ev)
    if visual >= 0.97 and (dt is None or dt <= 2):
        verdict = "same_photo"
        if dt is None and visual < 0.94:
            verdict = None
    elif visual >= 0.90 and dt is not None and dt <= 2 and geometry == 0.0:
        verdict = "edited"
    elif visual >= 0.80 and dt is not None and 0 < dt <= 10 and dev == 1.0:
        verdict = "similar_shot"
    elif visual >= 0.94 and dt is None:
        verdict = "same_photo"
    return round(score, 3), verdict, ev


def _candidate_pairs(files: list[F]) -> set[tuple[int, int]]:
    """Content-hash buckets plus multi-index hashing on 16-bit chunks of dHash/pHash.
    Small libraries also get all pairs; large ones rely on the buckets (no N²)."""
    pairs: set[tuple[int, int]] = set()
    imgs = [f for f in files if f.kind == "image"]
    vids = [f for f in files if f.kind == "video"]
    if len(imgs) <= 2500:
        pairs.update((a.id, b.id) for a, b in combinations(imgs, 2))
    else:
        buckets: dict[tuple, list[int]] = defaultdict(list)
        for f in imgs:
            if f.content:
                buckets[("c", f.content)].append(f.id)
            for name, h in (("d", f.dhash), ("p", f.phash)):
                if h is not None:
                    for i in range(4):
                        buckets[(name, i, (h >> (16 * i)) & 0xFFFF)].append(f.id)
        for ids in buckets.values():
            if 1 < len(ids) <= 200:
                pairs.update(combinations(sorted(ids), 2))
    vids.sort(key=lambda f: f.duration or 0)
    for i, a in enumerate(vids):  # sweep by duration
        for b in vids[i + 1:]:
            if (b.duration or 0) - (a.duration or 0) > max(1.0, 0.01 * (b.duration or 0)):
                break
            pairs.add((a.id, b.id))
    return {(min(p), max(p)) for p in pairs}


# ───────────────────────── grouping ─────────────────────────

def _save_groups(con, step: int, groups: list[dict]) -> None:
    """Replace a step's groups, carrying over statuses of groups with the same signature."""
    old = {
        r["signature"]: r["status"]
        for r in con.execute("SELECT signature, status FROM groups WHERE step = ? AND status != 'open'", (step,))
    }
    con.execute("DELETE FROM groups WHERE step = ?", (step,))
    con.execute("DELETE FROM selection WHERE step = ?", (step,))  # group ids change on a rebuild
    for g in groups:
        sig = ",".join(str(i) for i in sorted(m["file"].id for m in g["members"]))
        status = old.get(sig, "open")
        gid = con.execute(
            "INSERT INTO groups(step, verdict, score, keeper_file_id, status, signature, reclaimable) VALUES (?,?,?,?,?,?,?)",
            (step, g["verdict"], g["score"], g["keeper"].id, status, sig,
             sum(m["file"].size or 0 for m in g["members"] if m["file"].id != g["keeper"].id)),
        ).lastrowid
        for m in g["members"]:
            con.execute(
                "INSERT INTO group_members(group_id, file_id, role, score, evidence) VALUES (?,?,?,?,?)",
                (gid, m["file"].id, "keeper" if m["file"].id == g["keeper"].id else "member",
                 m.get("score"), json.dumps(m.get("evidence") or {})),
            )


def _anchor_groups(files: list[F], edges: dict[tuple[int, int], tuple], accept, ignored: set[str]) -> list[dict]:
    """Deterministic anchor grouping: best keeper first; members must match the anchor itself
    (no chaining A≈B≈C), one group per file."""
    by_id = {f.id: f for f in files}
    nbrs: dict[int, list[tuple[int, tuple]]] = defaultdict(list)
    for (a, b), e in edges.items():
        if accept(e):
            nbrs[a].append((b, e))
            nbrs[b].append((a, e))
    assigned: set[int] = set()
    groups = []
    for f in sorted((by_id[i] for i in nbrs), key=keeper_key, reverse=True):
        if f.id in assigned:
            continue
        mates = [(by_id[o], e) for o, e in nbrs[f.id] if o not in assigned]
        if not mates:
            continue
        members = [{"file": f, "score": 1.0, "evidence": {}}] + [
            {"file": o, "score": e[0], "evidence": e[2]} for o, e in sorted(mates, key=lambda x: -x[1][0])
        ]
        sig = ",".join(str(i) for i in sorted(m["file"].id for m in members))
        if sig in ignored:
            continue
        assigned.update(m["file"].id for m in members)
        verdicts = [e[1] for _, e in mates]
        groups.append({
            "keeper": f,
            "members": members,
            "verdict": max(set(verdicts), key=verdicts.count),
            "score": round(min(e[0] for _, e in mates), 3),
        })
    return groups


def step1_exact(lib: Library, job=None) -> int:
    with lib.connect() as con:
        files = load_files(con)
        _, ignored = _decided(con)
        by_hash: dict[str, list[F]] = defaultdict(list)
        for f in files:
            if f.full:
                by_hash[f.full].append(f)
        groups = []
        for members in by_hash.values():
            uniq: dict[tuple, F] = {}
            for f in members:  # hardlinks share an inode: one copy on disk, not a duplicate
                uniq.setdefault((f.source_id, f.inode) if f.inode else ("id", f.id), f)
            members = list(uniq.values())
            if len(members) < 2:
                continue
            keeper = max(members, key=keeper_key)
            sig = ",".join(str(i) for i in sorted(m.id for m in members))
            if sig in ignored:
                continue
            groups.append({
                "keeper": keeper, "verdict": "identical", "score": 1.0,
                "members": [{"file": m, "score": 1.0, "evidence": {"full_hash": m.full[:16]}} for m in
                            sorted(members, key=keeper_key, reverse=True)],
            })
        with tx(con):
            _save_groups(con, 1, groups)
    if job:
        job.update("step 1: identical", len(groups), len(groups), f"{len(groups)} identical groups")
    return len(groups)


def step2_near(lib: Library, job=None) -> int:
    threshold = float(lib.setting("near_threshold"))
    with lib.connect() as con:
        files = load_files(con)
        not_dup, ignored = _decided(con)
        in_exact_non_keeper = {
            r["file_id"] for r in con.execute(
                "SELECT gm.file_id FROM group_members gm JOIN groups g ON g.id = gm.group_id "
                "WHERE g.step = 1 AND gm.role != 'keeper'")
        }
        reps = [f for f in files if f.id not in in_exact_non_keeper]  # one representative per identical group
        by_id = {f.id: f for f in reps}
        edges = {}
        cands = _candidate_pairs(reps)
        for i, (a, b) in enumerate(sorted(cands)):
            if job and i % 2000 == 0:
                job.update("step 2: comparing", i, len(cands))
            fa, fb = by_id[a], by_id[b]
            if pair_subject(fa, fb) in not_dup:
                continue
            score, verdict, ev = compare(fa, fb)
            if verdict:
                edges[(a, b)] = (score, verdict, ev)
        dup = _anchor_groups(reps, edges, lambda e: e[1] in DUP_VERDICTS and e[0] >= threshold, ignored)
        in_dup = {m["file"].id for g in dup for m in g["members"] if m["file"].id != g["keeper"].id}
        bursts = _anchor_groups(
            [f for f in reps if f.id not in in_dup],
            {k: v for k, v in edges.items() if k[0] not in in_dup and k[1] not in in_dup},
            lambda e: e[1] == "similar_shot", ignored,
        )
        for g in bursts:
            g["verdict"] = "similar_shot"
        with tx(con):
            _save_groups(con, 2, dup + bursts)
    if job:
        job.update("step 2: near", len(dup), len(dup), f"{len(dup)} near-duplicate groups, {len(bursts)} similar-shot groups")
    return len(dup)


# ───────────────────────── captures (input to step 3) ─────────────────────────

def build_captures(lib: Library) -> None:
    """A capture = files that are the same photo/video: identical (step 1) plus near-duplicate
    (step 2) groups the user has not rejected. Removed copies stay in, as evidence."""
    with lib.connect() as con:
        ids = [r["id"] for r in con.execute(
            "SELECT id FROM files WHERE kind IN ('image', 'video') AND (present = 1 OR removed = 1)")]
        parent = {i: i for i in ids}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            if a in parent and b in parent:
                ra, rb = find(a), find(b)
                parent[max(ra, rb)] = min(ra, rb)

        for r in con.execute(
            "SELECT g.keeper_file_id, gm.file_id FROM groups g JOIN group_members gm ON gm.group_id = g.id "
            "WHERE g.step IN (1, 2) AND g.verdict IN ('identical', 'same_pixels', 'same_photo', 'edited', 'same_video') "
            "AND g.status NOT IN ('not_duplicate', 'ignored')"
        ):
            union(r["keeper_file_id"], r["file_id"])
        # removed copies are no longer in any group: the hold action remembers which keeper they duplicated
        for r in con.execute("SELECT file_id, payload FROM actions WHERE op = 'hold' AND status = 'done'"):
            keeper = json.loads(r["payload"] or "{}").get("keeper_id")
            if keeper:
                union(keeper, r["file_id"])
        with tx(con):
            con.execute("DELETE FROM captures")
            con.executemany("INSERT INTO captures(file_id, capture_id) VALUES (?, ?)", [(i, find(i)) for i in ids])


# ───────────────────────── step 4: re-check on harmonized metadata ─────────────────────────

def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def step4_recheck(lib: Library, job=None) -> int:
    from .harmonize import effective_values

    min_conf = float(lib.setting("recheck_min_confidence"))
    with lib.connect() as con:
        files = {f.id: f for f in load_files(con)}
        not_dup, ignored = _decided(con)
        caps: dict[int, list[F]] = defaultdict(list)
        for r in con.execute("SELECT file_id, capture_id FROM captures"):
            if r["file_id"] in files:
                caps[r["capture_id"]].append(files[r["file_id"]])
        values = effective_values(con)
    reps = {cid: max(fs, key=keeper_key) for cid, fs in caps.items() if fs}
    eff = {cid: values.get(cid, {}) for cid in reps}

    def t(cid):
        return _ts(eff[cid].get("capture_time"))

    cands: set[tuple[int, int]] = set()
    timed = sorted((c for c in reps if t(c)), key=t)
    for i, a in enumerate(timed):  # same harmonized time (±2 s)
        for b in timed[i + 1:]:
            if (t(b) - t(a)).total_seconds() > 2:
                break
            cands.add((min(a, b), max(a, b)))
    rep_files = list(reps.values())
    rep_cap = {f.id: cid for cid, f in reps.items()}
    for a, b in _candidate_pairs(rep_files):  # visual neighbours
        cands.add((min(rep_cap[a], rep_cap[b]), max(rep_cap[a], rep_cap[b])))

    edges = {}
    for ca, cb in cands:
        fa, fb = reps[ca], reps[cb]
        if pair_subject(fa, fb) in not_dup:
            continue
        if (fa.dto and fb.dto and fa.dto != fb.dto and fa.device and fa.device == fb.device
                and not fa.reencoded() and not fb.reencoded()):
            continue  # burst frames: siblings, never duplicates
        ea, eb = eff[ca], eff[cb]
        ev: dict = {}
        visual = None
        if fa.kind == fb.kind == "image":
            dists = [d for d in (_ham(fa.dhash, fb.dhash), _ham(fa.phash, fb.phash)) if d is not None]
            d = min(dists, default=None)
            visual = None if d is None else 1 - d / 64
        ta, tb = t(ca), t(cb)
        dt = abs((ta - tb).total_seconds()) if ta and tb else None
        tsig = None if dt is None else (1.0 if dt <= 2 else math.exp(-dt / 30))
        dev = None
        if ea.get("device_name") and eb.get("device_name"):
            dev = 1.0 if ea["device_name"] == eb["device_name"] else 0.0
        loc = None
        if ea.get("location_name") and eb.get("location_name"):
            loc = 1.0 if ea["location_name"] == eb["location_name"] else 0.0
        if ea.get("lat") is not None and eb.get("lat") is not None:
            loc = 1.0 if _distance_m((ea["lat"], ea["lon"]), (eb["lat"], eb["lon"])) < 200 else 0.0
        name = SequenceMatcher(None, _stem(fa.original_name), _stem(fb.original_name)).ratio()
        size = min(fa.size, fb.size) / max(fa.size, fb.size) if fa.size and fb.size else None
        strong = (visual is not None and visual >= 0.8) or (dt is not None and dt <= 2 and (dev == 1.0 or loc == 1.0))
        if not strong:
            continue
        conf = _known_avg([(0.45, visual), (0.2, tsig), (0.1, dev), (0.1, loc), (0.1, name), (0.05, size)])
        if visual is None:
            conf *= 0.9  # without a picture comparison, never claim near-certainty
        ev = {"visual": None if visual is None else round(visual, 3), "time_delta_s": dt, "same_device": dev,
              "same_place": loc, "name_similarity": round(name, 2), "size_ratio": None if size is None else round(size, 3)}
        if conf >= min_conf:
            edges[(fa.id, fb.id)] = (round(conf, 3), "likely", ev)
    groups = _anchor_groups(rep_files, edges, lambda e: True, ignored)
    with lib.connect() as con, tx(con):
        _save_groups(con, 4, groups)
    if job:
        job.update("step 4: re-check", len(groups), len(groups), f"{len(groups)} potential duplicate groups")
    return len(groups)
