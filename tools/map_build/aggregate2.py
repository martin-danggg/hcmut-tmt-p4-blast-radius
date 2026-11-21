"""Aggregate per-test-file trace dirs into a map keyed by test id."""
import marshal, glob, os, json, pickle, sys
from collections import defaultdict
M, OUT = sys.argv[1], sys.argv[2]
tests = {}   # id -> dict(rt=set((f,l)), imp=set((f,l)), opens=set(), dt=float)
for d in sorted(glob.glob(f"{M}/*/")):
    cf = f"{d}collect_0.bin"
    if not os.path.exists(cf):
        print("no collect", d); continue
    c = marshal.load(open(cf, "rb"))
    crt = set(map(tuple, c["rt"])); cimp = set(map(tuple, c["imp"])); cop = set(c["opens"])
    cinst = set(c.get("inst", []))
    for i, nodeid in enumerate(c["items"]):
        f = f"{d}t/{i}.bin"
        if not os.path.exists(f):
            print("missing", nodeid); continue
        r = marshal.load(open(f, "rb"))
        R = set(map(tuple, r["rt"])) | crt
        I = set(map(tuple, r["imp"])) | cimp
        O = set(r["opens"]) | cop
        N = set(r.get("inst", [])) | cinst
        for w in glob.glob(f"{d}w/{i}/*.bin"):
            x = marshal.load(open(w, "rb"))
            R |= set(map(tuple, x["rt"])); I |= set(map(tuple, x["imp"])); O |= set(x["opens"])
            N |= set(x.get("inst", []))
        tests[nodeid] = dict(rt=R, imp=I, opens=O, inst=N, dt=r["dt"], fail=r["fail"])
print("tests", len(tests), "fail", [k for k, v in tests.items() if v["fail"]])
pickle.dump(tests, open(OUT, "wb"))
