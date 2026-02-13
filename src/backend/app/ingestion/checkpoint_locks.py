"""Locking helpers for checkpointed ingestion."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


def try_pg_advisory_lock(db: Session, key: str) -> bool:
    try:
        # Use hashtext(key) for a stable 32-bit key in Postgres.
        result = db.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": key}).scalar()
        return bool(result)
    except SQLAlchemyError:
        return False


def pg_advisory_unlock(db: Session, key: str) -> None:
    try:
        db.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": key})
        db.commit()
    except SQLAlchemyError:
        db.rollback()
