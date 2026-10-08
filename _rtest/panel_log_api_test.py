"""自测：面板引擎日志端点 /api/bot/engine_log（尾读）+ /api/bot/open_log_dir

跑法（仓库根目录）：.venv\\Scripts\\python.exe _rtest\\panel_log_api_test.py
断言点：文件不存在不抛异常；tail 夹在 1..2000；超过 128KB 只回读末尾且末行正确；
        坏字节不炸解码；打开文件夹端点只验返回结构（os.startfile 被 stub，不弹窗口）。
"""
import asyncio
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _import_webui():
    """正常 import webui；若 destiny_data 还没把 _MAINT_WAIT_MAX 提前定义好（工作区里
    别人未完成的改动：常量在 _wait_maint_clear 之后才赋值，import 直接 NameError），
    就在内存里先补一个同名常量再 exec 源码——只影响本测试进程，不改任何文件。"""
    try:
        import webui
        return webui, "正常 import"
    except NameError as exc:
        if "_MAINT_WAIT_MAX" not in str(exc):
            raise
    src_path = os.path.join(ROOT, "destiny_data.py")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    mod = types.ModuleType("destiny_data")
    mod.__file__ = src_path
    sys.modules["destiny_data"] = mod           # webui 里的 import destiny_data 会命中它
    exec(compile("_MAINT_WAIT_MAX = 1800\n" + src, src_path, "exec"), mod.__dict__)
    import webui
    return webui, "preamble 内存注入 _MAINT_WAIT_MAX 后 import"


webui, IMPORT_NOTE = _import_webui()


def run(coro):
    return asyncio.run(coro)


def main():
    print("import webui：", IMPORT_NOTE)
    tmp = tempfile.mkdtemp(prefix="d2_eng_log_")
    log = os.path.join(tmp, "exe_stdout.log")
    cfg = {"p": log}
    # 只改本进程里的引用，让端点的日志路径落到临时目录
    webui.d2._writable_path = lambda name: cfg["p"]
    api = webui.bot_engine_log

    # 1) 文件不存在：exists=False、lines=[]，不抛异常
    r = run(api())
    assert r["exists"] is False and r["lines"] == [] and r["size"] == 0, r
    assert r["path"] == log and isinstance(r["mtime"], float), r
    print("[1] 文件不存在 ->", r)

    # 2) 3000 行日志：tail=10 取最后 10 行；tail 超界被夹到 1..2000
    with open(log, "w", encoding="utf-8") as f:
        f.write("".join(f"line {i:04d}\n" for i in range(3000)))
    r = run(api(tail=10))
    assert r["exists"] is True and r["size"] == os.path.getsize(log), r
    assert r["lines"] == [f"line {i:04d}" for i in range(2990, 3000)], r["lines"]
    r0 = run(api(tail=0))
    assert r0["lines"] == ["line 2999"], r0["lines"]          # 下界夹到 1
    rbig = run(api(tail=99999))
    assert len(rbig["lines"]) == 2000, len(rbig["lines"])     # 上界夹到 2000
    assert rbig["lines"][0] == "line 1000" and rbig["lines"][-1] == "line 2999", rbig["lines"]
    print(f"[2] tail=10 末行 {r['lines'][-1]!r}｜tail=0 -> {r0['lines']}｜"
          f"tail=99999 -> {len(rbig['lines'])} 行，首行 {rbig['lines'][0]!r}")

    # 3) 超过 128KB：只回读末尾；文件头的内容不该出现；坏字节按 replace 处理不抛
    with open(log, "w", encoding="utf-8") as f:
        for i in range(50):
            f.write(f"BIG{i:02d} " + "z" * 4000 + "\n")
        f.write("LAST-2\nLAST-1\nLAST-0\n")
    with open(log, "ab") as f:
        f.write(b"\xff\xfe bad-bytes line\n")
    size = os.path.getsize(log)
    assert size > 128 * 1024, size
    r = run(api(tail=3))
    assert r["size"] == size and r["exists"] is True, r
    assert r["lines"][0] == "LAST-1" and r["lines"][1] == "LAST-0", r["lines"]
    assert "bad-bytes line" in r["lines"][2], r["lines"]
    assert "BIG00" not in "\n".join(r["lines"]), "超过 128KB 的部分被整份读进来了"
    print(f"[3] {size} B 日志 tail=3 -> {[x[:24] for x in r['lines']]}（BIG00 不在结果里=只读了末尾）")

    # 4) 路径指向目录（读不到）：exists=False，不抛
    cfg["p"] = tmp
    r = run(api())
    assert r["exists"] is False and r["lines"] == [], r
    print("[4] 路径不可读（目录）-> exists =", r["exists"])
    cfg["p"] = log

    # 5) open_log_dir：真实调用会弹资源管理器，stub 掉 startfile 只验返回结构
    calls = []
    real = getattr(os, "startfile", None)
    os.startfile = lambda p: calls.append(p)          # noqa: S606 测试替身
    try:
        r = webui.open_log_dir()
    finally:
        if real is None:
            del os.startfile
        else:
            os.startfile = real
    assert r["ok"] is True and r["dir"] == tmp and calls == [tmp], (r, calls)
    print("[5] open_log_dir（stub startfile）->", r)

    print("全部断言通过")


if __name__ == "__main__":
    main()
