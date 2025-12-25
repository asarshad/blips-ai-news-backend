# QA Test Plan: Feeds, Videos & Reels

**Version:** 1.0  
**Last Updated:** December 25, 2025  
**QA Lead:** AI Assistant

---

## 1. Executive Summary

This document outlines the comprehensive QA testing strategy for the Blips mobile news feed application. The test plan covers:
- Content freshness and completeness
- API performance and reliability
- Video playback behavior
- Cross-screen consistency
- Edge cases and error handling

---

## 2. Test Categories

### 2.1 Content Freshness Tests

| Test ID | Description | Expected Result | Priority |
|---------|-------------|-----------------|----------|
| CF-001 | Verify articles from last 24h appear first | Most recent articles at top | High |
| CF-002 | Verify articles from last 72h are available | At least 50+ articles accessible | High |
| CF-003 | Check published_date accuracy | Dates match RSS source dates | Medium |
| CF-004 | Verify no content older than 7 days in main feed | Only recent content shown | Medium |
| CF-005 | Pull-to-refresh fetches new content | New content appears at top | High |
| CF-006 | Test after 3+ hours of no use | Fresh content on return | Medium |

**API Test Commands:**
```bash
# Check content freshness
curl -s "http://localhost:8000/api/v1/articles/recent?limit=50" | \
  jq '[.articles[] | .published_date] | unique | sort | reverse'

# Verify no stale content
curl -s "http://localhost:8000/api/v1/articles/recent?limit=100" | \
  jq '[.articles[] | select(.published_date < "2025-12-20")] | length'
# Expected: 0
```

---

### 2.2 Content Completeness Tests

| Test ID | Description | Expected Result | Priority |
|---------|-------------|-----------------|----------|
| CC-001 | All articles have AI-generated summaries | summary field non-empty, >50 chars | Critical |
| CC-002 | All videos have AI-generated summaries | summary field non-empty, not YouTube desc | Critical |
| CC-003 | All articles have images | image_url populated or graceful placeholder | High |
| CC-004 | All videos have thumbnails | thumbnail_url is valid YouTube thumbnail | High |
| CC-005 | Tags/topics are populated | At least 1 tag per article | Medium |
| CC-006 | Read time is calculated | read_time_minutes > 0 | Low |
| CC-007 | Video duration is populated | duration_seconds > 0 for videos | Medium |

**API Test Commands:**
```bash
# Check for empty summaries
curl -s "http://localhost:8000/api/v1/articles/recent?limit=100" | \
  jq '[.articles[] | select(.summary == "" or .summary == null)] | length'
# Expected: 0

# Check summary quality (should be AI-generated, not descriptions)
curl -s "http://localhost:8000/api/v1/videos/recent?limit=10" | \
  jq '.videos[] | {title: .title, summary_length: (.summary | length), has_subscribe: (.summary | contains("Subscribe"))}'
# Expected: summary_length > 50, has_subscribe = false

# Verify no YouTube descriptions leaked through
curl -s "http://localhost:8000/api/v1/videos/recent?limit=50" | \
  jq '[.videos[] | select(.summary | test("Subscribe|https://|http://|\\n\\n"))] | length'
# Expected: 0
```

---

### 2.3 Performance Tests

| Test ID | Description | Expected Result | Priority |
|---------|-------------|-----------------|----------|
| PF-001 | Articles API response time | < 200ms for limit=50 | High |
| PF-002 | Videos API response time | < 200ms for limit=20 | High |
| PF-003 | Pagination performance | Consistent response times across pages | Medium |
| PF-004 | Load test - concurrent requests | Handle 50 concurrent users | Medium |
| PF-005 | Database query performance | No N+1 queries, use indexes | High |
| PF-006 | Redis cache effectiveness | Cache hit rate > 80% | Medium |

**API Test Commands:**
```bash
# Response time test
time curl -s "http://localhost:8000/api/v1/articles/recent?limit=50" > /dev/null

# Pagination test
for i in 1 2 3 4 5; do
  time curl -s "http://localhost:8000/api/v1/articles/recent?limit=20&page=$i" > /dev/null
done

# Concurrent load test (requires Apache Bench)
ab -n 100 -c 10 "http://localhost:8000/api/v1/articles/recent?limit=20"
```

---

### 2.4 Video Playback Tests

