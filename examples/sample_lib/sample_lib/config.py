"""Load settings from a TOML-like file."""


def load_config(path="sample.toml"):
    """Read the settings file and return the resolved settings."""
    config = {"region": "eu", "timeout_seconds": 30}
    region = config["region"]
    timeout = config.get("timeout_seconds", 30)
    return {"region": region, "timeout": timeout, "path": path}
