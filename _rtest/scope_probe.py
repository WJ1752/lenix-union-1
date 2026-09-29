"""探针：现有 OAuth token 能不能读私有档案组件（仓库 102 / 装备 205 / 配装 206 / 成就 900）。

只打印「能不能读、读到几个东西」，不打印任何 token 内容。
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bungie_auth
import destiny_data as d2


async def main():
    tok = json.load(open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "bungie_token.json"), encoding="utf-8"))
    mtype, mid = tok.get("membership_type"), str(tok.get("membership_id"))
    print("账号:", tok.get("display_name"), "type=", mtype)
    for label, comps in (("仓库(102)", "102"), ("角色(200)", "200"),
                         ("装备(205)", "205"), ("配装(206)", "206"),
                         ("成就记录(900)", "900")):
        try:
            resp = await bungie_auth.authorized_get(
                f"/Platform/Destiny2/{mtype}/Profile/{mid}/", {"components": comps})
            keys = list(resp.keys())
            n = ""
            if "profileInventory" in resp:
                n = f" items={len(resp['profileInventory'].get('data', {}).get('items') or [])}"
            elif "characters" in resp:
                n = f" chars={len(resp['characters'].get('data') or {})}"
            elif "characterEquipment" in resp:
                n = f" chars={list((resp['characterEquipment'].get('data') or {}).keys())[:2]}"
            elif "characterLoadouts" in resp:
                n = f" chars={list((resp['characterLoadouts'].get('data') or {}).keys())[:2]}"
            elif "profileRecords" in resp:
                n = f" records={len(resp['profileRecords'].get('data', {}).get('records') or {})}"
            print(f"{label}: OK keys={keys}{n}")
        except Exception as exc:  # noqa: BLE001
            print(f"{label}: 失败 {d2.esc_err(exc)[:200]}")


asyncio.run(main())
