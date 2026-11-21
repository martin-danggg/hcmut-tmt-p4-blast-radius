# pytest plugin: per-test line coverage, each test in its own forked child.
import os, sys, marshal, time
ROOT = os.path.abspath(os.environ["BR_ROOT"])
OUT = os.environ["BR_OUT"]
SHARD = int(os.environ.get("BR_SHARD", "0"))
NSH = int(os.environ.get("BR_NSHARDS", "1"))
mon = sys.monitoring
S = {"rt": set(), "imp": set(), "opens": set(), "fail": False}
_fc = {}

def rel(f):
    r = _fc.get(f)
    if r is None:
        a = os.path.abspath(f)
        r = a[len(ROOT) + 1:] if a.startswith(ROOT + os.sep) else ""
        _fc[f] = r
    return r

def cb(code, line):
    r = rel(code.co_filename)
    if r:
        (S["rt"] if code.co_flags & 1 else S["imp"]).add((r, line))
    return mon.DISABLE

def hook(ev, args):
    if ev == "open" or ev == "os.listdir" or ev == "os.scandir":
        p = args[0] if args else None
        if isinstance(p, (str, bytes, os.PathLike)):
            r = rel(os.fsdecode(p))
            if r and not r.endswith((".py", ".pyc")):
                S["opens"].add(r)

def pytest_configure(config):
    mon.use_tool_id(5, "br")
    mon.register_callback(5, mon.events.LINE, cb)
    mon.set_events(5, mon.events.LINE)
    sys.addaudithook(hook)

def pytest_collection_finish(session):
    os.makedirs(f"{OUT}/t", exist_ok=True)
    with open(f"{OUT}/collect_{SHARD}.bin", "wb") as fh:
        marshal.dump({"rt": list(S["rt"]), "imp": list(S["imp"]), "opens": list(S["opens"]),
                      "items": [it.nodeid for it in session.items]}, fh)

def pytest_runtest_logreport(report):
    if report.failed:
        S["fail"] = True

def pytest_runtestloop(session):
    for i, item in enumerate(session.items):
        if i % NSH != SHARD:
            continue
        pid = os.fork()
        if pid == 0:
            try:
                d = f"{OUT}/w/{i}"
                os.environ["BR_TRACE_DIR"] = d
                os.environ["BR_ROOT"] = ROOT
                S["rt"], S["imp"], S["opens"], S["fail"] = set(), set(), set(), False
                mon.restart_events()
                t = time.perf_counter()
                item.ihook.pytest_runtest_protocol(item=item, nextitem=None)
                dt = time.perf_counter() - t
                mon.set_events(5, 0)
                rec = {"id": item.nodeid, "rt": list(S["rt"]), "imp": list(S["imp"]),
                       "opens": list(S["opens"]), "dt": dt, "fail": S["fail"]}
                with open(f"{OUT}/t/{i}.bin", "wb") as fh:
                    marshal.dump(rec, fh)
            finally:
                os._exit(0)
        os.waitpid(pid, 0)
    mon.set_events(5, 0)
    return True
