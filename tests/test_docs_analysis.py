from docs_drift_guard.code_analysis import extract_api
from docs_drift_guard.docs_analysis import (build_index, listing_leads, load_docs, parse_markdown,
                                            program_candidates, section_refs)

API_SRC = '''
import os
import argparse
K = os.environ.get("APP_KEY")
class Client:
    def get(self, k): pass
    def close(self): pass
def fetch(uid, timeout=30): pass
def local_name(): pass
p = argparse.ArgumentParser()
p.add_argument("--limit", default=5)
p.add_argument("--verbose", "-v", action="store_true")
'''


def api(tree):
    return extract_api(tree({"lib/__init__.py": "", "lib/cli.py": API_SRC}))


def sections(md: str):
    return parse_markdown("d.md", md).sections


def refs_of(md: str, api_):
    out = []
    for s in sections(md):
        out.extend(section_refs(s, api_))
    return out


def test_sections_and_headings_ignore_fenced_hashes():
    md = "intro\n\n# One\ntext\n```bash\n# not a heading\n```\n## Two\nmore\n"
    secs = sections(md)
    assert [s.heading for s in secs] == ["", "One", "Two"]
    assert [(s.start, s.end) for s in secs] == [(1, 2), (3, 7), (8, 9)]
    assert len(secs[1].blocks) == 1 and secs[1].blocks[0].lang == "bash"
    assert secs[1].blocks[0].lines == ["# not a heading"]


def test_inline_code_tables_and_positions():
    md = "# T\n\nUse `fetch(1)` here.\n\n| Flag | Default |\n|---|---|\n| `--limit` | `5` |\n| `-v` | off |\n"
    s = sections(md)[0]
    ic = s.inline[0]
    assert (ic.text, ic.line) == ("fetch(1)", 3) and s.line_text(3)[ic.start:ic.end] == "fetch(1)"
    assert s.tables[0].header == ["Flag", "Default"]
    assert [c.text for c in s.tables[0].rows[0]] == ["`--limit`", "`5`"]
    assert listing_leads(s, "flag") == ["--limit", "-v"]


def test_round_trip_preserves_crlf():
    text = "# A\r\n\r\nbody\r\n"
    d = parse_markdown("x.md", text)
    assert d.eol == "\r\n" and d.text == text


def test_python_block_refs_resolve_imports_and_instances(tree):
    md = ("# S\n```python\nfrom lib.cli import Client, fetch\n\nc = Client()\nc.get(1)\nfetch(2, timeout=3)\n```\n")
    refs = refs_of(md, api(tree))
    values = {(r.kind, r.value) for r in refs}
    assert ("symbol", "lib.cli.Client") in values
    assert ("symbol", "lib.cli.Client.get") in values  # c.get resolved through c = Client()
    assert ("call", "lib.cli.fetch") in values
    assert ("module", "lib.cli") in values


def test_variable_holding_an_instance_is_not_a_class_reference(tree):
    md = "# S\n```python\nfrom lib.cli import Client\nclient = Client()\nclient.close()\n```\n"
    refs = [r for r in refs_of(md, api(tree)) if r.kind == "symbol" and r.value == "lib.cli.Client"]
    assert all(r.use.text == "Client" for r in refs)  # `client` the variable is never matched


def test_foreign_calls_and_local_definitions_are_not_references(tree):
    md = ("# S\n```python\nimport requests\nrequests.get('u', timeout=1)\n"
          "def fetch(x):\n    return x\nfetch(1)\n```\nA local `fetch` helper.\n")
    refs = refs_of(md, api(tree))
    assert not [r for r in refs if r.value.endswith("fetch") or r.value.endswith("Client.get")]


def test_signature_display_def_does_not_shadow(tree):
    md = "# `fetch`\n\n```python\ndef fetch(uid, timeout=30)\n```\n"
    refs = refs_of(md, api(tree))
    assert any(r.kind == "symbol" and r.value == "lib.cli.fetch" and r.where == "inline" for r in refs)


def test_flags_in_shell_need_a_matching_program(tree):
    md = ("# S\n```bash\npython -m lib --limit 3\ngit push --force --limit 9\nlib -v\n```\n")
    a = api(tree)
    flags = [r for r in refs_of(md, a) if r.kind == "flag"]
    assert [(r.value, r.where, r.line) for r in flags] == [("--limit", "shell", 3), ("--verbose", "shell", 5)]
    assert program_candidates("lib", a) and not program_candidates("git", a)


def test_flag_refs_in_inline_code_and_prose_get_different_confidence(tree):
    md = "# S\n\nPass `--limit 3` or mention --verbose in prose.\n\n| `--limit` | 5 |\n"
    flags = [r for r in refs_of(md, api(tree)) if r.kind == "flag"]
    by = {(r.where, r.line): r.conf for r in flags}
    assert by[("inline", 3)] == 0.9 and by[("prose", 3)] == 0.7


def test_env_vars_and_paths_and_config(tree):
    md = "# S\n\nSet `APP_KEY`. See `lib/cli.py`.\n\n```bash\nexport APP_KEY=1\n```\n"
    refs = refs_of(md, api(tree))
    assert sum(1 for r in refs if r.kind == "env" and r.value == "APP_KEY") == 2
    assert any(r.kind == "module" and r.value == "lib.cli" and r.text == "lib/cli.py" for r in refs)


def test_reference_index_maps_symbols_to_locations(tree, tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("# A\n\nUse `fetch`.\n", encoding="utf-8")
    (tmp_path / "docs" / "b.md").write_text("# B\n\n```python\nfrom lib.cli import fetch\n```\n", encoding="utf-8")
    idx = build_index(load_docs(tmp_path, ["docs"]), api(tree))
    assert sorted(f for f, _, _ in idx[("symbol", "lib.cli.fetch")]) == ["docs/a.md", "docs/b.md"]


def test_changelogs_are_skipped_by_default(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# G\n", encoding="utf-8")
    (tmp_path / "docs" / "CHANGELOG.md").write_text("# C\n", encoding="utf-8")
    assert [d.path for d in load_docs(tmp_path, ["docs"])] == ["docs/guide.md"]
    assert len(load_docs(tmp_path, ["docs"], include_changelog=True)) == 2
