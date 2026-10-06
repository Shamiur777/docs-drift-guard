"""Offline demo: mutate a temp copy of examples/sample_lib and run the checker on it."""
from __future__ import annotations

import tempfile
from pathlib import Path

from .core import run_check
from .scenarios import DEMO, SAMPLE_DIR, build_pair


def run_demo() -> int:
    if not SAMPLE_DIR.is_dir():
        print("examples/sample_lib not found: run the demo from a checkout of the repository")
        return 2
    with tempfile.TemporaryDirectory(prefix="ddg-demo-") as tmp:
        base, head = build_pair(Path(tmp), DEMO)
        print("Demo: rename a function, change a default, remove a flag, add an env var")
        print("      (a temporary copy of examples/sample_lib is changed; your files are untouched)\n")
        res = run_check(base, head, ["docs"], docs_root=head, fix=True)

        print(f"== 1. Code changes detected ({len(res.changes)}) ==")
        for c in res.changes:
            print(f"  - {c.describe()}")

        print(f"\n== 2. Findings ({len(res.findings)}): {len(res.auto)} auto-fixed, {len(res.flagged)} flagged ==")
        for f in res.findings:
            action = "AUTO-FIXED" if f.route == "auto" else "FLAGGED   "
            print(f"  [{action}] {f.id} {f.file} > {f.heading}  confidence {f.confidence:.2f} ({f.level}, {f.kind})")

        print("\n== 3. Unified diff applied to the docs ==")
        print(res.diff.rstrip())

        print("\n== 4. Flagged for human review ==")
        for f in res.flagged:
            print(f"  {f.id} {f.file} > {f.heading} ({f.level}, {f.kind})")
            print(f"      change: {f.change.describe() if f.change else 'an example no longer works'}")
            for e in f.evidence[:2]:
                print(f"      evidence: {e}")
            if f.reason:
                print(f"      why not auto-fixed: {f.reason}")
            if f.suggestion:
                print(f"      suggested row (description left to a human): {f.suggestion}")

        print(f"\n== 5. After the fixes: {len(res.remaining_failures)} documented examples would still fail "
              f"against the new code; {len(res.flagged)} item(s) wait for a human ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_demo())
