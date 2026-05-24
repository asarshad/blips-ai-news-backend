from app.services.playlist_service import _push_repeated_article_thumbnails_later


def test_repeated_article_thumbnails_are_deferred_from_head():
    items = [
        {"id": 1, "image_url": "https://cdn.example.com/shared.jpg"},
        {"id": 2, "image_url": "https://cdn.example.com/unique.jpg"},
        {"id": 3, "image_url": "https://cdn.example.com/shared.jpg"},
        {"id": 4, "image_url": "https://cdn.example.com/another.jpg"},
    ]

    ordered = _push_repeated_article_thumbnails_later(items, window=3)

    assert [item["id"] for item in ordered] == [1, 2, 4, 3]
