"""Shared constants for the bounded major-news article fast path."""

MAJOR_NEWS_DISCOVERED_VIA = "major_news_probe"
MAJOR_NEWS_SOURCE_TYPE = "major_news_rss"
MAJOR_NEWS_SAME_DAY_SCORE_BOOST = 0.08
MAJOR_NEWS_CLASSIFIER_MIN_CONFIDENCE = 0.45

# Premium BREAKING-role sources whose items should receive retroactive
# major-tech-news classification during each probe run. These sources are
# ingested via the normal checkpoint path (not the major_news_probe insert
# path), so on_conflict_do_nothing prevents the probe from setting the flag
# at insert time. The retro-classify pass fills this gap.
MAJOR_NEWS_RETRO_CLASSIFY_SOURCES: list[str] = [
    "TechCrunch",
    "The Verge",
    "Ars Technica",
]
