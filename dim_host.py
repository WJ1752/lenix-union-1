"""内置 DIM（官方开源版构建产物）挂载。

DIM 源码独立构建（见 README 变更日志），构建产物放在 dim_app/ 目录，
本模块在 webui 启动时把它挂到 /dim 路径下，并在 index.html 里注入一段
<style>，按 .env 的 DIM_HIDE_NAV（逗号分隔）隐藏顶部导航里不需要的功能项。

可选值对应 DIM 导航链接的路径后缀：
  inventory, progress, vendors, records, loadouts, organizer, about, whats-new
留空或未配置则只隐藏 About / What's New。
"""
import logging
import os
import re
import sys
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from starlette.responses import RedirectResponse

log = logging.getLogger("dim_host")


def _dim_app_dir() -> Path | None:
    """定位 dim_app 目录：源码仓库 → PyInstaller 资源 → exe 同目录。"""
    exe_dir = Path(sys.executable).parent
    candidates = [
        Path(__file__).parent / "dim_app",
        Path(sys._MEIPASS) / "dim_app" if hasattr(sys, "_MEIPASS") else None,
        exe_dir / "dim_app",
        exe_dir / "_internal" / "dim_app",
    ]
    for c in candidates:
        if c and (c / "index.html").is_file():
            return c
    return None


def _load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
            if m:
                env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


# 注入的 CSS：按导航链接 href 后缀隐藏对应功能项
_HIDE_CSS = """
<style id="dim-hide-nav">
{rules}
a.menuItem[href$="/about"], a.menuItem[href$="/whats-new"] {{ display: none !important; }}
</style>
"""


def _inject_hide_css(index_html: Path, hide: list[str]) -> None:
    html = index_html.read_text(encoding="utf-8", errors="ignore")
    if 'id="dim-hide-nav"' in html:
        return  # 已注入过（换构建产物后需重新注入）
    rules = "\n".join(
        f'a.menuItem[href$="/{name.strip().lower()}"] {{ display: none !important; }}'
        for name in hide
        if name.strip()
    )
    injected = _HIDE_CSS.format(rules=rules)
    if "</head>" in html:
        html = html.replace("</head>", injected + "</head>", 1)
    else:
        html = injected + html
    index_html.write_text(html, encoding="utf-8")
    log.info("dim_host: 已按 DIM_HIDE_NAV 注入导航隐藏规则: %s", hide)


def mount_dim(app, base_dir: Path) -> bool:
    """把 DIM 挂到 /dim。返回是否挂载成功。"""
    dim_dir = _dim_app_dir()
    if dim_dir is None:
        log.warning("dim_host: 未找到 dim_app/index.html，内置 DIM 不启用，旧 DIM 板块继续生效")
        return False

    env = _load_env(base_dir / ".env")
    hide = [x for x in env.get("DIM_HIDE_NAV", "").split(",") if x.strip()]
    try:
        _inject_hide_css(dim_dir / "index.html", hide)
    except Exception as e:  # 注入失败不拦启动
        log.warning("dim_host: 注入隐藏 CSS 失败(%s)，DIM 全功能开放", e)

    # /dim → /dim/（StaticFiles(html=True) 才会正确给 index.html）
    @app.get("/dim", include_in_schema=False)
    async def _dim_redirect():
        return RedirectResponse("/dim/", status_code=307)

    app.mount("/dim", StaticFiles(directory=str(dim_dir), html=True), name="dim")
    log.info("dim_host: 内置 DIM 已挂载到 /dim（目录 %s）", dim_dir)
    return True


# 允许独立运行检查
if __name__ == "__main__":
    print("dim_app dir:", _dim_app_dir())
    print("env keys:", list(_load_env(Path(os.environ.get("DIM_ENV_PATH", ".env"))).keys()))
