# Configuration

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SAMPLE_API_URL` | `https://api.ledgerly.example` | Base URL of the API |
| `SAMPLE_API_KEY` | none | API key used for requests |
| `SAMPLE_CACHE_DIR` | `.cache` | Directory for the on-disk cache |

Set them in your shell before running anything:

```bash
export SAMPLE_API_KEY=demo-key
python -m sample_lib --limit 5
```

## Settings file

`load_config(path="sample.toml")` reads the settings file. These keys are recognised:

| Key | Default | Description |
|---|---|---|
| `region` | `eu` | Data region |
| `timeout_seconds` | `30` | Request timeout in seconds |

In code you read them as `config["region"]` or `config.get("timeout_seconds", 30)`.
