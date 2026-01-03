import sys
from pathlib import Path
from datetime import date

# Fix 2: ensure project root is on sys.path when running this file directly
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from storage.db import connect
from storage.goals_repo import create_goal, make_slug
from storage.checkins_repo import create_checkin
from site_builder.site_builder import build_site
from tests.helpers import test_db_path, init_fresh_test_db


def lookup_id(db_path: Path, table: str, name: str) -> int:
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT id FROM {table} WHERE name = ?", (name,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"lookup_id failed: {table}.{name} not found")
        return row["id"]


def wipe_dir(d: Path) -> None:
    if not d.exists():
        return
    for child in d.iterdir():
        if child.is_dir():
            wipe_dir(child)
            child.rmdir()
        else:
            child.unlink()


if __name__ == "__main__":
    # 1) Create an isolated test DB
    db_path = test_db_path("test_site_builder.db")
    init_fresh_test_db(db_path)

    josh_id = lookup_id(db_path, "owners", "Josh")
    fitness_id = lookup_id(db_path, "categories", "Fitness")
    upskilling_id = lookup_id(db_path, "categories", "Upskilling")

    # 2) Seed a couple goals + checkins (so pages have content)
    g1 = create_goal(
        slug=make_slug("Josh", "Read 50 books"),
        title="Read 50 books",
        category_id=upskilling_id,
        owner_id=josh_id,
        metric_type="TARGET_CUMULATIVE",
        unit="books",
        target_value=50,
        db_path=db_path,
    )
    create_checkin(goal_id=g1.id, date="2026-01-01", created_by_owner_id=josh_id, value_num=1, db_path=db_path)
    create_checkin(goal_id=g1.id, date="2026-01-02", created_by_owner_id=josh_id, value_num=2, db_path=db_path)

    g2 = create_goal(
        slug=make_slug("Josh", "Ada training"),
        title="Ada training",
        category_id=fitness_id,
        owner_id=josh_id,
        metric_type="CHECK",
        cadence_unit="weekly",
        cadence_target=1,
        db_path=db_path,
    )
    create_checkin(goal_id=g2.id, date="2026-01-02", created_by_owner_id=josh_id, value_num=1, db_path=db_path)

    # 3) Build site into an isolated output folder
    out_dir = (Path(__file__).resolve().parents[1] / "tmp" / "site_out").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    wipe_dir(out_dir)  # ensure deterministic test

    build_site(db_path=db_path, out_dir=out_dir)

    # 4) Assertions: key files exist
    assert (out_dir / "index.html").exists(), "index.html not generated"
    assert (out_dir / "assets" / "style.css").exists(), "style.css not generated"
    assert (out_dir / "assets" / "chart.umd.min.js").exists(), "chart placeholder not generated"
    assert (out_dir / "data" / "progress.json").exists(), "progress.json not generated"

    # Per-goal pages
    assert (out_dir / "goals" / g1.slug / "index.html").exists(), "goal page for g1 missing"
    assert (out_dir / "goals" / g2.slug / "index.html").exists(), "goal page for g2 missing"

    # 5) Sanity: index includes goal titles and links
    index_html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "Goals Tracker 2026" in index_html
    assert "Read 50 books" in index_html
    assert "Ada training" in index_html
    assert f'goals/{g1.slug}/' in index_html
    assert f'goals/{g2.slug}/' in index_html

    # Sanity: goal page includes chart canvas
    g1_html = (out_dir / "goals" / g1.slug / "index.html").read_text(encoding="utf-8")
    assert "<canvas" in g1_html
    assert "SERIES" in g1_html

    print("test_site_builder.py: PASS")