| Test ID | Description | Expected Result | Priority |
|---------|-------------|-----------------|----------|
| VP-001 | Video plays on tap | Video starts within 2 seconds | Critical |
| VP-002 | Video pauses when leaving screen | No audio continues | Critical |
| VP-003 | Video resumes on return | Continues from last position | High |
| VP-004 | Full screen video works | Enters/exits fullscreen cleanly | High |
| VP-005 | Mute/unmute toggle works | Audio state persists | Medium |
| VP-006 | Video buffering indicator | Shows loading state | Medium |
| VP-007 | Video error handling | Graceful error message on failure | High |
| VP-008 | Video autoplay in feed | Videos autoplay muted on scroll | Medium |
| VP-009 | Video stops on background | Stops when app backgrounded | Critical |
| VP-010 | Multiple videos don't play | Only one video plays at a time | High |

**Manual Test Script:**
1. Open video feed
2. Scroll to first video - verify autoplay (muted)
3. Tap video - verify unmutes and plays
4. Swipe to next video - verify previous stops
5. Press home button - verify video stops
6. Return to app - verify video state preserved
7. Rotate device - verify video continues
8. Turn off WiFi mid-video - verify error handling

---

### 2.5 Reels-Specific Tests

| Test ID | Description | Expected Result | Priority |
|---------|-------------|-----------------|----------|
| RL-001 | Reels load in vertical format | Full-screen vertical videos | Critical |
| RL-002 | Swipe up advances to next | Smooth transition | Critical |
| RL-003 | Swipe down returns to previous | Can go back | High |
| RL-004 | Loop at end of reel | Video loops seamlessly | Medium |
| RL-005 | Double-tap to like | Like animation plays | Medium |
| RL-006 | Share button works | Share sheet opens | Medium |
| RL-007 | Caption/summary visible | Text overlay on video | High |
| RL-008 | Video loads quickly | Start within 1 second | High |

---

### 2.6 Cross-Screen Consistency Tests

| Test ID | Description | Expected Result | Priority |
|---------|-------------|-----------------|----------|
| CS-001 | Article IDs consistent | Same ID in list and detail view | Critical |
| CS-002 | Video IDs consistent | Same ID across views | Critical |
| CS-003 | Content state syncs | Read state persists | Medium |
| CS-004 | Back navigation works | Returns to correct position | High |
| CS-005 | Deep linking works | Can link to specific article/video | Medium |
| CS-006 | Offline mode graceful | Shows cached content, offline indicator | Medium |

**Test Scenario:**
1. View article list, note first article ID
2. Tap to open detail view
3. Verify same ID shown
4. Press back
5. Verify same position in list
6. Switch to videos tab and back
7. Verify article position preserved

---

### 2.7 Edge Cases & Error Handling

| Test ID | Description | Expected Result | Priority |
|---------|-------------|-----------------|----------|
| EC-001 | Empty feed state | "No content available" message | High |
| EC-002 | Network error | Offline indicator, cached content | High |
| EC-003 | API timeout | Retry button shown | High |
| EC-004 | Invalid video URL | Graceful error, skip to next | High |
| EC-005 | Corrupted image | Placeholder image shown | Medium |
| EC-006 | Very long title | Text truncates with ellipsis | Medium |
| EC-007 | Unicode/emoji in content | Renders correctly | Medium |
| EC-008 | Rapid pagination | Handles quickly without duplicates | High |
| EC-009 | Kill app during video | Clean restart | Medium |
| EC-010 | Low memory warning | Handles gracefully | Medium |

**API Test Commands:**
```bash
# Simulate timeout
curl -s --max-time 1 "http://localhost:8000/api/v1/articles/recent"

# Test with invalid page
curl -s "http://localhost:8000/api/v1/articles/recent?page=9999"
# Expected: Empty list, not error

# Test with extreme limit
curl -s "http://localhost:8000/api/v1/articles/recent?limit=1000"
# Expected: Max capped at 50
```

---

## 3. Database Validation Queries

Run these queries to validate data integrity:

