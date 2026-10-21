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
    wb.set_group(view, "none")  # as the Grouping picker does: drops the automatic duplicates chip
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
    assert view["edits"] == {} and view["group"] == "device"
    assert view["columns"] == [["path", 200], ["key_dto", 168], ["key_created", 168]]  # the key dates are added


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


def test_duplicates_filter_chip_and_singles_group(lib):
    view = wb.load_view(lib)
    assert view["group"] == "dups" and view["filters"] == [wb.DUP_FILTER]
    groups, _, _ = wb.table(lib, view)
    assert all(g.key != wb.SINGLES for g in groups)  # the chip hides files without copies
    view["filters"] = []  # chip removed: they come back as one group at the bottom
    groups, _, _ = wb.table(lib, view)
    assert groups[-1].key == wb.SINGLES and groups[-1].label == "No duplicates"
    assert all(r["dup"] is None and not any(r["diff"].values()) for r in groups[-1].rows)
    wb.set_group(view, "folder")
    assert view["filters"] == []
    wb.set_group(view, "dups")
    assert view["filters"] == [wb.DUP_FILTER]
    wb.set_group(view, "folder")
    assert view["filters"] == []  # the automatic chip goes away again


def test_columns_follow_the_picker_order():
    cols = [["thumb", 48], ["path", 248], ["filename", 192], ["date", 160], ["author", 128]]
    wb.insert_column(cols, "dims", 96)          # File: right after the filename
    wb.insert_column(cols, "dup_conf", 144)     # Duplicates: before the dates
    wb.insert_column(cols, "exif:GPS:GPSLatitude", 152)  # Location, after the dates
    assert [c[0] for c in cols] == ["thumb", "path", "filename", "dims", "dup_conf", "date",
                                    "exif:GPS:GPSLatitude", "author"]
    shuffled = [["author", 1], ["edit:date", 1], ["date", 1], ["path", 1], ["dup_count", 1]]
    shuffled.sort(key=lambda c: wb.column_rank(c[0]))
    assert [c[0] for c in shuffled] == ["path", "dup_count", "date", "edit:date", "author"]
    assert list(wb.catalog([]))[:3] == ["File", "Duplicates", "Dates"]


def test_meta_check_counts():
    tags = {"ExifIFD:DateTimeOriginal": "2019:05:18 11:47:10", "IFD0:DateTime": "x", "GPS:GPSLatitude": "48",
            "IFD0:Make": "Apple", "IFD0:Model": "iPhone", "IFD0:Artist": "A", "IFD0:Orientation": "1", "IFD0:Copyright": ""}
    assert wb.meta_counts(tags) == {"date": 2, "place": 1, "device": 2, "author": 1}


def test_key_date_fields_first_and_xmp_date_read(tmp_path):
    from PIL import Image

    from meta_duplo import metadata
    path = tmp_path / "x.jpg"
    xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           b'<rdf:Description xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" '
           b'photoshop:DateCreated="2019-05-18T11:47:10"/></rdf:RDF></x:xmpmeta>')
    Image.new("RGB", (8, 8)).save(path, "JPEG", xmp=xmp)
    assert metadata.read_image(path).raw["tags"]["XMP:DateCreated"] == "2019-05-18T11:47:10"
    cat = wb.catalog(["ExifIFD:DateTimeDigitized", "ExifIFD:DateTimeOriginal", "XMP:DateCreated"])
    assert [f.key for f in cat["Dates"]][:2] == ["key_dto", "key_created"]  # always there, first among the dates
    assert not any(f.key.endswith(("DateTimeOriginal", "DateCreated")) for f in cat["Dates"])  # not listed twice
    cols = [["thumb", 48], ["path", 248], ["date", 160]]
    wb.insert_column(cols, "key_created", 168)
    assert [c[0] for c in cols] == ["thumb", "path", "key_created", "date"]
    view = wb.load_view_from({"v": 4, "columns": [["thumb", 48], ["key_dto", 200], ["key_created", 168], ["path", 248], ["date", 160]]})
    assert view["columns"] == [["thumb", 48], ["path", 248], ["key_dto", 200], ["key_created", 168], ["date", 160]]
