# sample_lib

`sample_lib` is a small client library for the fictional Ledgerly billing service. This page is a quick tour.

## Installation

Install it from a checkout:

```bash
pip install .
```

## Quickstart

```python
from sample_lib.client import fetch_user, create_invoice

user = fetch_user(42, timeout=10)
invoice = create_invoice(user["id"], 120, currency="EUR")
```

Use `Client` when you want to reuse one connection:

```python
from sample_lib.client import Client

client = Client(timeout=15)
record = client.get_record(7, include_history=True)
client.close()
```

## Command line quickstart

```bash
python -m sample_lib --limit 20 --status open
sample-lib --output json --verbose
```

## Getting help

Open an issue on the project tracker and include the output of `sample-lib --help`.
