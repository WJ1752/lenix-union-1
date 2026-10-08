"""核对 /帮助 三条清单面：除刻意隐藏的 /登录 /回调 /仓库 外必须全量（30 条）。

    .venv/Scripts/python.exe _rtest/test_help_list.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bot_cards

HIDDEN = {"登录", "回调", "仓库"}

src = open(os.path.join(ROOT, "nonebot_plugins", "destiny2.py"), encoding="utf-8").read()
regs = []
for m in re.finditer(r'on_command\(', src):
    i = m.end() - 1
    depth, j = 0, i
    while j < len(src):
        if src[j] == '(':
            depth += 1
        elif src[j] == ')':
            depth -= 1
            if depth == 0:
                break
        j += 1
    body = re.sub(r"\s+", " ", src[i:j + 1])
    nm = re.match(r'\(\s*"([^"]+)"', body)
    al = re.search(r"aliases=\{([^}]*)\}", body)
    aliases = [a.strip().strip('"') for a in (al.group(1).split(",") if al else [])]
    regs.append((nm.group(1) if nm else "?", [a for a in aliases if a and not a.startswith("*")]))

want = [n for n, _ in regs if n not in HIDDEN and n != "帮助"]   # 帮助不自列
cards = open(os.path.join(ROOT, "bot_cards.py"), encoding="utf-8").read()
hc = cards[cards.index("def help_card"):cards.index("def player_card")]
hp = src[src.index("HELP_PLAIN ="):src.index("@help_query.handle")]

OK = FAIL = 0


def check(label, cond, extra=""):
    global OK, FAIL
    OK, FAIL = OK + bool(cond), FAIL + (not bool(cond))
    print(f"{'ok  ' if cond else 'FAIL'} {label} {extra}")


def listed(text):
    # 只剥整行注释：行内的 `#1234`（/绑定 玩家名#编号）不是注释，不能一起剥掉
    out = set()
    text = re.sub(r"(?m)^\s*#[^\n]*", "", text)
    for n, aliases in regs:
        for t in [n] + aliases:
            if re.search(r"/" + re.escape(t) + r"(?![\w])", text):
                out.add(n)
                break
    return out


for label, text in (("help_card()", hc), ("HELP_PLAIN", hp)):
    got = listed(text)
    miss = [n for n in want if n not in got]
    leak = [n for n in HIDDEN if n in got]
    check(f"{label} 覆盖 {len(got & set(want))}/{len(want)}", not miss, f"缺 {miss}" if miss else "")
    check(f"{label} 没有泄漏隐藏指令", not leak, f"泄漏 {leak}" if leak else "")

rd = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
rd = rd[rd.index("## QQ 指令"):rd.index("## 项目结构")]
leak = [n for n in HIDDEN for t in [n] + dict(regs)[n]
        if re.search(r"/" + re.escape(t) + r"(?![\w])", rd)]
check("README 指令章节没列隐藏指令的指令行", not leak, f"泄漏 {sorted(set(leak))}" if leak else "")
check("README 已补 /队伍配装", "/队伍配装" in rd)

print(f"\n通过 {OK} / 失败 {FAIL}")
sys.exit(1 if FAIL else 0)
