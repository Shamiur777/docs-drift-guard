"""Optional LLM rewrite for drift that has no mechanical fix.

The result is only ever attached to the report as a suggestion. It is never
applied automatically.
"""
from __future__ import annotations

from .detect import Finding
from .provider import Provider

SYSTEM = (
    "You maintain technical documentation. Given a code change and a documentation section that "
    "is now inaccurate, return the corrected Markdown for that section only. Change as little as "
    "possible, keep the author's voice, and do not invent behaviour that the change does not state."
)


def build_prompt(f: Finding) -> str:
    change = f.change.describe() if f.change else "an example in this section no longer runs"
    evidence = "\n".join(f"- {e}" for e in f.evidence)
    return (f"Change: {change}\nEvidence:\n{evidence}\n"
            f"Section ({f.file}, lines {f.section.start}-{f.section.end}):\n{f.section.text}\n\n"
            "Return only the corrected section.")


def attach_suggestions(findings: list[Finding], provider: Provider) -> None:
    for f in findings:
        if f.kind == "semantic" or f.route == "auto" or f.suggestion:
            continue
        f.suggestion = provider.complete(SYSTEM, build_prompt(f))
        f.suggestion_source = provider.name
