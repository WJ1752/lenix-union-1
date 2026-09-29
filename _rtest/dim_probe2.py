"""探结构 2：仓库分组依据、itemComponents 形态、配装条目格式、各分量体积占比。"""
import asyncio
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bungie_auth

COMPONENTS = "102,104,200,201,205,206,300,301,302,304,305,306,308,310"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ITEMS = json.load(open(os.path.join(ROOT, "manifest_index", "dim_items.json"), encoding="utf-8"))
BUCKETS = json.load(open(os.path.join(ROOT, "manifest_index", "dim_buckets.json"), encoding="utf-8"))


def bname(h):
    v = BUCKETS.get(str(h))
    return f"{v[0] or '?'}(loc{v[3]})" if v else f"?{h}"


async def main():
    tok = json.load(open(os.path.join(ROOT, "bungie_token.json"), encoding="utf-8"))
    mt, mid = tok.get("membership_type"), str(tok.get("membership_id"))
    r = await bungie_auth.authorized_get(f"/Platform/Destiny2/{mt}/Profile/{mid}/",
                                         {"components": COMPONENTS})

    # (d) 各顶层键的体积
    sizes = sorted(((len(json.dumps(v, ensure_ascii=False)) / 1048576, k)
                    for k, v in r.items()), reverse=True)
    print("体积占比:", [(k, round(s, 2)) for s, k in sizes[:8]])

    # (a) 仓库里武器/护甲的 bucketHash 是不是都被报成「一般」
    vault = ((r.get("profileInventory") or {}).get("data") or {}).get("items") or []
    c = Counter()
    for i in vault:
        d = ITEMS.get(str(i.get("itemHash"))) or []
        ty = d[1] if d else None
        c[(ty, bname(i.get("bucketHash")))] += 1
    print("仓库 (itemType, 上报桶) 组合:", c.most_common(10))

    # (b) itemComponents 形态
    ic = r.get("itemComponents") or {}
    print("itemComponents 下的分量:", sorted(ic.keys()))
    inst = (ic.get("instances") or {}).get("data") or {}
    k = next(iter(inst), None)
    print("  instances 样例:", (inst.get(k) if k else None))
    st = (ic.get("stats") or {}).get("data") or {}
    k2 = next(iter(st), None)
    print("  stats 样例:", json.dumps(st.get(k2), ensure_ascii=False)[:220] if k2 else None)
    so = (ic.get("sockets") or {}).get("data") or {}
    k3 = next(iter(so), None)
    print("  sockets 样例键:", sorted((so.get(k3) or {}).keys()) if k3 else None,
          "| 插槽数:", len((so.get(k3) or {}).get("sockets") or []) if k3 else 0)

    # (c) 配装条目格式
    ld = ((r.get("characterLoadouts") or {}).get("data") or {})
    for cid, v in ld.items():
        ls = (v or {}).get("loadouts") or []
        if ls:
            one = ls[0]
            print("配装 items 列表样例:", json.dumps(one.get("items")[:2], ensure_ascii=False)[:300])
            # 有名字的配装
            named = [x for x in ls if (x.get("nameHash") or 0)]
            print("该角色有 nameHash 的配装数:", len(named), "/", len(ls))
        break

    # 角色 currency
    print("profileCurrencies:", json.dumps((r.get("profileCurrencies") or {}).get("data"),
                                          ensure_ascii=False)[:200])


asyncio.run(main())
