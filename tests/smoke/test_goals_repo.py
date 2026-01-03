import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from storage.db import connect
from storage.goals_repo import create_goal, list_goals, make_slug
from tests.helpers import test_db_path, init_fresh_test_db


def lookup_id(db_path: Path, table: str, name: str) -> int:
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT id FROM {table} WHERE name = ?", (name,))
        return cur.fetchone()["id"]


if __name__ == "__main__":
    db_path = test_db_path("test_goals_repo.db")
    init_fresh_test_db(db_path)

    owner_id = lookup_id(db_path, "owners", "Josh")
    category_id = lookup_id(db_path, "categories", "Fitness")

    slug = make_slug("Josh", "10 unbroken dead hang pullups")
    g = create_goal(
        slug=slug,
        title="10 unbroken dead hang pullups",
        category_id=category_id,
        owner_id=owner_id,
        metric_type="TARGET_THRESHOLD",
        unit="reps",
        target_value=10,
        target_direction=">=",
        db_path=db_path,                 # <-- important
    )

    goals = list_goals(db_path=db_path)  # <-- important
    print("Created:", g.slug)
    print("Active goals:", len(goals))
