"""原子写 JSON：先写同目录临时文件再 os.replace。

进程崩溃 / 断电最多留下一个 .tmp 孤儿，不会把原文件写成半截
（此前所有 json.dump 直接覆盖，绑定表 / token / 各缓存损坏过风险）。
失败抛异常，由调用方既有的 try/except 处理。
"""
import json
import os
import tempfile


def dump_json(path: str, data, *, ensure_ascii: bool = False,
              indent=None, separators=None) -> None:
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=ensure_ascii, indent=indent,
                      separators=separators)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
