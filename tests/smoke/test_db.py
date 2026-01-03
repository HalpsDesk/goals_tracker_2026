import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from storage.db import health_check
from tests.helpers import test_db_path, init_fresh_test_db


if __name__ == "__main__":
    db_path = test_db_path("test_db_init.db")
    init_fresh_test_db(db_path)

    print(health_check(db_path=db_path))
