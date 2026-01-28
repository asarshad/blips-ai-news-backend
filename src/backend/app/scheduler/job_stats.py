"""
Job statistics tracking for scheduled tasks.

Provides consistent logging and metrics for all background jobs.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class JobStats:
    """Statistics for a job run."""
    job_name: str
    started_at: datetime = field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    items_processed: int = 0
    items_skipped: int = 0
    items_failed: int = 0
    llm_calls: int = 0
    errors: list = field(default_factory=list)
    
    def complete(self):
        """Mark job as complete."""
        self.ended_at = datetime.utcnow()
    
    @property
    def duration_seconds(self) -> float:
        """Get job duration in seconds."""
        end = self.ended_at or datetime.utcnow()
        return (end - self.started_at).total_seconds()
    
    def log_summary(self):
        """Log job summary."""
        status = "SUCCESS" if not self.errors else f"COMPLETED_WITH_ERRORS ({len(self.errors)})"
        logger.info(
            f"[{self.job_name}] {status} | "
            f"duration={self.duration_seconds:.1f}s | "
            f"processed={self.items_processed} | "
            f"skipped={self.items_skipped} | "
            f"failed={self.items_failed} | "
            f"llm_calls={self.llm_calls}"
        )
        if self.errors:
            for err in self.errors[:5]:  # Log first 5 errors
                logger.error(f"[{self.job_name}] Error: {err}")
            if len(self.errors) > 5:
                logger.error(f"[{self.job_name}] ... and {len(self.errors) - 5} more errors")


def log_job_start(job_name: str) -> JobStats:
    """Log job start and return stats tracker."""
    logger.info(f"[{job_name}] STARTING")
    return JobStats(job_name=job_name)
