# API reference

## `fetch_user`

```python
def fetch_user(user_id, timeout=30, retries=3)
```

Fetch a user profile by id. Network errors are retried.

| Parameter | Description | Default |
|---|---|---|
| `user_id` | The id of the user | required |
| `timeout` | Seconds to wait for each request | `30` |
| `retries` | How many times to retry a failed request | `3` |

The `timeout` defaults to 30 seconds.

```python
from sample_lib.client import fetch_user

profile = fetch_user(7)
```

## `create_invoice`

`create_invoice(customer_id, amount, currency="USD")` creates an invoice for a customer.

| Parameter | Description | Default |
|---|---|---|
| `customer_id` | Who is billed | required |
| `amount` | Amount in the smallest currency unit | required |
| `currency` | Three-letter currency code | `"USD"` |

```python
from sample_lib.client import create_invoice

invoice = create_invoice("cus_1", 5000)
```

## `Client`

Create a client with `Client(base_url, timeout=30)`. The `timeout` applies to every request made through the client.

- `Client.get_record(record_id, include_history=False)` returns one billing record.
- `Client.list_records(limit=50, status="open")` lists records, filtered by status.
- `Client.close()` releases the connection.

```python
from sample_lib.client import Client

client = Client()
records = client.list_records(limit=10)
client.close()
```

## `format_amount`

`format_amount(value, currency="USD", decimals=2)` formats a money amount for display.

```python
from sample_lib.formatting import format_amount

print(format_amount(1234.5, decimals=1))
```

## `slugify`

`slugify(text, separator="-")` turns a title into a URL-friendly slug.

```python
from sample_lib.formatting import slugify

print(slugify("Hello World", separator="_"))
```

## `Cache`

`Cache.set(key, value, ttl=300)` stores a value. The `ttl` defaults to 300 seconds.

Entries are written below the directory named by `SAMPLE_CACHE_DIR` (default `.cache`).

```python
from sample_lib.cache import Cache

cache = Cache()
cache.set("greeting", "hello", ttl=60)
print(cache.get("greeting"))
cache.clear()
```
