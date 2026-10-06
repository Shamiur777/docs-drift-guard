from docs_drift_guard.code_analysis import extract_api
from docs_drift_guard.docs_analysis import parse_markdown
from docs_drift_guard.validate import new_failures, validate_docs

CODE = '''
import argparse
def make(a, b=1, *, c=None): pass
class Box:
    def __init__(self, size): pass
    def open(self, lid): pass
p = argparse.ArgumentParser()
p.add_argument("--n", default=1)
p.add_argument("--name", required=True)
'''


def api(tree, code=CODE):
    return extract_api(tree({"lib/__init__.py": "", "lib/m.py": code}))


def check(md, a):
    return validate_docs([parse_markdown("d.md", md)], a)


def test_valid_examples_pass(tree):
    md = ("# S\n```python\nfrom lib.m import make, Box\nmake(1, b=2, c=3)\nbox = Box(3)\nbox.open(lid=1)\n```\n"
          "```bash\npython -m lib --name x --n 2\n```\n")
    assert check(md, api(tree)) == []


def test_call_that_no_longer_binds(tree):
    md = "# S\n```python\nfrom lib.m import make\nmake()\nmake(1, 2, 3)\nmake(1, nope=1)\n```\n"
    fails = check(md, api(tree))
    assert [f.line for f in fails] == [4, 5, 6]
    assert "missing a required argument" in fails[0].reason
    assert "unexpected keyword" in fails[2].reason or "nope" in fails[2].reason
    assert all(f.target == "lib.m.make" for f in fails)


def test_inline_signature_is_bound_too(tree):
    fails = check("# S\n\nUse `make(a, b=1, bogus=2)` here.\n", api(tree))
    assert len(fails) == 1 and fails[0].kind == "inline"


def test_instance_method_calls_are_checked(tree):
    md = "# S\n```python\nfrom lib.m import Box\nb = Box(1)\nb.open()\nb.shut()\n```\n"
    fails = check(md, api(tree))
    assert {f.target for f in fails} == {"lib.m.Box.open", "lib.m.Box.shut"}


def test_imports_of_missing_names_and_modules(tree):
    md = "# S\n```python\nfrom lib.m import gone\nfrom lib.zzz import x\nimport lib.nope\nfrom other import q\n```\n"
    fails = check(md, api(tree))
    assert [f.target for f in fails] == ["lib.m.gone", "lib.zzz", "lib.nope"]  # `other` is not our package


def test_module_attribute_that_vanished(tree):
    md = "# S\n```python\nfrom lib import m\nm.make(1)\nm.gone(1)\n```\n"
    fails = check(md, api(tree))
    assert [f.target for f in fails] == ["lib.m.gone"]


def test_ellipsis_and_star_args_are_skipped(tree):
    md = "# S\n```python\nfrom lib.m import make\nmake(...)\nmake(*args)\nmake(**kw)\n```\n"
    assert check(md, api(tree)) == []


def test_cli_unknown_flag_and_missing_required_flag(tree):
    md = "# S\n```bash\npython -m lib --name a --gone 1\npython -m lib --n 3\n```\n"
    fails = check(md, api(tree))
    assert [(f.kind, f.target) for f in fails] == [("cli", "--gone"), ("cli", "--name")]


def test_commands_for_other_programs_are_ignored(tree):
    assert check("# S\n```bash\npip install --upgrade x\ngit push --force\n```\n", api(tree)) == []


def test_continuation_lines_count_as_one_command(tree):
    md = "# S\n```bash\npython -m lib \\\n  --name a \\\n  --n 2\n```\n"
    assert check(md, api(tree)) == []


def test_only_newly_broken_examples_are_reported(tree):
    base = api(tree)
    head = api(tree, CODE.replace("def make(a, b=1, *, c=None)", "def make(a, z, b=1, *, c=None)")
               .replace("class Box", "class Box2"))
    md = "# S\n```python\nfrom lib.m import make, Box\nmake(1)\nBox(1)\nnot_ours(1)\n```\n"
    docs = [parse_markdown("d.md", md)]
    newly = new_failures(docs, base, head)
    assert {f.target for f in newly} == {"lib.m.make", "lib.m.Box"}
    assert new_failures(docs, head, head) == []  # already broken before: not new
