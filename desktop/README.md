# Filomena for macOS (Tauri)

A Mac app around the same local server: no Python, `uv` or repository needed on the Mac that runs it (ADR-019).

```
Filomena.app
├── Contents/MacOS/filomena            Tauri shell (Rust): window, start page, starts and stops the server
└── Contents/MacOS/meta-duplo-server   the Python server, frozen with PyInstaller (packaging/)
```

The shell picks a free port and a random session token. It starts the server, waits until the server answers,
then opens the UI in its window. Quitting the app stops the server. If the app crashes, the server notices within
2 s and stops itself. Your library is the usual `~/MetaDuploLibraries/default`, the same one the command line uses.

## Get the app

GitHub → **Actions** → **desktop** → the latest run → **Artifacts**:

- `Filomena-macos-arm64` for Apple Silicon (M1 and later);
- `Filomena-macos-x86_64` for Intel Macs.

Each one holds a `.dmg` and a zipped `.app`. When a `v*` tag is pushed, the same files are attached to a release.

**First start:** the app is not signed with an Apple Developer ID yet, so macOS warns about it the first time.
Right-click `Filomena.app` and choose **Open**, then **Open** again. If macOS still refuses (or says the app is
"damaged"), run this once:

```bash
xattr -dr com.apple.quarantine /Applications/Filomena.app
```

**Videos:** dates and devices of videos come from `ffprobe`, which is not bundled yet. Install it with
`brew install ffmpeg`. Photos work without it.

## Build it on a Mac yourself

You need Xcode command line tools, [Rust](https://rustup.rs), Node 22 and `uv`.

```bash
uv sync --extra geo --group package
uv run pyinstaller packaging/meta-duplo-server.spec --noconfirm --distpath packaging/dist --workpath packaging/build
TRIPLE=$(rustc -vV | sed -n 's/host: //p')           # aarch64-apple-darwin or x86_64-apple-darwin
mkdir -p desktop/src-tauri/binaries
cp packaging/dist/meta-duplo-server desktop/src-tauri/binaries/meta-duplo-server-$TRIPLE
cd desktop && npm ci && npx tauri build
open src-tauri/target/release/bundle/macos/Filomena.app
```

`npx tauri dev` runs the shell from source (it still uses the frozen server in `binaries/`). To try a server
change without re-freezing, run `uv run meta-duplo serve` and open the printed link in Safari instead.

New icon: replace `app-icon.png` (1024 × 1024) and run `npm run icons`.

## Not done yet

- Signing with a Developer ID and notarization. These need an Apple Developer account ($99/year). Once you have
  one, add the certificate and an app-specific password as repository secrets, and `tauri build` signs and
  notarizes on its own.
- Bundling `ffprobe` (and later ExifTool, M1).
- A universal (arm64 + x86_64) build, auto-update, and a menu with *Open library…*.
