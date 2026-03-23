from app.article_image_selection import (
    ArticleImageCandidate,
    rank_article_image_candidates,
    select_best_article_image,
)


def test_select_best_article_image_prefers_extraction_over_rss_and_existing():
    selected = select_best_article_image(
        [
            ArticleImageCandidate(
                url="https://cdn.example.com/existing-story.jpg",
                source="existing",
            ),
            ArticleImageCandidate(
                url="https://cdn.example.com/rss-story.jpg",
                source="rss_summary",
            ),
            ArticleImageCandidate(
                url="https://cdn.example.com/extracted-story.jpg",
                source="extraction",
            ),
        ]
    )

    assert selected == "https://cdn.example.com/extracted-story.jpg"


def test_select_best_article_image_skips_generic_and_suspicious_candidates():
    selected = select_best_article_image(
        [
            ArticleImageCandidate(
                url="https://cdn.example.com/social-share.png",
                source="page_metadata",
            ),
            ArticleImageCandidate(
                url="https://cdn.example.com/article-hero.jpg",
                source="rss_content",
            ),
        ]
    )

    assert selected == "https://cdn.example.com/article-hero.jpg"


def test_rank_article_image_candidates_prefers_page_metadata_over_existing():
    ranked = rank_article_image_candidates(
        [
            ArticleImageCandidate(
                url="https://cdn.example.com/older-story.jpg",
                source="existing",
            ),
            ArticleImageCandidate(
                url="https://cdn.example.com/page-hero.jpg",
                source="page_metadata",
            ),
        ]
    )

    assert [candidate.url for candidate in ranked] == [
        "https://cdn.example.com/page-hero.jpg",
        "https://cdn.example.com/older-story.jpg",
    ]
