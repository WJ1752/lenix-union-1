"""光尘商店刷新归属探针：官方 GetVendors 里各 vendor 的 nextRefreshDate + 当前货架。

用途：确认「每天 1 点刷新」这个假设到底成不成立——API 每个 vendor 都给了
nextRefreshDate（下次刷新时刻），拿它跟本机北京时间对一下就知道货架归属哪一轮。

    .venv/Scripts/python.exe _rtest/ev_refresh_probe.py            # 查当前 + 对比缓存
    .venv/Scripts/python.exe _rtest/ev_refresh_probe.py --save     # 顺手存一份 raw 到 _rtest/
"""
import asyncio
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bungie_auth  # noqa: E402
import bungie_status as bst  # noqa: E402
import destiny_data as d2  # noqa: E402

TZ8 = datetime.timezone(datetime.timedelta(hours=8))


def cn(iso: str | None) -> str:
    """ISO8601 → 北京时间字符串"""
    if not iso:
        return "-"
    try:
        return (datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
                .astimezone(TZ8).strftime("%m-%d %H:%M:%S"))
    except Exception:  # noqa: BLE001
        return iso


def cached_items(path: str) -> dict:
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    dd = d.get("data") or {}
    out = {"day": d.get("day"), "at": d.get("at"), "items": {}}
    for s in dd.get("sections") or []:
        out["items"][s["name"]] = sorted(i["n"] for i in s["items"])
    return out


async def main() -> None:
    now = datetime.datetime.now(TZ8)
    print(f"本机北京时间 {now:%Y-%m-%d %H:%M:%S} · 维护态={bst.is_down()} · "
          f"授权={bungie_auth.authorized()}")
    day = d2._ev_day()
    print(f"当前按 1 点算的缓存键 day = {day}")
    for p in (os.path.join(ROOT, "eververse_cache.json"),
              os.path.join(ROOT, "dist_new", "D2Query", "eververse_cache.json")):
        c = cached_items(p)
        if c:
            at = datetime.datetime.fromtimestamp(c["at"], TZ8).strftime("%m-%d %H:%M") if c["at"] else "-"
            print(f"缓存 {os.path.relpath(p, ROOT)}: day={c['day']} 抓取于 {at}")
            for k, v in c["items"].items():
                print(f"    {k}({len(v)}): {v}")

    resps = await d2._ev_vendor_responses()
    raw_by_vendor: dict = {}
    for ci, resp in enumerate(resps, 1):
        vdata = ((resp.get("vendors") or {}).get("data")) or {}
        sdata = ((resp.get("sales") or {}).get("data")) or {}
        for vh, v in vdata.items():
            raw_by_vendor.setdefault(vh, []).append(v)
        for vh, g in sdata.items():
            raw_by_vendor.setdefault(vh, [])
        print(f"\n=== 角色 {ci}：{len(vdata)} 个 vendor ===")
        for title, cur, vendors in d2.EV_SECTIONS:
            for vh in vendors:
                v = vdata.get(vh)
                g = sdata.get(vh) or {}
                items = (g.get("saleItems") or {}) if isinstance(g, dict) else {}
                want = d2._EV_CUR_HASHES[cur]
                priced = []
                for sale in items.values():
                    if not isinstance(sale, dict):
                        continue
                    amt = None
                    for c in (sale.get("costs") or []):
                        if isinstance(c, dict) and str(c.get("itemHash")) in want:
                            amt = int(c.get("quantity") or 0)
                    if amt is None:
                        continue
                    rec = (d2._ev_index().get(str(sale.get("itemHash"))) or [])
                    priced.append((rec[0] if rec else f"?{sale.get('itemHash')}", amt))
                print(f"  [{title}] vendor {vh} 售卖 {len(items)} 件 / 本币 {len(priced)} 件 "
                      f"nextRefresh={cn(((v or {}).get('nextRefreshDate'))) if v else '无组件'}")
                if priced:
                    print(f"        {priced}")
    if "--save" in sys.argv:
        out = os.path.join(ROOT, "_rtest", "ev_vendor_raw.json")
        json.dump(resps, open(out, "w", encoding="utf-8"), ensure_ascii=False)
        print("已保存", out)


if __name__ == "__main__":
    asyncio.run(main())
