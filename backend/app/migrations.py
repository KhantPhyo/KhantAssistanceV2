"""Lightweight column migrations for SQLite. `create_all` makes new tables but
does NOT add new columns to existing tables — these helpers cover that gap so
users don't have to drop their DB on every model addition."""
import logging
from sqlalchemy import inspect, text

log = logging.getLogger("migrations")


def _existing_cols(insp, table) -> set[str]:
    try:
        return {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return set()


def migrate(engine) -> None:
    insp = inspect(engine)
    with engine.begin() as conn:
        # jobs.accept_mode (introduced for any/all accept-quorum logic)
        cols = _existing_cols(insp, "jobs")
        if cols and "accept_mode" not in cols:
            log.info("ALTER TABLE jobs ADD COLUMN accept_mode")
            conn.exec_driver_sql(
                "ALTER TABLE jobs ADD COLUMN accept_mode VARCHAR(8) DEFAULT 'any' NOT NULL"
            )
            # Backfill existing rows defensively (SQLite default sometimes lazy)
            conn.exec_driver_sql(
                "UPDATE jobs SET accept_mode='any' WHERE accept_mode IS NULL OR accept_mode=''"
            )
