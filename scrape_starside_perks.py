"""抓取 Starside 武器 PERK 详解（全中文+精确数值）→ manifest_index/perk_zh.json
来源: https://starside.work/weapon-perks/index.html （静态页，约 406 条）
幂等：重复跑覆盖。页面更新后重跑即可。
"""
import json
import re
from html.parser import HTMLParser

import httpx

URL = "https://starside.work/weapon-perks/index.html"


class Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []          # [{name, text}]
        self.cur = None          # 当前 article 状态
        self.buf = []
        self.in_name = False
        self.section = ""
        self.in_h2 = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "article" and "rec" in (a.get("class") or ""):
            self.cur = {"name": "", "paras": []}
        if tag == "div" and self.cur is not None and "r-nm" in (a.get("class") or ""):
            self.in_name = True
            self.buf = []
        elif tag == "p" and self.cur is not None:
            self.buf = []

    def handle_endtag(self, tag):
        if self.cur is None:
            return
        if tag == "div" and self.in_name:
            self.cur["name"] = "".join(self.buf).strip()
            self.in_name = False
        elif tag == "p":
            txt = "".join(self.buf).strip()
            if txt:
                self.cur["paras"].append(txt)
        elif tag == "article":
            if self.cur and self.cur["name"]:
                self.items.append(self.cur)
            self.cur = None

    def handle_data(self, data):
        if self.in_name or self.cur is not None:
            self.buf.append(data)


r = httpx.get(URL, timeout=30, verify=False,
              headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
r.raise_for_status()

p = Parser()
p.feed(r.text)
items = p.items

out = {}
dups = []
for it in items:
    name = it["name"]
    text = "\n".join(it["paras"])
    if name in out:
        dups.append(name)
        continue
    out[name] = {"text": text}

json.dump(out, open("manifest_index/perk_zh.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print(f"抓取 {len(items)} 条，入库 {len(out)} 条（重名跳过 {len(dups)}）")
