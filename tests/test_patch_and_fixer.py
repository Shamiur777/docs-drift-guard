import pytest

from docs_drift_guard.core import run_check
from docs_drift_guard.docs_analysis import parse_markdown
from docs_drift_guard.fixer import Edit, value_variants
from docs_drift_guard.patch import PatchError, apply_edits, render

BASE = '''
import os, argparse
def fetch(uid, timeout=30, retries=3):
    "Fetch it."
def build(x, name="a"): pass
K = os.environ.get("APP_KEY", "a")
p = argparse.ArgumentParser()
p.add_argument("--limit", default=5)
p.add_argument("--keep", default="x", help="keep it")
'''

DOC = '''# Fetching

```python
from lib.m import fetch

fetch(1, timeout=5, retries=2)
```

Call `fetch(uid, timeout=30)`. The `timeout` defaults to 30 seconds.

| Flag | Default |
|---|---|
| `--limit` | `5` |
| `--keep` | `x` |

Run `python -m lib --limit 5 --keep y`.
'''


def project(tree, code_edit=None, doc=DOC):
    base = tree({"lib/__init__.py": "", "lib/m.py": BASE, "docs/a.md": doc})
    head_code = BASE if code_edit is None else code_edit(BASE)
    head = tree({"lib/__init__.py": "", "lib/m.py": head_code, "docs/a.md": doc})
    return base, head


def fix(tree, edit, doc=DOC, **kw):
    base, head = project(tree, edit, doc)
    res = run_check(base, head, ["docs"], docs_root=head, fix=True, **kw)
    return res, (head / "docs" / "a.md").read_text(encoding="utf-8")


def test_apply_edits_replaces_deletes_and_keeps_the_rest():
    lines = ["alpha beta", "gamma", "delta"]
    out = apply_edits(lines, [Edit("f", 1, 6, 10, "beta", "BETA"), Edit("f", 2, 0, 5, "gamma", "", True)])
    assert out == ["alpha BETA", "delta"]


def test_apply_edits_rejects_overlap_and_stale_text():
    with pytest.raises(PatchError):
        apply_edits(["abcdef"], [Edit("f", 1, 0, 4, "abcd", "x"), Edit("f", 1, 2, 5, "cde", "y")])
    with pytest.raises(PatchError):
        apply_edits(["abcdef"], [Edit("f", 1, 0, 3, "zzz", "x")])


def test_render_produces_minimal_unified_diff_and_preserves_crlf():
    text = "# T\r\n\r\nUse `a` now.\r\n"
    doc = parse_markdown("d.md", text)
    new_texts, diff = render([doc], {"d.md": [Edit("d.md", 3, 5, 6, "a", "b")]})
    assert new_texts["d.md"] == "# T\r\n\r\nUse `b` now.\r\n"
    changed = [ln for ln in diff.splitlines() if ln.startswith(("-", "+")) and not ln.startswith(("---", "+++"))]
    assert changed == ["-Use `a` now.", "+Use `b` now."]


def test_value_variants_cover_quote_styles():
    assert value_variants("'a'", "'b'") == [("'a'", "'b'"), ('"a"', '"b"'), ("a", "b")]
    assert value_variants("30", "60") == [("30", "60")]


def test_rename_edits_only_the_identifier(tree):
    res, text = fix(tree, lambda c: c.replace("def fetch(", "def fetcher("))
    assert "from lib.m import fetcher" in text and "fetcher(1, timeout=5, retries=2)" in text
    assert "`fetcher(uid, timeout=30)`" in text
    assert "# Fetching" in text  # prose and headings are not touched
    assert res.applied and not res.flagged


def test_default_change_leaves_explicit_arguments_alone(tree):
    res, text = fix(tree, lambda c: c.replace("timeout=30", "timeout=60"))
    assert "`fetch(uid, timeout=60)`" in text and "defaults to 60 seconds" in text
    assert "fetch(1, timeout=5, retries=2)" in text  # an explicit value is not stale


def test_param_rename_updates_calls_signatures_and_inline_names(tree):
    res, text = fix(tree, lambda c: c.replace("timeout=30", "wait=30").replace("fetch(uid, timeout", "fetch(uid, wait"))
    assert "fetch(1, wait=5, retries=2)" in text
    assert "`fetch(uid, wait=30)`" in text
    assert "The `wait` defaults" in text


def test_removed_kwarg_is_dropped_with_its_comma(tree):
    res, text = fix(tree, lambda c: c.replace(", retries=3", ""))
    assert "fetch(1, timeout=5)" in text and "retries" not in text


def test_flag_removed_deletes_row_and_token_with_value(tree):
    res, text = fix(tree, lambda c: c.replace('p.add_argument("--limit", default=5)\n', ""))
    assert "--limit" not in text
    assert "Run `python -m lib --keep y`." in text


def test_flag_default_change_edits_table_cell_only(tree):
    res, text = fix(tree, lambda c: c.replace('"--keep", default="x"', '"--keep", default="z"'))
    assert "| `--keep` | `z` |" in text and "--keep y" in text


def test_conflicting_fixes_do_not_corrupt_the_file(tree):
    # a rename and a param removal both touch the same call line
    def edit(c):
        return c.replace("def fetch(uid, timeout=30, retries=3)", "def fetcher(uid, timeout=30)")
    res, text = fix(tree, edit)
    # either fully fixed or handed to a human, never mangled
    assert (("fetch(1" not in text) and ("retries=2" not in text)) or res.flagged
    assert text.startswith("# Fetching") and text.rstrip().endswith("keep y`.")


def test_nothing_changes_on_disk_without_fix(tree):
    base, head = project(tree, lambda c: c.replace("def fetch(", "def fetcher("))
    res = run_check(base, head, ["docs"], docs_root=head, fix=False)
    assert res.diff and not res.applied
    assert (head / "docs" / "a.md").read_text(encoding="utf-8") == DOC
