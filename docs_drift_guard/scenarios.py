"""Scripted "code change" scenarios on examples/sample_lib.

Each scenario mutates a copy of the sample library's code and states, by hand:
  * which doc sections a human would have to update (`stale`), and
  * what the docs should look like after the safe, mechanical fixes (`expected_edits`).
The expected values were written from reading the docs, not from the tool's output.
They are used by the tests, the demo and `python -m docs_drift_guard evaluate`.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "examples" / "sample_lib"

C = "sample_lib/client.py"
F = "sample_lib/formatting.py"
CACHE = "sample_lib/cache.py"
CFG = "sample_lib/config.py"
CLI = "sample_lib/cli.py"

IDX, API, CLID, CONF, GUIDE = "docs/index.md", "docs/api.md", "docs/cli.md", "docs/configuration.md", "docs/guide.md"


@dataclass
class Scenario:
    id: str
    category: str  # drift | control | bait
    description: str
    mutations: list[tuple[str, str, str]] = field(default_factory=list)  # (code file, old, new)
    new_files: dict[str, str] = field(default_factory=dict)
    moves: list[tuple[str, str]] = field(default_factory=list)
    stale: list[tuple[str, str]] = field(default_factory=list)  # (doc file, section heading)
    expected_edits: list[tuple[str, str, str]] = field(default_factory=list)  # (doc file, old, new)


def apply_code(root: Path, sc: Scenario) -> None:
    """Apply a scenario's code mutations to the tree at `root`."""
    for src, dst in sc.moves:
        (root / dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root / src), str(root / dst))
    for rel, old, new in sc.mutations:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        if old not in text:
            raise ValueError(f"{sc.id}: {old!r} not found in {rel}")
        path.write_text(text.replace(old, new), encoding="utf-8")
    for rel, content in sc.new_files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content, encoding="utf-8")


def expected_docs(root: Path, sc: Scenario) -> dict[str, str]:
    """The docs a correct auto-fix should produce, keyed by relative path."""
    out: dict[str, str] = {}
    for p in sorted((root / "docs").rglob("*.md")):
        out[p.relative_to(root).as_posix()] = p.read_text(encoding="utf-8")
    for rel, old, new in sc.expected_edits:
        if old not in out[rel]:
            raise ValueError(f"{sc.id}: expected edit {old!r} not in {rel}")
        out[rel] = out[rel].replace(old, new)
    return out


def build_pair(workdir: Path, sc: Scenario) -> tuple[Path, Path]:
    """Create pristine `base` and mutated `head` copies of the sample library under workdir."""
    base, head = workdir / "base", workdir / "head"
    for d in (base, head):
        shutil.copytree(SAMPLE_DIR, d, ignore=shutil.ignore_patterns("__pycache__"))
    apply_code(head, sc)
    return base, head


# ---- reusable expected edits ------------------------------------------------

RENAME_FETCH_USER = [
    (IDX, "from sample_lib.client import fetch_user, create_invoice", "from sample_lib.client import get_user, create_invoice"),
    (IDX, "user = fetch_user(42, timeout=10)", "user = get_user(42, timeout=10)"),
    (API, "## `fetch_user`", "## `get_user`"),
    (API, "def fetch_user(user_id, timeout=30, retries=3)", "def get_user(user_id, timeout=30, retries=3)"),
    (API, "from sample_lib.client import fetch_user\n\nprofile = fetch_user(7)",
     "from sample_lib.client import get_user\n\nprofile = get_user(7)"),
    (GUIDE, "`fetch_user` retries a failed request", "`get_user` retries a failed request"),
]
RENAME_FETCH_USER_STALE = [(IDX, "Quickstart"), (API, "`fetch_user`"), (GUIDE, "Fetching users reliably")]

LIMIT_DEFAULT = [(CLID, "| `--limit` | `50` |", "| `--limit` | `25` |")]
DRY_RUN_REMOVED = [
    (CLID, "| `--dry-run` | off | Show what would happen without changing anything |\n", ""),
    (CLID, "sample-lib --dry-run --verbose", "sample-lib --verbose"),
]
SAMPLE_CODE_ENV_ADDED = ('os.getenv("SAMPLE_API_KEY")', 'os.getenv("SAMPLE_API_KEY")\nTIMEOUT = os.environ.get("SAMPLE_TIMEOUT", "30")')


