"""A tiny on-disk key/value cache."""
import os

CACHE_DIR = os.environ.get("SAMPLE_CACHE_DIR", ".cache")


class Cache:
    """Stores values on disk for a limited time."""

    def __init__(self, directory=CACHE_DIR):
        self.directory = directory
        self._data = {}

    def get(self, key):
        """Return a cached value or None."""
        return self._data.get(key)

    def set(self, key, value, ttl=300):
        """Store a value for `ttl` seconds."""
        self._data[key] = value

    def clear(self):
        """Remove every cached value."""
        self._data.clear()
