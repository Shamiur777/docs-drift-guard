import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="")
    return root


@pytest.fixture
def tree(tmp_path):
    """Create a small project tree: tree({'pkg/a.py': '...'}) -> Path."""
    counter = {"n": 0}

    def make(files: dict[str, str]) -> Path:
        counter["n"] += 1
        return write_tree(tmp_path / f"t{counter['n']}", files)

    return make
