"""构建武器图鉴索引 manifest_index/weapon_catalog.json（离线，读现成缓存）

= manifest_index/weapon_filter_index.json 的全部筛选字段 + 品质（Manifest tierType）。
图鉴页(/catalog)只拉这一份文件，筛选与计数全在浏览器里算，不再往回打接口。

字段：n 名称 / t 类型 / a 弹药 / c 槽位 / e 元素 / f 框架 / p 可选特性名
      r 射速 / cr 可锻造 / g 锻造来源 / x 异域 / q 品质(tierType) / i 图标 / w 水印

改过 build_weapon_filter_index.py 之后要重跑本脚本，否则图鉴与 /武器筛选 不同步。
生成后要同步三处：源码 manifest_index/、D2Query.spec 的 datas、dist\D2Query\_internal\manifest_index\
"""
import json

WF = json.load(open("manifest_index/weapon_filter_index.json", encoding="utf-8"))
RAW = json.load(open("manifest_index/raw_items.json", encoding="utf-8"))

# 品质用 Manifest 的 inventory.tierType：6 异域 / 5 传说 / 4 稀有 / 3 罕见 / 2 普通
out = {}
for h, w in WF.items():
    tier = (RAW.get(h) or {}).get("inventory", {}).get("tierType") or 0
    out[h] = dict(w, q=tier)

json.dump(out, open("manifest_index/weapon_catalog.json", "w", encoding="utf-8"),
          ensure_ascii=False, separators=(",", ":"))

from collections import Counter  # noqa: E402

print("图鉴条目:", len(out))
print("品质分布:", dict(sorted(Counter(v["q"] for v in out.values()).items(), reverse=True)))
