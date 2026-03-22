from types import SimpleNamespace
from unittest.mock import MagicMock

from app.repositories.content_repo import ContentItemRepository


def _make_query_chain(session: MagicMock) -> MagicMock:
    query = MagicMock()
    session.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    return query


def test_get_articles_with_short_summaries_filters_out_long_and_blank_summaries():
    session = MagicMock()
    repo = ContentItemRepository(session)
    query = _make_query_chain(session)

    short_item = SimpleNamespace(summary="One two three four")
    long_item = SimpleNamespace(summary=" ".join(f"word{i}" for i in range(12)))
    blank_item = SimpleNamespace(summary="   ")
    another_short = SimpleNamespace(summary="Only five words here now")
    query.all.return_value = [short_item, long_item, blank_item, another_short]

    result = repo.get_articles_with_short_summaries(limit=2, max_words=10)

    assert result == [short_item, another_short]
    query.limit.assert_called_once_with(10)


def test_get_articles_with_short_summaries_respects_requested_limit():
    session = MagicMock()
    repo = ContentItemRepository(session)
    query = _make_query_chain(session)

    query.all.return_value = [
        SimpleNamespace(summary="short summary words"),
        SimpleNamespace(summary="another short summary"),
        SimpleNamespace(summary="third short summary"),
    ]

    result = repo.get_articles_with_short_summaries(limit=2, max_words=10)

    assert len(result) == 2
