"""Command line: `python -m docs_drift_guard check|demo|evaluate`."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .core import run_check
from .provider import make_provider
from .report import render_report
from .revisions import RevisionError, materialize


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="docs-drift-guard",
                                description="Find documentation that code changes made stale, and fix what is mechanical.")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="compare two revisions and check the docs")
    c.add_argument("--base", required=True, help="directory or git revision of the old code")
    c.add_argument("--head", required=True, help="directory or git revision of the new code")
    c.add_argument("--docs", nargs="+", default=["docs"], help="doc files or folders, relative to the docs root")
    c.add_argument("--src", default=".", help="code folder inside each revision (default: the whole tree)")
    c.add_argument("--docs-root", help="where the docs live and get patched (default: --head if a directory, else .)")
    c.add_argument("--repo", default=".", help="git repository used to resolve revisions")
    c.add_argument("--fix", action="store_true", help="write the high-confidence mechanical fixes to the docs")
    c.add_argument("--report", help="write the Markdown report to this file")
    c.add_argument("--diff", help="write the unified diff of the auto-fixes to this file")
    c.add_argument("--json", dest="json_out", help="write a machine-readable summary to this file")
    c.add_argument("--fail-on", choices=["high", "medium", "low", "never"], default="medium",
                   help="lowest confidence level that makes the exit code non-zero (default: medium)")
    c.add_argument("--llm", choices=["none", "mock", "anthropic"], default="none",
                   help="suggest rewrites for flagged items (mock output is simulated)")
    c.add_argument("--no-semantic", action="store_true", help="skip TF-IDF matching of related sections")
    c.add_argument("--include-changelog", action="store_true", help="also check changelog/release-note files")
    sub.add_parser("demo", help="run the bundled demo on a temporary copy of examples/sample_lib")
    e = sub.add_parser("evaluate", help="run the scripted scenarios and print measured numbers")
    e.add_argument("--json", dest="json_out", help="write results to this file")
    return p


def _summary(res) -> dict:
    return {
        "changes": len(res.changes),
        "findings": len(res.findings),
        "auto": len(res.auto),
        "flagged": len(res.flagged),
        "applied": res.applied,
        "example_failures": len(res.failures),
        "findings_detail": [
            {"id": f.id, "kind": f.kind, "file": f.file, "heading": f.heading, "confidence": f.confidence,
             "level": f.level, "route": f.route, "change": f.change.describe() if f.change else None}
            for f in res.findings],
    }


def cmd_check(args) -> int:
    with tempfile.TemporaryDirectory(prefix="ddg-work-") as tmp:
        try:
            base = materialize(args.base, args.repo, Path(tmp))
            head = materialize(args.head, args.repo, Path(tmp))
        except RevisionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        docs_root = args.docs_root or (args.head if Path(args.head).is_dir() else args.repo)
        if not (Path(docs_root) / args.docs[0]).exists():
            print(f"error: docs path {args.docs[0]!r} not found under {docs_root}", file=sys.stderr)
            return 2
        res = run_check(base, head, args.docs, docs_root=docs_root, src=args.src, fix=args.fix,
                        provider=make_provider(args.llm), semantic=not args.no_semantic,
                        include_changelog=args.include_changelog)
    report = render_report(res)
    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")
    if args.diff:
        Path(args.diff).write_text(res.diff, encoding="utf-8")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(_summary(res), indent=2), encoding="utf-8")
    if not args.report:
        print(report)
    code = res.exit_code(args.fail_on)
    print(f"SUMMARY changes={len(res.changes)} findings={len(res.findings)} auto={len(res.auto)} "
          f"flagged={len(res.flagged)} applied={res.applied} exit={code}")
    return code


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "demo":
        from .demo import run_demo
        return run_demo()
    from .evaluate import run_evaluation
    return run_evaluation(args.json_out)


if __name__ == "__main__":
    sys.exit(main())