def _scenarios() -> list[Scenario]:
    s: list[Scenario] = []
    add = s.append

    add(Scenario("rename_function", "drift", "fetch_user renamed to get_user",
                 [(C, "def fetch_user(", "def get_user(")],
                 stale=RENAME_FETCH_USER_STALE, expected_edits=RENAME_FETCH_USER))

    add(Scenario("rename_class", "drift", "Client renamed to ApiClient (methods carried over)",
                 [(C, "class Client:", "class ApiClient:")],
                 stale=[(IDX, "Quickstart"), (API, "`Client`")],
                 expected_edits=[
                     (IDX, "from sample_lib.client import Client", "from sample_lib.client import ApiClient"),
                     (IDX, "client = Client(timeout=15)", "client = ApiClient(timeout=15)"),
                     (IDX, "Use `Client` when", "Use `ApiClient` when"),
                     (API, "## `Client`", "## `ApiClient`"),
                     (API, "`Client(base_url, timeout=30)`", "`ApiClient(base_url, timeout=30)`"),
                     (API, "- `Client.get_record(", "- `ApiClient.get_record("),
                     (API, "- `Client.list_records(", "- `ApiClient.list_records("),
                     (API, "- `Client.close()`", "- `ApiClient.close()`"),
                     (API, "from sample_lib.client import Client\n\nclient = Client()",
                      "from sample_lib.client import ApiClient\n\nclient = ApiClient()"),
                 ]))

    add(Scenario("rename_method", "drift", "Client.get_record renamed to Client.fetch_record",
                 [(C, "def get_record(", "def fetch_record(")],
                 stale=[(IDX, "Quickstart"), (API, "`Client`")],
                 expected_edits=[
                     (IDX, "record = client.get_record(7", "record = client.fetch_record(7"),
                     (API, "- `Client.get_record(", "- `Client.fetch_record("),
                 ]))

    add(Scenario("default_changed_param", "drift", "fetch_user timeout default 30 -> 60",
                 [(C, "def fetch_user(user_id, timeout=30,", "def fetch_user(user_id, timeout=60,")],
                 stale=[(API, "`fetch_user`")],
                 expected_edits=[
                     (API, "def fetch_user(user_id, timeout=30, retries=3)", "def fetch_user(user_id, timeout=60, retries=3)"),
                     (API, "| `timeout` | Seconds to wait for each request | `30` |", "| `timeout` | Seconds to wait for each request | `60` |"),
                     (API, "The `timeout` defaults to 30 seconds.", "The `timeout` defaults to 60 seconds."),
                 ]))

    add(Scenario("default_changed_string", "drift", 'create_invoice currency default "USD" -> "EUR"',
                 [(C, 'def create_invoice(customer_id, amount, currency="USD")', 'def create_invoice(customer_id, amount, currency="EUR")')],
                 stale=[(API, "`create_invoice`")],
                 expected_edits=[
                     (API, '`create_invoice(customer_id, amount, currency="USD")`', '`create_invoice(customer_id, amount, currency="EUR")`'),
                     (API, '| `currency` | Three-letter currency code | `"USD"` |', '| `currency` | Three-letter currency code | `"EUR"` |'),
                 ]))

    add(Scenario("default_changed_class_init", "bait", "Client timeout default 30 -> 45; an unrelated requests.get(timeout=30) in the guide must not change",
                 [(C, "def __init__(self, base_url=DEFAULT_URL, timeout=30)", "def __init__(self, base_url=DEFAULT_URL, timeout=45)")],
                 stale=[(API, "`Client`")],
                 expected_edits=[(API, "`Client(base_url, timeout=30)`", "`Client(base_url, timeout=45)`")]))

    add(Scenario("default_changed_cli", "drift", "--limit default 50 -> 25",
                 [(CLI, '"--limit", type=int, default=50', '"--limit", type=int, default=25')],
                 stale=[(CLID, "Options")], expected_edits=LIMIT_DEFAULT))

    add(Scenario("default_changed_env", "drift", "SAMPLE_CACHE_DIR default .cache -> .sample-cache",
                 [(CACHE, '"SAMPLE_CACHE_DIR", ".cache"', '"SAMPLE_CACHE_DIR", ".sample-cache"')],
                 stale=[(CONF, "Environment variables"), (API, "`Cache`")],
                 expected_edits=[
                     (CONF, "| `SAMPLE_CACHE_DIR` | `.cache` |", "| `SAMPLE_CACHE_DIR` | `.sample-cache` |"),
                     (API, "(default `.cache`)", "(default `.sample-cache`)"),
                 ]))

    add(Scenario("param_renamed", "drift", "fetch_user parameter timeout renamed to request_timeout",
                 [(C, "def fetch_user(user_id, timeout=30, retries=3):", "def fetch_user(user_id, request_timeout=30, retries=3):"),
                  (C, '"timeout": timeout, "retries"', '"timeout": request_timeout, "retries"')],
                 stale=[(IDX, "Quickstart"), (API, "`fetch_user`")],
                 expected_edits=[
                     (IDX, "user = fetch_user(42, timeout=10)", "user = fetch_user(42, request_timeout=10)"),
                     (API, "def fetch_user(user_id, timeout=30, retries=3)", "def fetch_user(user_id, request_timeout=30, retries=3)"),
                     (API, "| `timeout` | Seconds to wait for each request |", "| `request_timeout` | Seconds to wait for each request |"),
                     (API, "The `timeout` defaults to 30 seconds.", "The `request_timeout` defaults to 30 seconds."),
                 ]))

    add(Scenario("param_removed", "drift", "fetch_user parameter retries removed",
                 [(C, "def fetch_user(user_id, timeout=30, retries=3):", "def fetch_user(user_id, timeout=30):"),
                  (C, ', "retries": retries}', "}")],
                 stale=[(API, "`fetch_user`")],
                 expected_edits=[
                     (API, "def fetch_user(user_id, timeout=30, retries=3)", "def fetch_user(user_id, timeout=30)"),
                     (API, "| `retries` | How many times to retry a failed request | `3` |\n", ""),
                 ]))

    add(Scenario("param_added_required", "drift", "create_invoice gains a required parameter region",
                 [(C, "def create_invoice(customer_id, amount, currency=", "def create_invoice(customer_id, amount, region, currency=")],
                 stale=[(IDX, "Quickstart"), (API, "`create_invoice`")]))

    add(Scenario("param_added_optional", "bait", "create_invoice gains an optional parameter; existing examples still work",
                 [(C, 'def create_invoice(customer_id, amount, currency="USD")', 'def create_invoice(customer_id, amount, currency="USD", note=None)')]))

    add(Scenario("symbol_removed_function", "drift", "create_invoice removed",
                 [(C, 'def create_invoice(customer_id, amount, currency="USD"):\n    """Create an invoice for a customer."""\n    return {"customer": customer_id, "amount": amount, "currency": currency}\n\n\n', "")],
                 stale=[(IDX, "Quickstart"), (API, "`create_invoice`")]))

    add(Scenario("symbol_removed_method", "drift", "Client.close removed",
                 [(C, '    def close(self):\n        """Release the connection."""\n', "")],
                 stale=[(IDX, "Quickstart"), (API, "`Client`")]))

    add(Scenario("symbol_removed_with_prose", "drift", "slugify removed; the guide describes it only in prose",
                 [(F, 'def slugify(text, separator="-"):\n    """Turn a title into a URL-friendly slug."""\n    return separator.join(text.lower().split())\n', "")],
                 stale=[(API, "`slugify`"), (GUIDE, "Building page addresses")]))

    add(Scenario("rename_unrelated_name", "drift", "format_amount replaced by render_money (too different to call a rename)",
                 [(F, "def format_amount(", "def render_money(")],
                 stale=[(API, "`format_amount`")]))

    add(Scenario("rename_medium_confidence", "drift", "slugify -> slugify_title with a reworded docstring",
                 [(F, 'def slugify(text, separator="-"):\n    """Turn a title into a URL-friendly slug."""', 'def slugify_title(text, separator="-"):\n    """Make a slug."""')],
                 stale=[(API, "`slugify`"), (GUIDE, "Building page addresses")]))

    add(Scenario("module_moved", "drift", "sample_lib/client.py moved to sample_lib/http.py",
                 moves=[(C, "sample_lib/http.py")],
                 stale=[(IDX, "Quickstart"), (API, "`fetch_user`"), (API, "`create_invoice`"), (API, "`Client`")],
                 expected_edits=[
                     (IDX, "from sample_lib.client import", "from sample_lib.http import"),
                     (API, "from sample_lib.client import", "from sample_lib.http import"),
                 ]))

    add(Scenario("symbol_moved", "drift", "slugify moved from formatting.py to text.py",
                 [(F, '\n\ndef slugify(text, separator="-"):\n    """Turn a title into a URL-friendly slug."""\n    return separator.join(text.lower().split())\n', "")],
                 new_files={"sample_lib/text.py": 'def slugify(text, separator="-"):\n    """Turn a title into a URL-friendly slug."""\n    return separator.join(text.lower().split())\n'},
                 stale=[(API, "`slugify`"), (GUIDE, "Building page addresses")],
                 expected_edits=[(API, "from sample_lib.formatting import slugify", "from sample_lib.text import slugify")]))

    add(Scenario("cli_flag_removed", "drift", "--dry-run removed (also mentioned in the changelog, which is history)",
                 [(CLI, '    parser.add_argument("--dry-run", action="store_true", help="show what would happen without changing anything")\n', "")],
                 stale=[(CLID, "Options"), (CLID, "Examples")], expected_edits=DRY_RUN_REMOVED))

    add(Scenario("cli_flag_removed_other_tool", "bait", "--verbose removed; pip install --verbose in the docs belongs to another tool",
                 [(CLI, '    parser.add_argument("--verbose", "-v", action="store_true", help="print extra detail")\n', "")],
                 stale=[(CLID, "Options"), (CLID, "Examples"), (IDX, "Command line quickstart")],
                 expected_edits=[
                     (CLID, "| `--verbose`, `-v` | off | Print extra detail |\n", ""),
                     (CLID, "sample-lib --dry-run --verbose", "sample-lib --dry-run"),
                     (IDX, "sample-lib --output json --verbose", "sample-lib --output json"),
                 ]))

    add(Scenario("cli_flag_removed_prose_bait", "bait", "--limit removed; the guide talks about rate limits, which is unrelated",
                 [(CLI, '    parser.add_argument("--limit", type=int, default=50, help="maximum number of records to show")\n', "")],
                 stale=[(CLID, "Options"), (CLID, "Examples"), (IDX, "Command line quickstart"), (CONF, "Environment variables")],
                 expected_edits=[
                     (CLID, "| `--limit` | `50` | Maximum number of records to show |\n", ""),
                     (CLID, "python -m sample_lib --limit 10\n", "python -m sample_lib\n"),
                     (IDX, "python -m sample_lib --limit 20 --status open", "python -m sample_lib --status open"),
                     (CONF, "python -m sample_lib --limit 5", "python -m sample_lib"),
                 ]))

    add(Scenario("cli_flag_renamed", "drift", "--output renamed to --format",
                 [(CLI, '"--output", default="table"', '"--format", default="table"')],
                 stale=[(CLID, "Options"), (CLID, "Examples"), (CLID, "Machine-readable output"), (IDX, "Command line quickstart")],
                 expected_edits=[
                     (CLID, "| `--output` | `table` |", "| `--format` | `table` |"),
                     (CLID, "--status closed --output json", "--status closed --format json"),
                     (CLID, "Use `--output json`", "Use `--format json`"),
                     (IDX, "sample-lib --output json --verbose", "sample-lib --format json --verbose"),
                 ]))

    add(Scenario("cli_flag_added", "drift", "new --page flag is missing from the options table",
                 [(CLI, '    parser.add_argument("--config"', '    parser.add_argument("--page", type=int, default=1, help="page of results to show")\n    parser.add_argument("--config"')],
                 stale=[(CLID, "Options")]))

    add(Scenario("cli_flag_now_required", "drift", "--config becomes required; every example without it fails",
                 [(CLI, '"--config", default="sample.toml"', '"--config", required=True, default="sample.toml"')],
                 stale=[(IDX, "Command line quickstart"), (CLID, "Examples"), (CONF, "Environment variables")]))

    add(Scenario("env_var_added", "drift", "new SAMPLE_TIMEOUT variable is missing from the table",
                 [(C, *SAMPLE_CODE_ENV_ADDED)],
                 stale=[(CONF, "Environment variables")]))

    add(Scenario("env_var_removed", "drift", "SAMPLE_API_KEY removed; the docs also use it in a shell example",
                 [(C, 'API_KEY = os.getenv("SAMPLE_API_KEY")\n', "")],
                 stale=[(CONF, "Environment variables")]))

    add(Scenario("env_var_renamed", "drift", "SAMPLE_API_URL renamed to SAMPLE_BASE_URL",
                 [(C, '"SAMPLE_API_URL"', '"SAMPLE_BASE_URL"')],
                 stale=[(CONF, "Environment variables")],
                 expected_edits=[(CONF, "| `SAMPLE_API_URL` |", "| `SAMPLE_BASE_URL` |")]))

    add(Scenario("config_key_renamed", "drift", "config key region renamed to data_region",
                 [(CFG, '{"region": "eu"', '{"data_region": "eu"'), (CFG, 'config["region"]', 'config["data_region"]')],
                 stale=[(CONF, "Settings file")],
                 expected_edits=[
                     (CONF, "| `region` | `eu` |", "| `data_region` | `eu` |"),
                     (CONF, '`config["region"]`', '`config["data_region"]`'),
                 ]))

    add(Scenario("config_key_removed", "drift", "config key timeout_seconds removed",
                 [(CFG, ', "timeout_seconds": 30}', "}"), (CFG, '    timeout = config.get("timeout_seconds", 30)\n', ""),
                  (CFG, '"timeout": timeout, ', "")],
                 stale=[(CONF, "Settings file")]))

    add(Scenario("combined_demo", "drift", "rename a function, change a default, remove a flag, add an env var",
                 [(C, "def fetch_user(", "def get_user("),
                  (CLI, '"--limit", type=int, default=50', '"--limit", type=int, default=25'),
                  (CLI, '    parser.add_argument("--dry-run", action="store_true", help="show what would happen without changing anything")\n', ""),
                  (C, *SAMPLE_CODE_ENV_ADDED)],
                 stale=RENAME_FETCH_USER_STALE + [(CLID, "Options"), (CLID, "Examples"), (CONF, "Environment variables")],
                 expected_edits=RENAME_FETCH_USER + LIMIT_DEFAULT + DRY_RUN_REMOVED))

    # ---- controls: nothing in the docs is stale
    add(Scenario("control_no_change", "control", "no code change at all"))
    add(Scenario("control_private_rename", "control", "private helper renamed",
                 [(C, "_sign_request", "_sign_payload")]))
    add(Scenario("control_docstring_only", "control", "docstring reworded, API unchanged",
                 [(C, '"""Fetch a user profile by id, retrying on network errors."""', '"""Return the profile for a user id, retrying network errors."""')]))
    add(Scenario("control_new_function", "control", "new public function nobody documented yet",
                 [(C, "def _sign_request(", 'def refund_invoice(invoice_id):\n    """Refund an invoice."""\n    return invoice_id\n\n\ndef _sign_request(')]))
    add(Scenario("control_unreferenced_change", "control", "undocumented function main() gains an optional argument",
                 [(CLI, "def main(argv=None):", "def main(argv=None, strict=False):")]))
    return s


SCENARIOS: list[Scenario] = _scenarios()
DEMO = next(sc for sc in SCENARIOS if sc.id == "combined_demo")
