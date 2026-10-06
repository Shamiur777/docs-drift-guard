"""The check pipeline: analyse code, analyse docs, detect drift, plan and apply fixes."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .code_analysis import ApiSurface, extract_api
from .detect import Finding, level
from .diff import CodeChange, diff_api
from .docs_analysis import DocFile, load_docs, parse_markdown
from .patch import plan_patch, render, write_files
from .provider import Provider
from .rewrite import attach_suggestions
from .validate import ExampleFailure, new_failures
from .detect import detect

LEVELS = {"low": 0, "medium": 1, "high": 2}


@dataclass
class CheckResult:
    base_api: ApiSurface
    head_api: ApiSurface
    changes: list[CodeChange]
    docs: list[DocFile]
    findings: list[Finding]
    failures: list[ExampleFailure]
    diff: str = ""
    new_texts: dict[str, str] = field(default_factory=dict)
    applied: bool = False
    remaining_failures: list[ExampleFailure] = field(default_factory=list)

    @property
    def auto(self) -> list[Finding]:
        return [f for f in self.findings if f.route == "auto"]

    @property
    def flagged(self) -> list[Finding]:
        return [f for f in self.findings if f.route == "flag"]

    def exit_code(self, fail_on: str = "medium") -> int:
        """0 = nothing needs attention, 1 = drift remains. `fail_on` is the lowest level that counts."""
        if fail_on == "never":
            return 0
        bar = LEVELS[fail_on]
        pending = [f for f in self.flagged if LEVELS[f.level] >= bar]
        if not self.applied:
            pending += [f for f in self.auto if LEVELS[f.level] >= bar]
        return 1 if pending else 0


def run_check(base_root: str | Path, head_root: str | Path, docs: list[str], docs_root: str | Path | None = None,
              src: str = ".", fix: bool = False, provider: Provider | None = None,
              semantic: bool = True, include_changelog: bool = False) -> CheckResult:
    base_api = extract_api(base_root, src)
    head_api = extract_api(head_root, src)
    changes = diff_api(base_api, head_api)
    docs_root = Path(docs_root or head_root)
    doc_files = load_docs(docs_root, docs, include_changelog)
    findings, failures = detect(base_api, head_api, doc_files, changes, semantic=semantic)
    plan = plan_patch(findings, doc_files)
    new_texts, diff = render(doc_files, plan)
    if provider is not None:
        attach_suggestions(findings, provider)
    res = CheckResult(base_api, head_api, changes, doc_files, findings, failures, diff, new_texts)
    if fix and new_texts:
        write_files(docs_root, new_texts)
        res.applied = True
    patched = [parse_markdown(d.path, new_texts[d.path]) if d.path in new_texts else d for d in doc_files]
    res.remaining_failures = new_failures(patched, base_api, head_api)
    return res