```sql
-- 1. Check for content without AI processing
SELECT type, COUNT(*) 
FROM content_items 
WHERE ai_processed = FALSE 
  AND published_at > NOW() - INTERVAL '7 days'
GROUP BY type;
-- Expected: 0 rows (all recent content should be processed)

-- 2. Check for empty summaries in AI-processed content
SELECT COUNT(*) 
FROM content_items 
WHERE ai_processed = TRUE 
  AND (summary IS NULL OR summary = '');
-- Expected: 0

-- 3. Check for suspiciously short summaries
SELECT id, type, title, LEFT(summary, 50) as summary_preview
FROM content_items 
WHERE ai_processed = TRUE 
  AND LENGTH(summary) < 50;
-- Expected: 0 rows

-- 4. Check for YouTube descriptions leaked as summaries
SELECT id, title, LEFT(summary, 100)
FROM content_items 
WHERE type = 'VIDEO' 
  AND (summary LIKE '%Subscribe%' OR summary LIKE '%https://%' OR summary LIKE '%http://%');
-- Expected: 0 rows

-- 5. Check content distribution (should have balanced types)
SELECT type, 
       COUNT(*) as total,
       COUNT(CASE WHEN ai_processed THEN 1 END) as ai_processed
FROM content_items 
WHERE published_at > NOW() - INTERVAL '72 hours'
GROUP BY type;

-- 6. Check for duplicate content
SELECT dedupe_key, COUNT(*) as cnt
FROM content_items
GROUP BY dedupe_key
HAVING COUNT(*) > 1;
-- Expected: 0 rows

-- 7. Check global_score distribution
SELECT 
  CASE 
    WHEN global_score = 0 THEN '0 (unscored)'
    WHEN global_score < 0.3 THEN '0.01-0.3 (low)'
    WHEN global_score < 0.6 THEN '0.3-0.6 (medium)'
    ELSE '0.6+ (high)'
  END as score_range,
  COUNT(*)
FROM content_items
WHERE published_at > NOW() - INTERVAL '72 hours'
GROUP BY 1;
```

---

## 4. Automated Test Suite

### 4.1 API Integration Tests

Create file: `tests/api/test_feeds.py`

```python
import pytest
import httpx
from datetime import datetime, timedelta

BASE_URL = "http://localhost:8000/api/v1"

class TestArticlesAPI:
    """Test suite for articles endpoints."""
    
    def test_recent_articles_returns_data(self):
        """Verify recent articles endpoint returns content."""
        response = httpx.get(f"{BASE_URL}/articles/recent?limit=10")
        assert response.status_code == 200
        data = response.json()
        assert "articles" in data
        assert len(data["articles"]) > 0
    
    def test_articles_have_summaries(self):
        """All articles should have non-empty AI summaries."""
        response = httpx.get(f"{BASE_URL}/articles/recent?limit=50")
        data = response.json()
        for article in data["articles"]:
            assert article.get("summary"), f"Article {article['id']} has no summary"
            assert len(article["summary"]) > 50, f"Article {article['id']} summary too short"
    
    def test_articles_have_images(self):
        """Most articles should have images."""
        response = httpx.get(f"{BASE_URL}/articles/recent?limit=50")
        data = response.json()
        with_images = sum(1 for a in data["articles"] if a.get("image_url"))
        # At least 80% should have images
        assert with_images / len(data["articles"]) >= 0.8
    
    def test_articles_ordered_by_score(self):
        """Articles should be ordered by global_score descending."""
        response = httpx.get(f"{BASE_URL}/articles/recent?limit=20")
        data = response.json()
        # First article should have ID (newest/highest scored content)
        assert data["articles"][0]["id"] is not None
    
    def test_pagination_works(self):
        """Pagination should return different content."""
        page1 = httpx.get(f"{BASE_URL}/articles/recent?limit=5&page=1").json()
        page2 = httpx.get(f"{BASE_URL}/articles/recent?limit=5&page=2").json()
        
        ids_page1 = {a["id"] for a in page1["articles"]}
        ids_page2 = {a["id"] for a in page2["articles"]}
        
        # Pages should have different content
        assert ids_page1.isdisjoint(ids_page2)


class TestVideosAPI:
    """Test suite for videos endpoints."""
    
    def test_recent_videos_returns_data(self):
        """Verify recent videos endpoint returns content."""
        response = httpx.get(f"{BASE_URL}/videos/recent?limit=10")
        assert response.status_code == 200
        data = response.json()
        assert "videos" in data
    
    def test_videos_have_ai_summaries(self):
        """All videos should have AI-generated summaries."""
        response = httpx.get(f"{BASE_URL}/videos/recent?limit=20")
        data = response.json()
        for video in data["videos"]:
            summary = video.get("summary", "")
            assert summary, f"Video {video['id']} has no summary"
            assert len(summary) > 50, f"Video {video['id']} summary too short"
            # Should not contain YouTube description patterns
            assert "Subscribe" not in summary, f"Video {video['id']} has YouTube description"
            assert "https://" not in summary, f"Video {video['id']} has URLs in summary"
    
    def test_videos_have_thumbnails(self):
        """All videos should have thumbnails."""
        response = httpx.get(f"{BASE_URL}/videos/recent?limit=20")
        data = response.json()
        for video in data["videos"]:
            assert video.get("thumbnail_url"), f"Video {video['id']} has no thumbnail"
            assert "youtube.com" in video["thumbnail_url"] or "img.youtube.com" in video["thumbnail_url"]
    
    def test_videos_have_duration(self):
        """All videos should have duration."""
        response = httpx.get(f"{BASE_URL}/videos/recent?limit=20")
        data = response.json()
        for video in data["videos"]:
            assert video.get("duration_seconds") and video["duration_seconds"] > 0


class TestAPIPerformance:
    """Performance tests for API endpoints."""
    
    def test_articles_response_time(self):
        """Articles API should respond quickly."""
        import time
        start = time.time()
        response = httpx.get(f"{BASE_URL}/articles/recent?limit=50")
        elapsed = time.time() - start
        assert response.status_code == 200
        assert elapsed < 0.5, f"Response took {elapsed:.2f}s, expected < 0.5s"
    
    def test_videos_response_time(self):
        """Videos API should respond quickly."""
        import time
        start = time.time()
        response = httpx.get(f"{BASE_URL}/videos/recent?limit=20")
        elapsed = time.time() - start
        assert response.status_code == 200
        assert elapsed < 0.5, f"Response took {elapsed:.2f}s, expected < 0.5s"
```

