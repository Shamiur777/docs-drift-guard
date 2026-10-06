# docs-drift-guard (self-healing documentation checks for CI)

[![tests](https://github.com/Shamiur777/docs-drift-guard/actions/workflows/tests.yml/badge.svg)](https://github.com/Shamiur777/docs-drift-guard/actions)

A GitHub Action and CLI that compares two revisions of a Python codebase, finds the Markdown documentation those changes made inaccurate, fixes the mechanical cases with minimal patches, and flags everything else for a human with the evidence.

The sample library, its docs and every scenario in this repo are synthetic and fictional. Nothing here calls a real service.

## The problem
Docs rot because nothing connects a code change to the sentence it broke. A function gets renamed, a default changes, a flag disappears, and the README keeps telling people to run the old command. Review catches some of it. This tool turns it into a CI check: on every pull request it says which doc sections are now wrong, why, and (when the fix is mechanical) proposes the exact patch.

## How it works

```
base rev ──┐                                 ┌─ docs sections ── references (code spans, snippets,
           ├─ ast: public API surface ──┐    │                   shell examples, flags, env vars, paths)
head rev ──┘   (signatures, defaults,   │    │
                CLI flags, env vars,    ▼    ▼
                config keys, modules)  structured diff ──► drift detection
                                                             1. exact references  (resolved through imports)
                                                             2. examples that now fail (bind / flag check)
                                                             3. TF-IDF related sections (low confidence)
                                                                   │
                                          confidence policy ◄──────┘
                       high + complete mechanical patch ──► auto-fix (unified diff, applied with --fix)
                       everything else ───────────────────► flagged: evidence + proposed patch or LLM suggestion
                                                             │
                                                  Markdown report ► PR comment, fix PR, exit code
```

- `code_analysis.py` uses `ast` only (nothing is imported or executed). It extracts functions, classes, methods (signature, defaults, decorators, docstring), argparse and click options, `os.environ`/`os.getenv` reads, config keys and module paths.
- `diff.py` produces typed changes: `symbol_removed`, `symbol_renamed` (similarity from name, signature, docstring and uniqueness), `symbol_moved`, `module_moved`, `param_removed`, `param_renamed`, `param_added_required`, `param_added_optional`, `param_reordered`, `default_changed`, `cli_flag_added/removed/renamed/now_required`, `cli_default_changed`, `env_var_added/removed/renamed`, `env_default_changed`, `config_key_added/removed/renamed`.
- `docs_analysis.py` splits Markdown into sections and resolves references. Python snippets (code blocks and inline code) are parsed, and `client.get_record(...)` is tied back to `Client.get_record` through imports and `client = Client()`. Shell examples only count when the program resolves to the project's CLI, so `pip install --verbose` never matches `--verbose`.
- `validate.py` checks examples against the new code without running them: calls must bind with `inspect.Signature.bind`, imports must exist, flags must exist and required flags must be present. Only examples that passed before and fail now are reported.
- `fixer.py` makes minimal text edits (a column range on a line, or a deleted table row). `patch.py` refuses overlapping or stale edits.
- `detect.py` applies the routing policy below. `rewrite.py` can ask a `Provider` for a suggested rewrite of flagged sections.

Routing policy (scores are ordinal heuristics, not calibrated probabilities):

| Situation | Route |
|---|---|
| exact reference, complete deterministic patch, confidence >= 0.85 | auto-fix |
| patch exists but confidence is lower (for example a rename with a reworded docstring) | flag, patch shown as a proposal |
| patch covers only some of the stale references | flag, partial patch shown |
| no mechanical fix (removed function, new required parameter, new env var) | flag |
| example in the docs now fails | flag |
| TF-IDF related section with no exact reference | flag at low confidence, never patched |

## Results
All numbers below come from `python -m docs_drift_guard evaluate`. It runs 52 scripted scenarios on `examples/sample_lib` (a small library with five doc files). Every scenario changes the code, then compares the checker's findings with a hand-written list of the sections a human would have to update, and compares the docs after `--fix` with a hand-written expected result.

There are two groups, and the difference matters:

- **Development scenarios (36).** I tuned the detector while running these (the TF-IDF rules, the rename scoring, the config-key confidence), so their numbers are optimistic.
- **Held-out scenarios (16).** Written after tuning. I ran them once, did not change any rule or threshold to fit them, and report that first run. After it I only fixed bugs that unit tests exposed (class members carried over on a class rename, env-var pairing, trailing-newline handling, removing flags inside inline commands); the held-out numbers were identical before and after those fixes.

| | Development (36) | Held-out (16) |
|---|---|---|
| Scenarios (drift / no-drift control / false-positive bait) | 27 / 5 / 4 | 13 / 1 / 2 |
| Stale doc sections a human must update | 61 | 22 |
| Detection recall, any confidence | 61/61 (100.0%) | 21/22 (95.5%) |
| Detection recall, medium confidence or higher | 58/61 (95.1%) | 21/22 (95.5%) |
| Spurious sections / all reported, any confidence | 5/66 (7.6%) | 2/23 (8.7%) |
| Spurious sections / all reported, medium or higher | 0/58 (0.0%) | 1/22 (4.5%) |
| No-drift controls with zero findings | 5/5 | 1/1 |
| Scenarios where the auto-fix equals the expected docs (those expecting a patch) | 19/19 | 11/11 |
| Docs after `--fix` equal expected docs, all scenarios (includes "must not touch" cases) | 36/36 | 16/16 |

What the held-out misses and false positives were, as they are:
- **Missed:** `docs/guide.md > Building page addresses` after `formatting.py` was renamed. The section names the module in plain words ("the formatting module") and never in code formatting, so nothing connects it to the change.
- **Spurious (medium):** `--status` default changed from `open` to `all`. The quickstart passes `--status open` explicitly. That is still correct, but the tool sees the old default value in a section that references the flag and flags it. A real limit: it cannot tell an explicit argument from a stated default in this fallback path.
- **Spurious (low):** a TF-IDF hint on the settings section after `--config` was renamed (shared words only).

Caveats you should keep in mind:
- This is one tiny library, one set of docs that I wrote, and scenarios that I wrote. The docs use conventions the fixer understands (tables with a Default column, signatures in code spans). Real documentation is messier, and I have not measured it.
- The "spurious" count is sections reported that my expected list did not contain. Some of those low-confidence hints may be reasonable for a reviewer; I count them against the tool anyway.
- Run time was not measured.

Tests: `python -m pytest -q` gives 144 passed (unit tests for each diff type, reference extraction, snippet validation, patch application, confidence routing, report format, CLI exit codes, a real `git archive` revision run, the provider classes with an injected fake client, and every scenario above).

## Run it
Windows (PowerShell or cmd), from the repo folder:

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -q                      # 144 tests, offline, no API key
.venv\Scripts\python -m docs_drift_guard demo          # rename + default + removed flag + new env var
.venv\Scripts\python -m docs_drift_guard evaluate      # reproduce the table above
```

On Linux or macOS use `.venv/bin/python`.

Check two revisions of your own repo (`--base` and `--head` take a directory or a git revision):

```bash
python -m docs_drift_guard check --base origin/main --head . --docs docs README.md --report out.md
python -m docs_drift_guard check --base origin/main --head . --docs docs --fix --diff docs-fix.patch
```

Exit codes: `0` nothing needs attention (or every finding was auto-fixed with `--fix`), `1` drift remains, `2` bad input. `--fail-on high|medium|low|never` sets the lowest confidence that counts (default `medium`), so low-confidence TF-IDF hints are reported but do not fail the build unless you ask. Changelogs and release notes are skipped by default because they correctly describe old names (`--include-changelog` to check them).

Demo output (abridged, from a real run):

```
== 1. Code changes detected (4) ==
  - `sample_lib.client.fetch_user` was renamed to `sample_lib.client.get_user` (similarity 0.91)
  - CLI flag `--dry-run` was removed
  - default of `--limit` changed from `50` to `25`
  - environment variable `SAMPLE_TIMEOUT` was added
== 2. Findings (7): 6 auto-fixed, 1 flagged ==
  [AUTO-FIXED] F001 docs/api.md > `fetch_user`  confidence 0.87 (high, exact)
  ...
  [FLAGGED   ] F007 docs/configuration.md > Environment variables  confidence 0.70 (medium, undocumented)
```

### Optional LLM suggestions
`--llm mock` attaches a deterministic placeholder to each flagged item. Its text is labelled simulated in the report and says that no model was called. `--llm anthropic` uses the `anthropic` package (`pip install anthropic`, `ANTHROPIC_API_KEY`, model from `DOCS_DRIFT_GUARD_MODEL`, default `claude-sonnet-5-5`). I have not run the Anthropic path against the real API: it is tested only with an injected fake client. Suggestions are only ever added to the report. They are never applied to files.

## GitHub Action
`action.yml` is a composite action that installs the package and runs `check`. `examples/workflows/docs-drift-guard.yml` shows the full pull request workflow: run the check with `fix: "true"`, post or update one report comment (`actions/github-script`), open a docs-fix pull request into the PR branch for the auto-fixed items (`peter-evans/create-pull-request`), and fail the job only if flagged items remain.

**Status: the workflows are included but have not run on GitHub yet; they run on first push.** I validated that the YAML parses and ran the same command line locally, nothing more.

Required permissions for the example workflow:
- `contents: write` so the fix branch can be pushed,
- `pull-requests: write` for the comment and the fix pull request,
- a checkout with `fetch-depth: 0` so the base revision exists.
Pull requests from forks get a read-only token, so the comment and fix-PR steps will not work there.

`.github/workflows/tests.yml` runs the test suite and the demo.

## Design decisions
- **Never silently guess.** Only a complete, deterministic patch at high confidence is applied. A partial patch, a lower-confidence rename or any ambiguity becomes a flagged finding with the proposed patch shown but not written.
- **Evidence over similarity.** Exact references resolved through imports and variable types come first; TF-IDF is a low-confidence fallback for prose, and it never produces a patch. It is skipped when the section literally contains the changed name, because exact analysis already decided what that mention means.
- **Examples are checked, not just matched.** A call that no longer binds is concrete evidence, so it is reported at high confidence even when no fix is known. Only newly broken examples are reported.
- **Static analysis only.** Docs snippets and the code under analysis are parsed, never imported or executed, so the check is safe to run on untrusted pull requests.
- **Shadowing and program context.** A snippet that defines its own `fetch_user`, or a `pip install --verbose` line, is not treated as a reference to the library.
- **Minimal patches.** Edits are column ranges on single lines or deleted table rows, applied together or not at all, and a unified diff is always produced.
- **Measured separately.** Development and held-out scenarios are reported separately, with the misses listed.

## Limits and next steps
- Python code only. Markdown docs only (no reStructuredText, docstrings or notebooks).
- Only the first-class patterns are patched: renamed/moved symbols and modules, changed defaults stated in signatures, tables or "defaults to" sentences, renamed or removed parameters in calls and signatures, renamed or removed flags, renamed or removed env vars and config keys. Prose that describes behaviour is flagged, not rewritten.
- Docs that name something in plain words (not in code formatting) are found only by the TF-IDF fallback, which is weak: one held-out miss above, and low-confidence noise.
- The CLI model is argparse and click with literal option strings. Sub-commands, option groups built in loops and flags added dynamically are not seen. Programs are matched to modules by name only (`python -m pkg`, `pkg-name`).
- Rename detection needs a similar name. A rename to an unrelated name is reported as a removal plus a flagged section, which is the safe outcome.
- A new public function, class or parameter with a default is not reported, and new CLI flags, env vars and config keys are reported only when a doc section already lists sibling items.
- Examples are checked statically (signature binding and flag specs), not executed, so a snippet that fails at runtime for another reason is not caught.
- Not done yet: running on a real repository other than the sample, a precision/recall study on real documentation, a PR-level cache for large repositories, and a live run of the Anthropic provider and of the GitHub workflows.
