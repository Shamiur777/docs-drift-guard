"""Small text and number formatting helpers."""


def format_amount(value, currency="USD", decimals=2):
    """Format a money amount for display."""
    return f"{value:.{decimals}f} {currency}"


def slugify(text, separator="-"):
    """Turn a title into a URL-friendly slug."""
    return separator.join(text.lower().split())
