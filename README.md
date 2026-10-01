# Adaptive Remesher

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Desktop prototype for **selectively simplifying (decimating) AI-generated 3D
meshes**, reducing polygons only where they aren't needed while preserving
detail, textures and regions manually marked as important (faces, logos,
fingers...).

## What it does

- **Two interchangeable decimation engines**:
  - `vtkDecimatePro`: collapses vertices in flat regions according to a fixed
    feature angle.
  - **Quadric Error Metric (QEM)**: continuous collapse cost weighted by
    curvature, with optional penalties for UV and normal distortion.
- **Pre-cleanup** with PyMeshLab: duplicate/unreferenced vertices,
  non-manifold edges and vertices, floating fragments, optional hole
  filling, and face reorientation (typical defects of AI-generated meshes).
- **Manual protection**: paint/erase brush and rectangle selection over the
  3D viewport, to mark regions that must never be simplified.
- **Automatic UV-seam protection**: detects and protects the duplicated
  vertices that form a texture seam.
- **Geometric error validation**: measures how far the decimated mesh has
  drifted from the original (approximates Hausdorff distance) and
  iteratively auto-protects regions where the error exceeds a threshold.
- Undo/redo, configuration presets (`.json`), live preview, side-by-side
  original/decimated comparison, and **batch processing** of entire folders.

> **A note on "for AI-generated meshes":** none of the techniques above
> detect or reason about mesh origin — they are generic topological
> cleanup, curvature-based decimation, UV-seam detection and geometric
> validation, the same techniques any mesh-processing tool would use. The
> name comes from the fact that this particular combination of problems
> (uniform density unrelated to visual importance, topological defects,
> loose fragments) is unusually common in AI-generated output, but the tool
> works equally well on any mesh that has them, whether it came from a scan,
> a careless CAD export, or careless manual modeling.

## Requirements

- Python 3.14 (verified with PySide6 6.11.2, PyVista 0.49.0, pyvistaqt
  0.13.1, VTK 9.7.0, PyMeshLab 2025.7.post1, NumPy 2.5.3, SciPy 1.18.1).
- Windows, macOS or Linux with Qt (PySide6) and VTK support.

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux

pip install -r requirements.txt
```

## Usage

```bash
python app.py
```

1. **Load mesh...**: opens an OBJ, STL, PLY, VTP, GLB or GLTF file. If
   "Clean automatically on load" is checked, it is cleaned before being
   displayed.
2. Optionally, paint protected regions with **Paint** / **Rectangle** over
   the viewport.
3. Adjust the decimation engine, the aggressiveness, and the automatic
   protection options (UV seams, error validation).
4. **Apply decimation** and, if you like, **Compare original/decimated**.
5. **Export mesh...** saves the result (and, if checked, a `.json` report
   with the decimation statistics).

### Batch processing

"Process folder..." applies the panel's current settings to every
compatible file in an input folder, without going through the viewer. It
does not include manual protection (brush/rectangle): only the configured
cleanup, decimation engine, and automatic protection.

### Presets

"Save preset..." / "Load preset..." save and restore every panel parameter
(excluding the mesh and manual protection marks) to a reusable `.json`,
shared across sessions or similar meshes.

## Project structure

```
app.py               Main Qt window: mesh/protection state, undo/redo,
                     presets, batch processing. Each panel section is
                     built by a function under ui/.
remesh_core.py       Pure domain logic, independent of Qt: cleanup, both
                     decimation engines, UV-seam protection, and the
                     decimation+error-validation pipeline shared between
                     the interactive flow and batch processing.
logging_config.py    Console + rotating file logging (see below).
ui/
  common.py          Shared styles, dialogs and widgets (tooltip helper,
                     busy cursor, collapsible panel, side-by-side
                     comparison).
  panels/            One build_xxx_panel(window, layout) function per
                     control-panel section (cleanup, protection,
                     decimation, error validation, batch...).
tests/               Tests for remesh_core.py (synthetic meshes, no Qt)
                     and for the UI with pytest-qt (real buttons/sliders).
```

## Development

```bash
pip install -r requirements-dev.txt
pytest              # unit tests + UI integration tests (pytest-qt)
ruff check .        # lint
mypy remesh_core.py app.py logging_config.py ui tests   # type checking
```

`tests/test_remesh_core.py` covers `remesh_core.py` (cleanup, both
decimation engines, mask-based protection, UV seams, and the full
pipeline) with synthetic meshes; it needs no graphical interface.
`tests/test_ui_integration.py` instantiates the real window and presses
real buttons/sliders with pytest-qt, on the "offscreen" Qt backend (no
display needed, also works in CI).

To have ruff/mypy/pytest run automatically on every commit:

```bash
pre-commit install
```

## Logging

The console shows INFO and above; `ADAPTIVE_REMESHER_DEBUG=1 python app.py`
raises the console to DEBUG. A rotating file next to the script
(`adaptive_remesher.log`, up to 3 files of 1MB) always captures DEBUG, so a
bug someone reports can be diagnosed without having to reproduce it live
(see `logging_config.py`).

## Build and releases

See [BUILD.md](BUILD.md) to build a desktop executable with PyInstaller
(manually, or automatically on Windows/macOS/Linux when pushing a `vX.Y.Z`
tag), and [CHANGELOG.md](CHANGELOG.md) for the change history.

## Known limitations

- The QEM engine with protection can, in about 10% of tested
  resolution/mask combinations, leave 1-2 seam edges unwelded at extreme
  reduction levels (>=99%), even after the two automatic repair passes
  (see CHANGELOG). This is a rare case, at aggressiveness far above typical
  use, and any residual gap is minimal.
- The protection tools (brush, rectangle) automatically disable after
  decimating and, when reactivated, switch back to showing the original
  mesh: they work against its KDTree/z-buffer, and with the QEM engine
  (which does move vertices) the decimated result would no longer match
  what's displayed. The decimated result isn't lost by this — only the
  view changes so painting can continue.
- The PyInstaller build (see BUILD.md) has been tested interactively only
  on Windows; the release workflow includes an automatic smoke test
  (starts without errors) on all three platforms.

## License

[MIT](LICENSE) © 2026 CEVI. Free to use, modify and redistribute (including
commercially), while keeping the copyright notice.
