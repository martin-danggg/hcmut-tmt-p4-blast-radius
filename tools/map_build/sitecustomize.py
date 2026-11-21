# Tracing for subprocesses (spawn pool workers) while building the map.
import os
_d = os.environ.get("BR_TRACE_DIR")
if _d:
    import sys, atexit, marshal
    _ROOT = os.environ["BR_ROOT"]
    _mon = sys.monitoring
    _rt, _imp, _opens, _fc, _inst, _tc = set(), set(), set(), {}, set(), {}

    def _rel(f):
        r = _fc.get(f)
        if r is None:
            a = os.path.abspath(f)
            r = a[len(_ROOT) + 1:] if a.startswith(_ROOT + os.sep) else ""
            _fc[f] = r
        return r

    def _cb(code, line):
        r = _rel(code.co_filename)
        if r:
            (_rt if code.co_flags & 1 else _imp).add((r, line))
        return _mon.DISABLE

    def _tkeys(tp):
        r = _tc.get(tp)
        if r is None:
            out = []
            for k in tp.__mro__:
                mod = sys.modules.get(getattr(k, "__module__", "") or "")
                f = getattr(mod, "__file__", None) if mod else None
                rf = _rel(f) if f else ""
                if rf:
                    out.append(rf + ":" + k.__qualname__)
            r = _tc[tp] = tuple(out)
        return r

    def _pystart(code, off):
        if code.co_name != "__init__" or not code.co_argcount:
            return _mon.DISABLE
        try:
            s = sys._getframe(1).f_locals.get(code.co_varnames[0])
        except Exception:
            return
        if s is not None:
            _inst.update(_tkeys(type(s)))

    def _hook(ev, args):
        if ev == "open" or ev == "os.listdir" or ev == "os.scandir":
            p = args[0] if args else None
            if isinstance(p, (str, bytes, os.PathLike)):
                r = _rel(os.fsdecode(p))
                if r and not r.endswith((".py", ".pyc")):
                    _opens.add(r)

    def _dump():
        try:
            os.makedirs(_d, exist_ok=True)
            with open(os.path.join(_d, f"{os.getpid()}.bin"), "wb") as fh:
                marshal.dump({"rt": list(_rt), "imp": list(_imp), "opens": list(_opens), "inst": list(_inst)}, fh)
        except Exception:
            pass

    _mon.use_tool_id(5, "br")
    _mon.register_callback(5, _mon.events.LINE, _cb)
    _mon.register_callback(5, _mon.events.PY_START, _pystart)
    _mon.set_events(5, _mon.events.LINE | _mon.events.PY_START)
    sys.addaudithook(_hook)
    atexit.register(_dump)
