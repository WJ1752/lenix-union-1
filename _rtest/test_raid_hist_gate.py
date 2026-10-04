"""增量翻页合并 _merge_hist_page 的口径单测"""
import sys
sys.path.insert(0, ".")
from destiny_data import _merge_hist_page


def mk(i, period, ref=1000):
    return {"instance": str(i), "ref": ref, "period": period}


def collect(pages, gate, seed=()):
    seen = {m["instance"] for m in seed}
    matches = list(seed)
    cont = True
    calls = 0
    for pg in pages:
        calls += 1
        if not _merge_hist_page(pg, gate, seen, matches):
            break
    return matches, calls


# 1) 无 gate：全量收集，页满继续翻，页不满停
p1 = [mk(d, f"2026-09-{d:02d} 12:00") for d in range(250, 0, -1)]
p2 = [mk(1000 + d, f"2026-08-{d:02d} 12:00") for d in range(19, 0, -1)]
ms, calls = collect([p1, p2], gate="")
assert len(ms) == 269 and calls == 2, (len(ms), calls)

# 2) 有 gate：第一页全是旧场（oldest <= gate）→ 收完新场即停,只翻 1 页
gate = "2026-09-05 12:00"
cache = [mk(1, "2026-09-05 12:00"), mk(2, "2026-09-01 12:00")]
new = [mk(3, "2026-09-10 12:00"), mk(1, "2026-09-05 12:00"), mk(0, "2026-09-05 13:00")]
ms, calls = collect([new], gate, seed=cache)
inst = sorted(m["instance"] for m in ms)
assert inst == ["0", "1", "2", "3"], inst   # 同分钟新场(13:00其实是新分钟)也能进;门内已缓存的1不重复
assert calls == 1

# 3) 页满且全部晚于 gate → 继续翻下一页
full = [mk(d, f"2026-09-1{d} 12:00") for d in range(5)] * 50
older = [mk(50 + i, "2026-09-01 12:00") for i in range(3)]
ms, calls = collect([full, older], gate)
assert calls == 2
assert all(m["instance"] not in {"50", "51", "52"} for m in ms), "旧场不该重复进"

# 4) 页满但 oldest == gate → 下一页全是旧场,必须停
edge = [mk(1, "2026-09-05 12:00")] + [mk(2, "2026-09-04 12:00")]
ms, calls = collect([edge], gate, seed=cache)
assert calls == 1

# 5) 无 instance 的兜底键去重
a = {"instance": "", "ref": 7, "period": "2026-09-05 12:00"}
b = {"instance": "", "ref": 7, "period": "2026-09-05 12:00"}
seen, matches = set(), []
_merge_hist_page([a], "", seen, matches)
_merge_hist_page([b], "", seen, matches)
assert len(matches) == 1

print("raid hist gate logic OK")
