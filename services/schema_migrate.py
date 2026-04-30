#!/usr/bin/env python3
"""数据库模式迁移：为老库追加 SM-2 列 + 新表，保持兼容。"""
from sqlalchemy import text

from database import engine
from models import Base


SQLITE_NEW_VOCAB_COLUMNS = [
    ("ease_factor", "FLOAT NOT NULL DEFAULT 2.5"),
    ("interval_days", "INTEGER NOT NULL DEFAULT 0"),
    ("repetitions", "INTEGER NOT NULL DEFAULT 0"),
    ("next_review_at", "DATETIME"),
    ("lapses", "INTEGER NOT NULL DEFAULT 0"),
]

SQLITE_NEW_ESSAY_COLUMNS = [
    ("task_type", "VARCHAR(40) NOT NULL DEFAULT 'independent'"),
    ("prompt_text", "TEXT NOT NULL DEFAULT ''"),
    ("holistic_score", "FLOAT NOT NULL DEFAULT 0"),
]


def _existing_columns(conn, table: str) -> set[str]:
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _table_exists(conn, table: str) -> bool:
    rows = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchall()
    return bool(rows)


def ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        existing = _existing_columns(conn, "vocabulary")
        for column_name, column_def in SQLITE_NEW_VOCAB_COLUMNS:
            if column_name not in existing:
                conn.exec_driver_sql(
                    f"ALTER TABLE vocabulary ADD COLUMN {column_name} {column_def}"
                )

        if _table_exists(conn, "essay_scores"):
            essay_cols = _existing_columns(conn, "essay_scores")
            for column_name, column_def in SQLITE_NEW_ESSAY_COLUMNS:
                if column_name not in essay_cols:
                    conn.exec_driver_sql(
                        f"ALTER TABLE essay_scores ADD COLUMN {column_name} {column_def}"
                    )


if __name__ == "__main__":
    ensure_schema()
    print("Schema ensured.")
