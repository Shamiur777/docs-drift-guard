"""Deterministic, minimal-patch fixes for mechanical documentation drift.

Every fixer returns a `Proposal`: the exact text edits plus a flag saying
whether they cover every stale reference. Anything that is not covered is
left for a human (see detect.py for the routing policy).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .code_analysis import ApiSurface
from .diff import CodeChange
from .docs_analysis import Ref, Section


@dataclass(frozen=True)
class Edit:
    path: str
    line: int  # 1-based
    start: int
    end: int
    old: str
    new: str
    delete_line: bool = False


@dataclass
class Proposal:
    edits: list[Edit] = field(default_factory=list)
    complete: bool = True
    note: str = ""

    def add(self, e: Edit | None) -> None:
        if e and e not in self.edits:
            self.edits.append(e)


def _edit(sec: Section, line: int, start: int, end: int, new: str) -> Edit | None:
    text = sec.line_text(line)
    if not (0 <= start <= end <= len(text)):
        return None
    return Edit(sec.file, line, start, end, text[start:end], new)


def _delete(sec: Section, line: int) -> Edit:
    return Edit(sec.file, line, 0, len(sec.line_text(line)), sec.line_text(line), "", True)


def _in_section(sec: Section, line: int) -> bool:
    return sec.start <= line <= sec.end


# ---------------------------------------------------------------- value variants

def value_variants(old: str, new: str) -> list[tuple[str, str]]:
    """Textual spellings of a default value in docs, paired with the replacement."""
    out = [(old, new)]
    if len(old) >= 2 and old[0] in "'\"" and old[-1] == old[0]:
        o_in = old[1:-1]
        n_in = new[1:-1] if len(new) >= 2 and new[0] in "'\"" else new
        out = [(f"'{o_in}'", f"'{n_in}'"), (f'"{o_in}"', f'"{n_in}"'), (o_in, n_in)]
    return out


def _value_pattern(variants: list[tuple[str, str]]) -> str:
    alts = "|".join(re.escape(v) for v, _ in sorted(variants, key=lambda t: -len(t[0])))
    return rf"(?<![\w.\-])(`?)({alts})\1(?![\w.]|\.\d)"


def _pick(variants, matched: str) -> str:
    for v, n in variants:
        if v == matched:
            return n
    return matched


def _name_mentioned(line: str, names: list[str]) -> bool:
    for n in names:
        if re.search(r"(?<![\w-])" + re.escape(n) + r"(?![\w-])", line):
            return True
    return False


def default_edits(sec: Section, names: list[str], old: str, new: str, sym_qual: str | None,
                  base: ApiSurface) -> list[Edit]:
    """Edit stated defaults for `names` (a parameter, flag or env var) in prose, tables and signatures."""
    variants = value_variants(old, new)
    edits: list[Edit] = []
    pat = re.compile(_value_pattern(variants))

    # signatures: `def f(a, b=OLD)` and signature-like inline calls
    if sym_qual:
        for src, an in sec.python_analyses():
            if not an.ok:
                continue
            for d in an.defs:
                sym = base.lookup(d.name)
                if not sym or sym.qualname != sym_qual:
                    continue
                for (pname, _, _, dl, dc, dtext) in d.params:
                    if pname in names and dtext is not None and dl:
                        text = sec.line_text(dl)
                        for v, n in sorted(variants, key=lambda t: -len(t[0])):
                            if text[dc:dc + len(v)] == v:
                                edits.append(_edit(sec, dl, dc, dc + len(v), n))
                                break
            for c in an.calls:
                if not c.qual or not c.sig_like:
                    continue
                sym = base.lookup(c.qual)
                if not sym or sym.qualname != sym_qual:
                    continue
                for (kname, kl, kc, kel, kec) in c.kwargs:
                    if kname in names and kl == kel:
                        text = sec.line_text(kl)
                        m = re.match(re.escape(kname) + r"\s*=\s*", text[kc:kec])
                        if m:
                            vstart = kc + m.end()
                            for v, n in sorted(variants, key=lambda t: -len(t[0])):
                                if text[vstart:kec] == v:
                                    edits.append(_edit(sec, kl, vstart, kec, n))
                                    break

    # tables with a Default column
    for t in sec.tables:
        dcols = [i for i, h in enumerate(t.header) if "default" in h.lower()]
        if not dcols:
            continue
        for row in t.rows:
            if not row or not _name_mentioned(row[0].text.replace("`", ""), names):
                continue
            for di in dcols:
                if di < len(row):
                    cell = row[di]
                    inner = cell.text.strip("`")
                    for v, n in variants:
                        if inner == v:
                            off = cell.text.find(inner)
                            edits.append(_edit(sec, cell.line, cell.start + off, cell.start + off + len(inner), n))
                            break

    # prose / list / help-text lines that state a default
    table_lines = {c.line for t in sec.tables for r in t.rows for c in r}
    for off, ln in enumerate(sec.lines):
        no = sec.start + off
        if no in table_lines or not _name_mentioned(ln, names):
            continue
        vm = re.search(r"default[^`\n]{0,25}?" + _value_pattern(variants), ln, re.I)
        if not vm:
            vm = re.search(_value_pattern(variants) + r"[^`\n]{0,12}?by default", ln, re.I)
        if vm:
            edits.append(_edit(sec, no, vm.start(2), vm.end(2), _pick(variants, vm.group(2))))
    out = []
    for e in edits:
        if e and e not in out and e.old != e.new:
            out.append(e)
    return out


# ---------------------------------------------------------------- per-kind fixers

def _refs(refs: list[Ref], *kinds: str) -> list[Ref]:
    return [r for r in refs if r.kind in kinds]


def fix_rename(change: CodeChange, sec: Section, refs: list[Ref], base: ApiSurface) -> Proposal:
    p = Proposal()
    old, new = change.base_symbol.name, change.head_symbol.name
    q = change.base_symbol.qualname
    for r in _refs(refs, "symbol"):
        if r.value != q or r.use is None:
            continue
        if r.use.aliased:
            continue
        if r.use.text == old:
            p.add(_edit(sec, r.line, r.start, r.end, new))
        else:
            p.complete = False
    for src, an in sec.python_analyses():
        if not an.ok:
            continue
        for d in an.defs:
            sym = base.lookup(d.name)
            if sym and sym.qualname == q and d.name == old and d.stub:
                p.add(_edit(sec, d.name_line, d.name_col, d.name_col + len(old), new))
    return p


def fix_move(change: CodeChange, sec: Section, refs: list[Ref], base: ApiSurface) -> Proposal:
    p = Proposal()
    old_q, new_q = change.base_symbol.qualname, change.head_symbol.qualname
    old_mod, new_mod = change.base_symbol.module, change.head_symbol.module
    for r in _refs(refs, "symbol"):
        if r.value != old_q or r.use is None:
            continue
        u = r.use
        if u.role == "import_name":
            mod = next((x.use for x in refs if x.use is not None and x.use.role == "import_module"
                        and x.use.line == u.line and x.use.qual == old_mod), None)
            if mod is not None and u.stmt_names == 1:
                p.add(_edit(sec, mod.line, mod.col, mod.end, new_mod))
            else:
                p.complete = False
        else:
            text = sec.line_text(u.line)
            if text[u.chain_start:u.end] == old_q:
                p.add(_edit(sec, u.line, u.chain_start, u.end, new_q))
            elif u.role == "name" and u.chain_start == u.col:
                continue  # a bare name that was imported: fixing the import is enough
            else:
                p.complete = False
    return p


def fix_module_move(change: CodeChange, sec: Section, refs: list[Ref]) -> Proposal:
    p = Proposal()
    old, new = change.old, change.new
    for r in _refs(refs, "module"):
        if not (r.value == old or r.value.startswith(old + ".")):
            continue
        target = new + r.value[len(old):]
        if r.use is not None:
            u = r.use
            if u.role == "import_name":  # from pkg import module
                if old.rsplit(".", 1)[0] == new.rsplit(".", 1)[0]:
                    p.add(_edit(sec, u.line, u.col, u.end, new.rsplit(".", 1)[1]))
                else:
                    p.complete = False
            else:
                text = sec.line_text(u.line)
                if text[u.chain_start:u.end] == r.value:
                    p.add(_edit(sec, u.line, u.chain_start, u.end, target))
                else:
                    p.complete = False
        else:  # file path in inline code
            text = sec.line_text(r.line)[r.start:r.end]
            o_path, n_path = r.value.replace(".", "/"), target.replace(".", "/")
            if o_path in text:
                p.add(_edit(sec, r.line, r.start, r.end, text.replace(o_path, n_path)))
            else:
                p.complete = False
    return p


def fix_param_rename(change: CodeChange, sec: Section, refs: list[Ref], base: ApiSurface) -> Proposal:
    p = Proposal()
    old, new = change.old, change.new
    q = change.base_symbol.qualname
    for src, an in sec.python_analyses():
        if not an.ok:
            continue
        for c in an.calls:
            sym = base.lookup(c.qual) if c.qual else None
            if sym and sym.qualname == q:
                for (kname, kl, kc, kel, kec) in c.kwargs:
                    if kname == old:
                        p.add(_edit(sec, kl, kc, kc + len(old), new))
        for d in an.defs:
            sym = base.lookup(d.name)
            if sym and sym.qualname == q:
                for (pname, pl, pc, *_rest) in d.params:
                    if pname == old:
                        p.add(_edit(sec, pl, pc, pc + len(old), new))
    for ic in sec.inline:
        if ic.text.strip("` ") == old and not any(e.line == ic.line and e.start <= ic.start < e.end for e in p.edits):
            p.add(_edit(sec, ic.line, ic.start, ic.end, new))
    for t in sec.tables:
        for row in t.rows:
            if row and row[0].text.strip("`") == old:
                c = row[0]
                off = c.text.find(old)
                p.add(_edit(sec, c.line, c.start + off, c.start + off + len(old), new))
    return p


def _remove_kwarg(sec: Section, name: str, line: int, col: int, end_line: int, end_col: int) -> Edit | None:
    if line != end_line:
        return None
    text = sec.line_text(line)
    kw = text[col:end_col]
    if text.strip() in (kw, kw + ","):
        return _delete(sec, line)
    before = text[:col].rstrip()
    after = text[end_col:]
    if before.endswith(","):
        s = len(before) - 1
        return _edit(sec, line, s, end_col, "")
    m = re.match(r"\s*,\s*", after)
    if m:
        return _edit(sec, line, col, end_col + m.end(), "")
    return _edit(sec, line, col, end_col, "")


def fix_param_removed(change: CodeChange, sec: Section, refs: list[Ref], base: ApiSurface) -> Proposal:
    p = Proposal()
    name = change.param
    q = change.base_symbol.qualname
    touched: set[int] = set()
    for src, an in sec.python_analyses():
        if not an.ok:
            continue
        for c in an.calls:
            sym = base.lookup(c.qual) if c.qual else None
            if sym and sym.qualname == q:
                for (kname, kl, kc, kel, kec) in c.kwargs:
                    if kname == name:
                        e = _remove_kwarg(sec, name, kl, kc, kel, kec)
                        if e:
                            p.add(e)
                            touched.add(kl)
                        else:
                            p.complete = False
        for d in an.defs:
            sym = base.lookup(d.name)
            if sym and sym.qualname == q and d.stub:
                text = sec.line_text(d.name_line)
                pat = re.compile(r",\s*\*{0,2}" + re.escape(name) + r"(?:\s*=\s*[^,)]+)?(?=[,)])")
                m = pat.search(text)
                if m is None:
                    m = re.search(r"(?<=\()\s*\*{0,2}" + re.escape(name) + r"(?:\s*=\s*[^,)]+)?\s*,?\s*", text)
                if m:
                    p.add(_edit(sec, d.name_line, m.start(), m.end(), ""))
                    touched.add(d.name_line)
                else:
                    p.complete = False
    for t in sec.tables:
        for row in t.rows:
            if row and row[0].text.strip("`") == name:
                p.add(_delete(sec, row[0].line))
                touched.add(row[0].line)
    for off, ln in enumerate(sec.lines):
        no = sec.start + off
        if re.match(r"\s*[-*]\s+`" + re.escape(name) + r"`", ln):
            nxt = sec.lines[off + 1] if off + 1 < len(sec.lines) else ""
            if not nxt.strip() or re.match(r"\s*[-*]\s", nxt) or off + 1 >= len(sec.lines):
                p.add(_delete(sec, no))
                touched.add(no)
            else:
                p.complete = False
    deleted = {e.line for e in p.edits if e.delete_line}
    for ic in sec.inline:
        if ic.line in touched or ic.line in deleted:
            continue
        if re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", ic.text):
            p.complete = False
            p.note = f"`{name}` is still mentioned in code formatting on line {ic.line}"
    return p


def _delete_listing_line(sec: Section, ref: Ref) -> Edit | None:
    """Delete a table row or bullet whose lead is the removed item."""
    text = sec.line_text(ref.line)
    if re.match(r"\s*\|\s*`?[^|]*" + re.escape(text[ref.start:ref.end]), text) and text.lstrip().startswith("|"):
        first = text.split("|")[1] if text.count("|") >= 2 else ""
        if text[ref.start:ref.end] in first:
            return _delete(sec, ref.line)
    if re.match(r"\s*[-*]\s+`?" + re.escape(text[ref.start:ref.end]), text):
        return _delete(sec, ref.line)
    return None


def fix_flag_removed(change: CodeChange, sec: Section, refs: list[Ref]) -> Proposal:
    p = Proposal()
    opt = change.base_cli
    for r in _refs(refs, "flag"):
        if r.value != change.subject:
            continue
        text = sec.line_text(r.line)
        e = _delete_listing_line(sec, r)
        if e:
            p.add(e)
        elif r.where == "shell" or (r.where == "inline" and r.conf >= 0.95):  # a command with a known program
            s, end = r.start, r.end
            if end < len(text) and text[end] == "=":
                m = re.match(r"=\S*", text[end:])
                end += m.end()
            elif opt.takes_value:
                m = re.match(r"\s+(?!-)[^\s\\]+", text[end:])
                if m:
                    end += m.end()
            while s > 0 and text[s - 1] == " ":
                s -= 1
            rest = (text[:s] + text[end:]).strip()
            if rest in ("", "\\"):
                p.complete = False
            else:
                p.add(_edit(sec, r.line, s, end, ""))
        else:
            p.complete = False
    return p


def fix_token_rename(change: CodeChange, sec: Section, refs: list[Ref], kind: str, head_names=()) -> Proposal:
    """Rename a flag, env var or config key token wherever it is referenced."""
    p = Proposal()
    for r in _refs(refs, kind):
        if r.value != change.subject:
            continue
        text = sec.line_text(r.line)[r.start:r.end]
        if kind == "flag":
            if text != change.old or text in head_names:
                continue
            p.add(_edit(sec, r.line, r.start, r.end, change.new))
        else:
            if text == change.old:
                p.add(_edit(sec, r.line, r.start, r.end, change.new))
            else:
                p.complete = False
    return p


def fix_listing_removed(change: CodeChange, sec: Section, refs: list[Ref], kind: str) -> Proposal:
    p = Proposal()
    for r in _refs(refs, kind):
        if r.value != change.subject:
            continue
        e = _delete_listing_line(sec, r)
        if e:
            p.add(e)
        else:
            p.complete = False
    return p


def propose(change: CodeChange, sec: Section, refs: list[Ref], base: ApiSurface, head: ApiSurface) -> Proposal:
    k = change.kind
    if k == "symbol_renamed":
        return fix_rename(change, sec, refs, base)
    if k == "symbol_moved":
        return fix_move(change, sec, refs, base)
    if k == "module_moved":
        return fix_module_move(change, sec, refs)
    if k == "param_renamed":
        return fix_param_rename(change, sec, refs, base)
    if k == "param_removed":
        return fix_param_removed(change, sec, refs, base)
    if k == "default_changed":
        e = default_edits(sec, [change.param], change.old_value, change.new_value,
                          change.base_symbol.qualname, base)
        return Proposal(e, bool(e))
    if k == "cli_default_changed":
        e = default_edits(sec, list(change.base_cli.flags), change.old_value, change.new_value, None, base)
        return Proposal(e, bool(e))
    if k == "env_default_changed":
        e = default_edits(sec, [change.subject], change.old_value, change.new_value, None, base)
        return Proposal(e, bool(e))
    if k == "cli_flag_renamed":
        names = {f for o in head.cli.values() for f in o.flags}
        return fix_token_rename(change, sec, refs, "flag", names)
    if k == "cli_flag_removed":
        return fix_flag_removed(change, sec, refs)
    if k == "env_var_renamed":
        return fix_token_rename(change, sec, refs, "env")
    if k == "env_var_removed":
        return fix_listing_removed(change, sec, refs, "env")
    if k == "config_key_renamed":
        return fix_token_rename(change, sec, refs, "config")
    if k == "config_key_removed":
        return fix_listing_removed(change, sec, refs, "config")
    return Proposal([], False)
