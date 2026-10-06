"""Parse Markdown docs into sections and resolve their references to code symbols."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .code_analysis import ApiSurface, CliOption
from .snippets import Analysis, CallUse, Use, analyze_python

FENCE = re.compile(r"^\s*(```+|~~~+)\s*([\w+-]*)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
INLINE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")
FLAG_TOKEN = re.compile(r"(?<![\w-])(--[A-Za-z][\w-]*)")
SHELL_LANGS = {"bash", "sh", "shell", "console", "shell-session", "zsh", "powershell", "cmd", "terminal"}
PY_LANGS = {"python", "py", "python3", "pycon"}


@dataclass
class InlineCode:
    text: str
    line: int
    start: int
    end: int


@dataclass
class CodeBlock:
    lang: str
    fence_line: int
    start_line: int  # first content line (1-based)
    lines: list[str]


@dataclass
class Cell:
    text: str
    line: int
    start: int
    end: int


@dataclass
class Table:
    header: list[str]
    rows: list[list[Cell]]


@dataclass
class Section:
    file: str
    index: int
    heading: str
    level: int
    start: int  # 1-based line of the heading (or 1 for a preamble)
    end: int  # last line, inclusive
    lines: list[str]
    inline: list[InlineCode] = field(default_factory=list)
    blocks: list[CodeBlock] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    _py: list | None = field(default=None, repr=False)

    def line_text(self, n: int) -> str:
        return self.lines[n - self.start]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def in_block(self, n: int) -> bool:
        return any(b.start_line - 1 <= n <= b.start_line + len(b.lines) for b in self.blocks)

    def python_analyses(self) -> list[tuple[object, Analysis]]:
        """Analyses for python blocks and inline code, computed once."""
        if self._py is None:
            out: list = []
            for b in self.blocks:
                if b.lang.lower() in PY_LANGS or (not b.lang and _looks_python(b.lines)):
                    out.append((b, analyze_python(b.lines, b.start_line)))
            for ic in self.inline:
                out.append((ic, analyze_python([ic.text], ic.line, ic.start, mode="eval")))
            self._py = out
        return self._py


@dataclass
class DocFile:
    path: str
    lines: list[str]
    sections: list[Section]
    eol: str = "\n"
    trailing: str = ""  # the final line break, if the file ends with one

    @property
    def text(self) -> str:
        return self.eol.join(self.lines) + self.trailing


def _looks_python(lines: list[str]) -> bool:
    return any(re.match(r"\s*(import |from \S+ import |def |class |>>> )", ln) for ln in lines)


def _split_cells(line: str, lineno: int) -> list[Cell]:
    bars = [i for i, ch in enumerate(line) if ch == "|" and (i == 0 or line[i - 1] != "\\")]
    cells = []
    for a, b in zip(bars, bars[1:]):
        raw = line[a + 1:b]
        stripped = raw.strip()
        start = a + 1 + (len(raw) - len(raw.lstrip()))
        cells.append(Cell(stripped, lineno, start, start + len(stripped)))
    return cells


def parse_markdown(path: str, text: str) -> DocFile:
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    trailing = ""
    if text.endswith("\n"):
        lines.pop()  # the empty piece after the final line break is not a line
        trailing = eol
    heads: list[tuple[int, int, str]] = []  # (line index, level, heading)
    blocks_at: list[tuple[int, CodeBlock]] = []
    fence = None
    cur: CodeBlock | None = None
    for i, ln in enumerate(lines):
        m = FENCE.match(ln)
        if fence is None:
            if m:
                fence = m.group(1)[0] * 3
                cur = CodeBlock(m.group(2), i + 1, i + 2, [])
                continue
            h = HEADING.match(ln)
            if h:
                heads.append((i, len(h.group(1)), h.group(2)))
        else:
            if m and m.group(1)[0] * 3 == fence and not m.group(2):
                blocks_at.append((cur.fence_line - 1, cur))
                fence, cur = None, None
            else:
                cur.lines.append(ln)
    if cur is not None:  # unterminated fence runs to the end of the file
        blocks_at.append((cur.fence_line - 1, cur))

    starts = [h[0] for h in heads]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
        heads.insert(0, (0, 0, ""))
    sections: list[Section] = []
    for n, (idx, level, title) in enumerate(heads):
        end = (heads[n + 1][0] if n + 1 < len(heads) else len(lines)) - 1
        sec = Section(path, n, title, level, idx + 1, end + 1, lines[idx:end + 1])
        sections.append(sec)
    for sec in sections:
        in_fence: set[int] = set()
        for idx, blk in blocks_at:
            if sec.start - 1 <= idx < sec.end:
                sec.blocks.append(blk)
                in_fence.update(range(blk.fence_line, blk.fence_line + len(blk.lines) + 2))
        for off, ln in enumerate(sec.lines):
            no = sec.start + off
            if no in in_fence:
                continue
            for m in INLINE.finditer(ln):
                sec.inline.append(InlineCode(m.group(2), no, m.start(2), m.end(2)))
        # tables
        i = 0
        while i < len(sec.lines):
            no = sec.start + i
            if sec.lines[i].lstrip().startswith("|") and no not in in_fence:
                j = i
                while j < len(sec.lines) and sec.lines[j].lstrip().startswith("|") and sec.start + j not in in_fence:
                    j += 1
                rows = [_split_cells(sec.lines[k], sec.start + k) for k in range(i, j)]
                if len(rows) >= 2 and all(re.fullmatch(r":?-{2,}:?", c.text) for c in rows[1]):
                    sec.tables.append(Table([c.text for c in rows[0]], rows[2:]))
                i = j
            else:
                i += 1
    return DocFile(path, lines, sections, eol, trailing)


CHANGELOG = re.compile(r"(changelog|history|release[-_ ]?notes|news)", re.I)


def load_docs(root: str | Path, paths: list[str], include_changelog: bool = False) -> list[DocFile]:
    """Load every .md file under the given paths (relative to root).

    Changelogs and release notes are skipped by default: they describe the past,
    so mentions of removed or renamed names there are correct.
    """
    root = Path(root)
    files: list[Path] = []
    for p in paths:
        target = root / p
        if target.is_dir():
            files.extend(sorted(target.rglob("*.md")))
        elif target.is_file():
            files.append(target)
    docs = []
    for f in files:
        if not include_changelog and CHANGELOG.search(f.stem):
            continue
        with open(f, encoding="utf-8", newline="") as fh:
            docs.append(parse_markdown(f.relative_to(root).as_posix(), fh.read()))
    return docs


# ---------------------------------------------------------------- references

@dataclass
class Ref:
    kind: str  # symbol | module | flag | env | config
    value: str  # qualname / module / primary flag / env name / config key
    where: str  # inline | python | shell | prose
    line: int
    start: int
    end: int
    conf: float
    use: Use | None = None
    call: CallUse | None = None
    text: str = ""  # literal text matched in the doc (e.g. an alias flag)


def program_candidates(prog: str, api: ApiSurface) -> list[CliOption]:
    """CLI options that belong to the program named `prog` (command or module)."""
    x = prog.replace("-", "_").removesuffix(".py")
    if x.startswith("./"):
        x = x[2:]
    x = x.replace("/", ".")
    return [o for o in api.cli.values()
            if o.module == x or o.module.startswith(x + ".") or o.module.split(".")[-1] == x.split(".")[-1]]


def shell_commands(lines: list[str], first_line: int):
    """Yield (lineno, prog, [(token, start, end)], command_id) per line, joining continuations."""
    prog = None
    cont = False
    cmd_id = -1
    for off, raw in enumerate(lines):
        ln = raw
        if ln.strip().startswith("#"):
            cont = False
            continue
        toks = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", ln)]
        if not cont:
            prog = None
            cmd_id += 1
            body = list(toks)
            while body and body[0][0] in ("$", ">", "PS>", "%"):
                body.pop(0)
            if body and body[0][0].startswith("$") and len(body[0][0]) == 1:
                body.pop(0)
            while body and re.fullmatch(r"[A-Za-z_]\w*=\S*", body[0][0]):
                body.pop(0)
            if body:
                head = body[0][0]
                if head in ("python", "python3", "py") or head.endswith("python.exe") or head.endswith("/python"):
                    rest = [t[0] for t in body[1:]]
                    if "-m" in rest and rest.index("-m") + 1 < len(rest):
                        prog = rest[rest.index("-m") + 1]
                    else:
                        script = next((t for t in rest if not t.startswith("-")), None)
                        prog = script
                else:
                    prog = head
        cont = ln.rstrip().endswith("\\")
        yield first_line + off, prog, toks, cmd_id


def _flag_option(token: str, options) -> CliOption | None:
    for o in options:
        if token in o.flags:
            return o
    return None


def section_refs(section: Section, api: ApiSurface) -> list[Ref]:
    """All references from a section to things that exist in `api`."""
    refs: list[Ref] = []
    all_options = list(api.cli.values())
    env_names = set(api.env)
    config_keys = set(api.config)

    # names the section defines itself (a non-stub def/class) are not references to the library
    shadow: set[str] = set()
    for src, analysis in section.python_analyses():
        if isinstance(src, CodeBlock) and analysis.ok:
            shadow |= analysis.local_names

    # --- python (code blocks and inline code)
    for src, analysis in section.python_analyses():
        where = "python" if isinstance(src, CodeBlock) else "inline"
        if not analysis.ok:
            continue
        for u in analysis.uses:
            if u.qual is None:
                continue
            if u.qual in api.modules or any(m.startswith(u.qual + ".") for m in api.modules):
                if u.qual in api.modules:
                    refs.append(Ref("module", u.qual, where, u.line, u.chain_start, u.end, 0.95, use=u))
                continue
            if "." not in u.qual and u.qual in shadow:
                continue
            sym = api.lookup(u.qual)
            if sym is None:
                continue
            refs.append(Ref("symbol", sym.qualname, where, u.line, u.col, u.end, 0.95, use=u))
        for c in analysis.calls:
            if c.qual and not ("." not in c.qual and c.qual in shadow):
                sym = api.lookup(c.qual)
                if sym:
                    refs.append(Ref("call", sym.qualname, where, c.line, c.start[1], c.end[1], 0.95, call=c))

    # --- inline code that is not python-ish: flags, paths
    for ic in section.inline:
        text = ic.text.strip()
        toks = text.split()
        prog_cands = None
        if toks and not toks[0].startswith("-"):
            if toks[0] in ("python", "python3") and "-m" in toks and toks.index("-m") + 1 < len(toks):
                prog = toks[toks.index("-m") + 1]
            else:
                prog = toks[0]
            if len(toks) > 1:
                prog_cands = program_candidates(prog, api)
                if not prog_cands:
                    prog = None
        else:
            prog = None
        if toks and (toks[0].startswith("-") or prog_cands):
            options = prog_cands if prog_cands else all_options
            for m in FLAG_TOKEN.finditer(ic.text):
                o = _flag_option(m.group(1), options)
                if o:
                    refs.append(Ref("flag", o.primary, "inline", ic.line, ic.start + m.start(1),
                                    ic.start + m.end(1), 0.95 if prog_cands else 0.9, text=m.group(1)))
            if toks[0].startswith("-") and not toks[0].startswith("--"):
                o = _flag_option(toks[0].split("=")[0], all_options)
                if o:
                    refs.append(Ref("flag", o.primary, "inline", ic.line, ic.start,
                                    ic.start + len(toks[0].split("=")[0]), 0.9, text=toks[0].split("=")[0]))
        if " " not in text and ("/" in text or re.search(r"\.(py|md|toml|ya?ml|json|cfg)$", text)):
            norm = text.lstrip("./").removesuffix(".py").replace("/", ".")
            if norm.endswith(".__init__"):
                norm = norm[:-9]
            if norm in api.modules:
                refs.append(Ref("module", norm, "inline", ic.line, ic.start, ic.end, 0.9, text=text))

    # --- shell blocks
    for blk in section.blocks:
        is_shell = blk.lang.lower() in SHELL_LANGS or (not blk.lang and not _looks_python(blk.lines) and any(
            re.match(r"\s*(\$ |python|pip|sample)", ln) for ln in blk.lines))
        if not is_shell:
            continue
        for lineno, prog, toks, _ in shell_commands(blk.lines, blk.start_line):
            if not prog:
                continue
            cands = program_candidates(prog, api)
            if not cands:
                continue
            for tok, s, e in toks:
                if tok.startswith("-"):
                    name = tok.split("=")[0]
                    o = _flag_option(name, cands)
                    if o:
                        refs.append(Ref("flag", o.primary, "shell", lineno, s, s + len(name), 0.95, text=name))

    # --- flags in prose (outside code spans and blocks)
    inline_spans = {(ic.line, ic.start, ic.end) for ic in section.inline}
    for off, ln in enumerate(section.lines):
        no = section.start + off
        if section.in_block(no):
            continue
        lead = bool(re.match(r"\s*(\||[-*]\s)\s*`?--", ln))
        for m in FLAG_TOKEN.finditer(ln):
            if any(l == no and a - 1 <= m.start(1) < b + 1 for l, a, b in inline_spans):
                continue
            o = _flag_option(m.group(1), all_options)
            if o:
                refs.append(Ref("flag", o.primary, "prose", no, m.start(1), m.end(1), 0.9 if lead else 0.7,
                                text=m.group(1)))

    # --- environment variables anywhere
    if env_names:
        pat = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(re.escape(n) for n in sorted(env_names, key=len, reverse=True))
                         + r")(?![A-Za-z0-9_])")
        for off, ln in enumerate(section.lines):
            for m in pat.finditer(ln):
                no = section.start + off
                where = "python" if section.in_block(no) else "prose"
                refs.append(Ref("env", m.group(1), where, no, m.start(1), m.end(1), 0.9))

    # --- config keys
    if config_keys:
        for off, ln in enumerate(section.lines):
            no = section.start + off
            for m in re.finditer(r"\b(?:config|settings|cfg|conf)(?:\[|\.get\()\s*[\"']([\w.-]+)[\"']", ln):
                if m.group(1) in config_keys:
                    refs.append(Ref("config", m.group(1), "python", no, m.start(1), m.end(1), 0.95))
        if re.search(r"\b(config|configuration|settings)\b", section.text, re.I):
            known_leads = [x for x in listing_leads(section, "config") if x in config_keys]
            for ic in section.inline:
                if ic.text.strip() in config_keys:
                    # a key listed in a table beside other known keys is a sure reference; a lone word is not
                    conf = 0.95 if len(known_leads) >= 2 and ic.text.strip() in known_leads else 0.8
                    refs.append(Ref("config", ic.text.strip(), "inline", ic.line, ic.start, ic.end, conf))
    return refs


def build_index(docs: list[DocFile], api: ApiSurface) -> dict[tuple[str, str], list[tuple[str, int, str]]]:
    """Reference index: (kind, value) -> [(file, line, section heading)]."""
    index: dict[tuple[str, str], list[tuple[str, int, str]]] = {}
    for d in docs:
        for s in d.sections:
            for r in section_refs(s, api):
                kind = "symbol" if r.kind == "call" else r.kind
                index.setdefault((kind, r.value), []).append((d.path, r.line, s.heading))
    return index


def listing_leads(section: Section, kind: str) -> list[str]:
    """Names that start table rows or bullets in a section (e.g. option/env var listings)."""
    leads: list[str] = []
    for t in section.tables:
        for row in t.rows:
            if row:
                leads.append(row[0].text.strip("` "))
    for off, ln in enumerate(section.lines):
        m = re.match(r"\s*[-*]\s+`([^`]+)`", ln)
        if m:
            leads.append(m.group(1))
    if kind == "flag":
        return [x for x in leads if x.startswith("-")]
    if kind == "env":
        return [x for x in leads if re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", x)]
    if kind == "config":
        return [x for x in leads if re.fullmatch(r"[a-z][a-z0-9_.]*", x)]
    return leads
