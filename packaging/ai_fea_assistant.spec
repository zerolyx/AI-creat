from pathlib import Path
import sys

ROOT = Path(SPEC).resolve().parents[1]
legacy_site_packages = ROOT / ".venv" / "Lib" / "site-packages"
gmsh_dll_candidates = [
    Path(sys.prefix) / "Lib" / "gmsh-4.15.dll",
    ROOT / ".venv" / "Lib" / "gmsh-4.15.dll",
]
gmsh_dll = next((path for path in gmsh_dll_candidates if path.exists()), gmsh_dll_candidates[0])
if not gmsh_dll.exists():
    raise FileNotFoundError(gmsh_dll)

ccx_root = ROOT / "runtime" / "ccx"
ccx_files = [
    (str(path), "runtime/ccx")
    for path in ccx_root.iterdir()
    if path.suffix.lower() in {".exe", ".dll"}
]

a = Analysis(
    [str(ROOT / "run_gui.py")],
    pathex=[str(ROOT), str(ROOT / "src"), str(legacy_site_packages)],
    binaries=[(str(gmsh_dll), "."), *ccx_files],
    datas=[
        (str(ROOT / "assets" / "fonts"), "assets/fonts"),
    ],
    hiddenimports=[
        "gmsh",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
    ],
    excludes=["pytest", "matplotlib"],
    noarchive=False,
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AI-FEA-Valve-Gripper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
