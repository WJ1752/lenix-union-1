"""从 raw_items.json 抽出「光尘商店」可能出售的物品，生成紧凑索引 eververse_items.json。

用途：每天的光尘商店来自第三方（TodayInDestiny），它只给物品 hash + 价格 + 分类（英文）；
物品的中文名/稀有度/类型要回 Manifest 取。raw_items.json 有 220MB，运行时不可能整读，
所以这里把它裁成一张小表：{hash: [中文名, 类型/描述, 稀有度, 图标路径, 大图路径]}。

第 5 项 screenshot 是游戏里点开物品看到的那张竖版大图（武器皮肤/三职业皮肤当背景用），
图标则是方形缩略图；两者都可能为空。

用法：
    .venv/Scripts/python.exe build_eververse_index.py          # 生成索引
    .venv/Scripts/python.exe build_eververse_index.py --diag   # 只看某几个 hash 的分类
"""
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(ROOT, "manifest_index", "raw_items.json")
OUT = os.path.join(ROOT, "manifest_index", "eververse_items.json")

KEY_RE = re.compile(r'"(\d+)":\s*\{')

# 只保留这些「类型名」里含关键字的东西（zh-chs），避免把武器/装备本体塞进来。
# 实测装饰品类型名形如「猎人通用皮肤」「泰坦通用皮肤」，机灵投影=「机灵投影」，
# 飞船=「飞船」，快雀=「载具」，着色器=「着色器」，传送=「传送特效」，终结=「终结技」。
KEEP_TYPE_KEYWORDS = (
    "装饰", "皮肤", "外壳", "投影", "飞船", "快雀", "载具", "表情", "动作", "着色器",
    "传送", "终结", "名片", "徽标", "方案", "包裹", "包",
)


def iter_items(path):
    """流式遍历 {"hash": {obj}} —— 逐个对象 json.loads，避免整读 220MB"""
    data = open(path, encoding="utf-8").read()
    n = len(data)
    i = 0
    while True:
        m = KEY_RE.search(data, i)
        if not m:
            return
        h = m.group(1)
        j = m.end() - 1
        depth = 0
        instr = False
        esc = False
        k = j
        while k < n:
            c = data[k]
            if instr:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    instr = False
            else:
                if c == '"':
                    instr = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
            k += 1
        yield h, data[j:k + 1]
        i = k + 1


def diag():
    targets = set(sys.argv[2:]) or {"1305696042", "2022851842", "1534425128", "2180162035"}
    for h, blob in iter_items(RAW):
        if h in targets:
            o = json.loads(blob)
            dp = o.get("displayProperties", {})
            print(h, "|", dp.get("name"), "|", o.get("itemTypeDisplayName"),
                  "| tier", (o.get("inventory") or {}).get("tierTypeName"),
                  "| cats", o.get("itemCategoryHashes"))


def build():
    t = time.time()
    out = {}
    types = {}
    for h, blob in iter_items(RAW):
        o = json.loads(blob)
        dp = o.get("displayProperties", {})
        name = (dp.get("name") or "").strip()
        if not name or not dp.get("hasIcon"):
            continue
        ty = o.get("itemTypeDisplayName") or ""
        # 银币区里混着「合成纤维模板」这类货币/空类型的材料包（类型名不在这张关键词表里），
        # 只靠类型名会把它们整条丢掉，导致商店少两格，这里按名字补一条。
        if not (any(k in ty for k in KEEP_TYPE_KEYWORDS) or "模板" in name or ty == "货币"):
            continue
        types[ty] = types.get(ty, 0) + 1
        tier = (o.get("inventory") or {}).get("tierTypeName") or o.get("tierTypeName") or ""
        out[h] = [name, ty, tier, dp.get("icon") or "", o.get("screenshot") or ""]
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(OUT) / 1048576
    print(f"写出 {len(out)} 条 → {OUT} ({size:.2f} MB)，用时 {time.time() - t:.1f}s")
    for ty, c in sorted(types.items(), key=lambda x: -x[1]):
        print(f"   {ty}: {c}")


if __name__ == "__main__":
    if "--diag" in sys.argv:
        diag()
    else:
        build()
