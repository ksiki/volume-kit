# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path(SPECPATH)

datas = []
if (project_root / "assets").is_dir():
    datas.append(("assets", "assets"))
if (project_root / "app" / "config.json").is_file():
    datas.append(("app/config.json", "app"))

icon_file = project_root / "assets" / "icon.ico"

a = Analysis(
    ["run.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "app.bootstrap",
        "app.cli",
        "app.core.proxy",
        "app.core.http_client",
        "app.ui.proxy_dialog",
        "app.ui.proxy_web",
        "app.ui.workers.proxy_check_worker",
        "socks",
        "sockshandler",
        "urllib3.contrib.socks",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VolumeKit",
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
    icon=str(icon_file) if icon_file.is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="VolumeKit",
)