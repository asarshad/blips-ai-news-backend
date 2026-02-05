from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class QualityBudgetRow(BaseModel):
    day: str
    content_type: str
    target: int
    reserved: int
    inserted: int
    remaining: int
    seen: int
    suppressed: int
    attempts: int
    updated_at: Optional[str] = None


class QualityTypeRow(BaseModel):
    content_type: str
    inserted: int
    suppressed: int
    total: int
    top_source: Optional[str] = None
    top_source_share: float


class QualityExtractionRow(BaseModel):
    article_total: int
    article_with_text: int
    article_text_coverage: float


class QualityReport(BaseModel):
    day: str
    budgets: List[QualityBudgetRow]
    types: List[QualityTypeRow]
    extraction: QualityExtractionRow
    warnings: List[str]

    # Allow forward-compatible fields without breaking clients.
    model_config = {"extra": "allow"}
