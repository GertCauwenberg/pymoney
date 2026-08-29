# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the desktop app.

    pip install 'pymoney-core[desktop]' pyinstaller
    pyinstaller packaging/pymoney-desktop.spec

Produces dist/PyMoney/ (one-folder; a single-file build starts slower and
unpacks to a temp dir on every launch, which is the wrong trade for an app
that is opened daily). PyInstaller ships hooks for PySide6 and SQLAlchemy, so
the only things to say here are which Qt modules we do not use -- the rest is
the default analysis.

Why PyInstaller rather than Briefcase: see docs in README.md, "The desktop app".
"""

from PySide6 import QtCore  # noqa: F401 - makes sure the hook finds Qt

block_cipher = None

a = Analysis(
    ["../src/pymoney/desktop/__main__.py"],
    pathex=["../src"],
    binaries=[],
    datas=[],
    hiddenimports=[
        "pymoney.desktop.ui.app",
        "pymoney.store.migrations.env",
        "sqlalchemy.dialects.sqlite",
    ],
    hookspath=[],
    runtime_hooks=[],
    # Qt modules the app never imports; each is tens of megabytes.
    excludes=[
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia",
        "PySide6.QtCharts",
        "PySide6.QtPdf",
        "psycopg",
        "fastapi",
        "uvicorn",
        "alembic",
    ],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PyMoney",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="PyMoney")
