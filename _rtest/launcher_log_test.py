"""d2query_launcher 日志功能自测：会话分隔 / 上次断点回放 / 强杀提示 / 轮转 / 落盘

**为什么需要它**：windowed exe 没有控制台，launcher 把 stdout 接到 exe 同目录的
exe_stdout.log，用户靠它诊断「上次跑到哪断的」。老实现只是一个 open(path, "a")：
重启后没有会话边界、文件只会无限长大、也分不出上次是正常退出还是被强杀。这段
逻辑现在抽成了 _read_tail / _rotate / _open_log_stream，这里按真实文件逐项钉住。

全部在 tempfile 目录里造数据，绝不碰 exe 目录/仓库根目录里真实的 exe_stdout.log。

用法：.venv\\Scripts\\python.exe _rtest\\launcher_log_test.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import d2query_launcher as launcher  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAILS = []
OKS = []


def check(name, cond, extra=""):
    (OKS if cond else FAILS).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ← {extra}" if extra and not cond else ""))


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _fresh(tmp, name):
    d = os.path.join(tmp, name)
    os.makedirs(d)
    return os.path.join(d, "exe_stdout.log")


# ---------- 1. 读尾部 ----------
def test_read_tail(tmp):
    print("\n[1] _read_tail：seek 读尾部，取不到当没有历史")
    check("文件不存在 → []", launcher._read_tail(os.path.join(tmp, "nope.log")) == [])
    p = os.path.join(tmp, "tail.log")
    lines = [f"L{i:02d}-{'x' * 20}" for i in range(10)]
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    check("默认 lines 上限内取末 3 行", launcher._read_tail(p, lines=3) == lines[-3:],
          str(launcher._read_tail(p, lines=3)))
    got = launcher._read_tail(p, lines=2, max_bytes=100)
    check("max_bytes 截断后半行被丢掉，末 2 行仍准确", got == lines[-2:], str(got))


# ---------- 2. 上次被强杀：分隔行 + 断点回放 + 提示 ----------
def test_resume_crash(tmp):
    print("\n[2] _open_log_stream：会话分隔 + 上次断点 + 未正常退出提示")
    p = _fresh(tmp, "crash")
    old = ["[bot] 启动完成", "[bot] 进度 3/10 拉取清单",
           "[bot] 卡在 登录回调，最后一行输出"]
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(old) + "\n")     # 没有正常退出标记 = 上次被强杀/崩溃
    stream = launcher._open_log_stream(p)
    try:
        check("返回对象可写", stream.writable())
        stream.write("[test] 本次会话写入一行\n")
        stream.flush()
        text = _read(p)
    finally:
        stream.close()
    check("写了会话开始分隔行（含 pid）",
          "===== 会话开始 " in text and f"（pid {os.getpid()}）=====" in text, repr(text[:120]))
    check("回放块表头是「上次会话最后 3 行」",
          "----- 上次会话最后 3 行（诊断断点用）-----" in text, text)
    check("上次尾部几行原样出现在新日志里", all(line in text for line in old), text)
    check("提示上次未正常结束", "[日志] 上次会话未正常结束" in text, text)
    check("本次会话写入的内容落盘", "[test] 本次会话写入一行" in text, text)
    check("会话开始行在回放块之前", text.index("===== 会话开始 ") < text.index("----- 上次会话最后"), text)


# ---------- 3. 上次正常退出：不误报 ----------
def test_normal_exit(tmp):
    print("\n[3] 上次写了正常退出标记 → 不提示强杀")
    p = _fresh(tmp, "normal")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("[bot] 最后一次输出\n===== 会话正常结束 2026-10-08 10:00:00 =====\n")
    stream = launcher._open_log_stream(p)
    try:
        stream.flush()
        text = _read(p)
    finally:
        stream.close()
    check("旧结束标记被回放出来（证据还在）",
          "===== 会话正常结束 2026-10-08 10:00:00 =====" in text, text)
    check("没有强杀提示", "[日志] 上次会话未正常结束" not in text, text)


# ---------- 4. 上次崩在启动阶段：更早会话的结束标记不算数 ----------
def test_marker_not_inherited(tmp):
    print("\n[4] 日志里留着更早会话的结束标记 → 仍判上次未正常结束")
    p = _fresh(tmp, "inherit")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("[bot] 旧输出\n"
                 "===== 会话正常结束 2026-10-08 09:00:00 =====\n"     # 更早会话正常收了尾
                 "===== 会话开始 2026-10-08 09:30:00（pid 1）=====\n"
                 "[bot] 启动中，只打了一行就没了\n")                       # 这次崩了，没写标记
    stream = launcher._open_log_stream(p)
    try:
        stream.flush()
        text = _read(p)
    finally:
        stream.close()
    check("结束标记不是最后一行内容 → 不认",
          "[日志] 上次会话未正常结束" in text, text)

    # 更刁的形态：上次崩溃前把上一轮的尾部（以结束标记结尾）原样回放进了日志
    p2 = _fresh(tmp, "inherit2")
    with open(p2, "w", encoding="utf-8") as fh:
        fh.write("===== 会话开始 2026-10-08 09:30:00（pid 1）=====\n\n"
                 "----- 上次会话最后 2 行（诊断断点用）-----\n"
                 "===== 会话正常结束 2026-10-08 09:00:00 =====\n"
                 "----- 上次会话回放结束 -----\n\n")                    # 回放完就崩，自己一句没写
    stream = launcher._open_log_stream(p2)
    try:
        stream.flush()
        text = _read(p2)
    finally:
        stream.close()
    check("回放块里的旧标记（哪怕在最末尾）也不认",
          "[日志] 上次会话未正常结束" in text, text)


# ---------- 5. 轮转 ----------
def test_rotate(tmp):
    print("\n[5] _rotate：超过 limit 才轮转，.1→.2，只留 keep 份")
    d = os.path.join(tmp, "rot")
    os.makedirs(d)
    p = os.path.join(d, "exe_stdout.log")
    n1 = os.path.join(d, "exe_stdout.1.log")
    n2 = os.path.join(d, "exe_stdout.2.log")
    for tag in ("A", "B", "C"):
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(tag * 500)              # 超过 limit=200
        launcher._rotate(p, limit=200, keep=2)
        if tag == "A":
            check("超限后当前文件变 .1，原名腾空",
                  os.path.exists(n1) and not os.path.exists(p) and _read(n1) == "A" * 500)
        elif tag == "B":
            check("再轮转：旧 .1 → .2，新 .1 是刚才那份",
                  _read(n2) == "A" * 500 and _read(n1) == "B" * 500, f"{_read(n2)[:3]}/{_read(n1)[:3]}")
        else:
            check("只留 keep 份：没有 .3", not os.path.exists(os.path.join(d, "exe_stdout.3.log")))
            check("窗口前移：最旧的被丢", _read(n1) == "C" * 500 and _read(n2) == "B" * 500)
    small = os.path.join(d, "small.log")
    with open(small, "w", encoding="utf-8") as fh:
        fh.write("s" * 50)                   # 未超限
    launcher._rotate(small, limit=200, keep=2)
    check("没超限不动它", os.path.exists(small) and not os.path.exists(os.path.join(d, "small.1.log")))
    check("目标文件不存在也不抛异常（首次运行）",
          launcher._rotate(os.path.join(d, "never.log"), limit=1) is None)


# ---------- 6. 开不了日志时的兜底 ----------
def test_devnull_fallback(tmp):
    print("\n[6] 路径打不开 → 兜底 devnull，绝不抛")
    bad = os.path.join(tmp, "isdir")
    os.makedirs(bad)                         # 拿目录当文件必开失败
    stream = launcher._open_log_stream(bad)
    try:
        check("返回 devnull 而不是异常", stream is not None and stream.name == os.devnull,
              getattr(stream, "name", "?"))
    finally:
        stream.close()


# ---------- 7. 端到端：windowed 启动路径 + atexit 收尾 + 强杀 ----------
DRIVER_SRC = '''"""windowed 启动路径替身：stdout/stderr 置空后 import launcher 副本。

