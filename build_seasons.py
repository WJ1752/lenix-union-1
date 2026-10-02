"""构建赛季索引 manifest_index/seasons.json（来自 DestinySeasonDefinition，简体中文）

条目：{number, name, start, end, bg, icon, art, prog, pres}
  - name 为中文赛季名（Manifest zh-chs），拿不到时回落 English/friendlyName
  - start/end 为 YYYY-MM-DD；end 是哨兵值（2099）时按「当前赛季」处理
  - bg 赛季背景图（历史赛季卡片做底图用；S10–S15 官方 Manifest 里没有）
  - icon 赛季主视觉小图（displayProperties.icon，150×150）：缺少 bg 的赛季拿它当底图，
    保证 /生涯 赛季网格每一格都有真图/底色，不会出现空白格
  - art 赛季主视觉（key art）大图。取值规则：优先 _ART_OVERRIDE 里人工确认的官方 key art，
    否则回落 bg（S8/S9/S16–S28 的 backgroundImagePath 本身就是赛季官方主视觉）。
    S10–S15 官方 Manifest 没有 backgroundImagePath，见 _ART_OVERRIDE 注释说明来源。
  - prog 赛季通行证「奖励等级」的 progressionHash：把玩家 characterProgressions 里的
    等级对回赛季（赛季定义里的 seasonPassProgressionHash 常年是 0，不能用；
    真正的 hash 在 DestinySeasonPassDefinition.rewardProgressionHash）

用途：/生涯 面板的赛季网格（赛季名 / 起止 / 天数 / 赛季等级）。
单独重建：python build_seasons.py
（build_manifest.py 建全量索引时也会调用 build_seasons()）
"""
import json
import os


# S10–S15 官方 key art（主视觉）人工映射表。
# 来源与依据：
#   * Bungie Manifest 的 DestinySeasonDefinition 对这 6 个赛季没有 backgroundImagePath
#     （英文/中文 manifest 都没有），/img/destiny_content/seasons/backgrounds/
#     background_season_{10..15}.{jpg,png} 及 4500+ 组合全部 404（实测）。
#   * 官方 key art 当年只挂在 bungie.net 赛季落地页（SeasonOfTheWorthy…SeasonOfTheLost）上，
#     该页现已 SPA 化、取不到原图 URL；下列 URL 为同一批官方 key art 原图的 Destinypedia
#     赛季页主图镜像（destiny.wiki.gallery），逐张人工看过确认是「有人物立绘的宽幅 key art」。
#     取值时为直接 200（非重定向），尺寸：S10 1920×1080 / S11 3840×2160 / S12 2560×1440 /
#     S13 3840×2160 / S14 3840×2160 / S15 2048×1152。
_ART_OVERRIDE = {
    10: "https://destiny.wiki.gallery/images/5/56/SotWCover.jpg",                  # 英杰赛季：安娜·布雷 + 萨瓦拉
    11: "https://destiny.wiki.gallery/images/b/b1/Season_of_Arrivals_Banner.jpg",  # 影临赛季：厄里斯·莫恩
    12: "https://destiny.wiki.gallery/images/b/be/SotHFullRes.jpg",                # 狂猎赛季：希乌阿拉斯
    13: "https://destiny.wiki.gallery/images/2/2e/SotC.jpg",                       # 天选赛季：查厄托
    14: "https://destiny.wiki.gallery/images/c/ca/SotS.jpg",                       # 永夜赛季：米瑟拉克斯
    15: "https://destiny.wiki.gallery/images/e/e2/SotL.jpg",                       # 神隐赛季：玛拉·索夫
}

