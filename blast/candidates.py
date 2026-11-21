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
        mask = 0
        hit = False
        for f in self.m.files_with_rt():
            text = self.src(f)
            if not text or name not in text:
                continue
            lines = []
            for i, ln in enumerate(text.split("\n"), 1):
                if name in ln and rx.search(ln):
                    if f == exclude_file and i in exclude_span:
                        continue
                    lines.append(i)
            if lines:
                hit = True
                mask |= self.m.rt(f, lines)
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
        for L in inserts:
            lines.add(L); lines.add(L + 1)
        import_level = False
        for ln in sorted(lines):
            s, stack, in_header = locate(old_tree, ln)
            if s is None:
                continue
            in_func = any(isinstance(x, FUNC) for x in stack)
            if isinstance(s, FUNC) and in_header:
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
            mask |= m.rt(rel, span)
            if not in_func:
                import_level = True
                mask |= self._import_level(rel, s, notes)
        # statements on the new side (inserted / replaced) at module or class level
        new_lines = self._new_lines
        for ln in sorted(new_lines):
            s, stack, in_header = locate(new_tree, ln)
            if s is None:
                continue
            if not any(isinstance(x, FUNC) for x in stack) and not (isinstance(s, FUNC) and not in_header):
                import_level = True
                mask |= self._import_level(rel, s, notes)
        if import_level:
            notes.append("import_level:" + rel)
        return mask

    def _import_level(self, rel, s, notes):
        m = self.m
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
                return m.loaded(rel)
            mask |= u
        return mask & m.loaded(rel)

    def analyze(self, files, new_texts):
        """files: parsed patch files; new_texts: rel -> new text (None if deleted). Returns (mask, notes)."""
        from .patchutil import changed_old_lines
        m = self.m
        mask = 0
        notes = []
        for f in files:
            rel = f["old"] or f["new"]
            if f.get("binary"):
                mask |= m.opened(rel) | m.loaded(rel)
                notes.append("binary:" + rel)
                continue
            if f["old"] is None or f["new"] is None:
                # created or deleted file
                mask |= m.opened(rel) | m.loaded(rel) | m.opened(os.path.dirname(rel))
                if rel.endswith(".py"):
                    notes.append("created_or_deleted_py:" + rel)
                    mask |= m.loaded_dir(os.path.dirname(rel))
                continue
            if f["old"] != f["new"]:
                notes.append("rename:" + rel)
                mask |= m.loaded(f["old"]) | m.opened(f["old"]) | m.opened(os.path.dirname(f["old"]))
                continue
            removed, inserts = changed_old_lines(f["hunks"])
            if rel.endswith(".py"):
                self._new_lines = set()
                for os_, ol, ns, nl, body in f["hunks"]:
                    n = ns if nl > 0 else ns + 1
                    for tag, _ in body:
                        if tag == "+":
                            self._new_lines.add(n)
                        if tag != "-":
                            n += 1
                mask |= self.py_file(rel, self.src(rel), new_texts[rel], removed, inserts, notes)
            else:
                mask |= m.opened(rel)
        return mask, notes
