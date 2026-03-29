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


class _FakeContentRepo:
    last_list_args = None

    def __init__(self, db):
        self.db = db

    def list_content(self, **kwargs):
        _FakeContentRepo.last_list_args = kwargs
        return [], 0


class _FakeBulkRepo:
    hold_calls = []
    reject_calls = []

    def __init__(self, db):
        self.db = db

    @classmethod
    def reset(cls):
        cls.hold_calls = []
        cls.reject_calls = []

    def hold(self, content_id, *, actor, note=None):
        self.__class__.hold_calls.append((content_id, actor, note))
        return {"id": content_id}

    def reject(self, content_id, *, actor, note=None):
        self.__class__.reject_calls.append((content_id, actor, note))
        return {"id": content_id}


class _FakeBulkService:
    approve_calls = []
    approve_publish_calls = []
    dispatch_calls = 0

    def __init__(self, db, repo=None):
        self.db = db
        self.repo = repo

    @classmethod
    def reset(cls):
        cls.approve_calls = []
        cls.approve_publish_calls = []
        cls.dispatch_calls = 0

    def approve_content(self, content_id, *, actor="admin", note=None, dispatch_events=True):
        self.__class__.approve_calls.append((content_id, actor, note, dispatch_events))
        return {"id": content_id}

    def approve_and_publish(
        self,
        content_id,
        *,
        actor="admin",
        boost_level=3,
        note=None,
        dispatch_events=True,
    ):
        self.__class__.approve_publish_calls.append(
            (content_id, actor, boost_level, note, dispatch_events)
        )
        return {"id": content_id}

    def dispatch_content_events_best_effort(self):
        self.__class__.dispatch_calls += 1


class _BlockedApprovalService(_FakeBulkService):
    def promote_content(self, content_id, *, actor="admin", dispatch_events=True):
        raise admin_ui.EditorialApprovalBlockedError(
            content_id=content_id,
            readiness_reason="missing_article_summary",
        )

    def approve_content(self, content_id, *, actor="admin", note=None, dispatch_events=True):
        raise admin_ui.EditorialApprovalBlockedError(
            content_id=content_id,
            readiness_reason="missing_article_summary",
        )

    def approve_and_publish(
        self,
        content_id,
        *,
        actor="admin",
        boost_level=3,
        note=None,
        dispatch_events=True,
    ):
        raise admin_ui.EditorialApprovalBlockedError(
            content_id=content_id,
            readiness_reason="missing_article_summary",
        )


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


def test_content_list_maps_missing_image_filter(monkeypatch):
    _FakeContentRepo.last_list_args = None
    monkeypatch.setattr(admin_ui, "EditorialRepository", _FakeContentRepo)

    response = admin_ui.ui_content_list(
        day=None,
        type="ARTICLE",
        q="openai",
        source=None,
        suppressed=None,
        manual_added=None,
        has_image="false",
        curation_status="PROMOTED",
        sort_by="published_at",
        page=2,
        flash=None,
        db=object(),
        admin_key="secret",
    )

    assert _FakeContentRepo.last_list_args is not None
    assert _FakeContentRepo.last_list_args["content_type"] == "ARTICLE"
    assert _FakeContentRepo.last_list_args["search_text"] == "openai"
    assert _FakeContentRepo.last_list_args["has_image"] is False
    assert _FakeContentRepo.last_list_args["curation_status"] == "PROMOTED"

    html = response.body.decode("utf-8")
    assert 'name="q"' in html
    assert "title, URL, summary" in html
    assert 'name="has_image"' in html
    assert "Missing" in html


def test_promotion_policy_badges_include_override_codes():
    item = type(
        "Item",
        (),
        {
            "promotion_reason": (
                "curated|core|fit=0.55|override_block=weak_tech_signal_video|"
                "preserved=editorial_override"
            )
        },
    )()

    markup = admin_ui._promotion_policy_badges(item)

    assert "override weak_tech_signal_video" in markup


def test_promotion_reason_markup_renders_block_reason():
    item = type(
        "Item",
        (),
        {
            "promotion_reason": "curated|core|fit=0.55|blocked=off_topic_broad_news_video"
        },
    )()

    markup = admin_ui._promotion_reason_markup(item)

    assert "blocked off_topic_broad_news_video" in markup
    assert "curated|core|fit=0.55|blocked=off_topic_broad_news_video" in markup


