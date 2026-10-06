"""Markdown report for humans (PR comment / CI artifact)."""
from __future__ import annotations

from .core import CheckResult
from .detect import Finding
from .fixer import Edit
from .patch import apply_edits


def _before_after(f: Finding, doc_lines: list[str]) -> list[tuple[str, str]]:
    by_line: dict[int, list[Edit]] = {}
    for e in f.edits:
        by_line.setdefault(e.line, []).append(e)
    pairs = []
    for line, edits in sorted(by_line.items()):
        before = doc_lines[line - 1]
        try:
            after_lines = apply_edits([before], [Edit(e.path, 1, e.start, e.end, e.old, e.new, e.delete_line)
                                                 for e in edits])
            after = after_lines[0] if after_lines else "(line removed)"
        except Exception:
            after = "(could not render)"
        pairs.append((f"{f.file}:{line}", before, after))
    return pairs


def _finding_block(f: Finding, res: CheckResult, patched: bool) -> list[str]:
    doc = next(d for d in res.docs if d.path == f.file)
    out = [f"#### {f.id} `{f.file}` > {f.heading} (lines {f.section.start}-{f.section.end})", ""]
    out.append(f"- **Change:** {f.change.describe() if f.change else 'an example in this section no longer works'}")
    out.append(f"- **Confidence:** {f.confidence:.2f} ({f.level}), found by {f.kind} match")
    action = ("patched" if patched else "patch ready (run with `--fix`)") if f.route == "auto" else "flagged for human review"
    out.append(f"- **Action:** {action}")
    if f.reason:
        out.append(f"- **Why not auto-fixed:** {f.reason}")
    out.append("- **Evidence:**")
    out.extend(f"  - {e}" for e in f.evidence)
    pairs = _before_after(f, doc.lines)
    if pairs:
        out.append("")
        out.append("Before / after" + ("" if f.route == "auto" else " (proposed, not applied)") + ":")
        out.append("")
        for loc, before, after in pairs:
            out.append(f"`{loc}`")
            out.append("```diff")
            out.append(f"- {before}")
            out.append(f"+ {after}" if after != "(line removed)" else "+ (line removed)")
            out.append("```")
    if f.suggestion:
        label = ("simulated by the mock provider, no model was called" if f.suggestion_source == "mock"
                 else f"LLM suggestion from `{f.suggestion_source}`, unverified")
        if f.kind == "undocumented":
            label = "suggested row, description left to a human"
        out += ["", f"Suggestion ({label}; never applied automatically):", "", "```", f.suggestion, "```"]
    out.append("")
    return out


def render_report(res: CheckResult) -> str:
    auto, flagged = res.auto, res.flagged
    lines = ["# Docs drift report", ""]
    if not res.findings:
        lines += ["No documentation drift detected.", "",
                  f"Code changes analysed: {len(res.changes)}."]
        return "\n".join(lines) + "\n"
    lines += [
        f"- Code changes detected: **{len(res.changes)}**",
        f"- Findings: **{len(res.findings)}** (auto-fixable: {len(auto)}, flagged for review: {len(flagged)})",
        f"- Documentation examples that would now fail: {len(res.failures)}"
        f"; still failing after the proposed patch: {len(res.remaining_failures)}",
        "",
        "Confidence scores are ordinal heuristics (high >= 0.85, medium >= 0.55), not calibrated probabilities. "
        "Only high-confidence mechanical fixes are patched; everything else needs a human.",
        "",
        "## Code changes",
        "",
    ]
    lines += [f"- {c.describe()}" for c in res.changes]
    lines.append("")
    if auto:
        lines += [f"## {'Auto-fixed' if res.applied else 'Auto-fixable'} ({len(auto)})", ""]
        for f in auto:
            lines += _finding_block(f, res, res.applied)
    if flagged:
        lines += [f"## Flagged for human review ({len(flagged)})", ""]
        for f in sorted(flagged, key=lambda x: -x.confidence):
            lines += _finding_block(f, res, False)
    if res.remaining_failures:
        lines += ["## Examples still failing after the patch", ""]
        lines += [f"- `{f.file}:{f.line}` {f.reason}" for f in res.remaining_failures]
        lines.append("")
    if res.diff:
        lines += ["## Unified diff of the auto-fixes", "", "```diff", res.diff.rstrip("\n"), "```", ""]
    return "\n".join(lines) + "\n"
