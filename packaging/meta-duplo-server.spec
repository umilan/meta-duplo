# PyInstaller spec: one self-contained `meta-duplo-server` binary (Python, dependencies, templates, static files).
# Build:  uv run pyinstaller packaging/meta-duplo-server.spec --noconfirm --distpath packaging/dist --workpath packaging/build
import os

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
WEB = os.path.join(ROOT, "meta_duplo", "web")

datas = [(os.path.join(WEB, "templates"), "meta_duplo/web/templates"),
         (os.path.join(WEB, "static"), "meta_duplo/web/static")]
binaries, hiddenimports = [], []
hiddenimports += collect_submodules("uvicorn") + collect_submodules("meta_duplo")
for pkg in ("pillow_heif",):  # native libraries for HEIC
    d, b, h = collect_all(pkg)
    datas, binaries, hiddenimports = datas + d, binaries + b, hiddenimports + h
try:  # offline place names (optional `geo` extra): its city table ships as data
    datas += collect_data_files("reverse_geocoder")
    hiddenimports += ["reverse_geocoder"]
except Exception:
    pass

a = Analysis([os.path.join(SPECPATH, "server.py")], pathex=[ROOT], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, excludes=["tkinter", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="meta-duplo-server", console=True,
          upx=False, strip=False, target_arch=os.environ.get("TARGET_ARCH") or None)
