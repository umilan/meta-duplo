"""Results table: server-side selection, smart select and the removal plan built from it."""
from __future__ import annotations

import pytest

from meta_duplo import plans
from meta_duplo.__main__ import add_source
from meta_duplo.db import Library
from meta_duplo.demo import make_demo
from meta_duplo.scan import Runner, scan_all
from meta_duplo.web import results


@pytest.fixture()
def lib(tmp_path):
    disk = make_demo(tmp_path / "disk", extra=12)
    lib = Library(tmp_path / "lib")
    add_source(lib, str(disk), writable=True)
    Runner(lib).run_sync("scan", lambda job: scan_all(lib, job))
    return lib


def test_load_sorts_and_pages(lib):
    groups, total = results.load(lib, 1, sort="size", desc=True, limit=5)
    assert total == 17 and len(groups) == 5  # 5 demo groups + 12 extra photos with a backup copy
    assert [g["total"] for g in groups] == sorted((g["total"] for g in groups), reverse=True)
    page2, _ = results.load(lib, 1, sort="size", desc=True, offset=5, limit=5)
    assert not {g["id"] for g in groups} & {g["id"] for g in page2}
    found, n = results.load(lib, 1, q="christmas")
    assert n == 1 and any(m["name"] == "christmas.jpg" for m in found[0]["members"])


def test_smart_select_and_plan(lib):
    n = results.smart_select(lib, 1, "keeper")
    s = results.summary(lib, 1)
    assert n == s["files"] == 18 and s["whole_groups"] == 0

    # keep the copy in the backup folder instead: keepers move, the selection follows
    results.smart_select(lib, 1, "folder", folder="disk/Backup 2019")
    groups, _ = results.load(lib, 1, q="IMG_0003")
    keeper = groups[0]["keeper"]
    assert keeper["rel_path"].startswith("Backup 2019/") and not keeper["selected"]

    chosen = results.selected_by_group(lib, 1)
    pid = plans.build_cleanup_plan(lib, list(chosen), remove_file_ids={f for fs in chosen.values() for f in fs})
    res = plans.apply_plan(lib, pid)
    assert res["failed"] == 0 and res["done"] == sum(len(v) for v in chosen.values())


def test_selecting_every_copy_is_blocked(lib):
    groups, _ = results.load(lib, 1, q="christmas")
    g = groups[0]
    for m in g["members"]:
        results.toggle(lib, m["id"], g["id"], 1, True)
    assert results.summary(lib, 1)["whole_groups"] == 1
    pid = plans.build_cleanup_plan(lib, [g["id"]], remove_file_ids={m["id"] for m in g["members"]})
    with lib.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM actions WHERE plan_id = ?", (pid,)).fetchone()[0] == 0
