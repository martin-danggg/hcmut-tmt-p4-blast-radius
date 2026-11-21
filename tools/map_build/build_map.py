"""Serialize the aggregated per-test traces into a compact map file shipped in the repo."""
import pickle, lzma, sys
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
from mapdata import MapData
tests = pickle.load(open(sys.argv[1], 'rb'))
md = MapData.from_agg(tests)
sets = {}
def sid(m):
    if m not in sets:
        sets[m] = len(sets)
    return sets[m]
rt = {f: [(l, sid(m)) for l, m in sorted(d.items())] for f, d in md._rt.items()}
imp = {f: [(l, sid(m)) for l, m in sorted(d.items())] for f, d in md._imp.items()}
loaded = {f: sid(m) for f, m in md._loaded.items()}
opened = {p: sid(m) for p, m in md._opened.items()}
inst = {k: sid(m) for k, m in md._inst.items()}
masks = [None] * len(sets)
for m, i in sets.items():
    masks[i] = m
blob = {"version": 3, "ids": md.ids, "masks": masks, "rt": rt, "imp": imp, "loaded": loaded, "opened": opened, "inst": inst,
        "dt": {t: round(tests[t]["dt"], 4) for t in md.ids}}
raw = pickle.dumps(blob, protocol=5)
data = lzma.compress(raw, preset=9)
open(sys.argv[2], 'wb').write(data)
print('unique sets', len(sets), 'raw', len(raw), 'xz', len(data))
