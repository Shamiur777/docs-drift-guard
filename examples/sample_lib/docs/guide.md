# Guide

## Fetching users reliably

Network calls can fail. `fetch_user` retries a failed request a few times before giving up, so most transient errors never reach your code.

## Rate limits

Your plan sets a limit on requests per minute. When you reach the limit, the service answers with HTTP 429. Back off and try again later.

## Writing your own helper

If you need different behaviour, define your own function. This snippet defines a local `fetch_user` that is unrelated to the library:

```python
def fetch_user(name):
    return {"name": name}

print(fetch_user("ada"))
```

## Calling other HTTP libraries

The `requests` package has its own call shape, shown here only for contrast:

```python
import requests

response = requests.get("https://example.org/items", timeout=30)
print(response.json())
```

## Building page addresses

To build URL-friendly slugs from page titles, use the slug helper in the formatting module.
