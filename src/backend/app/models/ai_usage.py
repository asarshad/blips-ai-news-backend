"""Persistent LLM usage telemetry for operations dashboards."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text

from app.db.base import Base


class AIUsage(Base):
    """One row per LLM call attempt, successful or failed."""

    __tablename__ = "ai_usage"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(32), nullable=False, index=True)
    model = Column(String(128), nullable=False, index=True)
    use_case = Column(String(96), nullable=False, index=True)
    status = Column(String(32), nullable=False, index=True)
    input_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    output_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    total_tokens = Column(Integer, nullable=False, default=0, server_default="0")
    estimated_cost_usd = Column(Float, nullable=False, default=0.0, server_default="0")
    response_id = Column(String(128), nullable=True)
    error_code = Column(String(128), nullable=True, index=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("ix_ai_usage_created_use_case", "created_at", "use_case"),
        Index("ix_ai_usage_created_model", "created_at", "model"),
    )
