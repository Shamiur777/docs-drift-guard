from docs_drift_guard.code_analysis import extract_api
from docs_drift_guard.diff import diff_api


def changes(tree, base: dict[str, str], head: dict[str, str]):
    return diff_api(extract_api(tree(base)), extract_api(tree(head)))


def kinds(cs):
    return sorted(c.kind for c in cs)


def one(cs, kind):
    hits = [c for c in cs if c.kind == kind]
    assert len(hits) == 1, [c.kind for c in cs]
    return hits[0]


def test_no_change_no_diff(tree):
    files = {"p/a.py": "def f(x, y=1): pass"}
    assert changes(tree, files, files) == []


def test_removed_symbol(tree):
    cs = changes(tree, {"p/a.py": "def keep(): pass\ndef gone(a): pass"}, {"p/a.py": "def keep(): pass"})
    assert kinds(cs) == ["symbol_removed"] and cs[0].subject == "p.a.gone"


def test_renamed_symbol_by_similarity(tree):
    base = {"p/a.py": 'def fetch_user(uid, timeout=30):\n    """Fetch a user."""'}
    head = {"p/a.py": 'def get_user(uid, timeout=30):\n    """Fetch a user."""'}
    c = one(changes(tree, base, head), "symbol_renamed")
    assert (c.old, c.new) == ("p.a.fetch_user", "p.a.get_user") and c.similarity > 0.85


def test_unrelated_replacement_is_removal_not_rename(tree):
    base = {"p/a.py": "def format_amount(v): pass"}
    head = {"p/a.py": "def zzz(v): pass"}
    assert kinds(changes(tree, base, head)) == ["symbol_removed"]


def test_rename_with_different_signature_scores_lower(tree):
    base = {"p/a.py": "def load_user(a, b, c): pass"}
    same = {"p/a.py": "def load_users(a, b, c): pass"}
    other = {"p/a.py": "def load_users(a, b, z): pass"}
    s1 = one(changes(tree, base, same), "symbol_renamed").similarity
    s2 = one(changes(tree, base, other), "symbol_renamed").similarity
    assert s1 > s2


def test_class_rename_carries_methods(tree):
    base = {"p/a.py": "class Client:\n    def get(self, k): pass"}
    head = {"p/a.py": "class ApiClient:\n    def get(self, k, extra): pass"}
    cs = changes(tree, base, head)
    assert one(cs, "symbol_renamed").subject == "p.a.Client"
    added = one(cs, "param_added_required")
    assert added.param == "extra" and added.subject == "p.a.Client.get"
    assert "symbol_removed" not in kinds(cs)


def test_removed_class_reports_class_not_every_method(tree):
    cs = changes(tree, {"p/a.py": "class C:\n    def m(self): pass\n    def n(self): pass"}, {"p/a.py": "x = 1"})
    assert [c.subject for c in cs] == ["p.a.C"]


def test_symbol_moved_between_modules(tree):
    base = {"p/a.py": "def slug(t, sep='-'): pass\ndef other(): pass", "p/b.py": ""}
    head = {"p/a.py": "def other(): pass", "p/b.py": "def slug(t, sep='-'): pass"}
    c = one(changes(tree, base, head), "symbol_moved")
    assert (c.old, c.new) == ("p.a.slug", "p.b.slug")


def test_module_moved(tree):
    base = {"p/old.py": "def f(): pass\ndef g(): pass", "p/__init__.py": ""}
    head = {"p/new.py": "def f(): pass\ndef g(): pass", "p/__init__.py": ""}
    cs = changes(tree, base, head)
    assert kinds(cs) == ["module_moved"]
    assert (cs[0].old, cs[0].new) == ("p.old", "p.new")


def test_param_removed_renamed_added_and_defaults(tree):
    base = {"p/a.py": "def f(a, timeout=30, retries=3, mode='x'): pass"}
    head = {"p/a.py": "def f(a, request_timeout=30, mode='y', extra=None): pass"}
    cs = changes(tree, base, head)
    assert one(cs, "param_renamed").old == "timeout" and one(cs, "param_renamed").new == "request_timeout"
    assert one(cs, "param_removed").param == "retries"
    d = one(cs, "default_changed")
    assert (d.param, d.old_value, d.new_value) == ("mode", "'x'", "'y'")
    assert one(cs, "param_added_optional").param == "extra"


