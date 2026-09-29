"""dim_data 自测：四个读取入口各跑一遍，只打印摘要"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dim_data as dd


async def main():
    t = time.time()
    inv = await dd.inventory()
    print(f"[背包] {time.time()-t:.1f}s 角色 {len(inv['chars'])} 个, 仓库 {inv['vaultUsed']}/{inv['vaultCap']}")
    for c in inv["chars"]:
        print(f"   {c['clsName']} 光等{c['light']} 装备{len(c['equipped'])} 背包{len(c['bag'])} "
              f"邮政官{len(c['post'])}/{c['postCap']}")
    print("   货币:", [(x["n"], x["q"]) for x in inv["currencies"]][:6])
    v = inv["vault"]
    from collections import Counter
    print("   仓库按定义桶:", Counter(dd.bname(x["db"]) for x in v).most_common(8))
    sample = next((x for x in v if x["db"] == 1498876634), None) or v[0]
    print("   样例:", json.dumps(sample, ensure_ascii=False)[:240])

    t = time.time()
    ld = await dd.loadouts()
    print(f"[配装] {time.time()-t:.1f}s")
    for c in ld["chars"]:
        named = [x for x in c["loadouts"] if x["items"]]
        print(f"   {c['clsName']}: {len(c['loadouts'])} 套, 有装备的 {len(named)}")
        if named:
            one = named[0]
            print("     样例:", one["name"], "| 图标", one["icon"][-24:], "| 件数", len(one["items"]),
                  "| 首件", (one["items"][0]["n"], one["items"][0]["here"]))

    t = time.time()
    tr = await dd.triumphs()
    print(f"[成就] {time.time()-t:.1f}s 凯旋分 {tr['score']} 参与记录 {tr['records']}")

    def summ(n, d=0):
        print("   " + "  " * d + f"{n['n']} {n['done']}/{n['total']}")
        for k in n["kids"][:4]:
            summ(k, d + 1)
    for cat in tr["cats"]:
        summ(cat)

    # 拿一件装备做详情
    eq = next(x for c in inv["chars"] for x in c["equipped"]
              if x["i"] and x["db"] == 1498876634)
    t = time.time()
    dt = await dd.item_detail(eq["i"], eq["h"])
    print(f"[详情] {time.time()-t:.1f}s {dt['name']} 光等{dt['power']} "
          f"属性{len(dt['stats'])} Perk{len(dt['perks'])} 插槽{len(dt['sockets'])}")
    print("   属性:", [(x["n"], x["v"]) for x in dt["stats"]][:5])
    print("   插槽:", [(x["n"], x["ty"]) for x in dt["sockets"]][:6])


asyncio.run(main())
