"""
storage/db.py

SQLite persistence layer for goals_tracker_2026.

Design goals:
- SQLite is file-based (no background service).
- This module owns DB creation, schema initialization, and minimal helpers.
- No GUI imports, no site-builder imports.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

# -------------------------
# Configuration
# -------------------------

DEFAULT_DB_FILENAME = "goals_tracker_2026.db"
DEFAULT_DATA_DIRNAME = "data"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def project_root() -> Path:
    """
    Resolves the project root as the parent of the 'storage' package directory.
    storage/db.py -> storage/ -> project root
    """
    return Path(__file__).resolve().parent.parent


def default_db_path() -> Path:
    return project_root() / DEFAULT_DATA_DIRNAME / DEFAULT_DB_FILENAME


# -------------------------
# Schema (v1)
# -------------------------

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

-- Configuration tables
CREATE TABLE IF NOT EXISTS owners (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL UNIQUE,
  active        INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL UNIQUE,
  sort_order    INTEGER NOT NULL DEFAULT 0,
  active        INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT NOT NULL
);

-- User data tables
CREATE TABLE IF NOT EXISTS goals (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  slug             TEXT NOT NULL UNIQUE,
  title            TEXT NOT NULL,
  category_id      INTEGER NOT NULL,
  owner_id         INTEGER NOT NULL,

  metric_type      TEXT NOT NULL,           -- CHECK | MEASURE | TARGET_CUMULATIVE | TARGET_THRESHOLD | MILESTONE | JOURNAL
  unit             TEXT,                    -- e.g., %, lb, $, books, sessions, km, seconds
  target_value     REAL,                    -- nullable
  target_direction TEXT,                    -- nullable: '>=' or '<=' for threshold-style comparisons

  cadence_unit     TEXT,                    -- nullable: daily | weekly | monthly
  cadence_target   REAL,                    -- nullable: e.g., 5 per week

  start_date       TEXT,                    -- YYYY-MM-DD (nullable)
  end_date         TEXT,                    -- YYYY-MM-DD (nullable)

  publish_notes    INTEGER NOT NULL DEFAULT 0,
  status           TEXT NOT NULL DEFAULT 'active',  -- active | archived

  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,

  FOREIGN KEY(category_id) REFERENCES categories(id),
  FOREIGN KEY(owner_id) REFERENCES owners(id)
);

CREATE INDEX IF NOT EXISTS idx_goals_owner_id ON goals(owner_id);
CREATE INDEX IF NOT EXISTS idx_goals_category_id ON goals(category_id);
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);

CREATE TABLE IF NOT EXISTS checkins (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  goal_id              INTEGER NOT NULL,
  date                TEXT NOT NULL,         -- YYYY-MM-DD
  value_num           REAL,                  -- nullable
  value_text          TEXT,                  -- nullable (race name, etc.)
  note                TEXT,                  -- nullable
  created_by_owner_id INTEGER NOT NULL,
  created_at          TEXT NOT NULL,

  FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE CASCADE,
  FOREIGN KEY(created_by_owner_id) REFERENCES owners(id)
);

CREATE INDEX IF NOT EXISTS idx_checkins_goal_id_date ON checkins(goal_id, date);
CREATE INDEX IF NOT EXISTS idx_checkins_date ON checkins(date);

CREATE TABLE IF NOT EXISTS milestones (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  goal_id     INTEGER NOT NULL,
  title       TEXT NOT NULL,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  is_done     INTEGER NOT NULL DEFAULT 0,
  done_date   TEXT,                           -- YYYY-MM-DD nullable
  created_at  TEXT NOT NULL,

  FOREIGN KEY(goal_id) REFERENCES goals(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_milestones_goal_id ON milestones(goal_id);
"""


# -------------------------
# Defaults (seeded once)
# -------------------------

DEFAULT_OWNERS = ["Josh", "Rutendo", "Both"]

DEFAULT_CATEGORIES = [
    ("Spiritual", 10),
    ("Upskilling", 20),
    ("Fitness", 30),
    ("Financial", 40),
    ("Habits", 50),
    ("Family & Friends", 60),
]


# -------------------------
# Exceptions
# -------------------------

class DbError(RuntimeError):
    pass


# -------------------------
# Public API
# -------------------------

@dataclass(frozen=True)
class DbInfo:
    path: Path
    exists: bool


def init_db(db_path: Optional[Path] = None) -> DbInfo:
    """
    Initialize the database:
    - Ensure data directory exists
    - Create tables if missing
    - Seed owners/categories if empty

    Safe to call multiple times.
    """
    path = db_path or default_db_path()
    existed_before = path.exists()

    # Ensure directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    with connect(path) as conn:
        _apply_pragmas(conn)
        _apply_schema(conn)
        _seed_defaults_if_needed(conn)

    return DbInfo(path=path, exists=existed_before)


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """
    Open a SQLite connection with sane defaults.
    Caller is responsible for closing (use 'with connect(...) as conn:').
    """
    path = db_path or default_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def health_check(db_path: Optional[Path] = None) -> dict:
    """
    Basic verification that DB is reachable and schema exists.
    Returns a small dict suitable for logging.
    """
    path = db_path or default_db_path()
    with connect(path) as conn:
        _apply_pragmas(conn)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        tables = [r["name"] for r in cur.fetchall()]
        cur.execute("SELECT COUNT(*) AS n FROM owners;")
        owners_n = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM categories;")
        categories_n = cur.fetchone()["n"]

    return {
        "db_path": str(path),
        "tables": tables,
        "owners_count": owners_n,
        "categories_count": categories_n,
    }


# -------------------------
# Internal helpers
# -------------------------

def _apply_pragmas(conn: sqlite3.Connection) -> None:
    # Enforce foreign keys per-connection in SQLite
    conn.execute("PRAGMA foreign_keys = ON;")
    # Improve concurrency / durability balance for a desktop app
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")


def _apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def _seed_defaults_if_needed(conn: sqlite3.Connection) -> None:
    now = utc_now_iso()
    cur = conn.cursor()

    # Owners
    cur.execute("SELECT COUNT(*) AS n FROM owners;")
    if cur.fetchone()["n"] == 0:
        cur.executemany(
            "INSERT INTO owners (name, active, created_at) VALUES (?, 1, ?);",
            [(name, now) for name in DEFAULT_OWNERS],
        )

    # Categories
    cur.execute("SELECT COUNT(*) AS n FROM categories;")
    if cur.fetchone()["n"] == 0:
        cur.executemany(
            "INSERT INTO categories (name, sort_order, active, created_at) VALUES (?, ?, 1, ?);",
            [(name, sort_order, now) for (name, sort_order) in DEFAULT_CATEGORIES],
        )

    conn.commit()
