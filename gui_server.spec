# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['gui_server.py'],
    pathex=[],
    binaries=[],
    datas=[('app', 'app'), ('static', 'static'), ('templates', 'templates')],
    hiddenimports=[
        'waitress',
        'app.training_jobs',
        'app.server_runtime',
        # Pickled model artifacts import these classes dynamically when the
        # frozen application loads genre_model.pkl.
        'app.hierarchical_genre',
        'app.genre_fusion',
        'app.deep_embeddings',
    ],
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
    a.binaries,
    a.datas,
    [],
    name='gui_server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
