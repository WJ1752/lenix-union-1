"""面板端多语言验证：/catalog 注入的别名表、/api/suggest、/perks、/armorsets。

    .venv/Scripts/python.exe _rtest/test_i18n_web.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webui
from fastapi.testclient import TestClient

c = TestClient(webui.app)
OK = FAIL = 0


def check(label, cond, extra=""):
    global OK, FAIL
    OK, FAIL = OK + bool(cond), FAIL + (not bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {label} {extra}")


r = c.get("/catalog")
html = r.text
check("/catalog 200", r.status_code == 200, f"({len(html)} 字节)")
check("别名表注入英文词 handcannon", '"handcannon":' in html or '"handcannon":"' in html)
check("别名表注入繁体词", "手炮" in html and ("脈衝步槍" in html or "手持加農砲" in html))
check("多词连读 JS 已注入", "英文/繁体多词条连读" in html)
check("图鉴 haystack 含 en/cht 字段", "(d.en||'')" in html and "(d.cht||'')" in html)

j = c.get("/api/catalog/index").json()
sample = next(v for v in j.values() if v.get("en"))
check("图鉴索引带 en/cht 字段", bool(sample.get("en") and sample.get("cht")),
      f"样例 {sample.get('n')} / {sample.get('en')} / {sample.get('cht')}")

s = c.get("/api/suggest", params={"type": "weapon", "q": "gjallar"}).json()
check("联想 英文名 gjallar → 加拉尔号角",
      any("加拉尔号角" in i["n"] for i in s["items"]), str(s)[:90])
s2 = c.get("/api/suggest", params={"type": "weapon", "q": "龍之氣息"}).json()
check("联想 繁体名 → 龙息", any("龙息" in i["n"] for i in s2["items"]), str(s2)[:90])

p = c.get("/perks", params={"q": "Incandescent"}).text
check("面板 perk 查英文 → 辉耀炽热", "辉耀炽热" in p)
a = c.get("/armorsets", params={"q": "Seventh Seraph"}).text
check("面板套装查英文 → 第七炽天使", "第七炽天使" in a)
a2 = c.get("/armorsets", params={"q": "第七熾天使"}).text
check("面板套装查繁体 → 第七炽天使", "第七炽天使" in a2)

print(f"\n通过 {OK} / 失败 {FAIL}")
sys.exit(1 if FAIL else 0)
