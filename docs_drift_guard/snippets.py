"""Static analysis of Python snippets found in docs (code blocks and inline code).

Snippets are parsed with `ast`, never executed. Every dotted name is resolved
through the snippet's own imports and simple `x = Class(...)` assignments, so
`client.get_record(...)` can be tied back to `Client.get_record`.
"""
from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass, field


@dataclass
class Use:
    """One name component with its resolved qualified name and exact position."""
    qual: str | None
    text: str
    line: int  # absolute 1-based line in the markdown file
    col: int
    end: int
    chain_start: int
    role: str  # name | attr | import_name | import_module
    aliased: bool = False
    stmt_names: int = 0


@dataclass
class CallUse:
    qual: str | None
    line: int
    npos: int
    kwargs: list[tuple[str, int, int, int, int]]  # name, line, col, end_line, end_col (absolute, chars)
    has_star: bool
    has_ellipsis: bool
    start: tuple[int, int]
    end: tuple[int, int]
    args_text: str = ""
    multiline: bool = False
    sig_like: bool = False  # positional args are bare names, e.g. a signature shown in prose


@dataclass
class DefUse:
    name: str
    name_line: int
    name_col: int
    stub: bool
    params: list[tuple[str, int, int, int, int, str | None]]  # name, line, col, dline, dcol, default text


@dataclass
class Analysis:
    ok: bool = False
    uses: list[Use] = field(default_factory=list)
    calls: list[CallUse] = field(default_factory=list)
    defs: list[DefUse] = field(default_factory=list)
    imports: list[tuple[str, str | None, int]] = field(default_factory=list)  # module, name, line
    local_names: set[str] = field(default_factory=set)
    error: str = ""


def _is_stub(node) -> bool:
    return all(isinstance(b, ast.Pass) or (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant))
               for b in node.body)


def _prepare(lines: list[str]) -> tuple[list[str], int]:
    """Strip REPL prompts and common indentation. Returns (lines, column offset)."""
    nonblank = [ln for ln in lines if ln.strip()]
    if nonblank and any(ln.startswith((">>> ", "... ")) for ln in nonblank):
        out = [ln[4:] if ln.startswith((">>> ", "... ")) else "" for ln in lines]
        return out, 4
    indent = min((len(ln) - len(ln.lstrip(" ")) for ln in nonblank), default=0)
    return [ln[indent:] for ln in lines], indent


