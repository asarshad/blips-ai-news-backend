"""Data quality reporting.

Goal: expose deterministic, DB-derived signals about ingestion quality.
No network calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.content import ContentItem, ContentType
from app.models.ingestion_budget import IngestionBudget


@dataclass(frozen=True)
class TypeQualityStats:
    content_type: str
    inserted: int
    suppressed: int
    total: int
    top_source: Optional[str]
    top_source_share: float


def build_quality_report(db: Session, *, day: date) -> Dict[str, object]:
    """Build a quality report for a single ingestion day."""

    budgets = (
        db.query(IngestionBudget)
        .filter(IngestionBudget.day == day)
        .order_by(IngestionBudget.content_type)
        .all()
    )

    budget_rows: List[Dict[str, object]] = []
    for b in budgets:
        target = int(b.target or 0)
        reserved = int(b.reserved or 0)
        inserted = int(b.inserted or 0)
        budget_rows.append(
            {
                "day": b.day.isoformat(),
                "content_type": b.content_type.value,
                "target": target,
                "reserved": reserved,
                "inserted": inserted,
                "remaining": max(0, target - reserved - inserted),
                "seen": int(b.seen or 0),
                "suppressed": int(b.suppressed or 0),
                "attempts": int(b.attempts or 0),
                "updated_at": b.updated_at.isoformat() if b.updated_at else None,
            }
        )

    # Per-type inserted/suppressed counts from content_items.
    per_type = (
        db.query(
            ContentItem.type.label("type"),
            func.count(ContentItem.id).label("total"),
            func.sum(case((ContentItem.is_suppressed.is_(True), 1), else_=0)).label("suppressed"),
        )
        .filter(ContentItem.ingestion_day == day)
        .group_by(ContentItem.type)
        .all()
    )

    totals_by_type = {
        row.type: {"total": int(row.total or 0), "suppressed": int(row.suppressed or 0)}
        for row in per_type
    }

    type_stats: List[Dict[str, object]] = []

    for ct in (ContentType.ARTICLE, ContentType.VIDEO, ContentType.REEL):
        total = int((totals_by_type.get(ct) or {}).get("total") or 0)
        suppressed = int((totals_by_type.get(ct) or {}).get("suppressed") or 0)
        inserted = max(0, total - suppressed)

        top = (
            db.query(ContentItem.source, func.count(ContentItem.id).label("c"))
            .filter(ContentItem.ingestion_day == day)
            .filter(ContentItem.type == ct)
            .filter(ContentItem.is_suppressed.is_(False))
            .group_by(ContentItem.source)
            .order_by(func.count(ContentItem.id).desc())
            .limit(1)
            .one_or_none()
        )

        top_source = top[0] if top else None
        top_count = int(top[1] or 0) if top else 0
        top_share = float(top_count) / float(inserted) if inserted > 0 else 0.0

        type_stats.append(
            {
                "content_type": ct.value,
                "inserted": inserted,
                "suppressed": suppressed,
                "total": total,
                "top_source": top_source,
                "top_source_share": round(top_share, 4),
            }
        )

    # Article extraction coverage (non-null content_text).
    article_total = (
        db.query(func.count(ContentItem.id))
        .filter(ContentItem.ingestion_day == day)
        .filter(ContentItem.type == ContentType.ARTICLE)
        .filter(ContentItem.is_suppressed.is_(False))
        .scalar()
        or 0
    )
    article_with_text = (
        db.query(func.count(ContentItem.id))
        .filter(ContentItem.ingestion_day == day)
        .filter(ContentItem.type == ContentType.ARTICLE)
        .filter(ContentItem.is_suppressed.is_(False))
        .filter(ContentItem.content_text.isnot(None))
        .scalar()
        or 0
    )

    extraction = {
        "article_total": int(article_total),
        "article_with_text": int(article_with_text),
        "article_text_coverage": round(float(article_with_text) / float(article_total), 4)
        if article_total
        else 0.0,
    }

    warnings: List[str] = []

    # Simple heuristics (tunable later).
    for ts in type_stats:
        if ts["top_source_share"] >= 0.6 and ts["inserted"] >= 10:
            warnings.append(
                f"source_dominance:{ts['content_type']} top_source_share={ts['top_source_share']} top_source={ts['top_source']}"
            )

    if extraction["article_total"] >= 10 and extraction["article_text_coverage"] < 0.3:
        warnings.append(f"low_article_text_coverage:{extraction['article_text_coverage']}")

    return {
        "day": day.isoformat(),
        "budgets": budget_rows,
        "types": type_stats,
        "extraction": extraction,
        "warnings": warnings,
    }
