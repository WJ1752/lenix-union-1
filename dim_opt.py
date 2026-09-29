"""配装器（DIM Loadout Optimizer）的护甲组合搜索。

对应 DIM 的 /optimizer：在「你拥有的护甲」里穷举 5 个护甲槽，按「属性优先级和范围」筛选，
输出总属性最高的若干套，可以保存成配装或直接装备。

DIM 说「0.13 秒内用 10 个 CPU 核生成 52360 个组合」，我们单线程 Python 做不到那个量级，
所以靠四件事把搜索空间压下来（结果质量基本不受影响）：
  1. 每槽先做 Pareto 支配剪枝：6 项属性全不占优的护甲直接丢掉（异域单独分组，不会被传说支配）
  2. 每槽按总属性取前 N 个候选（默认 90）
  3. DFS + 上界剪枝：剩余槽位就算全取该属性的最大件也够不到最小值 → 整枝砍掉
  4. 节点数封顶（默认 300 万），到顶就停并如实告诉前端「未搜完」
数值上还把「假定大师之作」的 +2 提前加进每件护甲，省掉搜索里的重复计算。
游戏规则也照 DIM 实现：一套配装最多只能带 1 件异域护甲。
"""
from __future__ import annotations

import time

# 5 个护甲槽（顺序就是界面上从左到右的顺序，跟游戏装备页一致）
ARMOR_BUCKETS = [
    (3448274439, "头盔"),
    (3551918588, "臂铠"),
    (14239492, "胸部护甲"),
    (20886954, "腿部护甲"),
    (1585787867, "职业护甲"),
]

# 6 项护甲属性（hash 是 Bungie 的 statTypeHash）
STATS = [
    (392767087, "生命值"),
    (1943323491, "职业"),
    (144602215, "超能"),
    (4244567218, "近战"),
    (1735777505, "手雷"),
    (2996146975, "武器"),
]
STAT_HASHES = [h for h, _ in STATS]
STAT_STR = [str(h) for h in STAT_HASHES]

# 大师之作每件给每项属性 +2（所以 5 件一起是 +10）
MW_BONUS = 2
BIG = 10 ** 6


class _Cand:
    __slots__ = ("x", "v", "tot", "exotic", "h")

    def __init__(self, x, v, exotic):
        self.x = x
        self.v = v                      # 6 项属性值（已含大师之作加成）
        self.tot = sum(v)
        self.exotic = exotic
        self.h = x.get("h")


def _dominates(a: _Cand, b: _Cand) -> bool:
    """a 支配 b：每一项属性都不低于 b，且至少一项更高"""
    better = False
    for i in range(6):
        if a.v[i] < b.v[i]:
            return False
        if a.v[i] > b.v[i]:
            better = True
    return better


def _pareto(cands: list) -> list:
    """Pareto 剪枝：只留不被别人支配的。护甲件数一般几十到几百，O(n²) 够用"""
    out = []
    for i, a in enumerate(cands):
        dominated = False
        for j, b in enumerate(cands):
            if i != j and b.exotic == a.exotic and _dominates(b, a):
                dominated = True
                break
        if not dominated:
            out.append(a)
    return out


def candidates(inv: dict, p: dict, tags: dict | None = None) -> tuple[dict, dict]:
    """从背包快照里挑出每个槽的候选护甲。返回 (槽桶hash → 候选列表, 统计信息)"""
    tags = tags or {}
    cls = int(p.get("cls", 0))
    excl_locked = bool(p.get("exclLocked"))
    excl_eq = bool(p.get("exclEquipped"))
    skip_junk = bool(p.get("skipJunk", True))
    mw = p.get("mw") or "none"

    pool = []
    for c in inv.get("chars") or []:
        if c.get("cls") != cls:
            continue
        pool += c.get("bag") or []
        pool += c.get("equipped") or []
    pool += inv.get("vault") or []

    buckets: dict[int, list] = {h: [] for h, _ in ARMOR_BUCKETS}
    for x in pool:
        b = int(x.get("db") or 0)
        if b not in buckets:
            continue
        if x.get("cls") not in (cls, 3):
            continue
        if not x.get("st"):
            continue
        if excl_locked and (x.get("s") or 0) & 1:
            continue
        if excl_eq and x.get("eq"):
            continue
        if skip_junk and (tags.get(x.get("tid") or "") or {}).get("t") == "junk":
            continue
        exotic = int(x.get("tt") or 0) == 6
        # 假定大师之作：传说=只有传说件算满；异域=异域件也算满（异域护甲本来就能满级）
        mw_on = (mw == "exotic") or (mw == "legendary" and not exotic)
        bonus = MW_BONUS if mw_on else 0
        v = tuple(int((x.get("st") or {}).get(h, 0)) + bonus for h in STAT_STR)
        buckets[b].append(_Cand(x, v, exotic))

    out, info = {}, {}
    cap = int(p.get("capPerSlot") or 90)
    for b, name in ARMOR_BUCKETS:
        lst = buckets[b]
        raw = len(lst)
        lst = _pareto(lst)
        lst.sort(key=lambda c: -c.tot)
        info[str(b)] = {"name": name, "raw": raw, "kept": len(lst), "used": min(len(lst), cap)}
        out[b] = lst[:cap]
    return out, info


