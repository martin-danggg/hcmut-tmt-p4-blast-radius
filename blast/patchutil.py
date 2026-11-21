"""Unified diff parsing and applying (patches are against the pinned sqlglot commit)."""
import re

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_patch(text):
    """Return list of file dicts: old, new (paths or None), hunks [(old_start, old_len, new_start, new_len, lines)]."""
    files = []
    cur = None
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("diff --git "):
            cur = {"old": None, "new": None, "hunks": [], "binary": False}
            m = re.match(r"diff --git a/(\S+) b/(\S+)", ln)
            if m:
                cur["old"], cur["new"] = m.group(1), m.group(2)
            files.append(cur)
        elif ln.startswith("--- ") and cur is not None and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            o = ln[4:].split("\t")[0]
            n = lines[i + 1][4:].split("\t")[0]
            cur["old"] = None if o == "/dev/null" else o[2:] if o.startswith("a/") else o
            cur["new"] = None if n == "/dev/null" else n[2:] if n.startswith("b/") else n
            i += 1
        elif ln.startswith("new file mode") and cur is not None:
            cur["created"] = True
        elif ln.startswith("deleted file mode") and cur is not None:
            cur["deleted"] = True
        elif ln.startswith("Binary files") and cur is not None:
            cur["binary"] = True
        elif ln.startswith("@@") and cur is not None:
            m = HUNK.match(ln)
            os_, ol, ns, nl = int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) or 1)
            body = []
            i += 1
            o_left, n_left = ol, nl
            while i < len(lines) and (o_left > 0 or n_left > 0):
                b = lines[i]
                if b.startswith("\\"):
                    i += 1
                    continue
                tag = b[:1] if b else " "
                if tag == " ":
                    o_left -= 1; n_left -= 1
                elif tag == "-":
                    o_left -= 1
                elif tag == "+":
                    n_left -= 1
                else:
                    break
                body.append((tag, b[1:]))
                i += 1
            while i < len(lines) and lines[i].startswith("\\"):
                i += 1
            cur["hunks"].append((os_, ol, ns, nl, body))
            continue
        i += 1
    for f in files:
        if f.get("created"):
            f["old"] = None
        if f.get("deleted"):
            f["new"] = None
    return files


def apply_hunks(old_text, hunks):
    """Apply hunks to old_text (str). Raises ValueError on mismatch."""
    old = old_text.split("\n") if old_text else []
    trailing_nl = old_text.endswith("\n") if old_text else True
    if old_text and trailing_nl:
        old = old[:-1]
    out = []
    pos = 0  # 0-based index into old
    for os_, ol, ns, nl, body in hunks:
        start = os_ - 1 if ol > 0 else os_
        if start < pos:
            raise ValueError("overlapping hunks")
        out.extend(old[pos:start])
        pos = start
        for tag, txt in body:
            if tag == " ":
                if pos >= len(old) or old[pos] != txt:
                    raise ValueError("context mismatch")
                out.append(txt); pos += 1
            elif tag == "-":
                if pos >= len(old) or old[pos] != txt:
                    raise ValueError("delete mismatch")
                pos += 1
            else:
                out.append(txt)
    out.extend(old[pos:])
    return "\n".join(out) + ("\n" if out else "")


def changed_old_lines(hunks):
    """Old line numbers removed/modified, and insertion points (old line after which lines were inserted)."""
    removed, inserts = set(), set()
    for os_, ol, ns, nl, body in hunks:
        o = os_ if ol > 0 else os_ + 1
        prev_minus = False
        for tag, txt in body:
            if tag == " ":
                o += 1; prev_minus = False
            elif tag == "-":
                removed.add(o); o += 1; prev_minus = True
            else:
                if not prev_minus:
                    inserts.add(o - 1)  # inserted after old line o-1
    return removed, inserts
