"""Map code changes to stale documentation and decide what to do with each finding.

Routing policy (confidence is an ordinal heuristic, not a calibrated probability):
  * high   (>= 0.85) and a complete deterministic patch exists -> `auto` (patched with --fix)
  * anything else (medium, low, or no mechanical fix)          -> `flag` for human review
Semantic (TF-IDF) matches are always `flag`, never patched.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .code_analysis import ApiSurface
from .diff import CodeChange
from .docs_analysis import DocFile, Ref, Section, listing_leads, section_refs
from .fixer import Edit, propose
from .validate import ExampleFailure, new_failures

HIGH, MEDIUM = 0.85, 0.55
SEMANTIC_MIN = 0.28
STOPWORDS = set("a an the of to in is it and or for on with by as be this that from at are was can use used "
                "you your will not if when then also into its has have any all sample lib".split())

SYMBOL_KINDS = {"symbol_removed", "symbol_renamed", "symbol_moved", "param_renamed", "param_removed",
                "default_changed"}
FLAG_KINDS = {"cli_flag_removed", "cli_flag_renamed", "cli_default_changed"}
ENV_KINDS = {"env_var_removed", "env_var_renamed", "env_default_changed"}
CONFIG_KINDS = {"config_key_removed", "config_key_renamed"}
SEMANTIC_KINDS = {"symbol_removed", "symbol_renamed", "symbol_moved", "module_moved", "param_removed",
                  "param_renamed", "default_changed", "cli_flag_removed", "cli_flag_renamed", "cli_default_changed",
                  "env_var_removed", "env_var_renamed", "env_default_changed", "config_key_removed",
                  "config_key_renamed"}
LABELS = {"flag": "CLI flags", "env": "environment variables", "config": "config keys"}
ADDED_KINDS = {"cli_flag_added": "flag", "env_var_added": "env", "config_key_added": "config"}


def level(conf: float) -> str:
    return "high" if conf >= HIGH else "medium" if conf >= MEDIUM else "low"


@dataclass
class Finding:
    id: str
    kind: str  # exact | example | semantic | undocumented
    change: CodeChange | None
    file: str
    section: Section
    confidence: float
    evidence: list[str] = field(default_factory=list)
    edits: list[Edit] = field(default_factory=list)
    complete: bool = False
    route: str = "flag"  # auto | flag
    reason: str = ""
    suggestion: str = ""
    suggestion_source: str = ""

    @property
    def level(self) -> str:
        return level(self.confidence)

    @property
    def heading(self) -> str:
        return self.section.heading or "(top of file)"

    @property
    def lines(self) -> tuple[int, int]:
        return self.section.start, self.section.end


def _tokens(text: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    words = re.findall(r"[A-Za-z][A-Za-z0-9]+", text.replace("_", " "))
    return [_stem(w.lower()) for w in words if w.lower() not in STOPWORDS and len(w) > 2]


def _stem(w: str) -> str:
    """Crude plural folding so `slugs` matches `slug`."""
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


class Tfidf:
    def __init__(self, docs: list[list[str]]):
        n = len(docs)
        df = Counter(t for d in docs for t in set(d))
        self.idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}

    def vec(self, tokens: list[str]) -> dict[str, float]:
        tf = Counter(t for t in tokens if t in self.idf)
        v = {t: c * self.idf[t] for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    @staticmethod
    def cosine(a: dict[str, float], b: dict[str, float]) -> float:
        return sum(x * b.get(t, 0.0) for t, x in a.items())


def _relevant_refs(change: CodeChange, refs: list[Ref]) -> list[Ref]:
    k = change.kind
    if k in SYMBOL_KINDS:
        q = change.base_symbol.qualname if change.base_symbol else change.subject
        is_class = change.base_symbol is not None and change.base_symbol.kind == "class"
        sel = [r for r in refs if r.kind in ("symbol", "call")
               and (r.value == q or (is_class and k in ("symbol_removed", "symbol_renamed")
                                     and r.value.startswith(q + ".")))]
        return sel
    if k == "module_moved":
        return [r for r in refs if r.kind == "module" and (r.value == change.old or r.value.startswith(change.old + "."))]
    if k in FLAG_KINDS:
        return [r for r in refs if r.kind == "flag" and r.value == change.subject]
    if k in ENV_KINDS:
        return [r for r in refs if r.kind == "env" and r.value == change.subject]
    if k in CONFIG_KINDS:
        return [r for r in refs if r.kind == "config" and r.value == change.subject]
    return []


def _mentions(section: Section, word: str) -> bool:
    """True if `word` appears in code formatting (inline span, code block); plain prose does not count."""
    pat = re.compile(r"(?<![\w-])" + re.escape(word) + r"(?![\w-])")
    if any(pat.search(ic.text) for ic in section.inline):
        return True
    return any(pat.search(ln) for b in section.blocks for ln in b.lines)


def _mentions_text(section: Section, word: str) -> bool:
    return re.search(r"(?<![\w-])" + re.escape(word) + r"(?![\w-])", section.text) is not None


def _old_token_present(section: Section, old: str) -> bool:
    from .fixer import value_variants
    for v, _ in value_variants(old, old):
        if re.search(r"(?<![\w.\-])" + re.escape(v) + r"(?![\w.])", section.text):
            return True
    return False


def _factor(change: CodeChange) -> float:
    if change.kind in ("symbol_renamed", "cli_flag_renamed", "env_var_renamed", "config_key_renamed"):
        return change.similarity
    if change.kind == "symbol_moved":
        return 0.95
    return 1.0


def _plain(value: str | None) -> str:
    if value is None:
        return ""
    return value[1:-1] if len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0] else value


def _suggestion_row(change: CodeChange, head: ApiSurface) -> str:
    """A table row for a new item; the description is left for a human to write."""
    if change.kind == "cli_flag_added":
        o = head.cli.get(change.new)
        return f"| `{change.new}` | {_plain(o.default) if o else ''} | {o.help if o else ''} |"
    if change.kind == "env_var_added":
        e = head.env.get(change.new)
        return f"| `{change.new}` | {_plain(e.default) if e else ''} | |"
    return f"| `{change.new}` | | |"


def detect(base: ApiSurface, head: ApiSurface, docs: list[DocFile], changes: list[CodeChange],
           semantic: bool = True) -> tuple[list[Finding], list[ExampleFailure]]:
    findings: list[Finding] = []
    counter = 0

    def new_id() -> str:
        nonlocal counter
        counter += 1
        return f"F{counter:03d}"

    sec_refs: dict[tuple[str, int], list[Ref]] = {}
    for d in docs:
        for s in d.sections:
            sec_refs[(d.path, s.index)] = section_refs(s, base)

    # 1. exact references
    for ch in changes:
        for d in docs:
            for s in d.sections:
                refs = sec_refs[(d.path, s.index)]
                if ch.kind in ADDED_KINDS:
                    kind = ADDED_KINDS[ch.kind]
                    leads = listing_leads(s, kind)
                    if kind == "flag":
                        mod = head.cli[ch.new].module
                        known = [x for x in leads if any(x in o.flags and o.module == mod for o in head.cli.values())]
                    elif kind == "env":
                        known = [x for x in leads if x in head.env]
                    else:
                        known = [x for x in leads if x in head.config]
                    if len(known) >= 2 and not _mentions_text(s, ch.new):
                        findings.append(Finding(
                            new_id(), "undocumented", ch, d.path, s, 0.7,
                            [f"section lists {len(known)} sibling {LABELS[kind]} ({', '.join('`' + x + '`' for x in known[:3])}) "
                             f"but not the new `{ch.new}`"],
                            suggestion=_suggestion_row(ch, head),
                            reason="new item missing from a listing; a human has to write its description"))
                    continue
                rel = _relevant_refs(ch, refs)
                if not rel:
                    continue
                if ch.kind in ("param_renamed", "param_removed", "default_changed"):
                    if not _mentions(s, ch.param if ch.kind != "param_renamed" else ch.old):
                        continue
                prop = propose(ch, s, refs, base, head)
                conf = max(r.conf for r in rel) * _factor(ch)
                min_conf = min(r.conf for r in rel) * _factor(ch)
                if ch.kind in ("default_changed", "cli_default_changed", "env_default_changed"):
                    if not prop.edits:
                        if not _old_token_present(s, ch.old_value):
                            continue
                        conf = min(conf, 0.6)
                        min_conf = conf
                evidence = [f"{r.where} reference to `{r.text or _short(s, r)}` at line {r.line}" for r in _dedupe(rel)]
                f = Finding(new_id(), "exact", ch, d.path, s, round(conf, 2), evidence, prop.edits, prop.complete)
                if prop.edits and prop.complete and min_conf >= HIGH:
                    f.route = "auto"
                else:
                    f.reason = _flag_reason(prop, min_conf, ch)
                findings.append(f)

    # 2. examples that now fail
    failures = new_failures(docs, base, head)
    for fl in failures:
        sec = next(s for d in docs if d.path == fl.file for s in d.sections if s.index == fl.section_index)
        ch = _change_for_failure(fl, changes)
        existing = next((f for f in findings if f.file == fl.file and f.section.index == fl.section_index
                         and f.change is not None and f.kind == "exact" and fl.target in _keys(f.change)), None)
        text = f"example at line {fl.line} would fail: {fl.reason}"
        if existing:
            existing.evidence.append(text)
            continue
        dup = next((f for f in findings if f.kind == "example" and f.file == fl.file
                    and f.section.index == fl.section_index and f.change is ch), None)
        if dup:
            dup.evidence.append(text)
            continue
        findings.append(Finding(new_id(), "example", ch, fl.file, sec, 0.9, [text],
                                reason="a documented example no longer works; no mechanical fix is known"))

    # 3. semantic matches (never auto-patched). Only for changes that make a statement false,
    #    only when the section shares >= 2 terms, and never when the section literally contains
    #    the changed name (exact analysis already decided what that mention means).
    if semantic:
        sections = [(d, s) for d in docs for s in d.sections]
        model = Tfidf([_tokens(s.text) for _, s in sections])
        svecs = [model.vec(_tokens(s.text)) for _, s in sections]
        for ch in changes:
            if ch.kind not in SEMANTIC_KINDS:
                continue
            literal = ch.subject.rsplit(".", 1)[-1] if ch.kind != "symbol_moved" else ch.base_symbol.name
            q = model.vec(list(dict.fromkeys(_tokens(f"{ch.hint} {literal} {ch.new.rsplit('.', 1)[-1]}"))))
            for (d, s), v in zip(sections, svecs):
                if any(f.file == d.path and f.section.index == s.index for f in findings):
                    continue
                if _mentions_text(s, literal):
                    continue
                shared = sorted(set(q) & set(v), key=lambda t: -q[t] * v[t])
                sim = Tfidf.cosine(q, v)
                if sim >= SEMANTIC_MIN and len(shared) >= 2:
                    findings.append(Finding(
                        new_id(), "semantic", ch, d.path, s, round(min(sim, 0.8), 2),
                        [f"TF-IDF similarity {sim:.2f} with the change; shared terms: {', '.join(shared[:4])}"],
                        reason="no exact reference; related by wording only"))
    return findings, failures


def _short(sec: Section, r: Ref) -> str:
    return sec.line_text(r.line)[r.start:r.end]


def _dedupe(refs: list[Ref]) -> list[Ref]:
    seen, out = set(), []
    for r in refs:
        k = (r.line, r.start, r.end)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out[:6]


def _flag_reason(prop, min_conf: float, ch: CodeChange) -> str:
    if not prop.edits:
        return "no deterministic fix for this kind of drift"
    if not prop.complete:
        return "a patch exists but does not cover every stale reference" + (f" ({prop.note})" if prop.note else "")
    if min_conf < HIGH:
        return f"patch available but confidence {min_conf:.2f} is below the auto-fix threshold {HIGH}"
    return ""


def _keys(ch: CodeChange) -> set[str]:
    keys = {ch.subject, ch.old, ch.new}
    if ch.base_symbol:
        keys.add(ch.base_symbol.qualname)
    if ch.base_cli:
        keys.update(ch.base_cli.flags)
    return keys


def _change_for_failure(fl: ExampleFailure, changes: list[CodeChange]) -> CodeChange | None:
    for ch in changes:
        if fl.target in _keys(ch) or (ch.kind == "module_moved" and fl.target.startswith(ch.subject + ".")):
            return ch
    return None
