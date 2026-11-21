"""Run selected sqlglot tests against a patched view of the pinned checkout.

The pinned checkout (read-only) is exposed through a symlink overlay under /tmp:
only the directories leading to patched files are real, every other entry is a
symlink, so an overlay costs a few kilobytes of /tmp.
"""
import os
import subprocess
import sys
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_overlay(src, dst, new_texts, deleted=()):
    deleted = set(deleted)
    changed = set(new_texts) | deleted
    need = set()
    for rel in changed:
        parts = rel.split("/")
        for i in range(1, len(parts)):
            need.add("/".join(parts[:i]))
    os.makedirs(dst)

    def fill(reldir):
        srcdir = os.path.join(src, reldir) if reldir else src
        dstdir = os.path.join(dst, reldir) if reldir else dst
        names = set(os.listdir(srcdir)) if os.path.isdir(srcdir) else set()
        for rel in new_texts:
            if os.path.dirname(rel) == reldir:
                names.add(os.path.basename(rel))
        for n in names:
            r = f"{reldir}/{n}" if reldir else n
            if r in deleted:
                continue
            if r in new_texts:
                with open(os.path.join(dstdir, n), "w", encoding="utf-8", errors="surrogateescape") as fh:
                    fh.write(new_texts[r])
            elif r in need:
                os.mkdir(os.path.join(dstdir, n))
                fill(r)
            else:
                os.symlink(os.path.join(srcdir, n), os.path.join(dstdir, n))

    fill("")


def env_for(ov, extra=None):
    env = dict(os.environ)
    env.update(
        PYTHONPATH=f"{ov}{os.pathsep}{APP}",
        SKIP_INTEGRATION="0",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHON_CPU_COUNT="1",
        HOME="/tmp",
    )
    if extra:
        env.update(extra)
    return env


class Job:
    """One pytest process over a list of node ids in an overlay."""

    def __init__(self, ov, nodeids, wall_limit, per_test_timeout, maxfail=0):
        self.ov = ov
        self.out = ov + ".fail"
        self.argfile = ov + ".args"
        with open(self.argfile, "w") as fh:
            fh.write("\n".join(nodeids) + "\n")
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "blast.br_fail",
               f"--timeout={per_test_timeout}", "--continue-on-collection-errors",
               "-W", "ignore::pytest.PytestUnraisableExceptionWarning", f"@{self.argfile}"]
        if maxfail:
            cmd.insert(-1, f"--maxfail={maxfail}")
        self.t0 = time.monotonic()
        self.limit = wall_limit
        self.p = subprocess.Popen(cmd, cwd=ov, env=env_for(ov, {"BR_FAIL_OUT": self.out}),
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True)

    def poll(self):
        """None while running; otherwise (ok, failing_ids, collect_errors)."""
        rc = self.p.poll()
        if rc is None:
            if time.monotonic() - self.t0 > self.limit:
                self.kill()
                return (False, set(), set())
            return None
        return self.result()

    def kill(self):
        try:
            os.killpg(self.p.pid, 9)
        except Exception:
            pass
        try:
            self.p.wait(5)
        except Exception:
            pass

    def result(self):
        try:
            with open(self.out) as fh:
                lines = fh.read().split("\n")
        except OSError:
            return (False, set(), set())
        if "END" not in lines:
            return (False, set(), set())
        fails, coll = set(), set()
        for ln in lines:
            if not ln or ln == "END":
                continue
            if ln.startswith("COLLECT::"):
                coll.add(ln[len("COLLECT::"):])
            else:
                fails.add(ln)
        return (True, fails, coll)


def import_check(ov, modules, timeout=20):
    """True if every module imports cleanly from the overlay."""
    code = "import importlib,sys\nfor m in sys.argv[1:]:\n    importlib.import_module(m)\n"
    try:
        r = subprocess.run([sys.executable, "-c", code, *modules], cwd=ov, env=env_for(ov),
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=timeout)
        return r.returncode == 0
    except Exception:
        return False
