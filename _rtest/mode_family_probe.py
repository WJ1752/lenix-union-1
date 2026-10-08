import sys, os, json, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
import destiny_data as d2

async def main():
    m = await d2.resolve_member("Wj#8984")
    mtype, mid = m["mtype"], m["mid"]
    prof = await d2.get_profile(mtype, mid)
    cid = list(prof["characters"]["data"])[0]
    # mode=18 第一页里 严酷考验 / 宗师日落 的 modes 数组
    for page in (0, 1, 2):
        rows = await d2.activity_history(mtype, mid, cid, 18, count=250, page=page)
        for x in rows:
            if "严酷考验" in x["name"] or "宗师日落" in x["name"] or "征服" in x["name"]:
                print(f"  p{page} modes={x['modes']} mode={x['mode']} mode_name={x['mode_name']} "
                      f"cat={x['mode_cat']} {x['name']}")
        if len(rows) < 250:
            break
    print("\n各模式家族场次（角色1，翻满）:")
    for mode in (0, 7, 18):
        names, tot = {}, 0
        page = 0
        while True:
            rows = await d2.activity_history(mtype, mid, cid, mode, count=250, page=page)
            tot += len(rows)
            for x in rows:
                nm = x["name"]
                for kw in ("严酷考验", "宗师日落", "征服", "突袭", "地牢"):
                    if kw in nm:
                        names[kw] = names.get(kw, 0) + 1
            if len(rows) < 250 or page > 30:
                break
            page += 1
        print(f"  mode={mode}: 总 {tot} 场 · " + " ".join(f"{k}={v}" for k, v in names.items()))

asyncio.run(main())
