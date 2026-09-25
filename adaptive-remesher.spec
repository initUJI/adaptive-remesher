# -*- mode: python ; coding: utf-8 -*-
#
# Genera un ejecutable de escritorio independiente (ver BUILD.md). Se
# regenero con `pyinstaller --windowed --name adaptive-remesher app.py`
# y no ha necesitado hidden-imports ni datas manuales: PyInstaller detecta
# solo PySide6/pyvista/VTK/PyMeshLab via sus hooks incluidos. Reconstruir
# con `pyinstaller adaptive-remesher.spec` tras cualquier cambio.

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='adaptive-remesher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='adaptive-remesher',
)
