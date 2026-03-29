from app.core.curation import review_queue_target_status
from app.models.content import ContentStatus, ContentType


def test_review_queue_target_status_auto_promotes_search_reels():
    status = review_queue_target_status(content_type=ContentType.REEL, acquisition_lane="search")

    assert status == ContentStatus.PROMOTED


def test_review_queue_target_status_auto_promotes_curated_reels_with_positive_cap():
    status = review_queue_target_status(
        content_type=ContentType.REEL,
        acquisition_lane="curated",
        reel_cap=1,
    )

    assert status == ContentStatus.PROMOTED


def test_review_queue_target_status_keeps_zero_cap_curated_reels_in_review():
    status = review_queue_target_status(
        content_type=ContentType.REEL,
        acquisition_lane="curated",
        reel_cap=0,
    )

    assert status == ContentStatus.CANDIDATE
