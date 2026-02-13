"""
Unit tests for ingestion budget repository.

Tests the reserve/finalize budget system that prevents over-ingestion.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.models.content import ContentType
from app.models.ingestion_budget import IngestionBudget
from app.repositories.ingestion_budget_repo import IngestionBudgetRepository

pytestmark = [pytest.mark.unit]


class TestIngestionBudgetRemaining:
    """Test remaining capacity calculation."""
    
    def test_remaining_with_capacity(self):
        """Returns target minus inserted minus reserved."""
        mock_db = MagicMock()
        budget = IngestionBudget(
            day=date.today(),
            content_type=ContentType.ARTICLE,
            target=100,
            inserted=30,
            reserved=10,
        )
        mock_db.query.return_value.filter.return_value.one_or_none.return_value = budget
        
        repo = IngestionBudgetRepository(mock_db)
        
        remaining = repo.remaining(day=date.today(), content_type=ContentType.ARTICLE)
        
        assert remaining == 60  # 100 - 30 - 10
    
    def test_remaining_exhausted(self):
        """Returns 0 when budget exhausted."""
        mock_db = MagicMock()
        budget = IngestionBudget(
            day=date.today(),
            content_type=ContentType.ARTICLE,
            target=100,
            inserted=90,
            reserved=10,
        )
        mock_db.query.return_value.filter.return_value.one_or_none.return_value = budget
        
        repo = IngestionBudgetRepository(mock_db)
        
        remaining = repo.remaining(day=date.today(), content_type=ContentType.ARTICLE)
        
        assert remaining == 0
    
    def test_remaining_over_budget_returns_zero(self):
        """Never returns negative even if over budget."""
        mock_db = MagicMock()
        budget = IngestionBudget(
            day=date.today(),
            content_type=ContentType.ARTICLE,
            target=100,
            inserted=110,  # Over target!
            reserved=5,
        )
        mock_db.query.return_value.filter.return_value.one_or_none.return_value = budget
        
        repo = IngestionBudgetRepository(mock_db)
        
        remaining = repo.remaining(day=date.today(), content_type=ContentType.ARTICLE)
        
        assert remaining == 0  # Not a negative number
    
    def test_remaining_no_budget_returns_zero(self):
        """Returns 0 when no budget exists."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.one_or_none.return_value = None
        
        repo = IngestionBudgetRepository(mock_db)
        
        remaining = repo.remaining(day=date.today(), content_type=ContentType.VIDEO)
        
        assert remaining == 0


class TestIngestionBudgetReserve:
    """Test reservation system."""
    
    def test_reserve_partial(self):
        """Reserves requested amount when capacity available."""
        mock_db = MagicMock()
        budget = IngestionBudget(
            day=date.today(),
            content_type=ContentType.VIDEO,
            target=50,
            inserted=10,
            reserved=5,
        )
        mock_db.query.return_value.filter.return_value.with_for_update.return_value.one_or_none.return_value = budget
        
        repo = IngestionBudgetRepository(mock_db)
        
        reserved = repo.reserve(day=date.today(), content_type=ContentType.VIDEO, want=10)
        
        assert reserved == 10
        assert budget.reserved == 15  # was 5, now 15
        mock_db.commit.assert_called()
    
    def test_reserve_capped(self):
        """Returns only what's available when requested exceeds capacity."""
        mock_db = MagicMock()
        budget = IngestionBudget(
            day=date.today(),
            content_type=ContentType.VIDEO,
            target=50,
            inserted=40,
            reserved=5,
        )
        mock_db.query.return_value.filter.return_value.with_for_update.return_value.one_or_none.return_value = budget
        
        repo = IngestionBudgetRepository(mock_db)
        
        reserved = repo.reserve(day=date.today(), content_type=ContentType.VIDEO, want=20)
        
        assert reserved == 5  # Only 5 remaining (50 - 40 - 5)
        assert budget.reserved == 10  # was 5, added 5
    
    def test_reserve_zero_when_exhausted(self):
        """Returns 0 when budget exhausted."""
        mock_db = MagicMock()
        budget = IngestionBudget(
            day=date.today(),
            content_type=ContentType.ARTICLE,
            target=100,
            inserted=90,
            reserved=10,
        )
        mock_db.query.return_value.filter.return_value.with_for_update.return_value.one_or_none.return_value = budget
        
        repo = IngestionBudgetRepository(mock_db)
        
        reserved = repo.reserve(day=date.today(), content_type=ContentType.ARTICLE, want=10)
        
        assert reserved == 0
        assert budget.reserved == 10  # Unchanged


class TestIngestionBudgetFinalize:
    """Test finalization of batch processing."""
    
    def test_finalize_batch_updates_counters(self):
        """Finalize decrements reserved and increments inserted."""
        mock_db = MagicMock()
        budget = IngestionBudget(
            day=date.today(),
            content_type=ContentType.ARTICLE,
            target=100,
            inserted=20,
            reserved=10,
            seen=50,
            suppressed=5,
            attempts=60,
        )
        mock_db.query.return_value.filter.return_value.with_for_update.return_value.one_or_none.return_value = budget
        
        repo = IngestionBudgetRepository(mock_db)
        
        repo.finalize_batch(
            day=date.today(),
            content_type=ContentType.ARTICLE,
            reserved_taken=10,
            inserted=8,
            seen=20,
            suppressed=2,
            attempts=25,
        )
        
        assert budget.reserved == 0  # Was 10, released 10
        assert budget.inserted == 28  # Was 20, added 8
        assert budget.seen == 70  # Was 50, added 20
        assert budget.suppressed == 7  # Was 5, added 2
        mock_db.commit.assert_called()