def test_new_required_param_and_default_dropped(tree):
    cs = changes(tree, {"p/a.py": "def f(a, b=1): pass"}, {"p/a.py": "def f(a, b, c): pass"})
    required = sorted(c.param for c in cs if c.kind == "param_added_required")
    assert required == ["b", "c"]  # b lost its default, c is brand new


def test_reordered_positional_params(tree):
    cs = changes(tree, {"p/a.py": "def f(a, b): pass"}, {"p/a.py": "def f(b, a): pass"})
    assert kinds(cs) == ["param_reordered"]


def test_cli_flag_added_removed_renamed_default_required(tree):
    base = {"c.py": 'import argparse\np = argparse.ArgumentParser()\n'
                    'p.add_argument("--limit", default=50, help="max rows")\n'
                    'p.add_argument("--output", default="table", help="output format")\n'
                    'p.add_argument("--old", help="gone")\n'
                    'p.add_argument("--cfg", default="a")\n'}
    head = {"c.py": 'import argparse\np = argparse.ArgumentParser()\n'
                    'p.add_argument("--limit", default=25, help="max rows")\n'
                    'p.add_argument("--format", default="table", help="output format")\n'
                    'p.add_argument("--new", help="brand new")\n'
                    'p.add_argument("--cfg", required=True, default="a")\n'}
    cs = changes(tree, base, head)
    assert one(cs, "cli_flag_renamed").old == "--output" and one(cs, "cli_flag_renamed").new == "--format"
    assert one(cs, "cli_flag_removed").subject == "--old"
    assert one(cs, "cli_flag_added").new == "--new"
    d = one(cs, "cli_default_changed")
    assert (d.subject, d.old_value, d.new_value) == ("--limit", "50", "25")
    assert one(cs, "cli_flag_now_required").subject == "--cfg"


def test_flag_kept_as_alias_is_not_a_removal(tree):
    base = {"c.py": 'import argparse\np = argparse.ArgumentParser()\np.add_argument("--out")'}
    head = {"c.py": 'import argparse\np = argparse.ArgumentParser()\np.add_argument("--output", "--out")'}
    assert kinds(changes(tree, base, head)) == ["cli_flag_added"]  # the old spelling still works: not a removal


def test_env_var_added_removed_renamed_default(tree):
    base = {"m.py": 'import os\nA = os.getenv("APP_URL", "x")\nB = os.getenv("APP_GONE")\nC = os.getenv("APP_MODE", "slow")'}
    head = {"m.py": 'import os\nA = os.getenv("APP_BASE_URL", "x")\nN = os.getenv("APP_NEW")\nC = os.getenv("APP_MODE", "fast")'}
    cs = changes(tree, base, head)
    r = one(cs, "env_var_renamed")
    assert (r.old, r.new) == ("APP_URL", "APP_BASE_URL")
    assert one(cs, "env_var_removed").subject == "APP_GONE"
    assert one(cs, "env_var_added").subject == "APP_NEW"
    d = one(cs, "env_default_changed")
    assert (d.old_value, d.new_value) == ("'slow'", "'fast'")


def test_config_key_added_removed_renamed(tree):
    base = {"m.py": 'def f(config):\n    config["region"]\n    config["old_key"]'}
    head = {"m.py": 'def f(config):\n    config["data_region"]\n    config["fresh"]'}
    cs = changes(tree, base, head)
    assert one(cs, "config_key_renamed").new == "data_region"
    assert kinds(cs).count("config_key_renamed") == 1
    assert {c.kind for c in cs} <= {"config_key_renamed", "config_key_removed", "config_key_added"}


def test_describe_is_readable(tree):
    c = one(changes(tree, {"p/a.py": "def f(a, t=1): pass"}, {"p/a.py": "def f(a, t=2): pass"}), "default_changed")
    assert "`t`" in c.describe() and "`1`" in c.describe() and "`2`" in c.describe()
