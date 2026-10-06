# Command line

Run the CLI with `python -m sample_lib` or the `sample-lib` command.

## Options

| Flag | Default | Description |
|---|---|---|
| `--limit` | `50` | Maximum number of records to show |
| `--status` | `open` | Only show records with this status |
| `--output` | `table` | Output format: `table` or `json` |
| `--verbose`, `-v` | off | Print extra detail |
| `--dry-run` | off | Show what would happen without changing anything |
| `--config` | `sample.toml` | Path to the settings file |

## Examples

```bash
python -m sample_lib --limit 10
python -m sample_lib --status closed --output json
sample-lib --dry-run --verbose
```

## Machine-readable output

Use `--output json` for machine-readable output.

## Working with other tools

Other tools have their own flags. For example, git can overwrite a remote branch, and pip can print more detail:

```bash
git push --force
pip install --verbose .
```