def test_review_bulk_action_limits_batch_size(monkeypatch):
    monkeypatch.setattr(admin_ui, "EditorialRepository", _FakeBulkRepo)
    monkeypatch.setattr(admin_ui, "EditorialService", _FakeBulkService)
    _FakeBulkRepo.reset()
    _FakeBulkService.reset()

    response = admin_ui.ui_review_bulk_action(
        action="approve",
        content_ids_csv=",".join(
            str(i) for i in range(1, admin_ui._ADMIN_UI_BULK_ACTION_LIMIT + 2)
        ),
        boost_level=3,
        note="",
        next_path="/api/v1/admin/ui/review?page=2",
        key="",
        referer=None,
        db=object(),
        admin_key="secret",
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/api/v1/admin/ui/review?page=2"
        "&flash=Error%3A+bulk+actions+are+limited+to+10+items+per+request"
    )
    assert _FakeBulkService.approve_calls == []
    assert _FakeBulkService.dispatch_calls == 0


def test_review_bulk_action_dispatches_events_once(monkeypatch):
    invalidations = []
    monkeypatch.setattr(admin_ui, "EditorialRepository", _FakeBulkRepo)
    monkeypatch.setattr(admin_ui, "EditorialService", _FakeBulkService)
    monkeypatch.setattr(
        admin_ui,
        "invalidate_tiered_feed_cache",
        lambda *args, **kwargs: invalidations.append((args, kwargs)),
    )
    _FakeBulkRepo.reset()
    _FakeBulkService.reset()

    response = admin_ui.ui_review_bulk_action(
        action="approve",
        content_ids_csv="42, 43, 42, 44",
        boost_level=3,
        note="looks good",
        next_path="/api/v1/admin/ui/review",
        key="",
        referer=None,
        db=object(),
        admin_key="secret",
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/api/v1/admin/ui/review?flash=Bulk+action+complete%3A+3+items+approved"
    )
    assert _FakeBulkService.approve_calls == [
        (42, "admin", "looks good", False),
        (43, "admin", "looks good", False),
        (44, "admin", "looks good", False),
    ]
    assert _FakeBulkService.dispatch_calls == 1
    assert len(invalidations) == 1


def test_review_bulk_action_surfaces_readiness_block(monkeypatch):
    monkeypatch.setattr(admin_ui, "EditorialRepository", _FakeBulkRepo)
    monkeypatch.setattr(admin_ui, "EditorialService", _BlockedApprovalService)
    _FakeBulkRepo.reset()

    response = admin_ui.ui_review_bulk_action(
        action="approve",
        content_ids_csv="42,43",
        boost_level=3,
        note="",
        next_path="/api/v1/admin/ui/review",
        key="",
        referer=None,
        db=object(),
        admin_key="secret",
    )

    assert response.status_code == 303
    assert (
        response.headers["location"]
        == "/api/v1/admin/ui/review?flash=Error%3A+2+items+are+still+waiting+on+readiness"
    )


def test_ui_approve_surfaces_article_readiness_block(monkeypatch):
    monkeypatch.setattr(admin_ui, "EditorialService", _BlockedApprovalService)

    response = admin_ui.ui_approve(
        content_id=42,
        note="",
        next_path="/api/v1/admin/ui/review",
        key="",
        referer=None,
        db=object(),
        admin_key="secret",
    )

    assert response.status_code == 303
    assert "Approval+blocked%3A+This+article+does+not+yet+have+a+usable+summary." in (
        response.headers["location"]
    )


def test_ui_promote_surfaces_article_readiness_block(monkeypatch):
    monkeypatch.setattr(admin_ui, "EditorialService", _BlockedApprovalService)

    response = admin_ui.ui_promote(
        content_id=42,
        next_path="/api/v1/admin/ui/review",
        key="",
        referer=None,
        db=object(),
        admin_key="secret",
    )

    assert response.status_code == 303
    assert "Promotion+blocked%3A+This+article+does+not+yet+have+a+usable+summary." in (
        response.headers["location"]
    )
