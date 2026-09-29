"""写操作通路验证（净效果为零，不留痕迹）。

1) 幂等：对已经装备着的物品再装备一次 —— 不改任何状态，只验授权/请求体/错误翻译。
2) 往返：挑一件仓库里、能装到某角色身上、且该角色对应桶没满的物品，
   移到角色 → 复核它在角色背包里 → 再移回仓库 → 复核它回到仓库。
   中途任一步失败都打印原因；物品最终不会停留在异常位置（真失败也只会在角色背包里）。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dim_data as dm


async def main():
    inv = await dm.inventory()
    ch = inv["chars"][0]
    # 1) 幂等装备
    eq = next((x for x in ch["equipped"] if x["i"] and x["h"]), None)
    if eq:
        try:
            await dm.equip(eq["i"], eq["h"], ch["id"])
            print(f"[幂等装备] 成功：重复装备「{eq['n']}」没有报错")
        except Exception as exc:  # noqa: BLE001
            print(f"[幂等装备] 报错（可接受）：{exc}")

    # 2) 找一个安全的往返对象：能到该角色 + 该角色对应桶没满 + 不在仓库里的垃圾栏
    caps = inv["bucketCap"]
    cand = None
    for x in inv["vault"]:
        if not (x["cx"] & (1 << ch["cls"])) or not x["i"]:
            continue
        used = sum(1 for y in ch["bag"] if y["b"] == x["b"])
        cap = caps.get(str(x["b"])) or 0
        if cap and used < cap:
            cand = x
            break
    if not cand:
        print("[往返搬运] 没找到合适的目标，跳过")
        return
    print(f"[往返搬运] 目标：{cand['n']}（定义桶 {dm.bname(cand['db'])}, "
          f"当前在 {cand['w']}, 该角色此桶占用 "
          f"{sum(1 for y in ch['bag'] if y['b'] == cand['b'])}/{caps.get(str(cand['b']))}）")

    try:
        await dm.move(cand["i"], cand["h"], ch["id"], "vault")
        print("  ① 仓库 → 角色：成功")
    except Exception as exc:  # noqa: BLE001
        print(f"  ① 仓库 → 角色：失败（{exc}）—— 到此为止，物品仍在仓库")
        return

    inv2 = await dm.inventory()
    here = any(y["i"] == cand["i"] for y in inv2["chars"][0]["bag"])
    print(f"  复核：它在角色背包里 = {here}")

    try:
        await dm.move(cand["i"], cand["h"], "vault", ch["id"])
        print("  ② 角色 → 仓库：成功")
    except Exception as exc:  # noqa: BLE001
        print(f"  ② 角色 → 仓库：失败（{exc}）—— 物品停在角色背包里，需要手动搬回")
        return

    inv3 = await dm.inventory()
    back = any(y["i"] == cand["i"] for y in inv3["vault"])
    print(f"  复核：它回到仓库了 = {back}；仓库 {inv3['vaultUsed']}/{inv3['vaultCap']}"
          f"（测试前 {inv['vaultUsed']}）")


asyncio.run(main())
