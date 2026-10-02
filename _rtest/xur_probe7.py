# -*- coding: utf-8 -*-
"""探针7：只读 dump 当前老九货单 + 分节结果（用现存 access_token，不刷新、不写盘）。"""
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDX = json.load(open(os.path.join(ROOT, "manifest_index", "vendor_items.json"),
                     encoding="utf-8"))
XUR = "2190858386"
CLS_ZH = {-1: "通用", 0: "泰坦", 1: "猎人", 2: "术士", 3: "通用"}
ARMOR_KW = ("护甲", "头盔", "臂甲", "披风", "猎戏", "印记", "臂环")


def rec(h):
    r = IDX.get(str(h))
    return (list(r) + [""] * 6)[:6] if r else ["?", "?", "?", "", -1, ""]


def sec_of(tier, ty, name):
    if "任务" not in ty and (name.endswith("催化") or "催化" in ty):
        return "异域武器催化"
    if "记忆水晶" in ty:
        return "异域印痕"
    if tier == "异域" and any(k in ty for k in ("披风", "猎戏", "印记")):
        return "职业金"
    armor = any(k in ty for k in ARMOR_KW)
    if tier == "异域":
        if armor:
            return "异域护甲"
        return "任务" if "任务" in ty else "异域武器"
    if tier == "传说" and not armor:
        if "材料" in ty or "可兑换" in ty:
            return "材料"
        return "任务" if "任务" in ty else "传说武器"
    if armor:
        return "传说护甲"
    if "材料" in ty or "可兑换" in ty:
        return "材料"
    if "任务" in ty:
        return "任务"
    return "其他"


def get(path, tok, key):
    req = urllib.request.Request(
        "https://www.bungie.net" + path,
        headers={"X-API-Key": key, "Authorization": "Bearer " + tok,
                 "User-Agent": "xur-probe7/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    tok = json.load(open(os.path.join(ROOT, "dist_new", "D2Query", "bungie_token.json"),
                         encoding="utf-8-sig"))
    access = tok.get("access_token") or ""
    key = ""
    for line in open(os.path.join(ROOT, ".env"), encoding="utf-8-sig", errors="ignore"):
        if line.strip().startswith("BUNGIE_API_KEY"):
            key = line.split("=", 1)[1].strip().strip('"').strip("'")
    me = get("/Platform/User/GetMembershipsForCurrentUser/", access, key)
    mems = ((me.get("Response") or {}).get("destinyMemberships")) or []
    mem = mems[0] if mems else {}
    mt, mid = mem.get("membershipType"), mem.get("membershipId")
    print("membership:", mt, mid, mem.get("displayName"))
    prof = get(f"/Platform/Destiny2/{mt}/Profile/{mid}/?components=200", access, key)
    chars = ((prof.get("Response", {}).get("characters") or {}).get("data")) or {}
    print("chars:", len(chars))
    all_items = {}
    for cid in chars:
        try:
            one = get(f"/Platform/Destiny2/{mt}/Profile/{mid}/Character/{cid}/"
                      f"Vendors/{XUR}/?components=400,304,305", access, key)
        except Exception as e:  # noqa: BLE001
            print(f"char {cid}: FAIL {type(e).__name__} {str(e)[:80]}")
            continue
        resp = one.get("Response") or {}
        sales = ((resp.get("sales") or {}).get("data")) or {}
        print(f"char {cid}: sales={len(sales)}")
        for k, s in sales.items():
            if not isinstance(s, dict):
                continue
            h = str(s.get("itemHash") or "")
            all_items.setdefault((k, h), s)
    print("---- merged:", len(all_items), "unique sale slots ----")
    rows = []
    for (k, h), s in sorted(all_items.items(), key=lambda x: int(x[0][0])):
        name, ty, tier, icon, cls, shot = rec(h)
        cost = ""
        for c in (s.get("costs") or []):
            cost = f"{c.get('quantity')}x{rec(c.get('itemHash'))[0]}"
            break
        st = s.get("saleStatus")
        rows.append((int(k), h, name, ty, tier, CLS_ZH.get(cls, cls), cost, st))
    for idx, h, name, ty, tier, cls, cost, st in rows:
        print(f"[{idx:>3}] {st} {sec_of(tier, ty, name):<6} {tier:<4}{cls:<3} "
              f"{name}（{ty}）= {cost}")


if __name__ == "__main__":
    main()
