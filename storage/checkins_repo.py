"""
storage/checkins_repo.py

Repository functions for CRUD operations on check-ins.

Notes:
- This module validates basic invariants (goal exists, owner exists, date format).
- It does NOT compute progress (that belongs in a separate progress module).
- It supports create, list, get, update, delete.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from storage.db import connect, utc_now_iso
from storage.goals_repo import get_goal_by_id, METRIC_TYPES


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Checkin:
    id: int
    goal_id: int
    date: str  # YYYY-MM-DD
    value_num: Optional[float]
    value_text: Optional[str]
    note: Optional[str]
    created_by_owner_id: int
    created_at: str


class CheckinValidationError(ValueError):
    pass


def _row_to_checkin(row: sqlite3.Row) -> Checkin:
    return Checkin(
        id=row["id"],
        goal_id=row["goal_id"],
        date=row["date"],
        value_num=row["value_num"],
        value_text=row["value_text"],
        note=row["note"],
        created_by_owner_id=row["created_by_owner_id"],
        created_at=row["created_at"],
    )


def _owner_exists(owner_id: int, *, db_path: Optional[Path] = None) -> bool:
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM owners WHERE id = ? AND active = 1", (owner_id,))
        return cur.fetchone() is not None


def _validate_date(date_str: str) -> None:
    if not date_str or not isinstance(date_str, str) or not _DATE_RE.match(date_str):
        raise CheckinValidationError("date must be a YYYY-MM-DD string.")


def _validate_checkin_value_against_goal(
    *,
    goal_metric_type: str,
    value_num: Optional[float],
    value_text: Optional[str],
) -> None:
    """
    Light validation. The GUI can enforce more, but repository ensures no nonsense.

    General rules:
    - CHECK: value_num should be 1 (or 0 if you ever use it), text optional
    - MILESTONE: should not use checkins (milestones table), so forbid
    - JOURNAL: value_num optional (e.g., rating), note optional, text optional
    - MEASURE / TARGET_*: typically numeric, but allow text for labels (like race name)
    """
    if goal_metric_type not in METRIC_TYPES:
        raise CheckinValidationError(f"Unknown goal metric_type: {goal_metric_type}")

    if goal_metric_type == "MILESTONE":
        raise CheckinValidationError("MILESTONE goals should not accept check-ins (use milestones table).")

    if goal_metric_type == "CHECK":
        if value_num is None:
            raise CheckinValidationError("CHECK goals require value_num (use 1 for done).")
        if value_num not in (0, 1):
            raise CheckinValidationError("CHECK goals value_num must be 0 or 1.")

    if goal_metric_type in {"MEASURE", "TARGET_CUMULATIVE", "TARGET_THRESHOLD"}:
        if value_num is None:
            raise CheckinValidationError(f"{goal_metric_type} goals require value_num.")

    if goal_metric_type == "JOURNAL":
        # value_num optional (rating). If provided, keep it in a reasonable range.
        if value_num is not None and not (0 <= value_num <= 10):
            raise CheckinValidationError("JOURNAL value_num (if provided) must be between 0 and 10.")


# -------------------------
# CRUD
# -------------------------

def create_checkin(
    *,
    goal_id: int,
    date: str,
    created_by_owner_id: int,
    value_num: Optional[float] = None,
    value_text: Optional[str] = None,
    note: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Checkin:
    if not isinstance(goal_id, int) or goal_id <= 0:
        raise CheckinValidationError("goal_id must be a positive integer.")
    if not isinstance(created_by_owner_id, int) or created_by_owner_id <= 0:
        raise CheckinValidationError("created_by_owner_id must be a positive integer.")
    _validate_date(date)

    goal = get_goal_by_id(goal_id, db_path=db_path)
    if not goal:
        raise CheckinValidationError(f"Goal id={goal_id} not found.")
    if goal.status != "active":
        raise CheckinValidationError("Cannot add check-ins to an archived goal.")

    if not _owner_exists(created_by_owner_id, db_path=db_path):
        raise CheckinValidationError(f"Owner id={created_by_owner_id} not found or inactive.")

    _validate_checkin_value_against_goal(
        goal_metric_type=goal.metric_type,
        value_num=value_num,
        value_text=value_text,
    )

    now = utc_now_iso()
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO checkins (
              goal_id, date, value_num, value_text, note, created_by_owner_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (goal_id, date, value_num, value_text, note, created_by_owner_id, now),
        )
        checkin_id = cur.lastrowid
        conn.commit()

        cur.execute("SELECT * FROM checkins WHERE id = ?", (checkin_id,))
        row = cur.fetchone()
        return _row_to_checkin(row)


def get_checkin_by_id(checkin_id: int, *, db_path: Optional[Path] = None) -> Optional[Checkin]:
    if not isinstance(checkin_id, int) or checkin_id <= 0:
        raise CheckinValidationError("checkin_id must be a positive integer.")
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM checkins WHERE id = ?", (checkin_id,))
        row = cur.fetchone()
        return _row_to_checkin(row) if row else None


def list_checkins(
    *,
    goal_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> list[Checkin]:
    """
    List check-ins optionally filtered by goal and/or date range.

    date_from/date_to are inclusive and must be YYYY-MM-DD.
    """
    where = []
    params: list[object] = []

    if goal_id is not None:
        if not isinstance(goal_id, int) or goal_id <= 0:
            raise CheckinValidationError("goal_id must be a positive integer.")
        where.append("goal_id = ?")
        params.append(goal_id)

    if date_from is not None:
        _validate_date(date_from)
        where.append("date >= ?")
        params.append(date_from)

    if date_to is not None:
        _validate_date(date_to)
        where.append("date <= ?")
        params.append(date_to)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    limit_sql = ""
    if limit is not None:
        if not isinstance(limit, int) or limit <= 0:
            raise CheckinValidationError("limit must be a positive integer.")
        limit_sql = " LIMIT ?"
        params.append(limit)

    sql = f"""
        SELECT * FROM checkins
        {where_sql}
        ORDER BY date DESC, id DESC
        {limit_sql}
    """

    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [_row_to_checkin(r) for r in cur.fetchall()]


def update_checkin(
    checkin_id: int,
    *,
    date: Optional[str] = None,
    value_num: Optional[float] = None,
    value_text: Optional[str] = None,
    note: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Checkin:
    """
    Update editable fields on a check-in. Does not allow changing goal_id or creator.
    """
    existing = get_checkin_by_id(checkin_id, db_path=db_path)
    if not existing:
        raise CheckinValidationError(f"Check-in id={checkin_id} not found.")

    new_date = date if date is not None else existing.date
    if new_date is not None:
        _validate_date(new_date)

    # Need goal to validate value rules
    goal = get_goal_by_id(existing.goal_id, db_path=db_path)
    if not goal:
        raise CheckinValidationError(f"Goal id={existing.goal_id} not found (data integrity issue).")

    new_value_num = value_num if value_num is not None else existing.value_num
    new_value_text = value_text if value_text is not None else existing.value_text

    _validate_checkin_value_against_goal(
        goal_metric_type=goal.metric_type,
        value_num=new_value_num,
        value_text=new_value_text,
    )

    new_note = note if note is not None else existing.note

    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE checkins
            SET date = ?,
                value_num = ?,
                value_text = ?,
                note = ?
            WHERE id = ?
            """,
            (new_date, new_value_num, new_value_text, new_note, checkin_id),
        )
        conn.commit()

    updated = get_checkin_by_id(checkin_id, db_path=db_path)
    assert updated is not None
    return updated


def delete_checkin(checkin_id: int, *, db_path: Optional[Path] = None) -> None:
    """
    Hard delete is acceptable for check-ins (they're user entry errors sometimes).
    If you want audit trail later, we can switch to soft-delete.
    """
    if not isinstance(checkin_id, int) or checkin_id <= 0:
        raise CheckinValidationError("checkin_id must be a positive integer.")
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM checkins WHERE id = ?", (checkin_id,))
        conn.commit()