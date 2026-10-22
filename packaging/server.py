"""Entry point of the bundled server: the binary the desktop app (Tauri) starts as its sidecar.

Run as `meta-duplo-server serve`; host, port and session token come from META_DUPLO_HOST / META_DUPLO_PORT /
META_DUPLO_TOKEN (the desktop shell picks a free port and a fresh token), the library from META_DUPLO_LIBRARY or the
default ~/MetaDuploLibraries/default.
"""
from __future__ import annotations

import multiprocessing

from meta_duplo.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # a frozen binary must not re-run itself in child processes
    main()
