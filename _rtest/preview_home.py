import ast, asyncio, sys, pathlib
src = pathlib.Path("webui.py").read_text(encoding="utf-8")
tree = ast.parse(src)
page = None
for node in tree.body:
    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "INDEX":
        page = node.value.value
assert page, "INDEX not found"
navcss = ("<style>.d2nav{display:flex;gap:8px;justify-content:center;margin:0 0 16px}"
 ".d2nav .nv{color:#cfd8e3;background:#141c2e;border:1px solid #2c3a52;border-radius:8px;"
 "padding:9px 20px;font-size:14px;text-decoration:none}"
 ".d2nav .nv.on{background:#2f6edb;border-color:#2f6edb;color:#fff;font-weight:bold}</style>")
nav = navcss + "<div class='d2nav'><a class='nv on' href='/'>玩家查询</a><a class='nv' href='/weapons'>武器查询</a><a class='nv' href='/perks'>Perk查询</a></div>"
html = page.replace("__NAV__", nav)
pathlib.Path("_rtest/home_preview.html").write_text(html, encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(".").resolve()))
from card_render import html_to_png, close
async def main():
    png = await html_to_png(html, width=1220, scale=1)
    pathlib.Path("_rtest/home_preview.png").write_bytes(png)
    await close()
asyncio.run(main())
print("ok")
