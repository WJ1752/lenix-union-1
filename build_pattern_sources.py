"""建立「锻造图案 → 掉落来源」索引（小日向式按副本/活动分类）

Bungie Manifest 的藏品定义（DestinyCollectibleDefinition）里每条藏品都带
`sourceString`（游戏内收藏品页显示的"来源：…"），用它把图案按**副本/活动**归类，
而不是按武器槽位分成"主武器/特殊/重武器"三大坨。

产出 manifest_index/pattern_sources.json：
  {"梦魇根源手炮": {"group": "梦魇根源", "raw": "来源：“梦魇根源”突袭", "dungeon": true}, ...}
`dungeon` = 来源是突袭/地牢（卡片里这两类排最前）。

用法：.venv\\Scripts\\python build_pattern_sources.py
"""
import json
import os
import re

import httpx

ENV_FILE = ".env"
IDX = "manifest_index"


def _api_key() -> str:
    if os.getenv("BUNGIE_API_KEY"):
        return os.getenv("BUNGIE_API_KEY", "")
    try:  # 与 destiny_data 一样从 .env 取
        for line in open(ENV_FILE, encoding="utf-8"):
            k, _, v = line.strip().partition("=")
            if k == "BUNGIE_API_KEY":
                os.environ["BUNGIE_API_KEY"] = v
                return v
    except OSError:
        pass
    return ""


# 来源里没写"突袭/地牢"但确实是副本的（遗落利坦、二象性…），单独认一下
DUNGEON_NAMES = {
    "最后一愿", "救赎花园", "深岩墓室", "玻璃拱顶", "门徒誓约", "梦魇根源",
    "克洛塔的末日", "国王的陨落", "救赎的边缘", "遗落利坦", "利维坦", "众神殿",
    "地牢二象性", "二象性", "预言", "贪婪之握", "守望者尖塔", "深渊机灵",
    "晚星之主", "分离教义", "平衡", "战争领主的废墟", "梦魇时间试炼",
}
# 同一来源的多种写法归一个组
ALIAS = {
    "泉源首领": "泉源",
    "由利维坦上的奇珍异兽园获得": "遗落利坦",
    "探索苍白之心": "探索苍白之心",
    "开启传说记忆水晶并获得阵营升级包": "记忆水晶/阵营升级",
}


def normalize(raw: str) -> tuple[str, bool]:
    """'来源：“深岩墓室”突袭' → ('深岩墓室', True)"""
    t = re.sub(r"^来源\s*[：:]\s*", "", (raw or "").strip())
    t = re.sub(r"[“”\"']", "", t).strip().strip("。.，, ")
    is_dungeon = ("突袭" in t) or ("地牢" in t)
    t = re.sub(r"(突袭|地牢)$", "", t).strip()
    for k, v in ALIAS.items():
        if k in t:
            t = v
            break
    if t in DUNGEON_NAMES:
        is_dungeon = True
    if not t:
        t = "其他来源"
    if len(t) > 14:  # 来源串偶尔是一整句话，截一下当组名
        t = t[:14] + "…"
    return t, is_dungeon


def main():
    c = httpx.Client(timeout=180, headers={"X-API-Key": _api_key()})
    m = c.get("https://www.bungie.net/Platform/Destiny2/Manifest/").json()["Response"]
    path = m["jsonWorldComponentContentPaths"]["zh-chs"]["DestinyCollectibleDefinition"]
    print("下载藏品定义…")
    col = json.loads(c.get("https://www.bungie.net" + path).text)
    print("藏品条目:", len(col))

    # itemHash → 各来源串（同一把武器可能有多条藏品：不同版本/不同来源）
    by_item: dict[str, list[str]] = {}
    for cd in col.values():
        s = (cd.get("sourceString") or "").strip()
        if s and "无法从收藏品" not in s:
            by_item.setdefault(str(cd.get("itemHash")), []).append(s)

    names: dict[str, list[str]] = {}
    for f in ("weapons_full.json", "weapons.json"):
        try:
            data = json.load(open(os.path.join(IDX, f), encoding="utf-8"))
        except OSError:
            continue
        for h, w in data.items():
            nm = w.get("name")
            if not nm:
                continue
            for s in by_item.get(str(h), []):
                names.setdefault(nm, []).append(s)

    out = {}
    for nm, srcs in names.items():
        # 副本来源优先（突袭/地牢），再取最短的那条（最短通常是"来源：XX突袭"这种干净的）
        srcs = sorted(set(srcs),
                      key=lambda s: (0 if ("突袭" in s or "地牢" in s) else 1, len(s)))
        group, dungeon = normalize(srcs[0])
        out[nm] = {"group": group, "raw": srcs[0], "dungeon": dungeon}

    json.dump(out, open(os.path.join(IDX, "pattern_sources.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("来源条目:", len(out))
    groups: dict[str, int] = {}
    for v in out.values():
        groups[v["group"]] = groups.get(v["group"], 0) + 1
    for g, n in sorted(groups.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {n:3d}  {g}")


if __name__ == "__main__":
    main()
