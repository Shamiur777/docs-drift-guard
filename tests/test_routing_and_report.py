import pytest

from docs_drift_guard.core import run_check
from docs_drift_guard.holdout import HOLDOUT
from docs_drift_guard.provider import MockProvider
from docs_drift_guard.report import render_report
from docs_drift_guard.scenarios import SCENARIOS, build_pair

ALL = {s.id: s for s in SCENARIOS + HOLDOUT}


@pytest.fixture
def run(tmp_path):
    n = {"i": 0}

    def go(scenario_id, **kw):
        n["i"] += 1
        base, head = build_pair(tmp_path / f"{scenario_id}-{n['i']}", ALL[scenario_id])
        return run_check(base, head, ["docs"], docs_root=head, **kw)
    return go


def test_high_confidence_mechanical_fixes_are_auto(run):
    res = run("rename_function")
    exact = [f for f in res.findings if f.kind == "exact"]
    assert exact and all(f.route == "auto" and f.level == "high" and f.edits for f in exact)


def test_medium_confidence_rename_is_flagged_with_a_proposed_patch_only(run):
    res = run("rename_medium_confidence")
    f = next(f for f in res.findings if f.kind == "exact" and f.file == "docs/api.md")
    assert f.route == "flag" and f.level in ("medium", "high") and f.edits
    assert "below the auto-fix threshold" in f.reason
    assert res.new_texts == {} and res.diff == ""  # nothing is patched


def test_removal_has_no_mechanical_fix_and_is_flagged(run):
    res = run("symbol_removed_function")
    exact = [f for f in res.findings if f.kind == "exact"]
    assert exact and all(f.route == "flag" and not f.edits for f in exact)
    assert all(f.level == "high" for f in exact)  # sure it is stale, but nobody can guess the replacement


def test_partial_patch_is_never_auto_applied(run):
    res = run("env_var_removed")  # table row is removable, the shell example is not
    f = next(f for f in res.findings if f.file == "docs/configuration.md")
    assert f.route == "flag" and f.edits and "does not cover every stale reference" in f.reason


def test_semantic_matches_are_low_confidence_and_never_patched(run):
    res = run("symbol_removed_with_prose")
    sem = [f for f in res.findings if f.kind == "semantic"]
    assert sem and all(f.route == "flag" and not f.edits and f.level == "low" for f in sem)


def test_failing_example_without_a_fix_is_flagged_high(run):
    res = run("param_added_required")
    ex = [f for f in res.findings if f.kind == "example"]
    assert ex and all(f.route == "flag" and f.level == "high" for f in ex)
    assert any("region" in e for f in ex for e in f.evidence)


def test_new_item_missing_from_listing_is_flagged_with_suggested_row(run):
    res = run("cli_flag_added")
    f = next(f for f in res.findings if f.kind == "undocumented")
    assert f.route == "flag" and f.suggestion.startswith("| `--page` |") and not f.edits


def test_explicit_values_and_other_tools_do_not_produce_findings(run):
    res = run("cli_flag_removed_other_tool")
    assert all(f.heading != "Working with other tools" for f in res.findings)
    res = run("default_changed_class_init")
    assert all(f.file != "docs/guide.md" for f in res.findings)


def test_controls_are_clean(run):
    for sid in ("control_no_change", "control_private_rename", "control_docstring_only", "control_new_function",
                "control_unreferenced_change", "h_control_body_change"):
        assert run(sid).findings == [], sid


def test_report_contains_evidence_before_after_and_diff(run):
    res = run("cli_flag_renamed", fix=True)
    text = render_report(res)
    assert text.startswith("# Docs drift report")
    assert "## Auto-fixed" in text and "Before / after" in text
    assert "- | `--output` | `table` |" in text and "+ | `--format` | `table` |" in text
    assert "```diff" in text and "--- a/docs/cli.md" in text
    assert "ordinal heuristics" in text  # confidence is not oversold
    assert "Evidence" in text and "Confidence:" in text


def test_report_separates_flagged_items_and_labels_mock_suggestions(run):
    res = run("symbol_removed_function", provider=MockProvider())
    text = render_report(res)
    assert "## Flagged for human review" in text and "## Auto-fixed" not in text
    assert "simulated by the mock provider, no model was called" in text
    assert "never applied automatically" in text


def test_report_for_clean_run(run):
    assert "No documentation drift detected." in render_report(run("control_no_change"))


def test_exit_code_policy(run):
    assert run("control_no_change").exit_code() == 0
    assert run("symbol_removed_function").exit_code() == 1
    unfixed = run("cli_flag_renamed")  # auto-fixable but not applied yet
    assert unfixed.exit_code() == 1
    assert run("cli_flag_renamed", fix=True).exit_code() == 0
    assert run("symbol_removed_function").exit_code("never") == 0
    sem_only = run("symbol_removed_with_prose")
    assert sem_only.exit_code("high") == 1 and run("rename_function", fix=True).exit_code("medium") == 0
    sem = run("rename_medium_confidence")  # low-confidence hints count only when --fail-on low is chosen
    assert any(f.level == "low" for f in sem.findings) and sem.exit_code("low") == 1
