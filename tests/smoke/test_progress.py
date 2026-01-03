import sys
from pathlib import Path
from datetime import date
from typing import Optional

# Fix 2: ensure project root is on sys.path when running this file directly
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from storage.db import connect
from storage.goals_repo import create_goal, make_slug
from storage.checkins_repo import create_checkin
from progress.progress import compute_goal_progress
from tests.helpers import test_db_path, init_fresh_test_db


def lookup_id(db_path: Path, table: str, name: str) -> int:
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT id FROM {table} WHERE name = ?", (name,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"lookup_id failed: {table}.{name} not found")
        return row["id"]


def insert_milestone(
    db_path: Path,
    goal_id: int,
    title: str,
    sort_order: int,
    is_done: int,
    done_date: Optional[str],
):
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO milestones (goal_id, title, sort_order, is_done, done_date, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (goal_id, title, sort_order, is_done, done_date),
        )
        conn.commit()


if __name__ == "__main__":
    db_path = test_db_path("test_progress.db")
    init_fresh_test_db(db_path)

    josh_id = lookup_id(db_path, "owners", "Josh")
    fitness_id = lookup_id(db_path, "categories", "Fitness")
    financial_id = lookup_id(db_path, "categories", "Financial")
    upskilling_id = lookup_id(db_path, "categories", "Upskilling")

    as_of = date(2026, 1, 2)

    # 1) TARGET_CUMULATIVE: Read 50 books, target 50, add 3 books
    g_cum = create_goal(
        slug=make_slug("Josh", "Read 50 books"),
        title="Read 50 books",
        category_id=upskilling_id,
        owner_id=josh_id,
        metric_type="TARGET_CUMULATIVE",
        unit="books",
        target_value=50,
        db_path=db_path,
    )
    create_checkin(goal_id=g_cum.id, date="2026-01-01", created_by_owner_id=josh_id, value_num=1, db_path=db_path)
    create_checkin(goal_id=g_cum.id, date="2026-01-02", created_by_owner_id=josh_id, value_num=2, db_path=db_path)

    p_cum = compute_goal_progress(goal=g_cum, as_of=as_of, db_path=db_path)
    assert p_cum.current_value == 3.0
    assert p_cum.status == "on_track"
    assert p_cum.percent_complete is not None and 5.9 < p_cum.percent_complete < 6.1  # 3/50=6%
    assert len(p_cum.series) == 2  # cumulative series points

    # 2) TARGET_THRESHOLD: 10 pullups >= 10, latest is 9 => at_risk
    g_thr = create_goal(
        slug=make_slug("Josh", "10 unbroken dead hang pullups"),
        title="10 unbroken dead hang pullups",
        category_id=fitness_id,
        owner_id=josh_id,
        metric_type="TARGET_THRESHOLD",
        unit="reps",
        target_value=10,
        target_direction=">=",
        db_path=db_path,
    )
    create_checkin(goal_id=g_thr.id, date="2026-01-02", created_by_owner_id=josh_id, value_num=9, db_path=db_path)

    p_thr = compute_goal_progress(goal=g_thr, as_of=as_of, db_path=db_path)
    assert p_thr.current_value == 9.0
    assert p_thr.status == "at_risk"

    # 3) MEASURE: Bible % complete, target 100 >=, latest is 7 => on_track (not done)
    g_meas = create_goal(
        slug=make_slug("Josh", "Bible in a year percent"),
        title="Bible in a year (% complete)",
        category_id=upskilling_id,
        owner_id=josh_id,
        metric_type="MEASURE",
        unit="%",
        target_value=100,
        target_direction=">=",
        db_path=db_path,
    )
    create_checkin(goal_id=g_meas.id, date="2026-01-02", created_by_owner_id=josh_id, value_num=7, db_path=db_path)

    p_meas = compute_goal_progress(goal=g_meas, as_of=as_of, db_path=db_path)
    assert p_meas.current_value == 7.0
    assert p_meas.status == "on_track"  # only becomes "done" when it meets target
    assert len(p_meas.series) == 1

    # 4) CHECK + cadence: Ada training 1/week, one checkin in this week => done
    g_chk = create_goal(
        slug=make_slug("Josh", "Ada training"),
        title="Ada training",
        category_id=fitness_id,
        owner_id=josh_id,
        metric_type="CHECK",
        cadence_unit="weekly",
        cadence_target=1,
        db_path=db_path,
    )
    create_checkin(goal_id=g_chk.id, date="2026-01-02", created_by_owner_id=josh_id, value_num=1, db_path=db_path)

    p_chk = compute_goal_progress(goal=g_chk, as_of=as_of, db_path=db_path)
    assert p_chk.window_count == 1
    assert p_chk.status == "done"
    assert p_chk.percent_complete == 100.0

    # 5) JOURNAL: Stick to budget rating, no cadence, one entry => on_track
    g_jrn = create_goal(
        slug=make_slug("Josh", "Stick to budget"),
        title="Stick to budget (weekly rating)",
        category_id=financial_id,
        owner_id=josh_id,
        metric_type="JOURNAL",
        unit="rating",
        db_path=db_path,
    )
    create_checkin(
        goal_id=g_jrn.id,
        date="2026-01-02",
        created_by_owner_id=josh_id,
        value_num=7,
        note="Mostly good",
        db_path=db_path,
    )

    p_jrn = compute_goal_progress(goal=g_jrn, as_of=as_of, db_path=db_path)
    assert p_jrn.status == "on_track"
    assert p_jrn.current_value == 7.0

    # 6) MILESTONE: Programming courses, 2 total, 1 done => on_track, 50%
    g_mil = create_goal(
        slug=make_slug("Josh", "Programming courses"),
        title="Programming/Python courses",
        category_id=upskilling_id,
        owner_id=josh_id,
        metric_type="MILESTONE",
        db_path=db_path,
    )
    insert_milestone(db_path, g_mil.id, "Course A", 1, 1, "2026-01-02")
    insert_milestone(db_path, g_mil.id, "Course B", 2, 0, None)

    p_mil = compute_goal_progress(goal=g_mil, as_of=as_of, db_path=db_path)
    assert p_mil.milestones_total == 2
    assert p_mil.milestones_done == 1
    assert p_mil.status == "on_track"
    assert p_mil.percent_complete is not None and 49.9 < p_mil.percent_complete < 50.1

    print("test_progress.py: PASS")
