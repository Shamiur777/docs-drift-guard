from docs_drift_guard.code_analysis import extract_api
from docs_drift_guard.scenarios import SAMPLE_DIR

SRC = '''
import os
import click
from dataclasses import dataclass, field

TOKEN = os.environ["APP_TOKEN"]
URL = os.environ.get("APP_URL", "http://x")
MODE = os.getenv("APP_MODE", default="fast")

def public(a, b=2, *args, c, d=None, **kw):
    """Doc."""

def _private(): pass

class Thing:
    """A thing."""
    def __init__(self, x, y=1): pass
    def run(self, n=3): pass
    @staticmethod
    def make(z): pass
    @classmethod
    def build(cls, w): pass
    @property
    def size(self): pass
    def _hidden(self): pass

@dataclass
class Point:
    x: int
    y: int = 0
    tags: list = field(default_factory=list)

@click.command()
@click.option("--count", "-c", default=3, help="how many")
@click.option("--shout", is_flag=True)
def hello(count, shout): pass

def load(config):
    a = config["alpha"]
    b = config.get("beta", 5)
'''

CLI = '''
import argparse
p = argparse.ArgumentParser()
p.add_argument("--n", "-n", type=int, default=4, help="count")
p.add_argument("--quiet", action="store_true")
p.add_argument("--name", required=True)
p.add_argument("path")
'''


def test_symbols_signatures_and_visibility(tree):
    api = extract_api(tree({"pkg/__init__.py": "", "pkg/m.py": SRC}))
    s = api.symbols
    assert "pkg.m._private" not in s and "pkg.m.Thing._hidden" not in s
    pub = s["pkg.m.public"]
    assert [(p.name, p.kind, p.default) for p in pub.params] == [
        ("a", "POSITIONAL_OR_KEYWORD", None), ("b", "POSITIONAL_OR_KEYWORD", "2"),
        ("args", "VAR_POSITIONAL", None), ("c", "KEYWORD_ONLY", None), ("d", "KEYWORD_ONLY", "None"),
        ("kw", "VAR_KEYWORD", None)]
    assert pub.doc == "Doc."
    assert pub.signature_text() == "public(a, b=2, *args, c, d=None, **kw)"
    assert [p.name for p in s["pkg.m.public"].params if p.required] == ["a", "c"]


def test_class_method_decorator_handling(tree):
    s = extract_api(tree({"m.py": SRC})).symbols
    assert [p.name for p in s["m.Thing"].params] == ["x", "y"]  # constructor, self dropped
    assert [p.name for p in s["m.Thing.run"].params] == ["n"]
    assert [p.name for p in s["m.Thing.make"].params] == ["z"]  # staticmethod keeps its first arg
    assert [p.name for p in s["m.Thing.build"].params] == ["w"]  # classmethod drops cls
    assert s["m.Thing.size"].kind == "property"
    assert s["m.Thing.build"].decorators == ("classmethod",)
    assert s["m.Thing.run"].owner == "m.Thing"


def test_dataclass_fields_become_constructor_params(tree):
    p = extract_api(tree({"m.py": SRC})).symbols["m.Point"]
    assert [(x.name, x.default) for x in p.params] == [("x", None), ("y", "0"), ("tags", "<factory>")]


def test_env_vars_in_every_form(tree):
    env = extract_api(tree({"m.py": SRC})).env
    assert set(env) == {"APP_TOKEN", "APP_URL", "APP_MODE"}
    assert env["APP_URL"].default == "'http://x'"
    assert env["APP_MODE"].default == "'fast'"
    assert env["APP_TOKEN"].default is None


def test_argparse_and_click_options(tree):
    api = extract_api(tree({"m.py": SRC, "cli.py": CLI}))
    n = api.cli["--n"]
    assert (n.flags, n.default, n.takes_value, n.help) == (("--n", "-n"), "4", True, "count")
    assert api.cli["--quiet"].takes_value is False and api.cli["--quiet"].default == "False"
    assert api.cli["--name"].required is True
    assert "path" not in api.cli  # positionals are not flags
    assert api.cli["--count"].default == "3" and api.cli["--count"].flags == ("--count", "-c")
    assert api.cli["--shout"].takes_value is False


def test_config_keys(tree):
    cfg = extract_api(tree({"m.py": SRC})).config
    assert set(cfg) == {"alpha", "beta"} and cfg["beta"].default == "5"


def test_module_paths_and_excluded_dirs(tree):
    root = tree({"pkg/__init__.py": "def a(): pass", "pkg/sub/x.py": "def b(): pass",
                 "tests/test_x.py": "def test_x(): pass", ".venv/lib/y.py": "def c(): pass"})
    api = extract_api(root)
    assert set(api.modules) == {"pkg", "pkg.sub.x"}
    assert "pkg.a" in api.symbols and "pkg.sub.x.b" in api.symbols
    assert not any("test_x" in q or ".c" in q for q in api.symbols)


def test_src_subfolder_and_syntax_errors_are_reported(tree):
    root = tree({"src/pkg/ok.py": "def f(): pass", "src/pkg/bad.py": "def (:"})
    api = extract_api(root, src="src")
    assert "pkg.ok.f" in api.symbols
    assert len(api.errors) == 1 and "bad.py" in api.errors[0]


def test_sample_library_surface():
    api = extract_api(SAMPLE_DIR)
    assert api.symbols["sample_lib.client.fetch_user"].signature_text() == "fetch_user(user_id, timeout=30, retries=3)"
    assert set(api.cli) == {"--limit", "--status", "--output", "--verbose", "--dry-run", "--config"}
    assert set(api.env) == {"SAMPLE_API_URL", "SAMPLE_API_KEY", "SAMPLE_CACHE_DIR"}
    assert set(api.config) == {"region", "timeout_seconds"}
    assert "sample_lib.client._sign_request" not in api.symbols
