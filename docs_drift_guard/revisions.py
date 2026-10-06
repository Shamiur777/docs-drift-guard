"""Resolve `--base` / `--head` arguments: a directory, or a git revision."""
from __future__ import annotations

import io
import subprocess
import tarfile
import tempfile
from pathlib import Path


class RevisionError(Exception):
    pass


def materialize(spec: str, repo: str | Path, workdir: Path) -> Path:
    """Return a directory holding the tree for `spec` (a path, or a git rev exported with git archive)."""
    p = Path(spec)
    if p.is_dir():
        return p
    try:
        out = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", spec],
                             capture_output=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        detail = exc.stderr.decode(errors="ignore").strip() if hasattr(exc, "stderr") and exc.stderr else str(exc)
        raise RevisionError(f"{spec!r} is neither a directory nor a git revision in {repo}: {detail}") from exc
    dest = Path(tempfile.mkdtemp(prefix="ddg-", dir=workdir))
    with tarfile.open(fileobj=io.BytesIO(out)) as tar:
        tar.extractall(dest, filter="data")
    return dest
