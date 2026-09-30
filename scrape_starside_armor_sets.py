"""抓取 Starside 护甲套装效果（全中文+精确数值）→ manifest_index/armor_sets.json
来源: https://starside.work/armor-sets/index.html （静态页，约 56 套）
幂等：重复跑覆盖。页面更新后重跑即可。

结构: [{"category","name","source","tags","bonuses":[{"piece","name","text"}], "aliases":[...]}]
aliases 是社区触发词（一愿/vog/kf…），页面里没有，维护在本脚本 ALIASES 表。
"""
import json
import re
from html import unescape

import httpx

URL = "https://starside.work/armor-sets/index.html"

# 社区触发词 → 套装名。页面套装名本身也可搜索；这里补充社区叫法。
# 映射一律按页面「来源」字段核对（来源=所属突袭/地牢），别按套装名猜。
ALIASES = {
    # 突袭（按来源）
    "伟大狩猎": ["一愿套", "一愿", "遗愿套", "遗愿", "最后一愿", "lastwish", "lw"],
    "第三百夫长": ["gos套", "gos", "救赎花园"],
    "传承之誓": ["dsc套", "dsc", "深岩墓室"],
    "埃希恩记忆": ["vog套", "vog", "玻璃宝库", "玻璃拱顶"],
    "共振狂怒": ["vow套", "vow", "vod套", "vod", "门徒誓约", "门徒之誓"],
    "欧里克斯记忆": ["kf套", "kf", "王之陨落", "王陨", "国王的陨落"],
    "奈扎雷克的梦魇": ["ron套", "ron", "rotn", "梦魇根源", "噩梦根源", "噩梦"],
    "克洛塔记忆": ["ce套", "ce", "克罗塔之终", "克罗塔的末日", "克罗塔"],
    "应许之物": ["se套", "se", "救赎的边缘", "救赎边缘", "救世边缘"],
    "集体心灵": ["dp套", "dp", "永恒沙漠"],
    "任性心灵套装": ["永恒沙漠史诗", "dp史诗"],
    # 地牢（按来源）
    "灵巫华服": ["st套", "st", "破碎王座"],
    "变节者利刃": ["poh套", "poh", "异端深渊"],
    "终曲": ["prophecy", "预言"],
    "渴望回响": ["goa套", "goa", "贪婪之握"],
    "深渊探索者": ["duality", "二象性"],
    "定制特机": ["sow套", "sow", "守望者尖塔"],
    "傀儡邪神": ["gotd套", "gotd", "深渊机灵"],
    "黑暗纪元": ["wr套", "wr", "战争领主的废墟"],
    "太空漫步": ["vh套", "vh", "晚星之主"],
    "剥露者": ["sd套", "sd", "分离教义"],
    # 目的地
    "第七炽天使": ["炽天使套装", "炽天使套", "炽天使", "发射基地"],
}


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)          # 去内嵌标签，留纯文本
    s = re.sub(r"\s+", " ", s)
    return unescape(s).strip()


def parse(html: str) -> list[dict]:
    sets = []
    cat = ""
    for m in re.finditer(
            r'<h2 class="cat-head"><span>(.*?)</span></h2>'
            r'|<article class="set"[^>]*>(.*?)</article>', html, re.S):
        if m.group(1) is not None:
            cat = _clean(m.group(1))
            continue
        blk = m.group(2)
        name = _clean(re.search(r"<h3>(.*?)</h3>", blk, re.S).group(1))
        sm = re.search(r"<dd[^>]*>(.*?)</dd>", blk, re.S)
        source = _clean(sm.group(1)) if sm else ""
        tags = [_clean(x) for x in re.findall(r'<ul class="set-tags"[^>]*>(.*?)</ul>', blk, re.S)
                for x in re.findall(r"<li>(.*?)</li>", x, re.S)]
        bonuses = []
        for b in re.finditer(
                r'<span class="piece">(.*?)</span>.*?<span class="bonus-name">(.*?)</span>'
                r'.*?<div class="bonus-body"[^>]*>(.*?)</div>', blk, re.S):
            bonuses.append({"piece": _clean(b.group(1)),
                            "name": _clean(b.group(2)),
                            "text": _clean(b.group(3))})
        if name and bonuses:
            sets.append({"category": cat, "name": name, "source": source,
                         "tags": tags, "bonuses": bonuses})
    return sets


def main():
    r = httpx.get(URL, timeout=30, verify=False,
                  headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
    r.raise_for_status()
    sets = parse(r.text)
    for s in sets:
        s["aliases"] = ALIASES.get(s["name"], [])
    json.dump(sets, open("manifest_index/armor_sets.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"入库 {len(sets)} 套：" + "、".join(s["name"] for s in sets))


if __name__ == "__main__":
    main()
