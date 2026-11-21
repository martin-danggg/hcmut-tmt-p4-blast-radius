"""Bitmask map built from aggregated traces (analysis-time and runtime share this API)."""
import os
from collections import defaultdict


class MapData:
    def __init__(self, ids, rt, imp, loaded, opened):
        self.ids = ids                  # list of test ids (bit i -> ids[i])
        self._rt = rt                   # file -> {line: mask}
        self._imp = imp                 # file -> {line: mask}
        self._loaded = loaded           # file -> mask
        self._opened = opened           # path -> mask
        self.all_mask = (1 << len(ids)) - 1
        self._file_tests = defaultdict(int)
        for i, t in enumerate(ids):
            self._file_tests[t.split("::")[0]] |= 1 << i

    @classmethod
    def from_blob(cls, b):
        masks = b["masks"]
        rt = {f: {l: masks[s] for l, s in v} for f, v in b["rt"].items()}
        loaded = {f: masks[s] for f, s in b["loaded"].items()}
        opened = {p: masks[s] for p, s in b["opened"].items()}
        return cls(b["ids"], rt, {}, loaded, opened)

    @classmethod
    def from_agg(cls, tests):
        ids = sorted(tests)
        rt = defaultdict(lambda: defaultdict(int)); imp = defaultdict(lambda: defaultdict(int))
        loaded = defaultdict(int); opened = defaultdict(int)
        for i, t in enumerate(ids):
            b = 1 << i
            d = tests[t]
            files = set()
            for f, l in d["rt"]:
                rt[f][l] |= b; files.add(f)
            for f, l in d["imp"]:
                imp[f][l] |= b; files.add(f)
            for f in files:
                loaded[f] |= b
            for p in d["opens"]:
                opened[p] |= b
        return cls(ids, {f: dict(v) for f, v in rt.items()}, {f: dict(v) for f, v in imp.items()},
                   dict(loaded), dict(opened))

    def rt(self, f, lines):
        d = self._rt.get(f)
        if not d:
            return 0
        m = 0
        for l in lines:
            m |= d.get(l, 0)
        return m

    def imp(self, f, lines):
        d = self._imp.get(f)
        if not d:
            return 0
        m = 0
        for l in lines:
            m |= d.get(l, 0)
        return m

    def loaded(self, f):
        return self._loaded.get(f, 0) | self._file_tests.get(f, 0)

    def loaded_dir(self, d):
        m = 0
        for f, v in self._loaded.items():
            if f.startswith(d + "/"):
                m |= v
        return m

    def opened(self, p):
        return self._opened.get(p, 0)

    def file_tests(self, f):
        return self._file_tests.get(f, 0)

    def files_with_rt(self):
        return self._rt.keys()

    def bits(self, mask):
        out = []
        i = 0
        while mask:
            if mask & 1:
                out.append(i)
            mask >>= 1; i += 1
        return out
