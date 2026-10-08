# -*- mode: python ; coding: utf-8 -*-
import glob
import os
from PyInstaller.utils.hooks import collect_all

# 机器人图片卡片要用的无头浏览器驱动（playwright python 包 + node driver）整包收集
pw_datas, pw_binaries, pw_hidden = collect_all('playwright')

# manifest_index 索引**自动收集**，别改回手写清单：手写版漏过一次 stats.json，
# 打包出来的 exe 一开武器详情就报 "No such file or directory: manifest_index\stats.json"。
# raw_* 是构建中间产物，只给构建脚本用，不进包：raw_items 220MB、en/zh-cht 的
# 物品与活动定义（build_locale_index.py 的下载缓存）也一并排除，只有**紧凑索引**
# name_i18n.json / item_cht.json 进包。
_MI_SKIP = {'raw_items.json', 'raw_plugsets.json', 'raw_items_en_lite.json',
            'raw_items_cht_lite.json', 'raw_perks_cht.json', 'raw_perks_en.json',
            'raw_acts_cht.json', 'raw_acts_en.json', 'raw_sets_chs.json',
            'raw_sets_cht.json', 'raw_sets_en.json', 'raw_dmg_zh-chs.json',
            'raw_dmg_zh-cht.json', 'raw_dmg_en.json'}
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
    hiddenimports=['psutil', 'card_render', 'bot_cards', 'bot_platform', 'bot_fireteam', 'weapon_filter', 'weapon_usage', 'raid_loot', 'greenlet', 'pyee', 'qr_png',
                   # bungie_status：维护检测（数据层/授权层/调度器都用）；bot_scheduler：
                   # 面板的「重发未发送图片」要往它的事件循环上投协程
                   'bungie_status', 'bot_scheduler',
                   'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'nonebot.drivers.fastapi', 'nonebot.drivers.httpx', 'nonebot.drivers.websockets', 'nonebot.adapters.onebot.v11', 'nonebot.adapters.qq'] + pw_hidden,
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
