"""
progress/progress.py

Progress computation layer (read-only).

Responsibilities:
- Load goal + its data (checkins, milestones) from SQLite
- Compute a unified GoalProgress summary for GUI + site builder
- No writes. No GUI. No deployment.

Conventions:
- Dates are stored as YYYY-MM-DD strings.
- Times (if used) should be stored as seconds in value_num.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Sequence, Tuple, List, Dict, Any

from storage.db import connect
from storage.goals_repo import Goal, get_goal_by_id
from storage.checkins_repo import Checkin, list_checkins


# -------------------------
# Types
# -------------------------

ProgressStatus = str  # "no_data" | "on_track" | "at_risk" | "done"


@dataclass(frozen=True)
class GoalProgress:
    goal_id: int
    slug: str
    title: str
    metric_type: str

    status: ProgressStatus

    # Core numbers (not always present)
    current_value: Optional[float]        # latest or total (depends on type)
    target_value: Optional[float]
    percent_complete: Optional[float]     # 0..100 when meaningful

    # Habit/cadence fields (when applicable)
    cadence_unit: Optional[str]
    cadence_target: Optional[float]
    window_start: Optional[str]           # YYYY-MM-DD
    window_end: Optional[str]             # YYYY-MM-DD
    window_count: Optional[int]           # number of "done" check-ins in window

    # Milestones (when applicable)
    milestones_total: Optional[int]
    milestones_done: Optional[int]

    # For charting / sparklines
    series: List[Tuple[str, float]]       # list of (YYYY-MM-DD, value)


# -------------------------
# Public API
# -------------------------

def compute_all_progress(
    *,
    goal_ids: Optional[Sequence[int]] = None,
    as_of: Optional[date] = None,
    db_path: Optional[Path] = None,
) -> List[GoalProgress]:
    """
    Compute progress for all active goals (or a subset of goal_ids).
    """
    as_of_d = as_of or date.today()

    goals = _load_goals(goal_ids=goal_ids, db_path=db_path)
    out: List[GoalProgress] = []

    for g in goals:
        out.append(compute_goal_progress(goal=g, as_of=as_of_d, db_path=db_path))

    return out


def compute_goal_progress(
    *,
    goal: Goal,
    as_of: Optional[date] = None,
    db_path: Optional[Path] = None,
) -> GoalProgress:
    """
    Compute progress for a single goal.
    """
    as_of_d = as_of or date.today()

    checkins = list_checkins(goal_id=goal.id, db_path=db_path)
    # list_checkins returns newest-first; we often want oldest-first for charts
    checkins_asc = list(reversed(checkins))

    milestones_total, milestones_done = (None, None)
    if goal.metric_type == "MILESTONE":
        milestones_total, milestones_done = _load_milestone_counts(goal_id=goal.id, db_path=db_path)

    # Dispatch by metric type
    if goal.metric_type == "TARGET_CUMULATIVE":
        return _progress_target_cumulative(goal, checkins_asc)

    if goal.metric_type == "TARGET_THRESHOLD":
        return _progress_target_threshold(goal, checkins_asc)

    if goal.metric_type == "MEASURE":
        return _progress_measure(goal, checkins_asc)

    if goal.metric_type == "CHECK":
        return _progress_check(goal, checkins_asc, as_of_d)

    if goal.metric_type == "JOURNAL":
        return _progress_journal(goal, checkins_asc, as_of_d)

    if goal.metric_type == "MILESTONE":
        return _progress_milestone(goal, milestones_total, milestones_done)

    # Fallback (should not happen if validation is correct)
    return GoalProgress(
        goal_id=goal.id,
        slug=goal.slug,
        title=goal.title,
        metric_type=goal.metric_type,
        status="no_data",
        current_value=None,
        target_value=goal.target_value,
        percent_complete=None,
        cadence_unit=goal.cadence_unit,
        cadence_target=goal.cadence_target,
        window_start=None,
        window_end=None,
        window_count=None,
        milestones_total=milestones_total,
        milestones_done=milestones_done,
        series=[],
    )


# -------------------------
# Loaders
# -------------------------

def _load_goals(*, goal_ids: Optional[Sequence[int]], db_path: Optional[Path]) -> List[Goal]:
    """
    Load goals directly; we keep this local so progress isn't coupled to list_goals signature.
    Only active goals by default.
    """
    with connect(db_path) as conn:
        cur = conn.cursor()
        if goal_ids:
            placeholders = ",".join(["?"] * len(goal_ids))
            cur.execute(
                f"SELECT * FROM goals WHERE status='active' AND id IN ({placeholders}) ORDER BY id",
                list(goal_ids),
            )
        else:
            cur.execute("SELECT * FROM goals WHERE status='active' ORDER BY id")
        rows = cur.fetchall()

    # Reuse Goal dataclass via the same fields as goals_repo row conversion
    goals: List[Goal] = []
    for r in rows:
        goals.append(
            Goal(
                id=r["id"],
                slug=r["slug"],
                title=r["title"],
                category_id=r["category_id"],
                owner_id=r["owner_id"],
                metric_type=r["metric_type"],
                unit=r["unit"],
                target_value=r["target_value"],
                target_direction=r["target_direction"],
                cadence_unit=r["cadence_unit"],
                cadence_target=r["cadence_target"],
                start_date=r["start_date"],
                end_date=r["end_date"],
                publish_notes=r["publish_notes"],
                status=r["status"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
        )
    return goals


def _load_milestone_counts(*, goal_id: int, db_path: Optional[Path]) -> Tuple[int, int]:
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM milestones WHERE goal_id = ?", (goal_id,))
        total = int(cur.fetchone()["n"])
        cur.execute("SELECT COUNT(*) AS n FROM milestones WHERE goal_id = ? AND is_done = 1", (goal_id,))
        done = int(cur.fetchone()["n"])
    return total, done


# -------------------------
# Date / window helpers
# -------------------------

def _week_start(d: date) -> date:
    # Monday as start of week
    return d - timedelta(days=d.weekday())


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _window_for_cadence(cadence_unit: str, as_of: date) -> Tuple[date, date]:
    """
    Inclusive window [start, end] for the cadence period containing as_of.
    """
    if cadence_unit == "daily":
        start = as_of
        end = as_of
    elif cadence_unit == "weekly":
        start = _week_start(as_of)
        end = start + timedelta(days=6)
    elif cadence_unit == "monthly":
        start = _month_start(as_of)
        # compute last day of month
        if start.month == 12:
            next_month = date(start.year + 1, 1, 1)
        else:
            next_month = date(start.year, start.month + 1, 1)
        end = next_month - timedelta(days=1)
    else:
        raise ValueError(f"Unsupported cadence_unit: {cadence_unit}")
    return start, end


def _in_window(d_str: str, start: date, end: date) -> bool:
    d = date.fromisoformat(d_str)
    return start <= d <= end


# -------------------------
# Series helpers
# -------------------------

def _numeric_series(checkins_asc: Sequence[Checkin]) -> List[Tuple[str, float]]:
    out: List[Tuple[str, float]] = []
    for c in checkins_asc:
        if c.value_num is None:
            continue
        out.append((c.date, float(c.value_num)))
    return out


def _cumulative_series(checkins_asc: Sequence[Checkin]) -> List[Tuple[str, float]]:
    total = 0.0
    out: List[Tuple[str, float]] = []
    for c in checkins_asc:
        if c.value_num is None:
            continue
        total += float(c.value_num)
        out.append((c.date, total))
    return out


def _latest_value(checkins_asc: Sequence[Checkin]) -> Optional[float]:
    for c in reversed(checkins_asc):
        if c.value_num is not None:
            return float(c.value_num)
    return None


# -------------------------
# Progress calculators
# -------------------------

def _progress_target_cumulative(goal: Goal, checkins_asc: Sequence[Checkin]) -> GoalProgress:
    series = _cumulative_series(checkins_asc)
    total = series[-1][1] if series else 0.0

    if goal.target_value is None or goal.target_value <= 0:
        # should not happen due to repo validation, but handle safely
        pct = None
        status = "no_data" if not series else "on_track"
    else:
        pct = min(100.0, (total / float(goal.target_value)) * 100.0)
        status = "done" if total >= float(goal.target_value) else ("no_data" if not series else "on_track")

    return GoalProgress(
        goal_id=goal.id,
        slug=goal.slug,
        title=goal.title,
        metric_type=goal.metric_type,
        status=status,
        current_value=total,
        target_value=goal.target_value,
        percent_complete=pct,
        cadence_unit=goal.cadence_unit,
        cadence_target=goal.cadence_target,
        window_start=None,
        window_end=None,
        window_count=None,
        milestones_total=None,
        milestones_done=None,
        series=series,
    )


def _progress_target_threshold(goal: Goal, checkins_asc: Sequence[Checkin]) -> GoalProgress:
    series = _numeric_series(checkins_asc)
    latest = _latest_value(checkins_asc)

    if latest is None or goal.target_value is None or goal.target_direction is None:
        return GoalProgress(
            goal_id=goal.id,
            slug=goal.slug,
            title=goal.title,
            metric_type=goal.metric_type,
            status="no_data",
            current_value=latest,
            target_value=goal.target_value,
            percent_complete=None,
            cadence_unit=goal.cadence_unit,
            cadence_target=goal.cadence_target,
            window_start=None,
            window_end=None,
            window_count=None,
            milestones_total=None,
            milestones_done=None,
            series=series,
        )

    target = float(goal.target_value)
    direction = goal.target_direction

    meets = (latest >= target) if direction == ">=" else (latest <= target)
    status = "done" if meets else "at_risk"

    # A rough percent indicator for threshold goals (optional, but useful)
    pct: Optional[float]
    if direction == ">=":
        pct = min(100.0, (latest / target) * 100.0) if target != 0 else None
    else:
        # smaller is better; if latest > target, percent < 100
        pct = min(100.0, (target / latest) * 100.0) if latest != 0 else None

    return GoalProgress(
        goal_id=goal.id,
        slug=goal.slug,
        title=goal.title,
        metric_type=goal.metric_type,
        status=status,
        current_value=latest,
        target_value=goal.target_value,
        percent_complete=pct,
        cadence_unit=goal.cadence_unit,
        cadence_target=goal.cadence_target,
        window_start=None,
        window_end=None,
        window_count=None,
        milestones_total=None,
        milestones_done=None,
        series=series,
    )


def _progress_measure(goal: Goal, checkins_asc: Sequence[Checkin]) -> GoalProgress:
    series = _numeric_series(checkins_asc)
    latest = _latest_value(checkins_asc)

    if latest is None:
        return GoalProgress(
            goal_id=goal.id,
            slug=goal.slug,
            title=goal.title,
            metric_type=goal.metric_type,
            status="no_data",
            current_value=None,
            target_value=goal.target_value,
            percent_complete=None,
            cadence_unit=goal.cadence_unit,
            cadence_target=goal.cadence_target,
            window_start=None,
            window_end=None,
            window_count=None,
            milestones_total=None,
            milestones_done=None,
            series=series,
        )

    # If target is configured, treat it like a soft threshold for status only
    status: ProgressStatus = "on_track"
    pct: Optional[float] = None

    if goal.target_value is not None and goal.target_direction in (">=", "<="):
        target = float(goal.target_value)
        direction = goal.target_direction
        meets = (latest >= target) if direction == ">=" else (latest <= target)
        status = "done" if meets else "on_track"
        # percent is optional; for measures it can be misleading, so only compute if meaningful
        if direction == ">=" and target != 0:
            pct = min(100.0, (latest / target) * 100.0)
        elif direction == "<=" and latest != 0:
            pct = min(100.0, (target / latest) * 100.0)

    return GoalProgress(
        goal_id=goal.id,
        slug=goal.slug,
        title=goal.title,
        metric_type=goal.metric_type,
        status=status,
        current_value=latest,
        target_value=goal.target_value,
        percent_complete=pct,
        cadence_unit=goal.cadence_unit,
        cadence_target=goal.cadence_target,
        window_start=None,
        window_end=None,
        window_count=None,
        milestones_total=None,
        milestones_done=None,
        series=series,
    )


def _progress_check(goal: Goal, checkins_asc: Sequence[Checkin], as_of: date) -> GoalProgress:
    # For CHECK goals, treat value_num==1 as "done event"
    done_dates = [c.date for c in checkins_asc if c.value_num == 1]

    # No cadence means binary completion: any done check-in => done
    if not goal.cadence_unit or not goal.cadence_target:
        status = "done" if done_dates else "no_data"
        series = [(d, 1.0) for d in done_dates]
        return GoalProgress(
            goal_id=goal.id,
            slug=goal.slug,
            title=goal.title,
            metric_type=goal.metric_type,
            status=status,
            current_value=float(len(done_dates)) if done_dates else None,
            target_value=None,
            percent_complete=None,
            cadence_unit=goal.cadence_unit,
            cadence_target=goal.cadence_target,
            window_start=None,
            window_end=None,
            window_count=None,
            milestones_total=None,
            milestones_done=None,
            series=series,
        )

    start, end = _window_for_cadence(goal.cadence_unit, as_of)
    window_count = sum(1 for d in done_dates if _in_window(d, start, end))
    target = float(goal.cadence_target)

    pct = min(100.0, (window_count / target) * 100.0) if target > 0 else None
    status: ProgressStatus
    if window_count == 0:
        status = "at_risk"
    else:
        status = "done" if window_count >= target else "on_track"

    # For sparklines, show daily presence as 1
    series = [(d, 1.0) for d in done_dates]

    return GoalProgress(
        goal_id=goal.id,
        slug=goal.slug,
        title=goal.title,
        metric_type=goal.metric_type,
        status=status,
        current_value=float(window_count),
        target_value=goal.cadence_target,
        percent_complete=pct,
        cadence_unit=goal.cadence_unit,
        cadence_target=goal.cadence_target,
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        window_count=window_count,
        milestones_total=None,
        milestones_done=None,
        series=series,
    )


def _progress_journal(goal: Goal, checkins_asc: Sequence[Checkin], as_of: date) -> GoalProgress:
    series = _numeric_series(checkins_asc)  # rating series if value_num exists
    latest = _latest_value(checkins_asc)

    # If cadence configured, judge based on entries in current window; otherwise, just "has any data"
    if goal.cadence_unit and goal.cadence_target:
        start, end = _window_for_cadence(goal.cadence_unit, as_of)
        window_count = sum(1 for c in checkins_asc if _in_window(c.date, start, end))
        target = float(goal.cadence_target)
        pct = min(100.0, (window_count / target) * 100.0) if target > 0 else None
        status: ProgressStatus = "done" if window_count >= target else ("at_risk" if window_count == 0 else "on_track")
        return GoalProgress(
            goal_id=goal.id,
            slug=goal.slug,
            title=goal.title,
            metric_type=goal.metric_type,
            status=status,
            current_value=latest,
            target_value=goal.cadence_target,
            percent_complete=pct,
            cadence_unit=goal.cadence_unit,
            cadence_target=goal.cadence_target,
            window_start=start.isoformat(),
            window_end=end.isoformat(),
            window_count=window_count,
            milestones_total=None,
            milestones_done=None,
            series=series,
        )

    # No cadence: status based on whether there is at least one entry
    status = "no_data" if not checkins_asc else "on_track"
    return GoalProgress(
        goal_id=goal.id,
        slug=goal.slug,
        title=goal.title,
        metric_type=goal.metric_type,
        status=status,
        current_value=latest,
        target_value=None,
        percent_complete=None,
        cadence_unit=goal.cadence_unit,
        cadence_target=goal.cadence_target,
        window_start=None,
        window_end=None,
        window_count=None,
        milestones_total=None,
        milestones_done=None,
        series=series,
    )


def _progress_milestone(goal: Goal, total: Optional[int], done: Optional[int]) -> GoalProgress:
    total_i = int(total or 0)
    done_i = int(done or 0)

    if total_i == 0:
        status: ProgressStatus = "no_data"
        pct = None
    else:
        pct = (done_i / total_i) * 100.0
        status = "done" if done_i >= total_i else ("at_risk" if done_i == 0 else "on_track")

    return GoalProgress(
        goal_id=goal.id,
        slug=goal.slug,
        title=goal.title,
        metric_type=goal.metric_type,
        status=status,
        current_value=float(done_i) if total_i else None,
        target_value=float(total_i) if total_i else None,
        percent_complete=pct,
        cadence_unit=None,
        cadence_target=None,
        window_start=None,
        window_end=None,
        window_count=None,
        milestones_total=total_i if total is not None else None,
        milestones_done=done_i if done is not None else None,
        series=[],
    )
