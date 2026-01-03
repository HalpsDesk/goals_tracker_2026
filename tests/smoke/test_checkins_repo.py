import sys
from pathlib import Path

# Fix 2: ensure project root is on sys.path when running this file directly
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from storage.db import connect
from storage.goals_repo import create_goal, make_slug
from storage.checkins_repo import create_checkin, list_checkins
from tests.helpers import test_db_path, init_fresh_test_db


def lookup_id(db_path: Path, table: str, name: str) -> int:
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT id FROM {table} WHERE name = ?", (name,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"lookup_id failed: {table}.{name} not found")
        return row["id"]


if __name__ == "__main__":
    db_path = test_db_path("test_checkins_repo.db")
    init_fresh_test_db(db_path)

    # Look up seeded IDs from the test DB
    josh_id = lookup_id(db_path, "owners", "Josh")
    fitness_id = lookup_id(db_path, "categories", "Fitness")

    # Create a CHECK goal (weekly cadence)
    slug = make_slug("Josh", "Ada training")
    goal = create_goal(
        slug=slug,
        title="Ada training",
        category_id=fitness_id,
        owner_id=josh_id,
        metric_type="CHECK",
        cadence_unit="weekly",
        cadence_target=1,
        db_path=db_path,
    )

    # Create a check-in
    c = create_checkin(
        goal_id=goal.id,
        date="2026-01-02",
        created_by_owner_id=josh_id,
        value_num=1,
        note="Short session",
        db_path=db_path,
    )
    print("Created checkin:", c)

    # List check-ins for that goal
    recent = list_checkins(goal_id=goal.id, db_path=db_path)
    print("Checkins for goal:", len(recent))

    # Optional: basic assertion-style checks (will raise if something is wrong)
    assert len(recent) == 1
    assert recent[0].goal_id == goal.id
    assert recent[0].date == "2026-01-02"
    assert recent[0].value_num == 1
