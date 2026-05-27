# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the JobBoardScraper standalone executable.

Build (from the project root):
    pip install pyinstaller pyinstaller-hooks-contrib
    pyinstaller jobboard.spec

Output:
    dist/JobBoardScraper        (macOS / Linux)
    dist/JobBoardScraper.exe    (Windows)

The GitHub Actions release workflow runs this automatically on every version tag.
"""
import sys
from PyInstaller.utils.hooks import collect_data_files

# ---------------------------------------------------------------------------
# Data files
# ---------------------------------------------------------------------------
# collect_data_files picks up everything declared in [tool.setuptools.package-data]:
#   jobboard/web/templates/*.html
#   jobboard/sources/jobsdb/*.sql  *.graphql
#   jobboard/sources/linkedin/*.sql
#   jobboard/*.sql  *.graphql
datas = collect_data_files("jobboard")

# Bundle the default config template so the launcher can copy it on first run.
# Never bundle the real config.yaml — it may contain personal API keys.
datas += [("config.template.yaml", ".")]

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    ["launcher.py"],
    pathex=["src"],             # tells PyInstaller where to find the jobboard package
    binaries=[],
    datas=datas,
    hiddenimports=[
        # uvicorn dynamically imports its event-loop and protocol backends —
        # PyInstaller's static analysis misses them without this list.
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        # anyio async backend
        "anyio._backends._asyncio",
        # APScheduler components loaded by name at runtime
        "apscheduler.schedulers.background",
        "apscheduler.executors.pool",
        "apscheduler.jobstores.memory",
        # pydantic v2 internals
        "pydantic_core",
        "pydantic.deprecated.class_validators",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

if sys.platform == "darwin":
    # macOS: produce a .app bundle (onedir mode) so users can right-click → Open
    # from Finder without needing Terminal, chmod, or xattr.
    exe = EXE(
        pyz,
        a.scripts,
        [],             # binaries/datas go into COLLECT, not embedded in EXE
        name="JobBoardScraper",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,   # must stay False for server apps
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="JobBoardScraper",
    )
    app = BUNDLE(
        coll,
        name="JobBoardScraper.app",
        bundle_identifier="com.jobbboardscraper.app",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleDisplayName": "JobBoardScraper",
            "CFBundleShortVersionString": "1.0.0",
        },
    )
else:
    # Windows / Linux: single self-contained executable
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="JobBoardScraper",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,          # UPX compression can break some binaries; leave off by default
        upx_exclude=[],
        runtime_tmpdir=None,
        console=True,       # keep terminal window so users can see startup messages / errors
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