def _suffix_max(cand: dict) -> dict:
    """sm[b][i] = 从槽 b（含）到最后，第 i 项属性最多还能加多少。

    必须是「含槽 b」的上界：dfs 在选 b 之前就用它剪枝，少算 b 自己的最大值会误剪掉更优的分支。
    """
    sm = {}
    nxt = [0] * 6
    for b, _ in reversed(ARMOR_BUCKETS):
        top = [0] * 6
        for c in cand.get(b) or []:
            cv = c.v
            for i in range(6):
                if cv[i] > top[i]:
                    top[i] = cv[i]
        nxt = [top[i] + nxt[i] for i in range(6)]
        sm[b] = nxt
    return sm


def search(inv: dict, p: dict, tags: dict | None = None) -> dict:
    """组合搜索。返回 {sets, nodes, elapsed, truncated, cands}"""
    t0 = time.time()
    p = dict(p or {})
    need_exotic = int(p.get("exoticHash") or 0)
    cand, info = candidates(inv, p, tags)

    mins, maxs, prio = [], [], []
    req = p.get("stats") or {}
    for h, _ in STATS:
        r = req.get(str(h)) or req.get(h) or {}
        mins.append(int(r.get("min") or 0))
        maxs.append(int(r.get("max") or 0) or BIG)
        prio.append(int(r.get("prio") or 0))
    # 属性优先级：DIM 是把属性拖成 1/2/3 级，排序先看 1 级总和，再看 2 级……没设的算最低级
    order = sorted(range(6), key=lambda i: (prio[i] if prio[i] else 9))
    max_results = int(p.get("maxResults") or 24)
    max_nodes = int(p.get("maxNodes") or 3_000_000)

    slots = [b for b, _ in ARMOR_BUCKETS]
    if need_exotic:
        # 指定了异域护甲：这件必带，其余槽不许再出异域，省掉一大堆无意义的组合
        pivot = None
        for b in slots:
            for c in cand[b]:
                if c.h == need_exotic:
                    pivot = (b, c)
                    break
            if pivot:
                break
        if not pivot:
            return {"ok": True, "sets": [], "count": 0, "nodes": 0, "truncated": False,
                    "elapsed": round(time.time() - t0, 3), "cands": info,
                    "cls": int(p.get("cls", 0)),
                    "note": "你身上没有这件异域护甲（可能在别的角色/未拥有）"}
        pb, pc = pivot
        cand = {b: ([pc] if b == pb else [c for c in cand[b] if not c.exotic]) for b in slots}
    sm = _suffix_max(cand)

    results: list = []      # [(总和向量, 每槽候选, 排序键)]，排序键降序，只留前 max_results
    nodes = 0
    truncated = False
    cur_c: list = [None] * 5

    def key(vec) -> tuple:
        """排序键：先按优先级排好的各项属性，最后比总和"""
        return tuple(vec[i] for i in order) + (sum(vec),)

    def push(acc):
        k = key(acc)
        if len(results) >= max_results and k <= results[-1][2]:
            return
        results.append((list(acc), list(cur_c), k))
        results.sort(key=lambda r: r[2], reverse=True)
        del results[max_results:]

    def dfs(depth: int, acc, exotic_used: int):
        nonlocal nodes, truncated
        if nodes > max_nodes:
            truncated = True
            return
        if depth == 5:
            nodes += 1
            for i in range(6):
                if acc[i] < mins[i] or acc[i] > maxs[i]:
                    return
            push(acc)
            return
        b = slots[depth]
        rest = sm[b]
        # 分支定界：剩余槽位全取每项属性的最大件也进不了前 N 名 → 整枝砍掉
        if len(results) >= max_results:
            bound = [acc[i] + rest[i] for i in range(6)]
            if key(bound) <= results[-1][2]:
                return
        for c in cand[b]:
            if c.exotic and exotic_used:
                continue
            cv = c.v
            nxt = [0] * 6
            ok = True
            for i in range(6):
                s = acc[i] + cv[i]
                if s > maxs[i] or s + rest[i] < mins[i]:
                    ok = False
                    break
                nxt[i] = s
            if not ok:
                continue
            cur_c[depth] = c
            dfs(depth + 1, nxt, exotic_used + (1 if c.exotic else 0))

    dfs(0, [0] * 6, 0)

    sets = []
    for tot, cs, _k in results:
        sets.append({
            "items": [_item(c.x) for c in cs],
            "totals": {str(h): tot[i] for i, (h, _) in enumerate(STATS)},
            "sum": sum(tot),
        })
    return {
        "ok": True,
        "sets": sets,
        "count": len(sets),
        "nodes": nodes,
        "truncated": truncated,
        "elapsed": round(time.time() - t0, 3),
        "cands": info,
        "cls": int(p.get("cls", 0)),
        "stats": [{"h": h, "n": n, "min": mins[i], "max": (0 if maxs[i] >= BIG else maxs[i]),
                   "prio": prio[i], "order": order.index(i) + 1}
                  for i, (h, n) in enumerate(STATS)],
    }


def _item(x: dict) -> dict:
    return {"i": x.get("i"), "h": x.get("h"), "n": x.get("n"), "ic": x.get("ic"),
            "wm": x.get("wm"), "tt": x.get("tt"), "pw": x.get("pw"), "ty": x.get("ty"),
            "b": x.get("b"), "db": x.get("db"), "cls": x.get("cls"), "w": x.get("w"),
            "eq": x.get("eq"), "st": x.get("st"), "tid": x.get("tid")}
