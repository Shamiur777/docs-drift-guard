"""Measured evaluation on the scripted scenarios (see scenarios.py)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .core import LEVELS, run_check
from .holdout import HOLDOUT
from .scenarios import SCENARIOS, Scenario, build_pair, expected_docs


def read_docs(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in sorted((root / "docs").rglob("*.md"))}


def run_scenario(sc: Scenario) -> dict:
    with tempfile.TemporaryDirectory(prefix="ddg-eval-") as tmp:
        base, head = build_pair(Path(tmp), sc)
        expected = expected_docs(base, sc)
        pristine = read_docs(base)
        res = run_check(base, head, ["docs"], docs_root=head, fix=True)
        after = read_docs(head)
    found = {(f.file, f.heading) for f in res.findings}
    found_medium = {(f.file, f.heading) for f in res.findings if LEVELS[f.level] >= LEVELS["medium"]}
    stale = set(sc.stale)
    changed = sorted(p for p in after if after[p] != pristine[p])
    wrong = sorted(p for p in after if after[p] != expected[p])
    return {
        "id": sc.id,
        "category": sc.category,
        "stale": len(stale),
        "detected": len(stale & found),
        "detected_medium": len(stale & found_medium),
        "reported": len(found),
        "spurious": sorted(f"{a} > {b}" for a, b in found - stale),
        "reported_medium": len(found_medium),
        "spurious_medium": sorted(f"{a} > {b}" for a, b in found_medium - stale),
        "missed": sorted(f"{a} > {b}" for a, b in stale - found),
        "expects_autofix": bool(sc.expected_edits),
        "autofix_exact": not wrong,
        "files_changed": changed,
        "files_wrong": wrong,
        "auto_findings": len(res.auto),
        "flagged_findings": len(res.flagged),
    }


def evaluate_all(scenarios: list[Scenario] | None = None) -> dict:
    rows = [run_scenario(sc) for sc in (SCENARIOS if scenarios is None else scenarios)]
    stale = sum(r["stale"] for r in rows)
    reported = sum(r["reported"] for r in rows)
    reported_m = sum(r["reported_medium"] for r in rows)
    controls = [r for r in rows if r["category"] == "control"]
    baits = [r for r in rows if r["category"] == "bait"]
    fixable = [r for r in rows if r["expects_autofix"]]
    return {
        "scenarios": len(rows),
        "by_category": {c: sum(1 for r in rows if r["category"] == c) for c in ("drift", "control", "bait")},
        "stale_sections": stale,
        "recall_any": sum(r["detected"] for r in rows) / stale,
        "recall_medium_plus": sum(r["detected_medium"] for r in rows) / stale,
        "reported_sections": reported,
        "spurious_any": sum(len(r["spurious"]) for r in rows),
        "spurious_rate_any": sum(len(r["spurious"]) for r in rows) / reported if reported else 0.0,
        "reported_medium_plus": reported_m,
        "spurious_medium_plus": sum(len(r["spurious_medium"]) for r in rows),
        "spurious_rate_medium_plus": sum(len(r["spurious_medium"]) for r in rows) / reported_m if reported_m else 0.0,
        "controls": len(controls),
        "controls_with_no_findings": sum(1 for r in controls if r["reported"] == 0),
        "bait_scenarios": len(baits),
        "bait_with_no_spurious_medium_plus": sum(1 for r in baits if not r["spurious_medium"]),
        "autofix_expected_scenarios": len(fixable),
        "autofix_exact": sum(1 for r in fixable if r["autofix_exact"]),
        "docs_exact_all_scenarios": sum(1 for r in rows if r["autofix_exact"]),
        "wrong_edit_scenarios": [r["id"] for r in rows if not r["autofix_exact"]],
        "rows": rows,
    }


def print_group(title: str, ev: dict) -> None:
    print(f"\n=== {title} ===")
    print(f"{'scenario':34} {'cat':8} stale found found(med+) spurious(any/med+) docs==expected")
    for r in ev["rows"]:
        print(f"{r['id']:34} {r['category']:8} {r['stale']:5} {r['detected']:5} {r['detected_medium']:10} "
              f"{len(r['spurious']):>9}/{len(r['spurious_medium']):<8} {'yes' if r['autofix_exact'] else 'NO'}")
    for r in ev["rows"]:
        for label in ("missed", "spurious", "files_wrong"):
            if r[label]:
                print(f"  {r['id']}: {label}: {r[label]}")
    n = ev["stale_sections"]
    print()
    print(f"scenarios: {ev['scenarios']} {ev['by_category']}")
    print(f"detection recall (any confidence):  {sum(r['detected'] for r in ev['rows'])}/{n} = {ev['recall_any']:.1%}")
    print(f"detection recall (medium+ only):    {sum(r['detected_medium'] for r in ev['rows'])}/{n} = {ev['recall_medium_plus']:.1%}")
    print(f"spurious sections (any confidence): {ev['spurious_any']}/{ev['reported_sections']} = {ev['spurious_rate_any']:.1%}")
    print(f"spurious sections (medium+ only):   {ev['spurious_medium_plus']}/{ev['reported_medium_plus']} = {ev['spurious_rate_medium_plus']:.1%}")
    print(f"no-drift controls with zero findings: {ev['controls_with_no_findings']}/{ev['controls']}")
    print(f"bait scenarios with no spurious medium+ finding: {ev['bait_with_no_spurious_medium_plus']}/{ev['bait_scenarios']}")
    print(f"auto-fix exact (scenarios expecting a patch): {ev['autofix_exact']}/{ev['autofix_expected_scenarios']}")
    print(f"patched docs equal expected docs (all scenarios): {ev['docs_exact_all_scenarios']}/{ev['scenarios']}")


def run_evaluation(json_out: str | None = None) -> int:
    groups = {"development scenarios (detector was tuned on these)": evaluate_all(SCENARIOS),
              "held-out scenarios (written after tuning)": evaluate_all(HOLDOUT)}
    for title, ev in groups.items():
        print_group(title, ev)
    if json_out:
        Path(json_out).write_text(json.dumps(groups, indent=2), encoding="utf-8")
    return 0
