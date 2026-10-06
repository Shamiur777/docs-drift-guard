"""Client for the fictional Ledgerly billing service."""
import os

DEFAULT_URL = os.environ.get("SAMPLE_API_URL", "https://api.ledgerly.example")
API_KEY = os.getenv("SAMPLE_API_KEY")


class Client:
    """Talks to the Ledgerly API."""

    def __init__(self, base_url=DEFAULT_URL, timeout=30):
        self.base_url = base_url
        self.timeout = timeout

    def get_record(self, record_id, include_history=False):
        """Return one billing record, optionally with its edit history."""
        return {"id": record_id, "history": [] if include_history else None}

    def list_records(self, limit=50, status="open"):
        """List billing records filtered by status."""
        return []

    def close(self):
        """Release the connection."""


def fetch_user(user_id, timeout=30, retries=3):
    """Fetch a user profile by id, retrying on network errors."""
    return {"id": user_id, "timeout": timeout, "retries": retries}


def create_invoice(customer_id, amount, currency="USD"):
    """Create an invoice for a customer."""
    return {"customer": customer_id, "amount": amount, "currency": currency}


def _sign_request(payload):
    """Internal helper, not part of the public API."""
    return payload