# S8/S9/S10 赛季徽标（/生涯 赛季格左上角 .sico 用）人工映射。
# 为什么不用 displayProperties.icon：
#   * S8/S9 的 icon URL 虽还在，但 Bungie 在同一 URL 上换过文件内容（实测 S8 已变 416×416、
#     Last-Modified 2026-04，不再是当年徽标）；
#   * S10 当年 Manifest 里就没有 icon（hasIcon=false）。
# 改用 Manifest 里各赛季头衔印记（seal）展示节点 displayProperties.icon：
# 200×200 官方徽记（不朽/黎明/全知全能），URL 带 32 位内容 hash，内容不会变。
# 已逐张下载验证（HTTP 200、PIL 尺寸 200×200 RGBA）。
_ICON_OVERRIDE = {
    8: "https://www.bungie.net/common/destiny2_content/icons/00364cdc6a352f251b1e46244176d0a6.png",
    9: "https://www.bungie.net/common/destiny2_content/icons/df8549cf24eb5b90b92a15d11599d88f.png",
    10: "https://www.bungie.net/common/destiny2_content/icons/727afea7b30642ef77fc1bbbf1a5452f.png",
}


def _iso(v: str | None) -> str:
    return (v or "")[:10]


def build_seasons(client, base: str, paths: dict, out_dir: str = "manifest_index") -> list:
    raw = json.loads(client.get(base + paths["DestinySeasonDefinition"]).text)
    passes = json.loads(client.get(base + paths["DestinySeasonPassDefinition"]).text)
    out = []
    for d in raw.values():
        num = d.get("seasonNumber")
        if not num:
            continue
        dp = d.get("displayProperties", {})
        start = _iso(d.get("startDate"))
        end = _iso(d.get("endDate"))
        if not start:            # 早期赛季（本体/前 7 赛季）Manifest 没给起止时间，跳过
            continue
        bg = base + d["backgroundImagePath"] if d.get("backgroundImagePath") else ""
        icon = _ICON_OVERRIDE.get(int(num)) or (base + dp["icon"] if dp.get("icon") else "")
        art = _ART_OVERRIDE.get(int(num)) or bg
        # S27（溯回）起 seasonPassList 有多条：真正的赛季 pass + 活动 pass（铁旗/凯旋等），
        # 全量记录（rew/pres + pass 名），运行时按「pass 名 ⊆ 赛季名」挑主条目；
        # prog/pres 仍指向第 0 条，兼容旧消费方。
        passes_out = []
        for sp in (d.get("seasonPassList") or []):
            sph = (sp or {}).get("seasonPassHash")
            pdef = passes.get(str(sph), {}) if sph else {}
            passes_out.append({
                "rew": int(pdef.get("rewardProgressionHash") or 0),
                "pres": int(pdef.get("prestigeProgressionHash") or 0),
                "name": ((pdef.get("displayProperties") or {}).get("name") or "").strip(),
            })
        out.append({
            "number": int(num),
            "name": dp.get("name") or f"第 {num} 赛季",
            "start": start,
            "end": end,
            "bg": bg,
            "icon": icon,
            "art": art,
            "prog": passes_out[0]["rew"] if passes_out else 0,
            "pres": passes_out[0]["pres"] if passes_out else 0,
            "passes": passes_out,
        })
    out.sort(key=lambda s: s["number"])
    json.dump(out, open(os.path.join(out_dir, "seasons.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    return out


if __name__ == "__main__":
    import httpx

    for _line in open(".env", encoding="utf-8"):
        if "=" in _line and not _line.startswith("#"):
            _k, _, _v = _line.strip().partition("=")
            os.environ.setdefault(_k, _v)
    os.makedirs("manifest_index", exist_ok=True)
    _c = httpx.Client(timeout=120, headers={"X-API-Key": os.environ.get("BUNGIE_API_KEY", "")})
    _m = _c.get("https://www.bungie.net/Platform/Destiny2/Manifest/").json()["Response"]
    _paths = _m["jsonWorldComponentContentPaths"]["zh-chs"]
    _out = build_seasons(_c, "https://www.bungie.net", _paths)
    print("赛季条目:", len(_out))
    for _s in _out:
        _st = "-"
        if _s["art"]:
            try:
                _r = _c.get(_s["art"])
                _st = _r.status_code
            except Exception as _e:  # noqa: BLE001
                _st = f"ERR {str(_e)[:40]}"
        print(f"  S{_s['number']:>2} {_s['name']}  art[{_st}] {_s['art'] or '(空)'}")
    print("art 非空:", sum(1 for _s in _out if _s["art"]), "/", len(_out))
