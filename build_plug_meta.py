# -*- coding: utf-8 -*-
"""从仓库本地 raw manifest 缓存生成 manifest_index/plug_meta.json。

plug_meta.json 结构：{plug_hash: {"name": 中文名, "icon": 完整图标 URL}}
用途：weapon_usage.py 在把社区使用率数据 join 成中文时的兜底词典
（第一优先级是 weapons_full.json 各武器自己的 plugs 池，查不到的 hash 才来这里）。

筛选口径（"会出现在武器 socket 类别里的插件"）：
  1. 先从 weapons_full.json 所有武器的 plugs 结构里收集 plug hash，
     再查 raw_items 得到这些 hash 的 plugCategoryHash → 得到全部武器 socket 类别（约 180 个）；
  2. 保留 raw_items 里 plugCategoryHash 属于该集合的物品（覆盖 1337/1338 的已知武器插件，
     并额外带上各 socket 类别里的其他可插插件），要求有中文名且有图标；
  3. 空插槽 / 占位类（如「空星相插槽」「空模组插槽」）按名称黑名单剔除。

用法：python build_plug_meta.py   （纯本地，无需网络）
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows GBK 控制台兜底

MI = "manifest_index"

# 占位/空插槽类插件，出现在同一 socket 类别里但没有信息量
_DENY_NAMES = {
    "空星相插槽", "空模组插槽", "空特性插槽", "空武器模组插槽", "空的插件插槽",
    "随机大师杰作", "已摧毁的插件", "未解锁的插槽",
}


def main():
    print("加载 raw_items.json（约 220MB，稍等）...")
    items = json.load(open(f"{MI}/raw_items.json", encoding="utf-8"))
    wf = json.load(open(f"{MI}/weapons_full.json", encoding="utf-8"))

    # 1) 收集 weapons_full 里出现过的全部插件 hash
    wf_hashes = set()

    def walk(o):
        if isinstance(o, dict):
            if "hash" in o and "n" in o:
                wf_hashes.add(str(o["hash"]))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for w in wf.values():
        walk(w.get("plugs"))

    # 2) 由此推导全部武器 socket 类别（plugCategoryHash 集合）
    cat_hashes = set()
    for h in wf_hashes:
        o = items.get(h)
        if o and o.get("plug"):
            cat_hashes.add(o["plug"].get("plugCategoryHash"))
    print(f"weapons_full 插件 hash {len(wf_hashes)} 个 → 武器 socket 类别 {len(cat_hashes)} 个")

    # 3) 按 socket 类别收集全部插件
    out = {}
    for h, o in items.items():
        plug = o.get("plug")
        if not plug or plug.get("plugCategoryHash") not in cat_hashes:
            continue
        dp = o.get("displayProperties") or {}
        name, icon = dp.get("name") or "", dp.get("icon") or ""
        if not name or name in _DENY_NAMES or not icon:
            continue
        if not icon.startswith("http"):
            icon = "https://www.bungie.net" + icon
        out[h] = {"name": name, "icon": icon}

    covered = sum(1 for h in wf_hashes if h in out)
    print(f"plug_meta 共 {len(out)} 条；覆盖 weapons_full 插件 {covered}/{len(wf_hashes)}")
    if covered < len(wf_hashes):
        miss = [h for h in wf_hashes if h not in out]
        print("未覆盖（多为无图标/占位条目，可忽略）：", miss[:6], "..." if len(miss) > 6 else "")

    path = os.path.join(MI, "plug_meta.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"写出 {path}（{os.path.getsize(path) // 1024} KB）")


if __name__ == "__main__":
    main()
