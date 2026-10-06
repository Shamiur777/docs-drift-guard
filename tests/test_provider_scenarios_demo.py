from types import SimpleNamespace

import pytest

from docs_drift_guard.demo import run_demo
from docs_drift_guard.evaluate import evaluate_all, run_scenario
from docs_drift_guard.holdout import HOLDOUT
from docs_drift_guard.provider import AnthropicProvider, MockProvider, make_provider
from docs_drift_guard.rewrite import attach_suggestions
from docs_drift_guard.scenarios import SCENARIOS, build_pair
from docs_drift_guard.core import run_check


def test_mock_provider_is_deterministic_and_says_it_is_simulated():
    p = MockProvider()
    a = p.complete("sys", "Change: x was removed\nbody")
    assert a == p.complete("sys", "Change: x was removed\nbody")
    assert a.startswith("[simulated, no model was called]") and len(p.calls) == 2


def test_scripted_provider():
    p = MockProvider(script=lambda prompt: "REWRITTEN")
    assert p.complete("s", "p") == "REWRITTEN"


class FakeClient:
    def __init__(self):
        self.calls = []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(text="fixed "), SimpleNamespace(text="text")])


def test_anthropic_provider_with_injected_fake_client(monkeypatch):
    monkeypatch.delenv("DOCS_DRIFT_GUARD_MODEL", raising=False)
    fake = FakeClient()
    p = AnthropicProvider(client=fake)
    assert p.complete("system prompt", "user prompt") == "fixed text"
    call = fake.calls[0]
    assert call["model"] == "claude-sonnet-5-5" and call["system"] == "system prompt"
    assert call["messages"] == [{"role": "user", "content": "user prompt"}]


def test_anthropic_model_comes_from_env(monkeypatch):
    monkeypatch.setenv("DOCS_DRIFT_GUARD_MODEL", "some-other-model")
    fake = FakeClient()
    AnthropicProvider(client=fake).complete("s", "p")
    assert fake.calls[0]["model"] == "some-other-model"


def test_anthropic_import_is_lazy_and_provider_factory():
    AnthropicProvider()  # constructing never imports the package or needs a key
    assert make_provider("none") is None and make_provider("mock").name == "mock"
    with pytest.raises(ValueError):
        make_provider("gpt")


def test_rewrites_only_go_to_flagged_non_semantic_findings_and_are_never_applied(tmp_path):
    sc = next(s for s in SCENARIOS if s.id == "combined_demo")
    base, head = build_pair(tmp_path, sc)
    before = (head / "docs" / "cli.md").read_text(encoding="utf-8")
    res = run_check(base, head, ["docs"], docs_root=head, fix=False)
    provider = MockProvider()
    attach_suggestions(res.findings, provider)
    for f in res.findings:
        if f.route == "auto" or f.kind == "semantic":
            assert f.suggestion == "" or f.kind == "undocumented"
    assert provider.calls == [] or all("Change:" in c for c in provider.calls)
    assert (head / "docs" / "cli.md").read_text(encoding="utf-8") == before  # suggestions never touch the files


@pytest.mark.parametrize("sc", SCENARIOS + HOLDOUT, ids=lambda s: s.id)
def test_every_scenario_patches_docs_exactly_as_expected(sc):
    row = run_scenario(sc)
    assert row["autofix_exact"], row["files_wrong"]
    if sc.category == "control":
        assert row["reported"] == 0


def test_development_scenarios_meet_the_documented_numbers():
    ev = evaluate_all(SCENARIOS)
    assert ev["scenarios"] >= 25 and ev["by_category"]["control"] >= 5 and ev["by_category"]["bait"] >= 3
    assert ev["recall_any"] == 1.0
    assert ev["spurious_medium_plus"] == 0
    assert ev["autofix_exact"] == ev["autofix_expected_scenarios"]


def test_held_out_scenarios_are_not_perfect_and_the_misses_are_known():
    ev = evaluate_all(HOLDOUT)
    missed = sorted(m for r in ev["rows"] for m in r["missed"])
    assert missed == ["docs/guide.md > Building page addresses"]  # prose naming a module in plain words
    assert ev["docs_exact_all_scenarios"] == ev["scenarios"]


def test_demo_runs_offline_and_shows_all_parts(capsys):
    assert run_demo() == 0
    out = capsys.readouterr().out
    for needle in ("AUTO-FIXED", "FLAGGED", "@@", "was renamed to", "`--dry-run` was removed",
                   "SAMPLE_TIMEOUT", "suggested row"):
        assert needle in out
