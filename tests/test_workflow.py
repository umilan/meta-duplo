"""End-to-end: the four workflow steps on the generated demo disk, including apply and undo."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from meta_duplo import harmonize, match, plans
from meta_duplo.__main__ import add_source
from meta_duplo.db import Library
from meta_duplo.demo import make_demo
from meta_duplo.scan import Runner, scan_all


def _tree(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.md5(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


@pytest.fixture()
def lib(tmp_path):
    disk = make_demo(tmp_path / "disk")
    lib = Library(tmp_path / "lib")
    add_source(lib, str(disk), writable=True)
    Runner(lib).run_sync("scan", lambda job: scan_all(lib, job))
    lib.disk = disk
    return lib


def _groups(lib, step):
    with lib.connect() as con:
        rows = con.execute(
            "SELECT g.id, g.verdict, g.keeper_file_id, group_concat(f.rel_path, '|') AS members FROM groups g "
            "JOIN group_members gm ON gm.group_id = g.id JOIN files f ON f.id = gm.file_id "
            "WHERE g.step = ? GROUP BY g.id", (step,)).fetchall()
    return [dict(r) for r in rows]


def test_step1_identical(lib):
    groups = _groups(lib, 1)
    assert len(groups) == 5
    triple = next(g for g in groups if "Copy of IMG_0003.JPG" in g["members"])
    assert triple["members"].count("|") == 2
    with lib.connect() as con:
        keeper = con.execute("SELECT rel_path FROM files WHERE id = ?", (triple["keeper_file_id"],)).fetchone()
    assert keeper["rel_path"] == "Camera/DCIM/IMG_0003.JPG"  # camera folder beats the backup copy


def test_step2_near(lib):
    verdicts = {g["verdict"]: g["members"] for g in _groups(lib, 2)}
    assert "IMG-20190515-WA0003.jpg" in verdicts["same_photo"] or any(
        "WA0003" in g["members"] for g in _groups(lib, 2) if g["verdict"] == "same_photo")
    assert "prague castle.jpg" in verdicts["same_pixels"]
    assert "VID-20190721-WA0001.mp4" in verdicts["same_video"]
    assert "IMG_0011.JPG" in verdicts["similar_shot"]  # a burst is review-only, never a duplicate
    assert not any("_crop" in g["members"] for g in _groups(lib, 2))  # undated crop: left for step 4


def test_steps_3_and_4_and_plans(lib):
    disk = lib.disk
    before = _tree(disk)

    # step 1 cleanup: move identical copies to the holding folder
    ids = [g["id"] for g in _groups(lib, 1)]
    pid = plans.build_cleanup_plan(lib, ids)
    res = plans.apply_plan(lib, pid)
    assert res["failed"] == 0 and res["done"] == 6
    assert not (disk / "Backup 2019" / "Copy of IMG_0003.JPG").exists()
    assert (disk / "_duplicates").is_dir()

    # step 3: recommendations pool evidence from removed copies too
    harmonize.build_proposals(lib)
    with lib.connect() as con:
        wa = con.execute(
            "SELECT p.* FROM proposals p JOIN captures c ON c.capture_id = p.capture_id JOIN files f ON f.id = c.file_id "
            "WHERE f.name = 'IMG-20190515-WA0003.jpg' AND p.field = 'capture_time'").fetchone()
        crop = con.execute(
            "SELECT p.* FROM proposals p JOIN captures c ON c.capture_id = p.capture_id JOIN files f ON f.id = c.file_id "
            "WHERE f.name = 'IMG_20190518_114710_crop.jpg' AND p.field = 'capture_time'").fetchone()
    assert wa["value"] == "2019-05-14 19:05:41"  # from the original's EXIF, not the WhatsApp receive date
    assert crop["value"] == "2019-05-18 11:47:10" and crop["status"] == "suggested"

    harmonize.accept_above(lib, 0.7)
    names = {p["old"]: p["new"] for p in harmonize.rename_preview(lib)}
    assert names["IMG_0001.JPG"] == "2019-05-14 18-22-03 (Bratislava, Apple iPhone 12) - IMG_0001.jpg"
    assert names["IMG_4102.JPG"] == "2018-12-24 17-31-12 (Canon EOS 5D Mark III) - IMG_4102.jpg"

    # step 4: the crop is only found once its date is harmonized
    match.step4_recheck(lib)
    step4 = _groups(lib, 4)
    assert any("IMG_20190518_114710_crop.jpg" in g["members"] and "IMG_0003.JPG" in g["members"] for g in step4)
    assert not any("IMG_0011.JPG" in g["members"] for g in step4)  # bursts stay siblings in step 4 too

    # rename plan with sidecars, then undo everything
    rid = plans.build_rename_plan(lib, write_xmp=True, set_mtime=True)
    res = plans.apply_plan(lib, rid)
    assert res["failed"] == 0
    assert (disk / "Camera/DCIM/2019-05-14 18-22-03 (Bratislava, Apple iPhone 12) - IMG_0001.jpg").exists()
    assert (disk / "Camera/DCIM/2019-05-14 18-22-03 (Bratislava, Apple iPhone 12) - IMG_0001.xmp").exists()

    plans.undo_plan(lib, rid)
    plans.undo_plan(lib, pid)
    assert _tree(disk) == before  # every byte back where it was


def test_render_name_tidies_missing_parts():
    pat = harmonize.DEFAULT_PATTERN
    v = {"capture_time": "2020-01-02 03:04:05", "location_name": "", "device_name": ""}
    assert harmonize.render_name(pat, v, "IMG_1.JPG", "JPG") == "2020-01-02 03-04-05 - IMG_1.jpg"
    assert harmonize.render_name(pat, {"capture_time": ""}, "IMG_1.JPG", "JPG") is None
    v = {"capture_time": "2020-01-02 03:04:05", "location_name": "Wien", "device_name": ""}
    assert harmonize.render_name(pat, v, "a/b.jpg", "jpg").startswith("2020-01-02 03-04-05 (Wien) - b")


def test_read_only_source_blocks_writes(tmp_path):
    disk = make_demo(tmp_path / "disk")
    lib = Library(tmp_path / "lib")
    add_source(lib, str(disk), writable=False)
    Runner(lib).run_sync("scan", lambda job: scan_all(lib, job))
    pid = plans.build_cleanup_plan(lib, [g["id"] for g in _groups(lib, 1)])
    with lib.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM actions WHERE plan_id = ?", (pid,)).fetchone()[0] == 0


def test_excluded_files_keep_their_name(lib):
    harmonize.build_proposals(lib)
    harmonize.accept_above(lib, 0.7)
    with lib.connect() as con:
        fid = con.execute("SELECT id FROM files WHERE name = 'IMG_0001.JPG' AND rel_path LIKE 'Camera/%'").fetchone()["id"]
        con.execute("INSERT INTO rename_exclude(file_id) VALUES (?)", (fid,))
    preview = {p["file_id"]: p for p in harmonize.rename_preview(lib)}
    assert preview[fid]["status"] == "excluded" and preview[fid]["new"] == "IMG_0001.JPG"
    pid = plans.build_rename_plan(lib)
    with lib.connect() as con:
        assert not con.execute("SELECT 1 FROM actions WHERE plan_id = ? AND file_id = ?", (pid, fid)).fetchone()
