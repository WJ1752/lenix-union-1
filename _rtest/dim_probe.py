"""探结构：一次拉全量分量，看各列表里都有哪些背包桶、字段长什么样。

用来确定：仓库/邮政官分别挂在哪个分量、配装(206)的结构、装备/实例字段名。
"""
import asyncio
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bungie_auth

COMPONENTS = "102,104,200,201,205,206,300,301,302,304,305,306,308,310"
BUCKETS = json.load(open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "manifest_index", "dim_buckets.json"), encoding="utf-8"))


def bname(h):
    v = BUCKETS.get(str(h))
    return f"{v[0] or '?'}(loc{v[3]},cap{v[2]})" if v else f"?{h}"


async def main():
    tok = json.load(open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "bungie_token.json"), encoding="utf-8"))
    mtype, mid = tok.get("membership_type"), str(tok.get("membership_id"))
    r = await bungie_auth.authorized_get(f"/Platform/Destiny2/{mtype}/Profile/{mid}/",
                                        {"components": COMPONENTS})
    print("分量:", sorted(r.keys()))
    print("响应大小: %.2f MB" % (len(json.dumps(r, ensure_ascii=False)) / 1048576))

    for key in ("profileInventory", "profileCurrencies"):
        d = (r.get(key) or {}).get("data") or {}
        if key == "profileInventory":
            items = d.get("items") or []
            print(f"{key}: {len(items)} 件; 桶分布:",
                  Counter(bname(i.get("bucketHash")) for i in items).most_common(8))
            print("  样例:", {k: v for k, v in (items[0] or {}).items() if k != "itemHash"})
            print("  itemHash:", items[0].get("itemHash"))

    chars = (r.get("characters") or {}).get("data") or {}
    print("角色数:", len(chars), "字段:", sorted(next(iter(chars.values())).keys())[:24])
    for cid, ch in chars.items():
        print("  ", cid[-4:], "class", ch.get("classType"), "light", ch.get("light"),
              "emblem", (ch.get("emblemPath") or "")[-30:])

    for key in ("characterInventories", "characterEquipment"):
        d = (r.get(key) or {}).get("data") or {}
        for cid, v in d.items():
            items = v.get("items") or []
            print(f"{key}[{cid[-4:]}]: {len(items)} 件;",
                  Counter(bname(i.get("bucketHash")) for i in items).most_common(6))

    # 配装 206
    ld = (r.get("characterLoadouts") or {}).get("data") or {}
    for cid, v in ld.items():
        ls = (v or {}).get("loadouts") or []
        print(f"characterLoadouts[{cid[-4:]}]: {len(ls)} 套")
        if ls:
            one = ls[0]
            print("   字段:", sorted(one.keys()))
            print("   名称/序号:", one.get("name"), one.get("iconHash"), one.get("colorHash"))
            it = (one.get("items") or {})
            print("   items 条数:", len(it), "样例:", list(it.items())[:1])
        break

    # 实例/属性/插槽
    for key in ("itemInstances", "itemStats", "itemSockets", "itemPerks"):
        d = (r.get(key) or {}).get("data") or {}
        k = next(iter(d), None)
        print(f"{key}: {len(d)} 条; 样例键:", sorted((d.get(k) or {}).keys()) if k else None)


asyncio.run(main())
