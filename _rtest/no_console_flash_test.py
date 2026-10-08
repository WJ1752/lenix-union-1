"""黑窗闪烁回归测试（静态扫描，不联网、不启动任何进程）

**为什么需要它**：exe 是 windowed 子系统（D2Query.spec `console=False`），本进程没有
控制台；此时任何不带隐藏标志的控制台子进程（netstat / tasklist / taskkill / wmic /
certutil …）都会让 Windows 给它**新分配一个控制台窗口**——用户看到的就是「正常使用时
每隔一阵闪一下 cmd 黑窗」。2026-10-08 实测：NapCat 看门狗每 60 秒 `onebot_connected()`
跑一次 netstat，于是每分钟闪一下；同文件里 5 处探测/清理调用（wmic 找 QQ、taskkill 杀进程树、
netstat+tasklist 清端口占用、tasklist 清引导进程）同样会闪。

这里的口径：**凡是本仓代码里创建子进程的地方，必须显式带上隐藏窗口的手段**
（`creationflags=` 里含 CREATE_NO_WINDOW / DETACHED_PROCESS，或 `startupinfo` 里
STARTF_USESHOWWINDOW + SW_HIDE）。漏一个就是一个闪烁源，靠人眼在源码里找是找不干净的
（这次就漏了 5 处），所以钉成测试。

判据是「解析 AST → 取 creationflags 表达式的源码文本 → 顺着同模块里的常量/零参函数
再解析一层」；这样 `creationflags=_NO_WIN` 与 `creationflags=_spawn_flags()` 两种写法
都能认（它们的定义体里必须有那串常量）。
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 只在仓库源码里扫（打包产物/虚拟环境/探针目录不扫）
SKIP_DIRS = {"_rtest", "build", "dist_build", "dist_new", "__pycache__", ".venv", ".git"}

SPAWN_ATTRS = {
    ("subprocess", "run"), ("subprocess", "Popen"), ("subprocess", "call"),
    ("subprocess", "check_output"), ("subprocess", "check_call"),
    ("subprocess", "getoutput"), ("subprocess", "getstatusoutput"),
    ("asyncio", "create_subprocess_exec"), ("asyncio", "create_subprocess_shell"),
}
# 认得出的「藏窗口」手段（源码文本层面）
HIDE_TOKENS = ("CREATE_NO_WINDOW", "DETACHED_PROCESS", "SW_HIDE", "STARTF_USESHOWWINDOW")

OKS = []
FAILS = []


def check(name, cond, extra=""):
    (OKS if cond else FAILS).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ← {extra}" if extra and not cond else ""))


def _dotted(node) -> str:
    """`subprocess.run` / `asyncio.create_subprocess_exec` → 'subprocess.run'"""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _src_map(tree, lines: list[str]) -> dict:
    """全文的 `名字 = 表达式` / `def 名字()` 源码文本（供再解析一层用）

    取全文而不只是模块顶部：`creationflags` 的取值可能来自函数内局部量
    （如 `startupinfo = subprocess.STARTUPINFO()` 这类写法），只认模块级会误报。
    """
    src = "\n".join(lines)
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = ast.get_source_segment(src, node) or ""
            continue
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Name):
                out[t.id] = ast.get_source_segment(src, node) or ""
    return out


def hides_window(expr_src: str, defs: dict, seen=None) -> bool:
    """表达式的源码文本里有没有「藏窗口」手段；是名字/零参调用就顺定义体再解析一层"""
    if not expr_src:
        return False
    if any(tok in expr_src for tok in HIDE_TOKENS):
        return True
    seen = seen or set()
    for name, body in defs.items():
        if not body or name in seen:
            continue
        # 表达式里引用了这个名字（`_NO_WIN` / `_spawn_flags()`）
        if name in expr_src:
            if any(tok in body for tok in HIDE_TOKENS):
                return True
            if hides_window(body, defs, seen | {name}):
                return True
    return False


def scan(path: str) -> list[str]:
    src = open(path, encoding="utf-8").read()
    lines = src.splitlines()
    tree = ast.parse(src, filename=path)
    defs = _src_map(tree, lines)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted(node.func)
        # 只看 `subprocess.xxx(...)` / `asyncio.create_subprocess_xxx(...)`
        if not any(dotted.endswith(f"{mod}.{attr}") or dotted == f"{mod}.{attr}"
                   for mod, attr in SPAWN_ATTRS):
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        flags = ast.get_source_segment(src, kw["creationflags"]) if "creationflags" in kw else ""
        startup = ast.get_source_segment(src, kw["startupinfo"]) if "startupinfo" in kw else ""
        if hides_window(flags or "", defs) or hides_window(startup or "", defs):
            continue
        try:
            where = os.path.relpath(path, ROOT)
        except ValueError:  # 换盘符的临时文件（自检用），退回绝对路径
            where = path
        bad.append(f"{where}:{node.lineno} {dotted}(...)")
    return bad


def main() -> int:
    offenders = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            scanned += 1
            offenders += scan(p)

    check(f"扫过 {scanned} 个源码文件", scanned > 20, f"只扫到 {scanned} 个，路径口径可能错了")
    check("没有「不藏窗口就创建子进程」的调用点", not offenders,
          "会闪黑窗：" + "；".join(offenders))

    # 看门狗那条 60 秒一次的 netstat 是本 bug 的元凶，单独钉住（漏了它 = 每分钟闪一下）
    nrc = open(os.path.join(ROOT, "napcat_runtime.py"), encoding="utf-8").read()
    check("napcat_runtime 定义了统一的 _NO_WIN 标志", "_NO_WIN = getattr(subprocess, \"CREATE_NO_WINDOW\"" in nrc)
    check("看门狗探测(netstat)带 _NO_WIN", "creationflags=_NO_WIN).stdout" in nrc)
    check("探测/清理 6 处子进程调用都带上了标志",
          nrc.count("creationflags=_NO_WIN") == 6, f"实际 {nrc.count('creationflags=_NO_WIN')} 处")

    print(f"\n===== 通过 {len(OKS)} / 失败 {len(FAILS)} =====")
    for f in FAILS:
        print("  FAIL:", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
