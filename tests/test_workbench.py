"""Workbench model: grouping, differences against the kept copy, filters, edit rules, commit."""
from __future__ import annotations

import pytest

from meta_duplo import harmonize, plans
from meta_duplo.__main__ import add_source
from meta_duplo.db import Library
from meta_duplo.demo import make_demo
from meta_duplo.scan import Runner, scan_all
from meta_duplo.web import workbench as wb


@pytest.fixture()
def lib(tmp_path):
    disk = make_demo(tmp_path / "disk")
    lib = Library(tmp_path / "lib")
    add_source(lib, str(disk), writable=True)
    Runner(lib).run_sync("scan", lambda job: scan_all(lib, job))
    harmonize.build_proposals(lib)
    return lib


def _group_with(groups, name):
    return next(g for g in groups if any(r["v"]["filename"] == name for r in g.rows))


def test_duplicates_grouping_and_differences(lib):
    view = wb.load_view(lib)
    groups, exif_keys, n = wb.table(lib, view)
    g = _group_with(groups, "Copy of IMG_0003.JPG")
    assert len(g.rows) == 3 and g.ref["v"]["path"].endswith("Camera/DCIM")
    copy = next(r for r in g.rows if r["v"]["filename"] == "Copy of IMG_0003.JPG")
    assert copy["diff"]["filename"] and not copy["diff"]["date"]
    assert wb.conf_label(copy) == "100 %"
    pc = _group_with(groups, "prague castle.jpg")  # same pixels, edited date
    castle = next(r for r in pc.rows if r["v"]["filename"] == "prague castle.jpg")
    assert wb.conf_label(castle).startswith("99 %") and castle["diff"]["date"]
    assert "IFD0:Artist" in exif_keys and "GPS:GPSDateStamp" in exif_keys


def test_filters_and_grouping_modes(lib):
    view = wb.load_view(lib)
    view["filters"] = [{"key": "dup_conf", "op": "gte", "value": "100"}]
    groups, _, _ = wb.table(lib, view)
    assert groups and all(any(r["v"]["dup_conf"] == 100 for r in g.rows) for g in groups)
    view = wb.load_view(lib)
    view.update(group="none", filters=[{"key": "date", "op": "empty", "value": ""}])
    groups, _, n = wb.table(lib, view)
    names = {r["v"]["filename"] for g in groups for r in g.rows}
    assert "IMG-20190515-WA0003.jpg" in names and "IMG_0001.JPG" not in names


def test_edit_rules_commit_and_partial_rename(lib):
    view = wb.load_view(lib)
    view.update(group="none")
    assert view["edits"] == {}  # no purple columns until the user asks for one
    view["edits"]["date"] = {"kind": "shift", "column": "date", "seconds": 3600}
    view["edits"]["filename"] = {"kind": "pattern"}
    groups, _, _ = wb.table(lib, view)
    r = next(r for g in groups for r in g.rows if r["v"]["filename"] == "IMG_4102.JPG")
    assert r["e"]["date"] == "2018-12-24 18:31:12"
    assert r["e"]["filename"].startswith("2018-12-24 18-31-12 (Canon EOS 5D Mark III)")
    assert wb.commit(lib, view, {r["id"]}) == 1
    names = {p["file_id"]: p["new"] for p in harmonize.rename_preview(lib)}
    assert names[r["id"]].startswith("2018-12-24 18-31-12")
    pid = plans.build_rename_plan(lib, write_xmp=False, only_ids={r["id"]})
    with lib.connect() as con:
        assert [a["file_id"] for a in con.execute("SELECT file_id FROM actions WHERE plan_id = ? AND op = 'rename'", (pid,))] == [r["id"]]


def test_old_views_lose_default_edit_columns(lib):
    import json
    lib.set_setting("workbench_view", json.dumps({"columns": [["path", 200], ["edit:filename", 300]],
                                                  "edits": {"filename": {"kind": "pattern"}}, "group": "device"}))
    view = wb.load_view(lib)
    assert view["columns"] == [["path", 200]] and view["edits"] == {} and view["group"] == "device"


def test_selection_presets(lib):
    view = wb.load_view(lib)
    groups, _, nrows = wb.table(lib, view)
    ids = {r["v"]["filename"]: r["id"] for g in groups for r in g.rows}
    newer, older, less = (set(wb.preset_ids(lib, view, p)) for p in ("dup_newer", "dup_older", "dup_lessmeta"))
    assert len(wb.preset_ids(lib, view, "all")) == nrows
    # same pixels, edited date: newer ticks the later copy, older ticks the original
    assert ids["prague castle.jpg"] in newer and ids["prague castle.jpg"] not in older
    assert ids["IMG_0004.JPG"] in older and ids["IMG_0004.JPG"] not in newer
    assert ids["IMG-20190515-WA0003.jpg"] in less  # WhatsApp copy lost its EXIF
    for g in groups:  # every preset keeps at least one copy of each duplicate set
        for picked in (newer, older, less):
            assert any(r["id"] not in picked for r in g.rows)
