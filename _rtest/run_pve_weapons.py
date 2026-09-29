"""端到端：跑一次 PVE 生涯武器任务（默认范围=当前赛季）"""
import asyncio, sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import destiny_data as D

NAME = sys.argv[1] if len(sys.argv) > 1 else "Wj#8984"
SCOPE = sys.argv[2] if len(sys.argv) > 2 else "current"


async def main():
    jid = await D.start_pve_weapons(NAME, SCOPE)
    print("job:", jid)
    t0 = time.time()
    while time.time() - t0 < 900:
        j = D.JOBS.get(jid) or {}
        if j.get("status") in ("done", "error"):
            break
        print(f"  {time.time()-t0:6.1f}s  {j.get('done')}/{j.get('total')}", flush=True)
        await asyncio.sleep(15)
    j = D.JOBS.get(jid) or {}
    print("status:", j.get("status"), "elapsed %.1fs" % (time.time() - t0), j.get("error", ""))
    r = j.get("result")
    if not r:
        print("no result"); return
    print("kind:", r["kind"], "| scope:", r["scope_label"], "| matches:", r["matches"],
          "| capped:", r["capped"], "| missed:", r["missed"], "| range:", r["range"])
    print("kills:", r["kills"], "weapon_kills:", r["weapon_kills"],
          "precision:", r["weapon_precision"], "melee/grenade/super:",
          r["melee"], r["grenade"], r["super"])
    for w in r["weapons"][:12]:
        print(f"   {w['kills']:>7,}  {w['name'][:24]:<26} {w['type'][:12]:<14} "
              f"出场{w['matches']} 精准{w['precision']}")
    # 顺手渲一张卡片
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import webui
    html = webui.render_wpvp(r)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wpve_card.html")
    open(out, "w", encoding="utf-8").write(html)
    print("html ->", out, len(html), "bytes")


asyncio.run(main())
