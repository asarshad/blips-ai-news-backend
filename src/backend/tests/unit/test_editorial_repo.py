from types import SimpleNamespace
from unittest.mock import MagicMock

from app.repositories.editorial_repo import EditorialRepository


def _make_query_chain(session: MagicMock) -> MagicMock:
    query = MagicMock()
    session.query.return_value = query
    query.filter.return_value = query
    query.group_by.return_value = query
    query.order_by.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    query.count.return_value = 0
    query.all.return_value = []
    return query


def test_list_candidate_queue_defaults_to_candidate_status_filter():
    session = MagicMock()
    repo = EditorialRepository(session)
    query = _make_query_chain(session)

    repo.list_candidate_queue(include_suppressed=True)

    assert query.filter.call_count == 1
    expr = query.filter.call_args_list[0].args[0]
    assert "curation_status" in str(expr)


def test_list_candidate_queue_all_status_skips_status_filter():
    session = MagicMock()
    repo = EditorialRepository(session)
    query = _make_query_chain(session)

    repo.list_candidate_queue(curation_status=None, include_suppressed=True)

    assert query.filter.call_count == 0


def test_list_candidate_queue_priority_sorts_reviewable_items_before_blocked():
    session = MagicMock()
    repo = EditorialRepository(session)
    query = _make_query_chain(session)

    repo.list_candidate_queue(include_suppressed=True, sort_by="priority")

    assert query.order_by.called
    blocked_expr = query.order_by.call_args.args[0]
    rendered = str(blocked_expr).lower()
    assert "promotion_reason" in rendered
    assert "like" in rendered
    assert "asc" in rendered


def test_candidate_queue_counts_can_include_all_statuses():
    session = MagicMock()
    repo = EditorialRepository(session)
    query = _make_query_chain(session)
    query.all.return_value = [
        (SimpleNamespace(value="ARTICLE"), 2),
        (SimpleNamespace(value="VIDEO"), 1),
    ]

    counts = repo.candidate_queue_counts(curation_status=None, include_suppressed=True)

    assert counts == {"ARTICLE": 2, "VIDEO": 1}
    assert query.filter.call_count == 0


def test_list_content_has_image_true_filters_non_blank_image_urls():
    session = MagicMock()
    repo = EditorialRepository(session)
    query = _make_query_chain(session)

    repo.list_content(has_image=True, page=1, page_size=50)

    assert query.filter.call_count == 1
    expr = query.filter.call_args_list[0].args[0]
    rendered = str(expr)
    assert "image_url" in rendered
    assert "length" in rendered.lower()
    assert "> :length_" in rendered or "> :param_" in rendered


def test_list_content_has_image_false_filters_blank_or_null_image_urls():
    session = MagicMock()
    repo = EditorialRepository(session)
    query = _make_query_chain(session)

    repo.list_content(has_image=False, page=1, page_size=50)

    assert query.filter.call_count == 1
    expr = query.filter.call_args_list[0].args[0]
    rendered = str(expr)
    assert "image_url" in rendered
    assert "length" in rendered.lower()
    assert "= :length_" in rendered or "= :param_" in rendered


def test_list_content_search_text_filters_title_summary_and_url():
    session = MagicMock()
    repo = EditorialRepository(session)
    query = _make_query_chain(session)

    repo.list_content(search_text="openai", page=1, page_size=50)

    assert query.filter.call_count == 1
    rendered = str(query.filter.call_args_list[0].args[0]).lower()
    assert "title" in rendered
    assert "summary" in rendered
    assert "description" in rendered
    assert "source_url" in rendered