由 launcher_log_test.py 生成并运行，真实走一遍「没有控制台 → _open_log_stream
落到 sys.executable 同目录」的启动路径；kill 模式用 os._exit 跳过 atexit，模拟被强杀。"""
import os
import sys
import traceback

TMP = os.path.dirname(os.path.abspath(__file__))
MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"
PROBE = os.path.join(TMP, "_probe_%s.txt" % MODE)
sys.path.insert(0, TMP)
sys.executable = os.path.join(TMP, "fake_exe.exe")   # 骗过 launcher：日志落点改到 TMP
sys.stdout = sys.stderr = None                        # 模拟 windowed exe 没有控制台
try:
    import d2query_launcher as L
    with open(PROBE, "w", encoding="utf-8") as fh:
        fh.write("log=%s" % L._log.name)
    L.sys.stdout.write("[driver] 窗口模式写一行 %s\\n" % MODE)
    L.sys.stdout.flush()
    if MODE == "kill":
        os._exit(1)                                   # 不走 atexit = 没写正常退出标记
except BaseException:
    with open(PROBE, "w", encoding="utf-8") as fh:
        fh.write(traceback.format_exc())
    raise SystemExit(7)
'''


def _boot_dir(tmp):
    d = os.path.join(tmp, "boot")
    os.makedirs(d)
    shutil.copy(os.path.join(ROOT, "d2query_launcher.py"),
                os.path.join(d, "d2query_launcher.py"))
    for mod in ("uvicorn", "webview"):       # 空壳依赖：只走日志路径，不起服务/窗口
        with open(os.path.join(d, mod + ".py"), "w", encoding="utf-8") as fh:
            fh.write("# 空壳（launcher_log_test.py 生成）\n")
    with open(os.path.join(d, "_driver.py"), "w", encoding="utf-8") as fh:
        fh.write(DRIVER_SRC)
    return d


def _boot_run(d, mode):
    r = subprocess.run(
        [sys.executable, os.path.join(d, "_driver.py"), mode], cwd=d, timeout=120,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # 无控制台时别闪黑窗
    return r.returncode


def _last_content_line(text):
    rows = [ln for ln in text.splitlines() if ln.strip()]
    return rows[-1] if rows else ""


def test_windowed_boot(tmp):
    print("\n[7] 端到端：windowed 启动（真子进程、stdout=None、atexit 收尾、os._exit 强杀）")
    d = _boot_dir(tmp)
    log = os.path.join(d, "exe_stdout.log")
    rc = _boot_run(d, "normal")
    text = _read(log)
    check("第一次启动 rc=0，日志落在 tmp 目录（没碰 exe 目录）",
          rc == 0 and os.path.exists(log), f"rc={rc}")
    check("import 时写出会话开始行", "===== 会话开始 " in text, text)
    check("stdout 被接到日志：driver 写的那行在文件里",
          "[driver] 窗口模式写一行 normal" in text, text)
    check("正常退出 → atexit 写了结束标记，且是最后一行内容",
          _last_content_line(text).startswith("===== 会话正常结束 "), _last_content_line(text))
    probe = _read(os.path.join(d, "_probe_normal.txt"))
    check("日志落点 = tmp/fake_exe.exe 同目录（sys.executable 替换生效）",
          probe == "log=" + log, probe)

    rc = _boot_run(d, "normal")
    text = _read(log)
    check("第二次启动：回放了上次尾部并有回放结束线",
          "----- 上次会话最后 " in text and "----- 上次会话回放结束 -----" in text, text)
    check("上次正常收尾 → 不提示强杀", "[日志] 上次会话未正常结束" not in text, text)

    rc = _boot_run(d, "kill")
    text = _read(log)
    check("第三次被强杀：rc=1 且没写结束标记",
          rc == 1 and not _last_content_line(text).startswith("===== 会话正常结束 "),
          f"rc={rc} last={_last_content_line(text)!r}")

    rc = _boot_run(d, "normal")
    text = _read(log)
    check("第四次启动：识别出上次未正常结束（回放块里的旧标记没骗过它）",
          "[日志] 上次会话未正常结束" in text, text[-600:])
    check("强杀前的最后输出出现在新日志里",
          "[driver] 窗口模式写一行 kill" in text, text[-600:])
    check("第四次也正常收尾",
          rc == 0 and _last_content_line(text).startswith("===== 会话正常结束 "),
          _last_content_line(text))


def main():
    tmp = tempfile.mkdtemp(prefix="d2_launcher_logtest_")
    try:
        test_read_tail(tmp)
        test_resume_crash(tmp)
        test_normal_exit(tmp)
        test_marker_not_inherited(tmp)
        test_rotate(tmp)
        test_devnull_fallback(tmp)
        test_windowed_boot(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n===== 通过 {len(OKS)} / 失败 {len(FAILS)} =====")
    for f in FAILS:
        print("  FAIL:", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
