"""
storage/goals_repo.py

Repository functions for CRUD operations on goals.

Notes:
- Slug is immutable: only set on create.
- "Delete" is implemented as archive (status='archived').
- Validation is enforced here so GUI and site-builder can rely on invariants.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Optional, Sequence

from storage.db import connect, utc_now_iso


# -------------------------
# Types / constants
# -------------------------

METRIC_TYPES = {
    "CHECK",
    "MEASURE",
    "TARGET_CUMULATIVE",
    "TARGET_THRESHOLD",
    "MILESTONE",
    "JOURNAL",
}

CADENCE_UNITS = {"daily", "weekly", "monthly"}

TARGET_DIRECTIONS = {">=", "<="}

STATUS_VALUES = {"active", "archived"}


@dataclass(frozen=True)
class Goal:
    id: int
    slug: str
    title: str
    category_id: int
    owner_id: int
    metric_type: str
    unit: Optional[str]
    target_value: Optional[float]
    target_direction: Optional[str]
    cadence_unit: Optional[str]
    cadence_target: Optional[float]
    start_date: Optional[str]  # YYYY-MM-DD
    end_date: Optional[str]    # YYYY-MM-DD
    publish_notes: int
    status: str
    created_at: str
    updated_at: str


class GoalValidationError(ValueError):
    pass


# -------------------------
# Slug helper
# -------------------------

_slug_re = re.compile(r"[^a-z0-9_]+")

def make_slug(owner_label: str, title: str) -> str:
    """
    Create a stable, readable slug.
    Example: ("Josh", "Read 50 books") -> "josh_read_50_books"
    """
    base = f"{owner_label}_{title}".strip().lower()
    base = base.replace("&", "and")
    base = base.replace("/", " ")
    base = _slug_re.sub("_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    if not base:
        raise GoalValidationError("Unable to generate slug from empty title/owner.")
    return base


# -------------------------
# Validation
# -------------------------

def _validate_goal_fields(
    *,
    slug: str,
    title: str,
    category_id: int,
    owner_id: int,
    metric_type: str,
    unit: Optional[str],
    target_value: Optional[float],
    target_direction: Optional[str],
    cadence_unit: Optional[str],
    cadence_target: Optional[float],
    start_date: Optional[str],
    end_date: Optional[str],
    publish_notes: int,
    status: str,
    slug_required: bool,
) -> None:
    if slug_required:
        if not slug or not isinstance(slug, str):
            raise GoalValidationError("slug is required.")
        if not re.fullmatch(r"[a-z0-9_]+", slug):
            raise GoalValidationError("slug must contain only lowercase letters, numbers, and underscores.")

    if not title or not isinstance(title, str):
        raise GoalValidationError("title is required.")

    if metric_type not in METRIC_TYPES:
        raise GoalValidationError(f"metric_type must be one of: {sorted(METRIC_TYPES)}")

    if not isinstance(category_id, int) or category_id <= 0:
        raise GoalValidationError("category_id must be a positive integer.")

    if not isinstance(owner_id, int) or owner_id <= 0:
        raise GoalValidationError("owner_id must be a positive integer.")

    if status not in STATUS_VALUES:
        raise GoalValidationError(f"status must be one of: {sorted(STATUS_VALUES)}")

    if publish_notes not in (0, 1):
        raise GoalValidationError("publish_notes must be 0 or 1.")

    # Cadence validation (optional)
    if cadence_unit is not None:
        if cadence_unit not in CADENCE_UNITS:
            raise GoalValidationError(f"cadence_unit must be one of: {sorted(CADENCE_UNITS)}")
        if cadence_target is None or cadence_target <= 0:
            raise GoalValidationError("cadence_target must be > 0 when cadence_unit is set.")
    else:
        if cadence_target is not None:
            raise GoalValidationError("cadence_target must be NULL when cadence_unit is NULL.")

    # Target validation depends on metric type
    if metric_type in {"TARGET_CUMULATIVE"}:
        if target_value is None or target_value <= 0:
            raise GoalValidationError("target_value must be > 0 for TARGET_CUMULATIVE.")
        # direction not needed
        if target_direction is not None:
            raise GoalValidationError("target_direction must be NULL for TARGET_CUMULATIVE.")

    if metric_type in {"TARGET_THRESHOLD"}:
        if target_value is None:
            raise GoalValidationError("target_value is required for TARGET_THRESHOLD.")
        if target_direction not in TARGET_DIRECTIONS:
            raise GoalValidationError("target_direction must be '>=' or '<=' for TARGET_THRESHOLD.")

    if metric_type in {"MEASURE"}:
        # target optional (but allowed for visual goal line)
        if target_direction is not None and target_direction not in TARGET_DIRECTIONS:
            raise GoalValidationError("target_direction must be NULL or one of '>=' '<=' for MEASURE.")

    if metric_type in {"CHECK"}:
        # targets optional; cadence is how you enforce frequency
        if target_direction is not None:
            raise GoalValidationError("target_direction must be NULL for CHECK.")
        # target_value generally not needed (leave NULL), but we won't forbid it.

    if metric_type in {"MILESTONE", "JOURNAL"}:
        if target_value is not None:
            raise GoalValidationError(f"target_value must be NULL for {metric_type}.")
        if target_direction is not None:
            raise GoalValidationError(f"target_direction must be NULL for {metric_type}.")
        # unit allowed but often unnecessary

    # Date validation (light)
    # Keep as strings in v1; validate format basically
    for field_name, val in (("start_date", start_date), ("end_date", end_date)):
        if val is not None:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", val):
                raise GoalValidationError(f"{field_name} must be YYYY-MM-DD or NULL.")

    if start_date and end_date and start_date > end_date:
        raise GoalValidationError("start_date must be <= end_date.")


def _row_to_goal(row: sqlite3.Row) -> Goal:
    return Goal(
        id=row["id"],
        slug=row["slug"],
        title=row["title"],
        category_id=row["category_id"],
        owner_id=row["owner_id"],
        metric_type=row["metric_type"],
        unit=row["unit"],
        target_value=row["target_value"],
        target_direction=row["target_direction"],
        cadence_unit=row["cadence_unit"],
        cadence_target=row["cadence_target"],
        start_date=row["start_date"],
        end_date=row["end_date"],
        publish_notes=row["publish_notes"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# -------------------------
# CRUD
# -------------------------

def create_goal(
    *,
    slug: str,
    title: str,
    category_id: int,
    owner_id: int,
    metric_type: str,
    unit: Optional[str] = None,
    target_value: Optional[float] = None,
    target_direction: Optional[str] = None,
    cadence_unit: Optional[str] = None,
    cadence_target: Optional[float] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    publish_notes: int = 0,
    status: str = "active",
    db_path: Optional[Path] = None,
) -> Goal:
    _validate_goal_fields(
        slug=slug,
        title=title,
        category_id=category_id,
        owner_id=owner_id,
        metric_type=metric_type,
        unit=unit,
        target_value=target_value,
        target_direction=target_direction,
        cadence_unit=cadence_unit,
        cadence_target=cadence_target,
        start_date=start_date,
        end_date=end_date,
        publish_notes=publish_notes,
        status=status,
        slug_required=True,
    )

    now = utc_now_iso()
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO goals (
              slug, title, category_id, owner_id,
              metric_type, unit, target_value, target_direction,
              cadence_unit, cadence_target,
              start_date, end_date,
              publish_notes, status,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                slug, title, category_id, owner_id,
                metric_type, unit, target_value, target_direction,
                cadence_unit, cadence_target,
                start_date, end_date,
                publish_notes, status,
                now, now
            ),
        )
        goal_id = cur.lastrowid
        conn.commit()

        cur.execute("SELECT * FROM goals WHERE id = ?", (goal_id,))
        return _row_to_goal(cur.fetchone())


def get_goal_by_slug(slug: str, *, db_path: Optional[Path] = None) -> Optional[Goal]:
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM goals WHERE slug = ?", (slug,))
        row = cur.fetchone()
        return _row_to_goal(row) if row else None


def get_goal_by_id(goal_id: int, *, db_path: Optional[Path] = None) -> Optional[Goal]:
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM goals WHERE id = ?", (goal_id,))
        row = cur.fetchone()
        return _row_to_goal(row) if row else None


def list_goals(
    *,
    status: str = "active",
    owner_id: Optional[int] = None,
    category_id: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> list[Goal]:
    if status not in STATUS_VALUES and status != "any":
        raise GoalValidationError("status must be 'active', 'archived', or 'any'.")

    where = []
    params: list[object] = []

    if status != "any":
        where.append("status = ?")
        params.append(status)

    if owner_id is not None:
        where.append("owner_id = ?")
        params.append(owner_id)

    if category_id is not None:
        where.append("category_id = ?")
        params.append(category_id)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT * FROM goals {where_sql} ORDER BY status, category_id, owner_id, title;"

    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [_row_to_goal(r) for r in cur.fetchall()]


def update_goal(
    goal_id: int,
    *,
    title: Optional[str] = None,
    category_id: Optional[int] = None,
    owner_id: Optional[int] = None,
    metric_type: Optional[str] = None,
    unit: Optional[str] = None,
    target_value: Optional[float] = None,
    target_direction: Optional[str] = None,
    cadence_unit: Optional[str] = None,
    cadence_target: Optional[float] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    publish_notes: Optional[int] = None,
    status: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Goal:
    """
    Update mutable goal fields. Slug is intentionally not editable here.
    """
    existing = get_goal_by_id(goal_id, db_path=db_path)
    if not existing:
        raise GoalValidationError(f"Goal id={goal_id} not found.")

    new_title = title if title is not None else existing.title
    new_category_id = category_id if category_id is not None else existing.category_id
    new_owner_id = owner_id if owner_id is not None else existing.owner_id
    new_metric_type = metric_type if metric_type is not None else existing.metric_type
    new_unit = unit if unit is not None else existing.unit
    new_target_value = target_value if target_value is not None else existing.target_value
    new_target_direction = target_direction if target_direction is not None else existing.target_direction
    new_cadence_unit = cadence_unit if cadence_unit is not None else existing.cadence_unit
    new_cadence_target = cadence_target if cadence_target is not None else existing.cadence_target
    new_start_date = start_date if start_date is not None else existing.start_date
    new_end_date = end_date if end_date is not None else existing.end_date
    new_publish_notes = publish_notes if publish_notes is not None else existing.publish_notes
    new_status = status if status is not None else existing.status

    _validate_goal_fields(
        slug=existing.slug,  # immutable
        title=new_title,
        category_id=new_category_id,
        owner_id=new_owner_id,
        metric_type=new_metric_type,
        unit=new_unit,
        target_value=new_target_value,
        target_direction=new_target_direction,
        cadence_unit=new_cadence_unit,
        cadence_target=new_cadence_target,
        start_date=new_start_date,
        end_date=new_end_date,
        publish_notes=new_publish_notes,
        status=new_status,
        slug_required=False,
    )

    now = utc_now_iso()
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE goals
            SET title = ?,
                category_id = ?,
                owner_id = ?,
                metric_type = ?,
                unit = ?,
                target_value = ?,
                target_direction = ?,
                cadence_unit = ?,
                cadence_target = ?,
                start_date = ?,
                end_date = ?,
                publish_notes = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                new_title,
                new_category_id,
                new_owner_id,
                new_metric_type,
                new_unit,
                new_target_value,
                new_target_direction,
                new_cadence_unit,
                new_cadence_target,
                new_start_date,
                new_end_date,
                new_publish_notes,
                new_status,
                now,
                goal_id,
            ),
        )
        conn.commit()

    updated = get_goal_by_id(goal_id, db_path=db_path)
    assert updated is not None
    return updated


def archive_goal(goal_id: int, *, db_path: Optional[Path] = None) -> Goal:
    return update_goal(goal_id, status="archived", db_path=db_path)


def reactivate_goal(goal_id: int, *, db_path: Optional[Path] = None) -> Goal:
    return update_goal(goal_id, status="active", db_path=db_path)
