# -*- mode: python ; coding: utf-8 -*-
import glob
import os
from PyInstaller.utils.hooks import collect_all

# 机器人图片卡片要用的无头浏览器驱动（playwright python 包 + node driver）整包收集
pw_datas, pw_binaries, pw_hidden = collect_all('playwright')

# manifest_index 索引**自动收集**，别改回手写清单：手写版漏过一次 stats.json，
# 打包出来的 exe 一开 DIM 页面就报 "No such file or directory: manifest_index\stats.json"。
# raw_* 是构建中间产物（raw_items.json 有 220MB），只给构建脚本用，不进包。
_MI_SKIP = {'raw_items.json', 'raw_plugsets.json'}
mi_datas = [(p, 'manifest_index') for p in sorted(glob.glob('manifest_index/*.json'))
            if os.path.basename(p) not in _MI_SKIP]

# Saya 掉落表切块长图（raid_images/ 是原图构建中间产物，不进包）
# 注意必须逐子目录指定 dest，否则 PyInstaller 会把所有 seg_*.jpg 打平到一个目录里
loot_datas = [(p, 'raid_images_proc' + '/' + os.path.basename(os.path.dirname(p)))
              for p in sorted(glob.glob('raid_images_proc/*/*.jpg'))]


a = Analysis(
    ['d2query_launcher.py'],
    pathex=[],
    binaries=pw_binaries,
    datas=[*mi_datas, *loot_datas, ('certs/localhost.pem', 'certs'), ('certs/localhost-key.pem', 'certs')] + pw_datas,
    hiddenimports=['card_render', 'bot_cards', 'weapon_filter', 'raid_loot', 'dim_data', 'dim_web', 'dim_ui', 'dim_user', 'dim_opt', 'greenlet', 'pyee', 'qr_png', 'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'nonebot.drivers.fastapi', 'nonebot.drivers.http', 'nonebot.drivers.websockets', 'nonebot.adapters.onebot.v11'] + pw_hidden,
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
    name='D2Query',
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
    name='D2Query',
)
