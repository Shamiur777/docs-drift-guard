"""Apply text edits to docs and produce unified diffs."""
from __future__ import annotations

import difflib
from pathlib import Path

from .detect import Finding
from .docs_analysis import DocFile
from .fixer import Edit


class PatchError(Exception):
    pass


def apply_edits(lines: list[str], edits: list[Edit]) -> list[str]:
    """Apply edits to a list of lines. Raises PatchError on overlap or stale text."""
    out = list(lines)
    ordered = sorted(set(edits), key=lambda e: (e.line, e.start), reverse=True)
    prev: Edit | None = None
    for e in ordered:
        if prev is not None and prev.line == e.line and (e.end > prev.start or prev.delete_line or e.delete_line):
            raise PatchError(f"overlapping edits on line {e.line}")
        text = out[e.line - 1]
        if text[e.start:e.end] != e.old:
            raise PatchError(f"line {e.line} no longer contains {e.old!r}")
        if e.delete_line:
            out[e.line - 1] = None  # type: ignore[assignment]
        else:
            out[e.line - 1] = text[:e.start] + e.new + text[e.end:]
        prev = e
    return [ln for ln in out if ln is not None]


def _conflicts(accepted: list[Edit], new: list[Edit]) -> bool:
    for e in new:
        for a in accepted:
            if a == e:
                continue
            if a.line == e.line and (a.delete_line or e.delete_line or (a.start < e.end and e.start < a.end)):
                return True
    return False


def plan_patch(findings: list[Finding], docs: list[DocFile]) -> dict[str, list[Edit]]:
    """Choose the auto findings whose edits do not collide. Demotes colliding ones to `flag`."""
    accepted: dict[str, list[Edit]] = {}
    for f in findings:
        if f.route != "auto":
            continue
        cur = accepted.setdefault(f.file, [])
        if _conflicts(cur, f.edits):
            f.route = "flag"
            f.reason = "its patch overlaps another patch; review by hand"
            continue
        cur.extend(e for e in f.edits if e not in cur)
    return accepted


def render(docs: list[DocFile], plan: dict[str, list[Edit]]) -> tuple[dict[str, str], str]:
    """Return (new text per file, unified diff of all changes)."""
    new_texts: dict[str, str] = {}
    diff_parts: list[str] = []
    for d in docs:
        edits = plan.get(d.path)
        if not edits:
            continue
        new_lines = apply_edits(d.lines, edits)
        new_text = d.eol.join(new_lines) + d.trailing
        new_texts[d.path] = new_text
        diff_parts.append("".join(difflib.unified_diff(
            [ln + "\n" for ln in d.lines], [ln + "\n" for ln in new_lines],
            fromfile=f"a/{d.path}", tofile=f"b/{d.path}")))
    return new_texts, "".join(diff_parts)


def write_files(root: str | Path, new_texts: dict[str, str]) -> None:
    for rel, text in new_texts.items():
        with open(Path(root) / rel, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
