"""Unit tests for the promotion scoring pipeline (Quality Gate).

Covers:
- compute_clickbait_penalty()
- compute_cluster_hotness()
- compute_duplicate_penalty()
- compute_promotion_recency()
- score_candidate()
- PromotionService.run_promotion_job() – CANDIDATE → PROMOTED transitions
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.integrations.youtube_channels import ChannelConfig, ChannelRole, ContentFormat, QualityTier
from app.models.content import ContentItem, ContentStatus, ContentType
from app.services.promotion_service import (
    PromotionConfig,
    PromotionService,
    classify_promotion_block,
    compute_clickbait_penalty,
    compute_cluster_hotness,
    compute_duplicate_penalty,
    compute_editorial_tech_score,
    compute_promotion_recency,
    compute_story_importance,
    score_candidate,
)

# ── compute_clickbait_penalty ────────────────────────────────────────────────


class TestClickbaitPenalty:
    def test_clean_technical_title_zero(self):
        assert compute_clickbait_penalty("Linux 6.8 Kernel Released") == 0.0

    def test_empty_title_zero(self):
        assert compute_clickbait_penalty("") == 0.0

    def test_exclamation_spam(self):
        # "!!!" matches _CLICKBAIT_PATTERNS
        score = compute_clickbait_penalty("This is amazing!!!")
        assert score >= 0.20

    def test_you_wont_believe_pattern(self):
        score = compute_clickbait_penalty("You won't believe what they found")
        assert score >= 0.20

    def test_allcaps_title(self):
        score = compute_clickbait_penalty("THIS IS ALL CAPS HEADLINE TITLE")
        assert score >= 0.30

    def test_multiple_patterns_capped_at_1(self):
        title = "YOU WON'T BELIEVE!!! SHOCKING MIND-BLOWING SECRET THEY DON'T WANT YOU TO KNOW!!!"
        assert compute_clickbait_penalty(title) == 1.0

    def test_top_n_ways_pattern(self):
        score = compute_clickbait_penalty("Top 10 ways to learn Python fast")
        assert score >= 0.20

    def test_very_short_title_penalty(self):
        score = compute_clickbait_penalty("WOW!")
        assert score > 0.0

    def test_very_long_title_penalty(self):
        long_title = "A" * 201
        score = compute_clickbait_penalty(long_title)
        assert score >= 0.10

    def test_mixed_case_technical_no_penalty(self):
        # "GPT-4o" is not clickbait
        assert compute_clickbait_penalty("OpenAI Releases GPT-4o with Improved Reasoning") == 0.0


# ── compute_cluster_hotness ──────────────────────────────────────────────────


class TestClusterHotness:
    def test_single_item_no_signal_zero(self):
        result = compute_cluster_hotness("c1", 0, {"c1": 1}, signal_hits_cap=5)
        assert result == 0.0

    def test_score_in_unit_range(self):
        result = compute_cluster_hotness("c1", 3, {"c1": 4}, signal_hits_cap=5)
        assert 0.0 <= result <= 1.0

    def test_larger_cluster_higher_score(self):
        small = compute_cluster_hotness("c1", 0, {"c1": 1}, signal_hits_cap=5)
        large = compute_cluster_hotness("c1", 0, {"c1": 8}, signal_hits_cap=5)
        assert large > small

    def test_more_signal_hits_higher_score(self):
        no_hits = compute_cluster_hotness("c1", 0, {"c1": 2}, signal_hits_cap=5)
        hits = compute_cluster_hotness("c1", 5, {"c1": 2}, signal_hits_cap=5)
        assert hits > no_hits

    def test_unknown_cluster_treated_as_size_1(self):
        result = compute_cluster_hotness(None, 0, {}, signal_hits_cap=5)
        assert result == 0.0

    def test_max_cluster_max_signal_near_1(self):
        # cluster=8 (log2(8)/3=1.0) + signal_hits=cap → 1.0
        result = compute_cluster_hotness("c1", 5, {"c1": 8}, signal_hits_cap=5)
        assert result == 1.0


# ── compute_duplicate_penalty ────────────────────────────────────────────────


class TestDuplicatePenalty:
    def test_size_1_no_penalty(self):
        assert compute_duplicate_penalty("c1", {"c1": 1}) == 0.0

    def test_size_2_no_penalty(self):
        assert compute_duplicate_penalty("c1", {"c1": 2}) == 0.0

    def test_size_3_adds_penalty(self):
        penalty = compute_duplicate_penalty("c1", {"c1": 3})
        assert penalty > 0.0

    def test_larger_cluster_higher_penalty(self):
        p3 = compute_duplicate_penalty("c1", {"c1": 3})
        p10 = compute_duplicate_penalty("c1", {"c1": 10})
        assert p10 > p3

    def test_penalty_capped_at_half(self):
        p = compute_duplicate_penalty("c1", {"c1": 1000})
        assert p <= 0.5

    def test_unknown_cluster_no_penalty(self):
        assert compute_duplicate_penalty(None, {}) == 0.0


# ── compute_promotion_recency ─────────────────────────────────────────────────


class TestRecencyScore:
    def test_just_published_near_1(self):
        fresh = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert compute_promotion_recency(fresh, half_life_hours=12) > 0.95

    def test_at_half_life_approx_half(self):
        half_life = 12.0
        at_hl = datetime.now(timezone.utc) - timedelta(hours=half_life)
        score = compute_promotion_recency(at_hl, half_life_hours=half_life)
        assert abs(score - 0.5) < 0.05

    def test_old_content_near_zero(self):
        old = datetime.now(timezone.utc) - timedelta(hours=96)
        assert compute_promotion_recency(old, half_life_hours=12) < 0.02

    def test_naive_datetime_handled(self):
        # Should not raise even without tzinfo
        naive = datetime.utcnow() - timedelta(hours=1)
        score = compute_promotion_recency(naive, half_life_hours=12)
        assert 0.0 < score <= 1.0


# ── score_candidate ───────────────────────────────────────────────────────────


class TestScoreCandidate:
    def _make_item(
        self,
        title: str = "AI Research Breakthrough at Scale",
        source: str = "techcrunch",
        cluster_id: str = "c1",
        signal_hits: int = 0,
        hours_old: float = 2.0,
    ) -> ContentItem:
        item = MagicMock(spec=ContentItem)
        item.source = source
        item.title = title
        item.cluster_id = cluster_id
        item.signal_hits = signal_hits
        item.published_at = datetime.now(timezone.utc) - timedelta(hours=hours_old)
        item.topics = []
        item.entities = []
        item.description = ""
        item.summary = ""
        return item

    def test_score_in_reasonable_range(self):
        score = score_candidate(self._make_item(), {"c1": 2}, PromotionConfig())
        assert -0.2 <= score <= 1.0

    def test_clickbait_title_lowers_score(self):
        clean = score_candidate(
            self._make_item(title="Python 3.13 Released"), {"c1": 1}, PromotionConfig()
        )
        bad = score_candidate(
            self._make_item(title="You WON'T BELIEVE!!!"), {"c1": 1}, PromotionConfig()
        )
        assert clean > bad

    def test_signal_hits_raise_score(self):
        no_hits = score_candidate(self._make_item(signal_hits=0), {"c1": 1}, PromotionConfig())
        hits = score_candidate(self._make_item(signal_hits=5), {"c1": 1}, PromotionConfig())
        assert hits > no_hits

    def test_old_content_lower_score(self):
        fresh = score_candidate(self._make_item(hours_old=1), {"c1": 1}, PromotionConfig())
        stale = score_candidate(self._make_item(hours_old=48), {"c1": 1}, PromotionConfig())
        assert fresh > stale

    def test_result_rounded_to_4_decimals(self):
        score = score_candidate(self._make_item(), {"c1": 1}, PromotionConfig())
        assert score == round(score, 4)

    def test_story_importance_boosts_relevant_launch_candidate(self):
        item = self._make_item(title="MacBook Air hands on review", hours_old=6)
        item.entities = ["macbook", "apple"]
        item.topics = ["mobile/hardware"]
        item.description = "Apple launch coverage with benchmarks and hands-on impressions."

        generic = score_candidate(
            self._make_item(title="Generic tech roundup", hours_old=6),
            {"c1": 1},
            PromotionConfig(w_story=0.2),
            story_topic_counts={"mobile/hardware": 6},
            story_entity_counts={"macbook": 8, "apple": 6},
        )
        boosted = score_candidate(
            item,
            {"c1": 1},
            PromotionConfig(w_story=0.2),
            story_topic_counts={"mobile/hardware": 6},
            story_entity_counts={"macbook": 8, "apple": 6},
        )

        assert boosted > generic

    def test_trending_discovery_is_penalized_less_than_search(self):
        search_item = self._make_item(title="Google Maps update recap", hours_old=6)
        search_item.acquisition_lane = "search"
        search_item.source_status = "discovery"
        search_item.views_per_hour = 2200.0
        search_item.format_fit_score = 1.0
        search_item.channel_id = "channel-1"

        trending_item = self._make_item(title="Google Maps update recap", hours_old=6)
        trending_item.acquisition_lane = "trending"
        trending_item.source_status = "discovery"
        trending_item.views_per_hour = 2200.0
        trending_item.format_fit_score = 1.0
        trending_item.channel_id = "channel-1"

        config = PromotionConfig(
            w_source=0.18,
            w_cluster=0.14,
            w_recency=0.10,
            w_clickbait=0.09,
            w_duplicate=0.07,
            w_velocity=0.13,
            w_format_fit=0.08,
            w_story=0.16,
            discovery_lane_penalty=0.04,
        )

        search_score = score_candidate(search_item, {"c1": 1}, config)
        trending_score = score_candidate(trending_item, {"c1": 1}, config)

        assert trending_score > search_score

    def test_missing_format_fit_does_not_receive_free_score_boost(self):
        item = self._make_item(title="OpenAI dev tools update", hours_old=2)
        item.format_fit_score = None
        item.views_per_hour = 0.0
        item.acquisition_lane = "curated"
        item.source_status = "core"

        score = score_candidate(
            item,
            {"c1": 1},
            PromotionConfig(
                w_source=0.0,
                w_cluster=0.0,
                w_recency=0.0,
                w_clickbait=0.0,
                w_duplicate=0.0,
                w_velocity=0.0,
                w_format_fit=0.08,
                w_category_gap=0.0,
                w_creator_fatigue=0.0,
                w_story=0.0,
            ),
        )

        assert score == 0.0


class TestStoryImportance:
    def test_launch_keywords_and_story_overlap_drive_score(self):
        item = MagicMock(spec=ContentItem)
        item.title = "OpenAI launch keynote recap"
        item.description = "The new GPT update matters for developers."
        item.summary = ""
        item.topics = ["ai"]
        item.entities = ["openai", "gpt"]
        item.views_per_hour = 0.0
        item.published_at = datetime.now(timezone.utc) - timedelta(hours=2)

        score = compute_story_importance(
            item,
            {"ai": 10},
            {"openai": 12, "gpt": 8},
        )

        assert score > 0.4


class TestEditorialGates:
    def _make_item(self, title: str, *, summary: str = "", description: str = "") -> ContentItem:
        item = MagicMock(spec=ContentItem)
        item.title = title
        item.summary = summary
        item.description = description
        item.topics = []
        item.entities = []
        item.acquisition_lane = "search"
        item.source_status = "discovery"
        item.source = "Trusted Tech Lab"
        item.channel_id = "channel-1"
        item.published_at = datetime.now(timezone.utc) - timedelta(hours=2)
        item.views_per_hour = 180.0
        item.format_fit_score = 1.0
        return item

    def test_compute_editorial_tech_score_rejects_off_topic_news_language(self):
        item = self._make_item(
            "President Trump says White House is delaying China summit",
            summary="Politics coverage with no clear technology angle.",
        )

        score = compute_editorial_tech_score(item, channel_role=ChannelRole.NEWS)

        assert score < 0.2

    def test_classify_promotion_block_rejects_low_story_discovery_reel(self):
        item = self._make_item(
            "He Couldn't Play More Than 5 Minutes!!",
            summary="A dramatic short with very little actual tech context.",
        )

        reason = classify_promotion_block(
            item,
            ContentType.REEL,
            story_topic_counts={},
            story_entity_counts={},
        )

        assert reason == "weak_editorial_reel"

    def test_classify_promotion_block_rejects_official_promo_reel(self):
        item = self._make_item(
            "Wait for the splashdown... | Osmo 360",
            summary="A cinematic teaser with almost no editorial substance.",
        )
        item.acquisition_lane = "curated"
        item.source_status = "core"

        reason = classify_promotion_block(
            item,
            ContentType.REEL,
            story_topic_counts={},
            story_entity_counts={},
            channel_config=ChannelConfig(
                channel_id="channel-1",
                name="DJI",
                role=ChannelRole.OFFICIAL,
                content_format=ContentFormat.LONG_FORM,
                daily_cap=2,
                daily_reel_cap=0,
                allow_reels=False,
                quality_tier=QualityTier.STANDARD,
            ),
        )

        assert reason == "weak_editorial_reel"

    def test_classify_promotion_block_rejects_audio_only_news_video(self):
        item = self._make_item(
            "Mad Money 03/16/26 | Audio Only",
            summary="A market recap with no direct product or platform coverage.",
        )
        item.acquisition_lane = "curated"
        item.source_status = "core"

        reason = classify_promotion_block(
            item,
            ContentType.VIDEO,
            story_topic_counts={},
            story_entity_counts={},
            channel_config=ChannelConfig(
                channel_id="channel-1",
                name="CNBC Television",
                role=ChannelRole.NEWS,
                content_format=ContentFormat.LONG_FORM,
                daily_cap=2,
                quality_tier=QualityTier.STANDARD,
            ),
        )

        assert reason == "off_topic_news_video"

    def test_classify_promotion_block_rejects_broad_news_live_video(self):
        item = self._make_item(
            "LIVE: Halls of Congress as Bondi and Blanche give briefing to House Oversight Committee",
            summary="Reuters live politics coverage.",
        )
        item.acquisition_lane = "curated"
        item.source_status = "core"
        item.source = "Reuters"

        reason = classify_promotion_block(
            item,
            ContentType.VIDEO,
            story_topic_counts={},
            story_entity_counts={},
            channel_config=ChannelConfig(
                channel_id="channel-1",
                name="Reuters",
                role=ChannelRole.NEWS,
                content_format=ContentFormat.LONG_FORM,
                daily_cap=2,
                quality_tier=QualityTier.PREMIUM,
            ),
        )

        assert reason == "off_topic_broad_news_video"

    def test_classify_promotion_block_rejects_high_confidence_non_tech_broad_news_video(self):
        item = self._make_item(
            "Houthis fire missiles at Israel, Iran's missile toll revealed and Tiger Woods arrest",
            summary="Reuters general-news roundup.",
        )
        item.acquisition_lane = "curated"
        item.source_status = "core"
        item.source = "Reuters"
        item.tech_relevance = "none"
        item.tech_relevance_confidence = 0.97
        item.is_mixed_roundup = True

        reason = classify_promotion_block(
            item,
            ContentType.VIDEO,
            story_topic_counts={},
            story_entity_counts={},
            channel_config=ChannelConfig(
                channel_id="channel-1",
                name="Reuters",
                role=ChannelRole.NEWS,
                content_format=ContentFormat.LONG_FORM,
                daily_cap=2,
                quality_tier=QualityTier.PREMIUM,
            ),
        )

        assert reason == "llm_mixed_roundup_broad_news_video"

    def test_classify_promotion_block_rejects_broad_news_politics_reel(self):
        item = self._make_item(
            "Stratton’s Illinois win signals Democratic shift left on immigration",
            summary="Reuters politics clip.",
        )
        item.acquisition_lane = "curated"
        item.source_status = "core"
        item.source = "Reuters"

        reason = classify_promotion_block(
            item,
            ContentType.REEL,
            story_topic_counts={},
            story_entity_counts={},
            channel_config=ChannelConfig(
                channel_id="channel-1",
                name="Reuters",
                role=ChannelRole.NEWS,
                content_format=ContentFormat.LONG_FORM,
                daily_cap=2,
                quality_tier=QualityTier.PREMIUM,
            ),
        )

        assert reason == "off_topic_broad_news_reel"


# ── PromotionService ──────────────────────────────────────────────────────────


class TestPromotionService:
    def _make_candidate(
        self,
        id_: int = 1,
        content_type: ContentType = ContentType.ARTICLE,
        title: str = "Good Technical Article",
        cluster_id: str = "c1",
    ) -> ContentItem:
        item = MagicMock(spec=ContentItem)
        item.id = id_
        item.type = content_type
        item.curation_status = ContentStatus.CANDIDATE
        item.source = "techcrunch"
        item.title = title
        item.cluster_id = cluster_id
        item.signal_hits = 3
        item.published_at = datetime.now(timezone.utc) - timedelta(hours=1)
        item.is_suppressed = False
        item.promotion_score = None
        item.summary = ""
        item.description = ""
        item.topics = []
        item.entities = []
        item.acquisition_lane = "curated"
        item.source_status = "core"
        item.channel_id = f"channel-{id_}"
        item.views_per_hour = 220.0
        item.format_fit_score = 1.0
        return item

    def test_candidate_gets_promoted_above_threshold(self):
        mock_db = MagicMock()
        svc = PromotionService(mock_db)
        candidate = self._make_candidate()

        with (
            patch.object(svc, "_get_cluster_sizes", return_value={"c1": 3}),
            patch.object(svc, "_get_candidates", return_value=[candidate]),
            patch.object(svc, "_rescore_promoted", return_value=0),
        ):
            result = svc.run_promotion_job()

        assert candidate.curation_status == ContentStatus.PROMOTED
        assert candidate.promotion_score is not None
        assert result.promoted_count >= 1
        mock_db.commit.assert_called_once()

    def test_low_score_item_stays_candidate(self):
        mock_db = MagicMock()
        # Use min_score=1.0 so nothing gets promoted
        cfg = PromotionConfig(min_score=1.0)
        svc = PromotionService(mock_db, config=cfg)
        candidate = self._make_candidate()

        with (
            patch.object(svc, "_get_cluster_sizes", return_value={}),
            patch.object(svc, "_get_candidates", return_value=[candidate]),
            patch.object(svc, "_rescore_promoted", return_value=0),
        ):
            result = svc.run_promotion_job()

        assert candidate.curation_status == ContentStatus.CANDIDATE
        assert result.promoted_count == 0

    def test_source_health_refresh_runs_even_without_promotions(self):
        mock_db = MagicMock()
        cfg = PromotionConfig(min_score=1.0)
        svc = PromotionService(mock_db, config=cfg)

        with (
            patch("app.services.promotion_service.Session", object),
            patch("app.services.video_source_service.refresh_video_source_health") as mock_refresh,
            patch.object(svc, "_get_cluster_sizes", return_value={}),
            patch.object(svc, "_get_candidates", return_value=[]),
            patch.object(svc, "_rescore_promoted", return_value=0),
        ):
            result = svc.run_promotion_job()

        mock_refresh.assert_called_once_with(mock_db)
        assert result.promoted_count == 0

    def test_top_n_cap_respected(self):
        mock_db = MagicMock()
        cfg = PromotionConfig(top_n_per_type=2, min_score=0.0)
        svc = PromotionService(mock_db, config=cfg)
        candidates = [self._make_candidate(id_=i) for i in range(5)]

        def _get_candidates_for_type(content_type):
            # Only return candidates for ARTICLE so we can assert on exactly that type
            if content_type == ContentType.ARTICLE:
                return candidates
            return []

        with (
            patch.object(svc, "_get_cluster_sizes", return_value={}),
            patch.object(svc, "_get_candidates", side_effect=_get_candidates_for_type),
            patch.object(svc, "_rescore_promoted", return_value=0),
        ):
            result = svc.run_promotion_job()

        promoted = [c for c in candidates if c.curation_status == ContentStatus.PROMOTED]
        # top_n_per_type=2 and only ARTICLE returned items → at most 2 promoted
        assert len(promoted) <= 2
        assert result.promoted_count <= 2

    def test_db_rollback_on_error(self):
        mock_db = MagicMock()
        svc = PromotionService(mock_db)

        with patch.object(svc, "_get_cluster_sizes", side_effect=RuntimeError("DB crash")):
            result = svc.run_promotion_job()

        mock_db.rollback.assert_called_once()
        assert any("Promotion run failed" in e for e in result.errors)

    def test_reel_type_processed(self):
        """REEL candidates now flow through the same promotion gate."""
        mock_db = MagicMock()
        svc = PromotionService(mock_db)

        with (
            patch.object(svc, "_get_cluster_sizes", return_value={}),
            patch.object(svc, "_get_candidates", return_value=[]) as mock_gc,
            patch.object(svc, "_rescore_promoted", return_value=0),
        ):
            svc.run_promotion_job()

        called_types = [call.args[0] for call in mock_gc.call_args_list]
        assert ContentType.REEL in called_types
        assert ContentType.ARTICLE in called_types
        assert ContentType.VIDEO in called_types

    def test_reel_channel_cap_is_respected(self):
        mock_db = MagicMock()
        cfg = PromotionConfig(top_n_per_type=5, min_score=0.0)
        svc = PromotionService(mock_db, config=cfg)
        first = self._make_candidate(
            id_=1, content_type=ContentType.REEL, title="GitHub Copilot tip"
        )
        second = self._make_candidate(
            id_=2,
            content_type=ContentType.REEL,
            title="GitHub developer update",
        )
        first.source = "GitHub"
        second.source = "GitHub"
        first.channel_id = "channel-cap"
        second.channel_id = "channel-cap"
        first.summary = "Developer automation tip for Copilot users."
        second.summary = "Developer workflow update for GitHub automation."

        with (
            patch.object(svc, "_get_cluster_sizes", return_value={}),
            patch.object(
                svc,
                "_get_candidates",
                side_effect=lambda content_type: (
                    [first, second] if content_type == ContentType.REEL else []
                ),
            ),
            patch.object(svc, "_get_source_profiles", return_value={}),
            patch.object(
                svc,
                "_get_recent_promoted_channel_counts",
                return_value={"channel-cap": 0},
            ),
            patch.object(svc, "_rescore_promoted", return_value=0),
            patch(
                "app.services.promotion_service._channel_config_for_item",
                return_value=ChannelConfig(
                    channel_id="channel-cap",
                    name="GitHub",
                    role=ChannelRole.OFFICIAL,
                    content_format=ContentFormat.MIXED,
                    daily_cap=2,
                    daily_reel_cap=1,
                    quality_tier=QualityTier.STANDARD,
                ),
            ),
        ):
            result = svc.run_promotion_job()

        promoted = [
            item for item in (first, second) if item.curation_status == ContentStatus.PROMOTED
        ]
        assert len(promoted) == 1
        assert result.promoted_count == 1

    def test_rescore_promoted_demotes_blocked_broad_news_video(self):
        class _Query:
            def filter(self, *_args, **_kwargs):
                return self

            def all(self):
                return [item]

        mock_db = MagicMock()
        mock_db.query.return_value = _Query()
        svc = PromotionService(mock_db)
        item = self._make_candidate(
            id_=99,
            content_type=ContentType.VIDEO,
            title="LIVE: California group proposes renaming street after Dolores Huerta",
        )
        item.curation_status = ContentStatus.PROMOTED
        item.source = "Reuters"
        item.channel_id = "reuters-1"

        with (
            patch(
                "app.services.promotion_service.apply_content_policy",
                side_effect=lambda q, **_kw: q,
            ),
            patch.object(svc, "_get_source_profiles", return_value={}),
            patch(
                "app.services.promotion_service._channel_config_for_item",
                return_value=ChannelConfig(
                    channel_id="reuters-1",
                    name="Reuters",
                    role=ChannelRole.NEWS,
                    content_format=ContentFormat.LONG_FORM,
                    daily_cap=2,
                    quality_tier=QualityTier.PREMIUM,
                ),
            ),
        ):
            rescored = svc._rescore_promoted(
                ContentType.VIDEO,
                {},
                svc._config_for_type(ContentType.VIDEO),
                promoted_topic_counts={},
                promoted_channel_counts={},
                story_topic_counts={},
                story_entity_counts={},
            )

        assert rescored == 1
        assert item.curation_status == ContentStatus.CANDIDATE
        assert "blocked=off_topic_broad_news_video" in item.promotion_reason

    def test_rescore_promoted_demotes_weak_tech_signal_video(self):
        class _Query:
            def filter(self, *_args, **_kwargs):
                return self

            def all(self):
                return [item]

        mock_db = MagicMock()
        mock_db.query.return_value = _Query()
        svc = PromotionService(mock_db)
        item = self._make_candidate(
            id_=100,
            content_type=ContentType.VIDEO,
            title="Next generation of tech winners to emerge outside of U.S., says Tekne Capital's Kothari",
        )
        item.curation_status = ContentStatus.PROMOTED
        item.source = "CNBC Television"
        item.channel_id = "cnbc-1"

        with (
            patch(
                "app.services.promotion_service.apply_content_policy",
                side_effect=lambda q, **_kw: q,
            ),
            patch.object(svc, "_get_source_profiles", return_value={}),
            patch(
                "app.services.promotion_service._channel_config_for_item",
                return_value=ChannelConfig(
                    channel_id="cnbc-1",
                    name="CNBC Television",
                    role=ChannelRole.NEWS,
                    content_format=ContentFormat.LONG_FORM,
                    daily_cap=2,
                    quality_tier=QualityTier.PREMIUM,
                ),
            ),
        ):
            rescored = svc._rescore_promoted(
                ContentType.VIDEO,
                {},
                svc._config_for_type(ContentType.VIDEO),
                promoted_topic_counts={},
                promoted_channel_counts={},
                story_topic_counts={},
                story_entity_counts={},
            )

        assert rescored == 1
        assert item.curation_status == ContentStatus.CANDIDATE
        assert "blocked=weak_tech_signal_video" in item.promotion_reason

    def test_classifier_none_blocks_broad_news_video_at_moderate_confidence(self):
        item = self._make_candidate(
            id_=101,
            content_type=ContentType.VIDEO,
            title="Humpback whale Timmy struggles to escape shallow waters",
        )
        item.source = "Reuters"
        item.summary = None
        item.description = "A whale rescue story from Reuters Newsfeed."
        item.topics = ["twitter"]
        item.entities = ["twitter"]
        item.tech_relevance = "none"
        item.tech_relevance_confidence = 0.58

        block_reason = classify_promotion_block(
            item,
            ContentType.VIDEO,
            story_topic_counts={"twitter": 8},
            story_entity_counts={"twitter": 8},
            channel_config=ChannelConfig(
                channel_id="reuters-2",
                name="Reuters",
                role=ChannelRole.NEWS,
                content_format=ContentFormat.LONG_FORM,
                daily_cap=2,
                quality_tier=QualityTier.PREMIUM,
            ),
        )

        assert block_reason == "llm_non_tech_broad_news_video"
