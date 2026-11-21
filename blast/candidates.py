"""Which tests can a patch affect? Derived only from the pinned commit's source and the coverage map."""
import ast, os, re

FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)
BLOCKS = ("body", "orelse", "finalbody")


def _first_line(s):
    if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and s.decorator_list:
        return min(d.lineno for d in s.decorator_list)
    return s.lineno


def _children_blocks(s):
    out = []
    for b in BLOCKS:
        v = getattr(s, b, None)
        if isinstance(v, list) and v and isinstance(v[0], ast.stmt):
            out.append(v)
    for h in getattr(s, "handlers", []) or []:
        out.append(h.body)
    for c in getattr(s, "cases", []) or []:
        out.append(c.body)
    return out


def locate(tree, line):
    """Innermost statement containing line. Returns (stmt, stack_of_enclosing_stmts, in_header)."""
    stack = []
    body = tree.body
    found = None
    while True:
        nxt = None
        for s in body:
            if _first_line(s) <= line <= (s.end_lineno or s.lineno):
                nxt = s
                break
        if nxt is None:
            return found, stack[:-1] if found else stack, True
        blocks = _children_blocks(nxt)
        inner = None
        for b in blocks:
            if b and _first_line(b[0]) <= line <= (b[-1].end_lineno or b[-1].lineno):
                inner = b
                break
        stack.append(nxt)
        found = nxt
        if inner is None:
            return nxt, stack[:-1], bool(blocks)
        # line may be in a block but between statements (blank/comment) -> descend handles None
        sub = None
        for s in inner:
            if _first_line(s) <= line <= (s.end_lineno or s.lineno):
                sub = s
                break
        if sub is None:
            return nxt, stack[:-1], True
        body = inner


def _is_type_checking(test):
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def in_header_of(s, line):
    return line in header_span(s)


def header_span(s):
    blocks = _children_blocks(s)
    if not blocks:
        return range(_first_line(s), (s.end_lineno or s.lineno) + 1)
    first_child = min(_first_line(b[0]) for b in blocks)
    return range(_first_line(s), max(_first_line(s), first_child - 1) + 1)


def full_span(s):
    return range(_first_line(s), (s.end_lineno or s.lineno) + 1)