def analyze_python(lines: list[str], first_line: int, col_offset: int = 0, mode: str = "exec") -> Analysis:
    """Analyze `lines` (which start at markdown line `first_line`).

    `col_offset` is the column of the snippet start (used for inline code).
    """
    res = Analysis()
    prepared, indent = _prepare(lines)
    src = "\n".join(prepared)
    shift = indent + col_offset
    tree = None
    sig_src = src + ("" if src.rstrip().endswith(":") else ":") + "\n    pass"  # a bare `def f(a, b)` signature
    for attempt in ((src, mode), (sig_src, "exec") if src.lstrip().startswith("def ") else None):
        if attempt is None:
            continue
        try:
            tree = ast.parse(attempt[0], mode=attempt[1])
            break
        except SyntaxError as exc:
            res.error = str(exc)
    if tree is None:
        return res
    res.ok = True

    def cc(lineno: int, byte_col: int) -> int:
        text = prepared[lineno - 1] if 0 < lineno <= len(prepared) else ""
        return len(text.encode()[:byte_col].decode(errors="ignore")) + shift

    def ab(lineno: int) -> int:
        return first_line + lineno - 1

    imports: dict[str, str] = {}
    aliased_names: set[str] = set()
    var_types: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                bound = a.asname or a.name.split(".")[0]
                imports[bound] = a.name if a.asname else a.name.split(".")[0]
                if a.asname:
                    aliased_names.add(bound)
                res.imports.append((a.name, None, ab(node.lineno)))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                bound = a.asname or a.name
                imports[bound] = f"{node.module}.{a.name}"
                if a.asname:
                    aliased_names.add(bound)
                res.imports.append((node.module, a.name, ab(node.lineno)))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if _is_stub(node):
                continue
            res.local_names.add(node.name)
            if not isinstance(node, ast.ClassDef):
                for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
                    res.local_names.add(a.arg)
        elif isinstance(node, (ast.For, ast.comprehension)):
            for n in ast.walk(node.target):
                if isinstance(n, ast.Name):
                    res.local_names.add(n.id)

    def chain(node):
        parts: list[tuple[str, ast.AST]] = []
        while isinstance(node, ast.Attribute):
            parts.append((node.attr, node))
            node = node.value
        if isinstance(node, ast.Name):
            parts.append((node.id, node))
            return parts[::-1], None
        if isinstance(node, ast.Call):
            return parts[::-1], node
        return None, None

    def base_qual(node) -> str | None:
        """Qualified name for the object that a Name or Call evaluates to."""
        if isinstance(node, ast.Call):
            parts, inner = chain(node.func)
            if parts is None:
                return None
            quals = qualify(parts, inner)
            return quals[-1] if quals else base_qual(inner)
        return None

    def qualify(parts, inner_call) -> list[str | None]:
        quals: list[str | None] = []
        if inner_call is not None:
            prev = base_qual(inner_call)
            start = 0
        else:
            first = parts[0][0]
            if first in imports:
                prev = imports[first]
            elif first in var_types:
                prev = var_types[first]
            elif first in res.local_names:
                prev = None
            else:
                prev = first
            quals.append(prev)
            start = 1
        for name, _ in parts[start:]:
            prev = f"{prev}.{name}" if prev else None
            quals.append(prev)
        return quals

    # variable types from `x = Class(...)` and `with Class(...) as x`
    for node in ast.walk(tree):
        pairs = []
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            pairs = [(t, node.value) for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.With):
            pairs = [(i.optional_vars, i.context_expr) for i in node.items
                     if isinstance(i.optional_vars, ast.Name) and isinstance(i.context_expr, ast.Call)]
        for target, call in pairs:
            parts, inner = chain(call.func)
            if parts:
                q = qualify(parts, inner)[-1]
                if q:
                    var_types[target.id] = q
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id not in var_types:
                    res.local_names.add(t.id)

    # import statements as uses
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            line_text = prepared[node.lineno - 1]
            m = re.search(r"\bfrom\s+(" + re.escape(node.module) + r")\b", line_text)
            if m:
                res.uses.append(Use(node.module, node.module, ab(node.lineno), m.start(1) + shift,
                                    m.end(1) + shift, m.start(1) + shift, "import_module", False, len(node.names)))
            for a in node.names:
                if a.lineno == node.lineno:
                    start = cc(a.lineno, a.col_offset)
                    res.uses.append(Use(f"{node.module}.{a.name}", a.name, ab(a.lineno), start,
                                        start + len(a.name), start, "import_name", False, len(node.names)))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.lineno == node.lineno:
                    start = cc(a.lineno, a.col_offset)
                    res.uses.append(Use(a.name, a.name, ab(a.lineno), start, start + len(a.name), start,
                                        "import_module", False, 1))

    # chains
    inner_attrs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Attribute, ast.Name)) or id(node) in inner_attrs:
            continue
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            continue
        parts, inner = chain(node)
        if parts is None:
            continue
        quals = qualify(parts, inner)
        first_node = parts[0][1]
        chain_start = cc(first_node.lineno, first_node.col_offset) if inner is None else -1
        first_name = parts[0][0]
        for i, ((name, n), q) in enumerate(zip(parts, quals)):
            if i == 0 and inner is None and first_name in var_types and first_name not in imports:
                continue  # a variable holding an instance is not a reference to its class
            if isinstance(n, ast.Name):
                col = cc(n.lineno, n.col_offset)
                end = col + len(name)
            else:
                end_col = cc(n.end_lineno, n.end_col_offset)
                col, end = end_col - len(name), end_col
            res.uses.append(Use(q, name, ab(n.end_lineno if isinstance(n, ast.Attribute) else n.lineno),
                                col, end, chain_start if chain_start >= 0 else col,
                                "name" if isinstance(n, ast.Name) else "attr",
                                aliased=(parts[0][0] in aliased_names and i == 0)))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            parts, inner = chain(node.func)
            q = qualify(parts, inner)[-1] if parts else None
            kws = []
            for k in node.keywords:
                if k.arg:
                    kws.append((k.arg, ab(k.lineno), cc(k.lineno, k.col_offset), ab(k.end_lineno),
                                cc(k.end_lineno, k.end_col_offset)))
            res.calls.append(CallUse(
                q, ab(node.lineno), len([a for a in node.args if not isinstance(a, ast.Starred)]), kws,
                any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords),
                any(isinstance(a, ast.Constant) and a.value is Ellipsis for a in node.args)
                or any(isinstance(k.value, ast.Constant) and k.value.value is Ellipsis for k in node.keywords),
                (ab(node.lineno), cc(node.lineno, node.col_offset)),
                (ab(node.end_lineno), cc(node.end_lineno, node.end_col_offset)),
                multiline=node.lineno != node.end_lineno,
                sig_like=any(isinstance(a, ast.Name) for a in node.args)))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ps = []
            a = node.args
            pos = a.posonlyargs + a.args
            defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
            allp = list(zip(pos, defaults)) + list(zip(a.kwonlyargs, a.kw_defaults))
            for arg, d in allp:
                ps.append((arg.arg, ab(arg.lineno), cc(arg.lineno, arg.col_offset),
                           ab(d.lineno) if d is not None else 0,
                           cc(d.lineno, d.col_offset) if d is not None else 0,
                           ast.unparse(d) if d is not None else None))
            m = re.search(r"\bdef\s+(" + re.escape(node.name) + r")\b", prepared[node.lineno - 1])
            res.defs.append(DefUse(node.name, ab(node.lineno), (m.start(1) if m else 0) + shift, _is_stub(node), ps))
    return res
