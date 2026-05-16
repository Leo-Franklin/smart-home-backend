# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

block_cipher = None

# Collect WSDL/XSD files for onvif-zeep
wsdl_src = Path('.venv/Lib/site-packages/wsdl')
wsdl_files = [(str(f), 'wsdl') for f in wsdl_src.rglob('*') if f.is_file()]

# External tools
nmap_src = Path('tools/nmap')
nmap_files = [(str(f), 'nmap') for f in nmap_src.rglob('*') if f.is_file()]
ffmpeg_files = [
    (str(Path('tools/ffmpeg/ffmpeg.exe')), 'ffmpeg'),
]

a = Analysis(
    ['app/main.py'],
    pathex=['.'],
    binaries=nmap_files + ffmpeg_files,
    datas=[
        ('app', 'app'),
        ('frontend', 'frontend'),
    ] + wsdl_files,
    hiddenimports=[
        'scapy.all',
        'scapy.layers.all',
        'scapy.layers.l2',
        'scapy.layers.inet',
        'passlib.handlers.bcrypt',
        'sqlalchemy.dialects.sqlite',
        'sqlalchemy.ext.asyncio',
        'aiosqlite',
        'jose',
        'jose.jwt',
        'multipart',
        'python_multipart',
        'pystray',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=False,
    name='SmartHome',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SmartHome',
)