# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-directory/windowed spec for Anki Quick Add."""
from pathlib import Path


project_root = Path(SPECPATH).parent

# Only bundle fonts that are actually selected by ui/theme.py.
# Listing them explicitly prevents stale/unused font files in fonts/ from
# silently inflating the executable.
bundled_font_files = [
    "Inter-VF.ttf",
    "SourceHanSansSC-Regular.otf",
    "SourceHanSansSC-Medium.otf",
    "NotoSansJP-VF.ttf",
]
datas = [
    *((str(project_root / "fonts" / name), "fonts") for name in bundled_font_files),
    (str(project_root / "assets" / "anki-quick-add.ico"), "assets"),
    (str(project_root / "config.json"), "."),
    (str(project_root / "ui" / "icons"), "ui/icons"),
]

a = Analysis(
    [str(project_root / "app.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.QtBluetooth",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtLocation",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtNetwork",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtPositioning",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtRemoteObjects",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtUiTools",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtWebEngineWidgets",
    ],
    noarchive=False,
    optimize=0,
)

# PyInstaller's PySide6 hooks can still collect optional Qt runtime DLLs and
# plugins through dependency discovery even when their Python modules are
# excluded above.  This app is pure Qt Widgets and uses Python urllib for the
# AnkiConnect HTTP call, so these optional runtimes are not used.
drop_binary_basenames = {
    "opengl32sw.dll",
    "qt6network.dll",
    "qtnetwork.pyd",
    "qt6pdf.dll",
    "qt6quick.dll",
    "qt6qml.dll",
    "qt6qmlmeta.dll",
    "qt6qmlmodels.dll",
    "qt6qmlworkerscript.dll",
    "qt6virtualkeyboard.dll",
    "libcrypto-3-x64.dll",
    "libssl-3-x64.dll",
}
drop_binary_prefixes = (
    "pyside6/plugins/tls/",
    "pyside6/plugins/platforminputcontexts/qtvirtualkeyboard",
)

def keep_binary(entry):
    destination = str(entry[0]).replace("\\", "/")
    basename = destination.rsplit("/", 1)[-1].lower()
    lowered = destination.lower()
    if basename in drop_binary_basenames:
        return False
    if any(lowered.startswith(prefix) for prefix in drop_binary_prefixes):
        return False
    if lowered == "pyside6/plugins/imageformats/qpdf.dll":
        return False
    return True

a.binaries = [entry for entry in a.binaries if keep_binary(entry)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AnkiQuickAdd",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(project_root / "assets" / "anki-quick-add.ico"),
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="AnkiQuickAdd",
)
