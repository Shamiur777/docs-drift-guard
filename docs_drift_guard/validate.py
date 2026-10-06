"""Check documentation examples against an API surface.

Python snippets are checked statically: imports must exist and calls must bind
to the real signature (`inspect.Signature.bind`). Shell examples are checked
against the argparse/click option spec. Nothing is executed.
"""
from __future__ import annotations

from dataclasses import dataclass

from .code_analysis import ApiSurface
from .docs_analysis import (FLAG_TOKEN, DocFile, program_candidates, shell_commands)

IGNORED_FLAGS = {"-h", "--help", "--version", "-m", "-V", "-c", "-q", "-u", "-e"}


@dataclass
class ExampleFailure:
    file: str
    section_index: int
    heading: str
    line: int
    kind: str  # python | inline | cli
    reason: str
    target: str  # qualname, module or flag the failure is about
    snippet: str = ""

    @property
    def key(self) -> tuple:
        return (self.file, self.line, self.kind, self.target)


def _validate_python(doc, sec, src, an, api: ApiSurface, out: list[ExampleFailure]) -> None:
    from .docs_analysis import CodeBlock
    kind = "python" if isinstance(src, CodeBlock) else "inline"
    pkgs = api.packages()

    def fail(line, reason, target):
        text = doc.lines[line - 1].strip() if 0 < line <= len(doc.lines) else ""
        out.append(ExampleFailure(doc.path, sec.index, sec.heading, line, kind, reason, target, text))

    for module, name, line in an.imports:
        if module.split(".")[0] not in pkgs:
            continue
        if module not in api.modules and not any(m.startswith(module + ".") for m in api.modules):
            fail(line, f"module `{module}` does not exist", module)
        elif name and name != "*" and module in api.modules:
            if name not in api.modules[module] and f"{module}.{name}" not in api.modules:
                fail(line, f"cannot import `{name}` from `{module}`", f"{module}.{name}")
    for u in an.uses:
        if u.role == "attr" and u.qual and "." in u.qual:
            parent = u.qual.rsplit(".", 1)[0]
            if parent in api.modules and u.qual not in api.symbols and u.qual not in api.modules:
                fail(u.line, f"module `{parent}` has no attribute `{u.text}`", u.qual)
    for c in an.calls:
        if not c.qual or c.has_star or c.has_ellipsis:
            continue
        sym = api.lookup(c.qual)
        if sym is None:
            parent = c.qual.rsplit(".", 1)[0] if "." in c.qual else None
            cls = api.lookup(parent) if parent else None
            if cls and cls.kind == "class" and not c.qual.startswith("_"):
                fail(c.line, f"class `{cls.name}` has no method `{c.qual.rsplit('.', 1)[1]}`", c.qual)
            continue
        if sym.kind == "property" or (sym.kind == "class" and not sym.params):
            continue
        try:
            sym.signature().bind(*[None] * c.npos, **{k[0]: None for k in c.kwargs})
        except TypeError as exc:
            fail(c.line, f"call does not match `{sym.signature_text()}`: {exc}", sym.qualname)


def _validate_cli(doc, sec, api: ApiSurface, out: list[ExampleFailure]) -> None:
    def fail(line, reason, target):
        out.append(ExampleFailure(doc.path, sec.index, sec.heading, line, "cli", reason, target,
                                  doc.lines[line - 1].strip()))

    commands: dict[int, dict] = {}
    for blk in sec.blocks:
        from .docs_analysis import SHELL_LANGS, _looks_python
        import re
        is_shell = blk.lang.lower() in SHELL_LANGS or (not blk.lang and not _looks_python(blk.lines) and any(
            re.match(r"\s*(\$ |python|pip|sample)", ln) for ln in blk.lines))
        if not is_shell:
            continue
        local: dict[int, dict] = {}
        for lineno, prog, toks, cid in shell_commands(blk.lines, blk.start_line):
            if not prog:
                continue
            cmd = local.setdefault(cid, {"prog": prog, "line": lineno, "tokens": []})
            cmd["tokens"].extend((t, lineno) for t, _, _ in toks)
        commands.update({(blk.fence_line, k): v for k, v in local.items()})
    for cmd in commands.values():
        cands = program_candidates(cmd["prog"], api)
        if not cands:
            continue
        known = {f for o in cands for f in o.flags}
        present = set()
        for tok, lineno in cmd["tokens"]:
            if tok.startswith("-"):
                name = tok.split("=")[0]
                present.add(name)
                if name not in known and name not in IGNORED_FLAGS and name.startswith("--"):
                    fail(lineno, f"flag `{name}` is not accepted by `{cmd['prog']}`", name)
        for o in cands:
            if o.required and not (present & set(o.flags)):
                fail(cmd["line"], f"required flag `{o.primary}` is missing", o.primary)


def validate_docs(docs: list[DocFile], api: ApiSurface) -> list[ExampleFailure]:
    out: list[ExampleFailure] = []
    for doc in docs:
        for sec in doc.sections:
            for src, an in sec.python_analyses():
                if an.ok:
                    _validate_python(doc, sec, src, an, api, out)
            _validate_cli(doc, sec, api, out)
    return out


def new_failures(docs: list[DocFile], base: ApiSurface, head: ApiSurface) -> list[ExampleFailure]:
    """Examples that fail against head but did not fail against base."""
    before = {f.key for f in validate_docs(docs, base)}
    return [f for f in validate_docs(docs, head) if f.key not in before]
