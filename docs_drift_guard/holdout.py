"""Held-out scenarios, written after the detector was tuned on scenarios.py.

No threshold or rule was adjusted to fit these when they were first run; the
README reports the first-run numbers. Same sample library, different mutations.
"""
from __future__ import annotations

from .scenarios import API, C, CACHE, CFG, CLI, CLID, CONF, F, GUIDE, IDX, Scenario


def _holdout() -> list[Scenario]:
    s: list[Scenario] = []
    add = s.append
    add(Scenario("h_rename_function", "drift", "create_invoice renamed to make_invoice",
                 [(C, "def create_invoice(", "def make_invoice(")],
                 stale=[(IDX, "Quickstart"), (API, "`create_invoice`")],
                 expected_edits=[
                     (IDX, "from sample_lib.client import fetch_user, create_invoice", "from sample_lib.client import fetch_user, make_invoice"),
                     (IDX, 'invoice = create_invoice(user["id"], 120', 'invoice = make_invoice(user["id"], 120'),
                     (API, "## `create_invoice`", "## `make_invoice`"),
                     (API, '`create_invoice(customer_id, amount, currency="USD")` creates', '`make_invoice(customer_id, amount, currency="USD")` creates'),
                     (API, 'from sample_lib.client import create_invoice\n\ninvoice = create_invoice("cus_1", 5000)',
                      'from sample_lib.client import make_invoice\n\ninvoice = make_invoice("cus_1", 5000)'),
                 ]))
    add(Scenario("h_method_removed_by_rename", "drift", "Cache.set renamed to Cache.put (too different to call a rename)",
                 [(CACHE, "def set(self, key, value, ttl=300)", "def put(self, key, value, ttl=300)")],
                 stale=[(API, "`Cache`")]))
    add(Scenario("h_default_method_param", "drift", "Cache.set ttl default 300 -> 600",
                 [(CACHE, "def set(self, key, value, ttl=300)", "def set(self, key, value, ttl=600)")],
                 stale=[(API, "`Cache`")],
                 expected_edits=[
                     (API, "`Cache.set(key, value, ttl=300)`", "`Cache.set(key, value, ttl=600)`"),
                     (API, "The `ttl` defaults to 300 seconds.", "The `ttl` defaults to 600 seconds."),
                 ]))
    add(Scenario("h_default_int", "drift", "format_amount decimals default 2 -> 0",
                 [(F, 'currency="USD", decimals=2):', 'currency="USD", decimals=0):')],
                 stale=[(API, "`format_amount`")],
                 expected_edits=[(API, '`format_amount(value, currency="USD", decimals=2)`', '`format_amount(value, currency="USD", decimals=0)`')]))
    add(Scenario("h_flag_removed_with_value", "drift", "--status removed (takes a value)",
                 [(CLI, '    parser.add_argument("--status", default="open", help="only show records with this status")\n', "")],
                 stale=[(CLID, "Options"), (CLID, "Examples"), (IDX, "Command line quickstart")],
                 expected_edits=[
                     (CLID, "| `--status` | `open` | Only show records with this status |\n", ""),
                     (CLID, "python -m sample_lib --status closed --output json", "python -m sample_lib --output json"),
                     (IDX, "python -m sample_lib --limit 20 --status open", "python -m sample_lib --limit 20"),
                 ]))
    add(Scenario("h_flag_renamed_other_name", "drift", "--config renamed to --settings-file",
                 [(CLI, '"--config", default="sample.toml"', '"--settings-file", default="sample.toml"')],
                 stale=[(CLID, "Options")],
                 expected_edits=[(CLID, "| `--config` | `sample.toml` |", "| `--settings-file` | `sample.toml` |")]))
    add(Scenario("h_env_var_removed", "drift", "SAMPLE_CACHE_DIR removed; one doc line is a table row, another is prose",
                 [(CACHE, 'CACHE_DIR = os.environ.get("SAMPLE_CACHE_DIR", ".cache")', 'CACHE_DIR = ".cache"')],
                 stale=[(CONF, "Environment variables"), (API, "`Cache`")],
                 expected_edits=[(CONF, "| `SAMPLE_CACHE_DIR` | `.cache` | Directory for the on-disk cache |\n", "")]))
    add(Scenario("h_param_removed_method", "drift", "Client.get_record loses include_history",
                 [(C, "def get_record(self, record_id, include_history=False):", "def get_record(self, record_id):"),
                  (C, '"history": [] if include_history else None}', '"history": None}')],
                 stale=[(IDX, "Quickstart"), (API, "`Client`")],
                 expected_edits=[
                     (IDX, "record = client.get_record(7, include_history=True)", "record = client.get_record(7)"),
                     (API, "- `Client.get_record(record_id, include_history=False)` returns", "- `Client.get_record(record_id)` returns"),
                 ]))
    add(Scenario("h_param_renamed_inline_and_block", "drift", "format_amount decimals renamed to places",
                 [(F, 'currency="USD", decimals=2):', 'currency="USD", places=2):'),
                  (F, "{value:.{decimals}f}", "{value:.{places}f}")],
                 stale=[(API, "`format_amount`")],
                 expected_edits=[
                     (API, '`format_amount(value, currency="USD", decimals=2)`', '`format_amount(value, currency="USD", places=2)`'),
                     (API, "print(format_amount(1234.5, decimals=1))", "print(format_amount(1234.5, places=1))"),
                 ]))
    add(Scenario("h_required_param_method", "drift", "Client.list_records gains a required tenant parameter",
                 [(C, 'def list_records(self, limit=50, status="open")', 'def list_records(self, tenant, limit=50, status="open")')],
                 stale=[(API, "`Client`")]))
    add(Scenario("h_config_key_removed", "drift", "config key region removed",
                 [(CFG, '{"region": "eu", "timeout_seconds": 30}', '{"timeout_seconds": 30}'),
                  (CFG, '    region = config["region"]\n', ""),
                  (CFG, '{"region": region, "timeout": timeout, "path": path}', '{"timeout": timeout, "path": path}')],
                 stale=[(CONF, "Settings file")]))
    add(Scenario("h_module_moved_two_symbols", "drift", "formatting.py renamed to fmt.py; the guide names the module in plain prose",
                 moves=[(F, "sample_lib/fmt.py")],
                 stale=[(API, "`format_amount`"), (API, "`slugify`"), (GUIDE, "Building page addresses")],
                 expected_edits=[(API, "from sample_lib.formatting import", "from sample_lib.fmt import")]))
    add(Scenario("h_bait_requests_get", "bait", "Cache.get removed; requests.get in the guide is another library",
                 [(CACHE, '    def get(self, key):\n        """Return a cached value or None."""\n        return self._data.get(key)\n\n', "")],
                 stale=[(API, "`Cache`")]))
    add(Scenario("h_control_body_change", "control", "implementation of slugify changes, signature does not",
                 [(F, "return separator.join(text.lower().split())", "return separator.join(text.lower().strip().split())")]))
    add(Scenario("h_env_default_url", "drift", "SAMPLE_API_URL default gets a /v2 suffix",
                 [(C, '"https://api.ledgerly.example")', '"https://api.ledgerly.example/v2")')],
                 stale=[(CONF, "Environment variables")],
                 expected_edits=[(CONF, "| `SAMPLE_API_URL` | `https://api.ledgerly.example` |", "| `SAMPLE_API_URL` | `https://api.ledgerly.example/v2` |")]))
    add(Scenario("h_cli_default_string", "bait", "--status default open -> all; an example passes --status open explicitly",
                 [(CLI, '"--status", default="open"', '"--status", default="all"')],
                 stale=[(CLID, "Options")],
                 expected_edits=[(CLID, "| `--status` | `open` |", "| `--status` | `all` |")]))
    return s


HOLDOUT: list[Scenario] = _holdout()
