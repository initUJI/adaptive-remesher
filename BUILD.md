# Packaging (PyInstaller)

Builds a standalone desktop executable, so the app can be shared with
someone who doesn't have Python installed.

## Requirements

```bash
pip install -r requirements-dev.txt
```

## Build

```bash
pyinstaller adaptive-remesher.spec
```

The result lands in `dist/adaptive-remesher/` (a folder containing
`adaptive-remesher.exe` on Windows, or the equivalent binary on
macOS/Linux, alongside its dependencies). That whole directory is what
needs to be distributed; the standalone `.exe`/binary won't start without
the rest of the files.

`adaptive-remesher.spec` already includes what's needed: PySide6,
PyVista/VTK and PyMeshLab are detected automatically via PyInstaller's
built-in hooks (no need to declare hidden-imports or datas by hand). If the
build stops starting after updating a dependency, regenerating the spec
from scratch is usually enough:

```bash
pyinstaller --windowed --name adaptive-remesher app.py
```

then compare the resulting `adaptive-remesher.spec` with the one in the
repo (review the diff before replacing it).

## Verifying the build

```bash
./dist/adaptive-remesher/adaptive-remesher.exe
```

Confirmed to start with no arguments or extra configuration on Windows
with Python 3.14. macOS/Linux haven't been tested *manually*, but
`.github/workflows/release.yml` now includes an automatic "smoke test"
step that starts the freshly built executable on all three platforms
(with `QT_QPA_PLATFORM=offscreen`) and fails the build if the process
exits on its own within a few seconds — the typical symptom of a missing
native dependency or a broken import at packaging time. This doesn't
replace testing the app for real (it doesn't interact with the window, it
only confirms it starts and doesn't blow up on import), but it catches the
most common way a broken build fails on an OS where there's no way to test
it by hand.

## Notes

- The build is specific to the platform `pyinstaller` runs on: to
  distribute on Windows, macOS and Linux, the executable has to be built
  separately on each one (no cross-compilation).
- `build/` and `dist/` are in `.gitignore`: they're generated artifacts,
  not version-controlled.

## Automated releases (multi-OS)

`.github/workflows/release.yml` builds the executable on Windows, macOS
and Linux and attaches them as assets to a GitHub Release, when a
`vX.Y.Z` tag is pushed:

```bash
git tag v0.2.0
git push origin v0.2.0
```

The Windows build has been verified manually (see above); the macOS/Linux
ones go through the workflow's automatic smoke test, but haven't been
tested interactively — check that Action's logs after the first tag if
something fails there.
