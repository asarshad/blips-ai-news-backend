from app.api.admin import ui as admin_ui


class _FakeRepo:
    last_list_args = None
    last_count_args = None

    def __init__(self, db):
        self.db = db

    def list_candidate_queue(self, **kwargs):
        _FakeRepo.last_list_args = kwargs
        return [], 0

    def candidate_queue_counts(self, **kwargs):
        _FakeRepo.last_count_args = kwargs
        return {}

    def get_content_by_id(self, _content_id):
        return None


def test_review_queue_promoted_scope_passes_filters(monkeypatch):
    _FakeRepo.last_list_args = None
    _FakeRepo.last_count_args = None
    monkeypatch.setattr(admin_ui, "EditorialRepository", _FakeRepo)

    response = admin_ui.ui_review_queue(
        review_status="PROMOTED",
        include_suppressed=True,
        type=None,
        source=None,
        discovered_via=None,
        min_signal_hits=0,
        start_day=None,
        end_day=None,
        sort_by="priority",
        selected_id=None,
        page=1,
        flash=None,
        db=object(),
        admin_key="secret",
    )

    assert _FakeRepo.last_list_args is not None
    assert _FakeRepo.last_list_args["curation_status"] == "PROMOTED"
    assert _FakeRepo.last_list_args["include_suppressed"] is True
    assert _FakeRepo.last_count_args["curation_status"] == "PROMOTED"
    assert _FakeRepo.last_count_args["include_suppressed"] is True

    html = response.body.decode("utf-8")
    assert 'name="review_status"' in html
    assert "Promoted" in html
    assert 'name="include_suppressed"' in html


def test_review_queue_all_scope_maps_to_no_status_filter(monkeypatch):
    _FakeRepo.last_list_args = None
    _FakeRepo.last_count_args = None
    monkeypatch.setattr(admin_ui, "EditorialRepository", _FakeRepo)

    response = admin_ui.ui_review_queue(
        review_status="ALL",
        include_suppressed=False,
        type=None,
        source=None,
        discovered_via=None,
        min_signal_hits=0,
        start_day=None,
        end_day=None,
        sort_by="priority",
        selected_id=None,
        page=1,
        flash=None,
        db=object(),
        admin_key="secret",
    )

    assert _FakeRepo.last_list_args is not None
    assert _FakeRepo.last_list_args["curation_status"] is None
    assert _FakeRepo.last_list_args["include_suppressed"] is False
    assert _FakeRepo.last_count_args["curation_status"] is None
    assert _FakeRepo.last_count_args["include_suppressed"] is False

    html = response.body.decode("utf-8")
    assert "All statuses" in html
