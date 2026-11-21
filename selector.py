"""Blast Radius selector (team HCMUT TMT).

For each patch: work out which tests can possibly be affected, using a coverage map
built from the pinned sqlglot commit (per-test line coverage, files each test opened,
modules each test loaded) plus the pinned source itself (AST), then split the
selected tests over workers. Any doubt falls back to running every test.
"""
import json
import lzma
import os
import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from blast.candidates import Analyzer  # noqa: E402
from blast.lpt import best_split  # noqa: E402
from blast.mapdata import MapData  # noqa: E402
from blast.patchutil import apply_hunks, parse_patch  # noqa: E402

DATA = Path(os.environ.get("BR_DATA", "/data"))
SRC = os.environ.get("BR_SRC", "/opt/sqlglot")
MAP = os.environ.get("BR_MAP", os.path.join(os.path.dirname(os.path.abspath(__file__)), "map", "map.pkl.xz"))


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def main() -> None:
    t0 = time.process_time()
    tj = json.loads((DATA / "tests.json").read_text())
    tests = tj["tests"]
    ms = {t["number"]: round(t["seconds"] * 1000) for t in tests}
    num = {t["id"]: t["number"] for t in tests}
    all_items = [(ms[n], n) for n in ms]

    try:
        md = MapData.from_blob(pickle.loads(lzma.decompress(Path(MAP).read_bytes())))
        known = set(md.ids)
        unknown = [num[i] for i in num if i not in known]
        analyzer = Analyzer(md, SRC)
    except Exception as e:  # no map: be safe
        log("map load failed:", repr(e))
        md = None

    out = []
    for patch in sorted((DATA / "commits").glob("*.patch")):
        cid = patch.stem
        sel = None
        try:
            if md is None:
                raise RuntimeError("no map")
            files = parse_patch(patch.read_text(encoding="utf-8", errors="surrogateescape"))
            if not files:
                raise ValueError("empty patch")
            new = {}
            for f in files:
                if f["old"] and f["new"] and not f.get("binary"):
                    old = analyzer.src(f["old"])
                    if old is None:
                        raise ValueError("unknown file " + f["old"])
                    new[f["new"]] = apply_hunks(old, f["hunks"])
            mask, notes = analyzer.analyze(files, new)
            sel = {num[md.ids[i]] for i in md.bits(mask) if md.ids[i] in num}
            if mask and unknown:
                sel.update(unknown)
            log(cid, len(sel), notes)
        except Exception as e:
            log(cid, "fallback to all:", repr(e))
            sel = None
        items = all_items if sel is None else [(ms[n], n) for n in sel]
        _, workers = best_split(items)
        out.append(json.dumps({"commit": cid, "workers": workers}))
    sys.stdout.write("\n".join(out) + ("\n" if out else ""))
    sys.stdout.flush()
    log(f"done {len(out)} commits, cpu {time.process_time() - t0:.2f}s")


if __name__ == "__main__":
    main()