def bound_names(s):
    names = set()
    if isinstance(s, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        tg = s.targets if isinstance(s, ast.Assign) else [s.target]
        for t in tg:
            for n in ast.walk(t):
                if isinstance(n, ast.Name):
                    names.add(n.id)
                elif isinstance(n, ast.Attribute):
                    names.add(n.attr)
    elif isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.add(s.name)
    elif isinstance(s, (ast.Import, ast.ImportFrom)):
        for a in s.names:
            names.add((a.asname or a.name).split(".")[0])
    return names


def changed_columns(hunks):
    """old line -> (start_col, end_col) of the changed characters, for 1:1 replaced lines."""
    out = {}
    for os_, ol, ns, nl, body in hunks:
        o = os_ if ol > 0 else os_ + 1
        i = 0
        while i < len(body):
            if body[i][0] == " ":
                o += 1; i += 1; continue
            minus, plus = [], []
            while i < len(body) and body[i][0] == "-":
                minus.append(body[i][1]); i += 1
            while i < len(body) and body[i][0] == "+":
                plus.append(body[i][1]); i += 1
            if len(minus) == len(plus):
                for k, (a, b) in enumerate(zip(minus, plus)):
                    ab, bb = a.encode("utf-8"), b.encode("utf-8")
                    p = 0
                    while p < min(len(ab), len(bb)) and ab[p] == bb[p]:
                        p += 1
                    q = 0
                    while q < min(len(ab), len(bb)) - p and ab[-1 - q] == bb[-1 - q]:
                        q += 1
                    out[o + k] = (p, max(p + 1, len(ab) - q))
            o += len(minus)
    return out


def lambda_around(stmt, line, cols):
    """Innermost Lambda inside stmt whose source range contains the changed range on `line`."""
    best = None
    for n in ast.walk(stmt):
        if isinstance(n, ast.Lambda) and n.end_lineno is not None:
            start = (n.lineno, n.col_offset)
            end = (n.end_lineno, n.end_col_offset)
            if start <= (line, cols[0]) and (line, cols[1]) <= end:
                if best is None or (n.lineno, n.col_offset) >= (best.lineno, best.col_offset):
                    best = n
    return best


class Analyzer:
    """map_ must provide: ids, rt(file, lines)->mask, imp(file, lines)->mask, loaded(file)->mask,
    opened(path)->mask, all_mask, files_with_rt (iterable of files having rt data)."""

    def __init__(self, map_, src_root):
        self.m = map_
        self.root = src_root
        self._src_cache = {}

    def src(self, rel):
        if rel not in self._src_cache:
            p = os.path.join(self.root, rel)
            try:
                with open(p, encoding="utf-8") as fh:
                    self._src_cache[rel] = fh.read()
            except OSError:
                self._src_cache[rel] = None
        return self._src_cache[rel]

    def users(self, name, exclude_file=None, exclude_span=()):
        """Tests that executed a line mentioning `name` (word) in any traced source file."""
        rx = re.compile(r"\b%s\b" % re.escape(name))
        # a line that only (re)defines the name does not read it
        rdef = re.compile(r"^\s*(?:(?:async\s+)?def\s+%s\b|class\s+%s\b|%s\s*(?::[^=]*)?=(?!=))"
                          % ((re.escape(name),) * 3))
        mask = 0
        hit = False
        for f in self.m.files_with_lines():
            text = self.src(f)
            if not text or name not in text:
                continue
            lines = []
            for i, ln in enumerate(text.split("\n"), 1):
                if name in ln and rx.search(ln):
                    if f == exclude_file and i in exclude_span:
                        continue
                    if rdef.match(ln) and len(rx.findall(ln)) == 1:
                        continue
                    lines.append(i)
            if lines:
                hit = True
                mask |= self.m.rt(f, lines) | self.m.imp(f, lines)
        return mask, hit

    def py_file(self, rel, old_text, new_text, removed, inserts, notes):
        """Candidate mask for a modified python file."""
        m = self.m
        try:
            old_tree = ast.parse(old_text)
        except SyntaxError:
            return m.loaded(rel)
        try:
            new_tree = ast.parse(new_text)
        except SyntaxError:
            notes.append("syntax_error:" + rel)
            return m.loaded(rel) | m.file_tests(rel)
        if ast.dump(old_tree) == ast.dump(new_tree):
            notes.append("ast_equal:" + rel)
            return 0
        mask = 0
        lines = set(removed)
        imap = getattr(self, "_imap", {})
        for L in inserts:
            if L in imap and self._whole_toplevel_insert(new_tree, new_text, imap[L]):
                notes.append("toplevel_insert:%s:%d" % (rel, L))
                continue
            lines.add(L); lines.add(L + 1)
        import_level = False
        for ln in sorted(lines):
            s, stack, in_header = locate(old_tree, ln)
            if s is None:
                continue
            if any(isinstance(x, ast.If) and _is_type_checking(x.test) and not in_header_of(x, ln) for x in stack + [s]):
                notes.append("type_checking_only:" + rel)
                continue
            in_func = any(isinstance(x, FUNC) for x in stack)
            if isinstance(s, FUNC) and in_header:
                self._classes = [x.name for x in stack if isinstance(x, ast.ClassDef)]
                # signature / decorator: whoever runs the function body
                mask |= m.rt(rel, full_span(s))
                if not in_func:
                    import_level = True
                    mask |= self._import_level(rel, s, notes)
                continue
            if isinstance(s, ast.ClassDef) and in_header:
                import_level = True
                mask |= m.loaded(rel)
                notes.append("class_header:" + rel)
                continue
            span = header_span(s) if in_header and _children_blocks(s) else full_span(s)
            self._classes = [x.name for x in stack if isinstance(x, ast.ClassDef)]
            if not in_func and ln in self._cols:
                lam = lambda_around(s, ln, self._cols[ln])
                if lam is not None:
                    # the edit is inside a lambda body: only callers of the lambda see it
                    mask |= m.rt(rel, range(lam.lineno, lam.end_lineno + 1))
                    self._lambda_lines.add(ln)
                    if ln in self._old2new:
                        self._lambda_new.add(self._old2new[ln])
                    notes.append("in_lambda:%s:%d" % (rel, ln))
                    continue
            mask |= m.rt(rel, span)
            if not in_func:
                import_level = True
                mask |= self._import_level(rel, s, notes)
        # statements on the new side (inserted / replaced) at module or class level
        new_lines = self._new_lines - self._lambda_new
        for ln in sorted(new_lines):
            s, stack, in_header = locate(new_tree, ln)
            if s is None:
                continue
            if any(isinstance(x, ast.If) and _is_type_checking(x.test) and not in_header_of(x, ln) for x in stack + [s]):
                notes.append("type_checking_only:" + rel)
                continue
            self._classes = [x.name for x in stack if isinstance(x, ast.ClassDef)]
            if not any(isinstance(x, FUNC) for x in stack) and not (isinstance(s, FUNC) and not in_header):
                import_level = True
                mask |= self._import_level(rel, s, notes)
        if import_level:
            notes.append("import_level:" + rel)
        return mask

    def _new_name_unused(self, rel, name):
        """A module-level def that did not exist before and is referenced nowhere else."""
        old = self.src(rel) or ""
        if re.search(r"\b%s\b" % re.escape(name), old):
            return False
        rx = re.compile(r"\b%s\b" % re.escape(name))
        for f, text in getattr(self, "_all_new", {}).items():
            if text and len(rx.findall(text)) > (1 if f == rel else 0):
                return False
        for f in self.m.files_with_lines():
            text = self.src(f)
            if text and name in text and rx.search(text):
                return False
        return True

    @staticmethod
    def _insert_map(hunks):
        """old line L (insert after) -> list of new line numbers inserted there (pure insertions only)."""
        out = {}
        for os_, ol, ns, nl, body in hunks:
            o = os_ if ol > 0 else os_ + 1
            n = ns if nl > 0 else ns + 1
            i = 0
            while i < len(body):
                if body[i][0] == " ":
                    o += 1; n += 1; i += 1; continue
                mi = 0
                while i < len(body) and body[i][0] == "-":
                    mi += 1; i += 1
                added = []
                while i < len(body) and body[i][0] == "+":
                    added.append(n); n += 1; i += 1
                if mi == 0 and added:
                    out.setdefault(o - 1, []).extend(added)
                o += mi
        return out

    def _whole_toplevel_insert(self, new_tree, new_text, added):
        """True if the inserted lines form whole statements at module/class level."""
        lines = new_text.split("\n")
        added = set(added)
        seen = False
        for ln in added:
            txt = lines[ln - 1].strip() if 0 < ln <= len(lines) else ""
            if not txt or txt.startswith("#"):
                continue
            s, stack, in_header = locate(new_tree, ln)
            if s is None:
                return False
            chain = stack + [s]
            for k, x in enumerate(chain):
                if isinstance(x, FUNC):
                    s = x  # outermost function: the inserted unit must be the whole def
                    break
            if not set(full_span(s)) <= added:
                return False
            seen = True
        return seen

    @staticmethod
    def _pair_lines(hunks):
        pairs = {}
        for os_, ol, ns, nl, body in hunks:
            o = os_ if ol > 0 else os_ + 1
            n = ns if nl > 0 else ns + 1
            i = 0
            while i < len(body):
                if body[i][0] == " ":
                    o += 1; n += 1; i += 1; continue
                mi = pi = 0
                while i < len(body) and body[i][0] == "-":
                    mi += 1; i += 1
                while i < len(body) and body[i][0] == "+":
                    pi += 1; i += 1
                if mi == pi:
                    for k in range(mi):
                        pairs[o + k] = n + k
                o += mi; n += pi
        return pairs

    def _import_level(self, rel, s, notes):
        m = self.m
        if isinstance(s, ast.If) and _is_type_checking(s.test):
            notes.append("type_checking_only:" + rel)
            return 0
        if (isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not s.decorator_list
                and not getattr(self, "_classes", []) and self._new_name_unused(rel, s.name)):
            notes.append("unused_new_def:" + s.name)
            return 0
        if isinstance(s, (ast.If, ast.Try, ast.With, ast.For, ast.While, ast.Import, ast.ImportFrom, ast.Expr)):
            if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str):
                return 0  # docstring / bare string
            return m.loaded(rel)
        names = bound_names(s)
        if not names:
            return m.loaded(rel)
        mask = 0
        for n in names:
            u, hit = self.users(n, exclude_file=rel, exclude_span=full_span(s))
            if not hit:
                notes.append("no_users:" + n)
                mask = m.all_mask
                break
            mask |= u
        mask &= m.loaded(rel)
        # inside a class: only tests that instantiated the class (or an enclosing class), or a subclass
        chain = getattr(self, "_classes", [])
        if chain:
            im = 0
            for k in range(1, len(chain) + 1):
                im |= m.inst(rel + ":" + ".".join(chain[:k]))
            if im:
                mask &= im
                notes.append("inst_filter:" + ".".join(chain))
        return mask

    def analyze(self, files, new_texts):
        """files: parsed patch files; new_texts: rel -> new text (None if deleted). Returns (mask, notes)."""
        from .patchutil import changed_old_lines
        m = self.m
        mask = 0
        notes = []
        self._all_new = new_texts
        for f in files:
            rel = f["old"] or f["new"]
            if f.get("binary"):
                mask |= m.opened(rel) | m.loaded(rel)
                notes.append("binary:" + rel)
                continue
            if f["old"] is None or f["new"] is None:
                # created or deleted file
                if rel.endswith(".py"):
                    # a new module changes nothing until something imports it (that edit is analysed
                    # on its own); a deleted one breaks whoever loaded it
                    notes.append(("deleted_py:" if f["new"] is None else "created_py:") + rel)
                    mask |= m.loaded(rel) | m.opened(rel)
                else:
                    mask |= m.opened(rel) | m.loaded(rel) | m.opened(os.path.dirname(rel))
                continue
            if f["old"] != f["new"]:
                notes.append("rename:" + rel)
                mask |= m.loaded(f["old"]) | m.opened(f["old"]) | m.opened(os.path.dirname(f["old"]))
                continue
            removed, inserts = changed_old_lines(f["hunks"])
            if rel.endswith(".py"):
                self._cols = changed_columns(f["hunks"])
                self._lambda_lines = set()
                self._new_lines = set()
                for os_, ol, ns, nl, body in f["hunks"]:
                    n = ns if nl > 0 else ns + 1
                    for tag, _ in body:
                        if tag == "+":
                            self._new_lines.add(n)
                        if tag != "-":
                            n += 1
                self._lambda_new = set()
                self._old2new = self._pair_lines(f["hunks"])
                self._imap = self._insert_map(f["hunks"])
                self._py_rel = rel
                mask |= self.py_file(rel, self.src(rel), new_texts[rel], removed, inserts, notes)
            else:
                mask |= m.opened(rel)
        return mask, notes
