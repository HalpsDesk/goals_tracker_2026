from __future__ import annotations

from pathlib import Path
import shutil

from storage.db import init_db


def test_db_path(name: str) -> Path:
    """
    Returns a stable test DB path under tests/tmp/.
    Example: tests/tmp/test_db_init.db
    """
    tmp_dir = Path(__file__).resolve().parent / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir / name


def reset_test_db(path: Path) -> None:
    """
    Delete an existing test DB so smoke tests are re-runnable.
    """
    if path.exists():
        path.unlink()


def init_fresh_test_db(path: Path) -> None:
    """
    Convenience: delete + init schema + seed owners/categories.
    """
    reset_test_db(path)
    init_db(db_path=path)
