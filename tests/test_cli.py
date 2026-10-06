import json
import shutil
import subprocess

import pytest

from docs_drift_guard.cli import main
from docs_drift_guard.scenarios import SAMPLE_DIR, SCENARIOS, build_pair

SC = {s.id: s for s in SCENARIOS}


def pair(tmp_path, sid):
    return build_pair(tmp_path / sid, SC[sid])


def check(base, head, *extra):
    return main(["check", "--base", str(base), "--head", str(head), "--docs", "docs", *extra])


def test_exit_0_when_nothing_drifted(tmp_path, capsys):
    base, head = pair(tmp_path, "control_no_change")
    assert check(base, head) == 0
    assert "No documentation drift detected." in capsys.readouterr().out


def test_exit_1_when_drift_needs_attention(tmp_path):
    base, head = pair(tmp_path, "symbol_removed_function")
    assert check(base, head) == 1


def test_fix_writes_docs_and_exits_0_when_everything_was_mechanical(tmp_path):
    base, head = pair(tmp_path, "cli_flag_renamed")
    assert check(base, head) == 1  # auto-fixable but not applied
    assert "--output" in (head / "docs" / "cli.md").read_text(encoding="utf-8")
    assert check(base, head, "--fix") == 0
    text = (head / "docs" / "cli.md").read_text(encoding="utf-8")
    assert "--output" not in text and "--format" in text


def test_fix_still_exits_1_when_flagged_items_remain(tmp_path):
    base, head = pair(tmp_path, "cli_flag_added")
    assert check(base, head, "--fix") == 1


def test_fail_on_levels(tmp_path):
    base, head = pair(tmp_path, "symbol_removed_with_prose")
    assert check(base, head, "--fail-on", "never") == 0
    assert check(base, head, "--fail-on", "high") == 1


def test_report_diff_and_json_outputs(tmp_path):
    base, head = pair(tmp_path, "cli_flag_renamed")
    out = tmp_path / "out"
    out.mkdir()
    code = check(base, head, "--report", str(out / "r.md"), "--diff", str(out / "p.patch"), "--json", str(out / "s.json"))
    assert code == 1
    assert (out / "r.md").read_text(encoding="utf-8").startswith("# Docs drift report")
    assert "+| `--format` | `table` |" in (out / "p.patch").read_text(encoding="utf-8")
    summary = json.loads((out / "s.json").read_text(encoding="utf-8"))
    assert summary["auto"] >= 1 and summary["applied"] is False and summary["findings_detail"]


def test_exit_2_for_bad_inputs(tmp_path, capsys):
    base, head = pair(tmp_path, "control_no_change")
    assert main(["check", "--base", str(base), "--head", str(head), "--docs", "nope"]) == 2
    assert main(["check", "--base", "no-such-rev-xyz", "--head", str(head), "--repo", str(tmp_path)]) == 2
    assert "neither a directory nor a git revision" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        main(["check", "--head", str(head)])  # --base missing
    assert exc.value.code == 2


def test_llm_mock_flag_adds_labelled_suggestions(tmp_path):
    base, head = pair(tmp_path, "symbol_removed_function")
    report = tmp_path / "r.md"
    check(base, head, "--llm", "mock", "--report", str(report))
    assert "simulated by the mock provider" in report.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_revisions_as_base(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(SAMPLE_DIR, repo, ignore=shutil.ignore_patterns("__pycache__"))
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "base"], check=True)
    cli = repo / "sample_lib" / "cli.py"
    cli.write_text(cli.read_text(encoding="utf-8").replace('"--output"', '"--format"'), encoding="utf-8")
    code = main(["check", "--base", "HEAD", "--head", str(repo), "--repo", str(repo), "--docs", "docs", "--fix"])
    assert code == 0
    assert "--format" in (repo / "docs" / "cli.md").read_text(encoding="utf-8")