---

## 5. Manual Testing Checklist

### 5.1 Pre-Release Checklist

- [ ] All API endpoints return 200 status
- [ ] No articles with empty summaries
- [ ] No videos with YouTube descriptions as summaries
- [ ] Content freshness < 72 hours for top content
- [ ] Pagination works correctly
- [ ] Response times < 500ms
- [ ] No duplicate content in feeds
- [ ] Images/thumbnails load correctly
- [ ] Video playback works on iOS
- [ ] Video playback works on Android
- [ ] Reels swipe navigation works
- [ ] Pull-to-refresh fetches new content
- [ ] Offline mode shows cached content
- [ ] Error states display correctly

### 5.2 Regression Test Checklist

After any code change, verify:

- [ ] Articles API still returns AI summaries
- [ ] Videos API still returns AI summaries
- [ ] No increase in empty summary count
- [ ] Response times haven't degraded
- [ ] Pagination still works
- [ ] Video playback unaffected
- [ ] Mobile app still connects to API

---

## 6. Monitoring & Alerts

Set up monitoring for:

1. **API Health**: Check `/health` endpoint every minute
2. **Summary Quality**: Alert if `ai_processed = FALSE` count increases
3. **Content Freshness**: Alert if no new content in 24 hours
4. **Error Rate**: Alert if 5xx errors > 1%
5. **Response Time**: Alert if p95 > 1 second

---

## 7. Test Execution Schedule

| Test Type | Frequency | Environment |
|-----------|-----------|-------------|
| Unit Tests | Every commit | CI/CD |
| Integration Tests | Every PR | Staging |
| Performance Tests | Daily | Staging |
| Manual Testing | Weekly | Staging + Prod |
| Full Regression | Before release | Staging |

---

## 8. Known Issues & Workarounds

| Issue | Impact | Workaround | Status |
|-------|--------|------------|--------|
| ~~Legacy API serving old tables~~ | Critical - no AI summaries | Updated routes to use content_items | Fixed ✅ |
| YouTube transcript fetch fails sometimes | Minor - some videos lack detailed summaries | Use video description as fallback | Open |
| RSS feeds slow during holidays | Medium - less content | Increase feed sources | Planned |

---

## 9. Test Data Requirements

For comprehensive testing, ensure:

- Minimum 50 articles in `content_items` with `ai_processed = TRUE`
- Minimum 10 videos in `content_items` with `ai_processed = TRUE`
- Content spanning at least 3 different `published_at` dates
- Content from at least 3 different sources
- At least 5 unique topics represented

---

## 10. Contact & Escalation

- **QA Lead**: [AI Assistant]
- **Backend Dev**: Check AGENT_GUIDE.md
- **Mobile Dev**: Check blips-mobile/docs/MOBILE_STRUCTURE.md
- **Critical Issues**: Create GitHub issue with `priority: critical` label
