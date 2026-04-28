"""AI usage metrics for admin dashboards."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.ai_usage import AIUsage


def _round_money(value: float | None) -> float:
    return round(float(value or 0.0), 6)


def _row_metrics(row: Any) -> dict[str, Any]:
    calls = int(getattr(row, "calls", 0) or 0)
    successes = int(getattr(row, "successes", 0) or 0)
    failures = int(getattr(row, "failures", 0) or 0)
    return {
        "calls": calls,
        "successes": successes,
        "failures": failures,
        "success_rate_pct": round(successes / max(calls, 1) * 100, 1),
        "input_tokens": int(getattr(row, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(row, "output_tokens", 0) or 0),
        "total_tokens": int(getattr(row, "total_tokens", 0) or 0),
        "estimated_cost_usd": _round_money(getattr(row, "estimated_cost_usd", 0.0)),
    }


def compute_ai_usage_metrics(db: Session, *, days: int = 7) -> dict[str, Any]:
    """Return model/use-case/token/cost breakdowns for recent LLM usage."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(days=days)
    base_filters = [AIUsage.created_at >= cutoff]

    totals_row = (
        db.query(
            func.count(AIUsage.id).label("calls"),
            func.sum(case((AIUsage.status == "success", 1), else_=0)).label("successes"),
            func.sum(case((AIUsage.status != "success", 1), else_=0)).label("failures"),
            func.sum(AIUsage.input_tokens).label("input_tokens"),
            func.sum(AIUsage.output_tokens).label("output_tokens"),
            func.sum(AIUsage.total_tokens).label("total_tokens"),
            func.sum(AIUsage.estimated_cost_usd).label("estimated_cost_usd"),
        )
        .filter(*base_filters)
        .one()
    )

    def grouped(*columns: Any) -> list[dict[str, Any]]:
        rows = (
            db.query(
                *columns,
                func.count(AIUsage.id).label("calls"),
                func.sum(case((AIUsage.status == "success", 1), else_=0)).label("successes"),
                func.sum(case((AIUsage.status != "success", 1), else_=0)).label("failures"),
                func.sum(AIUsage.input_tokens).label("input_tokens"),
                func.sum(AIUsage.output_tokens).label("output_tokens"),
                func.sum(AIUsage.total_tokens).label("total_tokens"),
                func.sum(AIUsage.estimated_cost_usd).label("estimated_cost_usd"),
            )
            .filter(*base_filters)
            .group_by(*columns)
            .order_by(func.sum(AIUsage.estimated_cost_usd).desc().nullslast(), func.count(AIUsage.id).desc())
            .all()
        )
        output = []
        for row in rows:
            data = _row_metrics(row)
            for index, column in enumerate(columns):
                key = getattr(column, "key", None) or getattr(column, "name", None) or f"group_{index}"
                data[key] = row[index]
            output.append(data)
        return output

    daily_rows = (
        db.query(
            func.date(AIUsage.created_at).label("day"),
            func.count(AIUsage.id).label("calls"),
            func.sum(case((AIUsage.status == "success", 1), else_=0)).label("successes"),
            func.sum(case((AIUsage.status != "success", 1), else_=0)).label("failures"),
            func.sum(AIUsage.input_tokens).label("input_tokens"),
            func.sum(AIUsage.output_tokens).label("output_tokens"),
            func.sum(AIUsage.total_tokens).label("total_tokens"),
            func.sum(AIUsage.estimated_cost_usd).label("estimated_cost_usd"),
        )
        .filter(*base_filters)
        .group_by(func.date(AIUsage.created_at))
        .order_by(func.date(AIUsage.created_at).desc())
        .all()
    )
    daily = [
        {
            "day": row.day.isoformat() if hasattr(row.day, "isoformat") else str(row.day),
            **_row_metrics(row),
        }
        for row in daily_rows
    ]

    recent_errors = (
        db.query(AIUsage)
        .filter(*base_filters, AIUsage.status != "success")
        .order_by(AIUsage.created_at.desc())
        .limit(25)
        .all()
    )

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "totals": _row_metrics(totals_row),
        "by_model": grouped(AIUsage.provider, AIUsage.model),
        "by_use_case": grouped(AIUsage.use_case),
        "by_status": grouped(AIUsage.status),
        "daily": daily,
        "recent_errors": [
            {
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "provider": row.provider,
                "model": row.model,
                "use_case": row.use_case,
                "error_code": row.error_code,
                "error_message": (row.error_message or "")[:300],
            }
            for row in recent_errors
        ],
    }
