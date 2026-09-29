"""抓取 Starside 异域武器页的「↑升金 / 催化剂」文案 → manifest_index/exotic_catalysts_zh.json

来源: https://starside.work/exotic-weapon/index.html （静态页，约 141 把金枪）
Bungie 的 Manifest 里只有一半催化带数值（investmentStats），而且不少金枪的催化物品
没有中文名/名字对不上（如「低语催化」≠「蠕虫低语催化」），Clarity 社区那份又只有英文，
所以催化数值与效果文案以这份人工整理的页面为准，{武器名: 中文文案（可多行）}。
幂等：重复跑覆盖。
"""
import json
import re
from html import unescape

import httpx

URL = "https://starside.work/exotic-weapon/index.html"
OUT = "manifest_index/exotic_catalysts_zh.json"

_TAG = re.compile(r"<[^>]+>")


def _text(html: str) -> str:
    """HTML 片段 → 纯文本（<br> 转换行，其余标签去掉）"""
    s = re.sub(r"<br\s*/?>", "\n", html)
    s = re.sub(r"</span>\s*<span", "</span> <span", s)  # 「↑无双刀锋4 层」→ 补回空格
    s = _TAG.sub("", s)
    s = unescape(s)
    s = re.sub(r"[ \t\u3000]+", " ", s)
    return "\n".join(x.strip() for x in s.split("\n") if x.strip())


def parse(html: str) -> dict:
    out = {}
    for art in html.split('<article class="rec r-exotic')[1:]:
        nm = re.search(r'<div class="r-nm exo">(.*?)</div>', art, re.S)
        if not nm:
            continue
        name = _text(nm.group(1))
        if not name:
            continue
        # 只看「异域特性」那一格：右侧评测格也会用 <span class="exotic"> 高亮武器名，
        # 不框住范围会把「某某武器一键删除 80% 输出场景…」这种评测当催化抓进来。
        m = re.search(r'<div class="r-cell r-txt r-perk">(.*?)(?=<div class="r-cell|</article>)',
                      art, re.S)
        scope = m.group(1) if m else art
        # 升金/催化文案都写在含 <span class="exotic">↑…</span> 的段落里；
        # 有的整段就是数值（「4 层 = +155%…」），有的只是标题 + <br> 说明。
        lines = []
        for p in re.findall(r"<p\b[^>]*>(.*?)</p>", scope, re.S):
            if 'class="exotic"' not in p:
                continue
            t = _text(p).strip()
            if t and t not in lines:
                lines.append(t)
        if lines:
            out[name] = "\n".join(lines)
    return out


if __name__ == "__main__":
    r = httpx.get(URL, timeout=60, follow_redirects=True)
    r.raise_for_status()
    data = parse(r.text)
    json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"解析 {len(data)} 把金枪的催化文案 → {OUT}")
