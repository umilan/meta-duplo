"""CLI: medorg demo | add-source | scan | serve."""
from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

from .db import Library, now

DEFAULT_LIBRARY = os.environ.get("MEDORG_LIBRARY", str(Path.home() / "MedorgLibraries" / "default"))


def add_source(lib: Library, root: str, writable: bool = False, role: str = "scan") -> int:
    root = str(Path(root).expanduser().resolve())
    with lib.connect() as con:
        con.execute(
            "INSERT INTO sources(label, root, writable, role, added_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(root) DO UPDATE SET writable = excluded.writable, role = excluded.role",
            (Path(root).name, root, int(writable), role, now()),
        )
        return con.execute("SELECT id FROM sources WHERE root = ?", (root,)).fetchone()["id"]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="medorg")
    ap.add_argument("--library", default=DEFAULT_LIBRARY, help="library folder (default: %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="create a demo disk and add it as a writable source")
    d.add_argument("path", nargs="?", default=str(Path.home() / "medorg-demo"))
    a = sub.add_parser("add-source", help="add a folder to scan")
    a.add_argument("path")
    a.add_argument("--writable", action="store_true", help="allow approved plans to move and rename files here")
    sub.add_parser("scan", help="run steps 1-2 (inventory, metadata, hashes, duplicates)")
    s = sub.add_parser("serve", help="start the web UI on localhost")
    s.add_argument("--host", default=os.environ.get("MEDORG_HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("MEDORG_PORT", "8765")))
    s.add_argument("--no-token", action="store_true", help="disable the session token (development only)")
    args = ap.parse_args(argv)

    lib = Library(args.library)
    if args.cmd == "demo":
        from .demo import make_demo

        root = make_demo(args.path)
        add_source(lib, str(root), writable=True)
        print(f"Demo disk created at {root} and added as a writable source to {lib.path}")
    elif args.cmd == "add-source":
        add_source(lib, args.path, writable=args.writable)
        print(f"Added {args.path}")
    elif args.cmd == "scan":
        from .scan import Runner, scan_all

        Runner(lib).run_sync("scan", lambda job: scan_all(lib, job))
        with lib.connect() as con:
            for r in con.execute("SELECT name, status, message FROM jobs ORDER BY id DESC LIMIT 1"):
                print(dict(r))
    elif args.cmd == "serve":
        import uvicorn

        from .web.app import create_app

        token = None if args.no_token else os.environ.get("MEDORG_TOKEN") or secrets.token_urlsafe(16)
        app = create_app(lib, token)
        url = f"http://{args.host}:{args.port}/" + (f"?token={token}" if token else "")
        print(f"medorg is running: {url}")
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
