"""Blast Radius selector (team HCMUT TMT).

1. Static: for each patch, work out which tests can possibly be affected, using a map
   built from the pinned sqlglot commit (per-test line coverage, files each test opened,
   modules loaded, classes instantiated) plus the pinned source itself (AST).
2. Dynamic checks (optional, budgeted): import the patched modules when a change runs at
   import time; optionally run the cheap candidates to decide about very slow ones.
3. Split the selected tests over workers (LPT, best worker count).
Any doubt falls back to a larger selection, never a smaller one.
"""
import json
import lzma
import os
import pickle
import shutil
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
TMP = os.environ.get("BR_TMP", "/tmp/br")
DYNAMIC = os.environ.get("BR_DYNAMIC", "1") == "1"          # import checks
HEAVY_SKIP = os.environ.get("BR_HEAVY_SKIP", "0") == "1"     # light-signal for slow tests
BUDGET = float(os.environ.get("BR_BUDGET", "110"))          # wall seconds for dynamic work
HEAVY_S = 3.0


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def module_name(rel):
    if not rel.startswith("sqlglot/") or not rel.endswith(".py"):
        return None
    m = rel[:-3].replace("/", ".")
    return m[: -len(".__init__")] if m.endswith(".__init__") else m


def main() -> None:
    t_start = time.monotonic()
    tj = json.loads((DATA / "tests.json").read_text())
    tests = tj["tests"]
    ms = {t["number"]: round(t["seconds"] * 1000) for t in tests}
    num = {t["id"]: t["number"] for t in tests}
    all_nums = set(ms)

    md = analyzer = None
    unknown = []
    try:
        md = MapData.from_blob(pickle.loads(lzma.decompress(Path(MAP).read_bytes())))
        known = set(md.ids)
        unknown = [num[i] for i in num if i not in known]
        analyzer = Analyzer(md, SRC)
    except Exception as e:
        log("map load failed:", repr(e))

    plans = []  # dicts: cid, sel (set or None=all), new, notes, files
    for patch in sorted((DATA / "commits").glob("*.patch")):
        cid = patch.stem
        plan = {"cid": cid, "sel": None, "new": {}, "deleted": set(), "notes": [], "files": []}
        try:
            if md is None:
                raise RuntimeError("no map")
            files = parse_patch(patch.read_text(encoding="utf-8", errors="surrogateescape"))
            if not files:
                raise ValueError("empty patch")
            for f in files:
                if f.get("binary"):
                    raise ValueError("binary patch")
                if f["old"] and f["new"]:
                    old = analyzer.src(f["old"])
                    if old is None:
                        raise ValueError("unknown file " + f["old"])
                    plan["new"][f["new"]] = apply_hunks(old, f["hunks"])
                elif f["new"]:
                    plan["new"][f["new"]] = apply_hunks("", f["hunks"])
                else:
                    plan["deleted"].add(f["old"])
            mask, notes = analyzer.analyze(files, plan["new"])
            sel = {num[md.ids[i]] for i in md.bits(mask) if md.ids[i] in num}
            if mask and unknown:
                sel.update(unknown)
            plan.update(sel=sel, notes=notes, files=files)
        except Exception as e:
            log(cid, "fallback to all:", repr(e))
        plans.append(plan)

    if DYNAMIC and md is not None:
        try:
            dynamic(plans, md, num, ms, t_start)
        except Exception as e:
            log("dynamic stage failed:", repr(e))

    out = []
    for plan in plans:
        sel = all_nums if plan["sel"] is None else plan["sel"]
        _, workers = best_split([(ms[n], n) for n in sel])
        out.append(json.dumps({"commit": plan["cid"], "workers": workers}))
        log(plan["cid"], len(sel), plan["notes"][:6])
    sys.stdout.write("\n".join(out) + ("\n" if out else ""))
    sys.stdout.flush()
    log(f"done {len(out)} commits in {time.monotonic() - t_start:.1f}s")


def dynamic(plans, md, num, ms, t_start):
    from blast.runner import Job, import_check, make_overlay

    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)
    id_of = {v: k for k, v in num.items()}

    def overlay(plan):
        ov = os.path.join(TMP, plan["cid"])
        if not os.path.isdir(ov):
            make_overlay(SRC, ov, plan["new"], plan["deleted"])
        return ov

    # 1) import checks: a change that runs at import time must not break the import
    for plan in plans:
        if plan["sel"] is None or time.monotonic() - t_start > BUDGET:
            continue
        mods = []
        for n in plan["notes"]:
            if n.startswith(("import_level:", "class_header:")):
                mod = module_name(n.split(":", 1)[1])
                if mod and not mod.endswith("__main__") and mod not in mods:
                    mods.append(mod)
        if not mods:
            continue
        ok = import_check(overlay(plan), mods)
        plan["notes"].append(f"import_check:{'ok' if ok else 'FAIL'}")
        if not ok:
            for n in plan["notes"]:
                if n.startswith(("import_level:", "class_header:")):
                    rel = n.split(":", 1)[1]
                    plan["sel"] |= {num[md.ids[i]] for i in md.bits(md.loaded(rel)) if md.ids[i] in num}

    # 2) light signal for slow candidates
    if not HEAVY_SKIP:
        return
    jobs = []
    for plan in plans:
        sel = plan["sel"]
        if not sel:
            continue
        heavy = {n for n in sel if ms[n] >= HEAVY_S * 1000}
        light = sel - heavy
        if not heavy or not light:
            continue
        static_cost = best_split([(ms[n], n) for n in sel])[0] / 1000
        light_cost = 1.6 * sum(ms[n] for n in light) / 1000 + 2.5
        if light_cost >= static_cost - 3:
            continue
        jobs.append((static_cost - light_cost, plan, light, heavy))
    jobs.sort(key=lambda j: -j[0])
    running = []
    queue = list(jobs)
    while queue or running:
        while queue and len(running) < 2 and time.monotonic() - t_start < BUDGET:
            _, plan, light, heavy = queue.pop(0)
            limit = max(10.0, 3 * sum(ms[n] for n in light) / 1000 + 8)
            job = Job(overlay(plan), [id_of[n] for n in sorted(light, key=lambda n: ms[n])], limit, 60,
                      maxfail=1)
            running.append((job, plan, light, heavy))
        if not running:
            break
        time.sleep(0.05)
        for item in running[:]:
            job, plan, light, heavy = item
            r = job.poll()
            if r is None:
                continue
            running.remove(item)
            ok, fails, coll = r
            if ok and not fails and not coll:
                plan["sel"] = set()
                plan["notes"].append("light_pass:skip_heavy")
            else:
                plan["notes"].append("light_fail:keep_all")


if __name__ == "__main__":
    main()
