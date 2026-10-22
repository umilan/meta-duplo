"""What the desktop app (desktop/, Tauri) relies on: the token link works from another origin, and the server
stops when the app that started it is gone."""
from __future__ import annotations

import subprocess
import sys
import time

from fastapi.testclient import TestClient

from meta_duplo.db import Library
from meta_duplo.web.app import create_app


def test_token_link_sets_cookie_without_redirect(tmp_path):
    client = TestClient(create_app(Library(tmp_path / "lib"), "secret"))
    assert client.get("/", follow_redirects=False).status_code == 403
    r = client.get("/?token=secret", follow_redirects=False)
    # a page that moves on by itself: a redirect from another origin would hold the strict cookie back
    assert r.status_code == 200 and 'http-equiv="refresh"' in r.text and "secret" not in r.text
    assert "samesite=strict" in r.headers["set-cookie"].lower()
    assert client.get("/", follow_redirects=False).status_code != 403  # the cookie now opens the app


def test_server_stops_with_parent(tmp_path):
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    code = ("import time; from meta_duplo.__main__ import stop_with_parent; "
            f"stop_with_parent({parent.pid}, every=0.1); time.sleep(30)")
    child = subprocess.Popen([sys.executable, "-c", code])
    time.sleep(0.5)
    assert child.poll() is None  # the parent is alive: keep serving
    parent.kill()
    parent.wait()
    assert child.wait(timeout=5) != 0  # stopped by SIGTERM once the parent was gone
