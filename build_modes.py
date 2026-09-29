"""构建模式索引 manifest_index/modes.json（来自 DestinyActivityModeDefinition）

modeType → {name, cat, parents[], order, agg}
用途：把对局 activityDetails.modes 翻译成「试炼 / 铁旗占领模式 / 打击 / 地牢」这类可读模式。
cat: 0=无 1=PvE 2=PvP 3=PvP竞技合作(智谋)；agg=True 表示聚合类模式(如「熔炉竞技场」)。

单独重建：python build_modes.py
（build_manifest.py 建全量索引时也会调用 build_modes()）
"""
import json
import os


def build_modes(client, base: str, paths: dict, out_dir: str = "manifest_index") -> dict:
    raw = json.loads(client.get(base + paths["DestinyActivityModeDefinition"]).text)
    h2mt = {d["hash"]: d["modeType"] for d in raw.values() if d.get("modeType") is not None}
    out = {}
    for d in raw.values():
        mt = d.get("modeType")
        if mt is None:
            continue
        dp = d.get("displayProperties", {})
        out[str(mt)] = {
            "name": dp.get("name") or d.get("friendlyName") or f"模式{mt}",
            "cat": d.get("activityModeCategory", 0),
            "parents": [h2mt[x] for x in (d.get("parentHashes") or []) if x in h2mt],
            "order": d.get("order", 0),
            "agg": bool(d.get("isAggregateMode")),
        }
    json.dump(out, open(os.path.join(out_dir, "modes.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    return out


if __name__ == "__main__":
    import httpx

    for _line in open(".env", encoding="utf-8"):
        if "=" in _line and not _line.startswith("#"):
            _k, _, _v = _line.strip().partition("=")
            os.environ.setdefault(_k, _v)
    os.makedirs("manifest_index", exist_ok=True)
    _c = httpx.Client(timeout=120, headers={"X-API-Key": os.environ.get("BUNGIE_API_KEY", "")})
    _m = _c.get("https://www.bungie.net/Platform/Destiny2/Manifest/").json()["Response"]
    _paths = _m["jsonWorldComponentContentPaths"]["zh-chs"]
    _out = build_modes(_c, "https://www.bungie.net", _paths)
    print("模式条目:", len(_out))
