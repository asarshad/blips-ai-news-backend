-- INVENTORY HEALTH QUERIES (Corrected for content_items table)
-- Table: content_items (not articles/videos)
-- Column: created_at (not ingested_at)
-- Types: 'ARTICLE', 'VIDEO', 'REEL'

-- 1. OVERALL INVENTORY HEALTH CHECK
-- Shows counts by tier for articles and videos

-- Articles by freshness tier
SELECT 
    CASE 
        WHEN created_at >= NOW() - INTERVAL '8 hours' THEN 'A (Fresh: 0-8h)'
        WHEN created_at >= NOW() - INTERVAL '24 hours' THEN 'B (Recent: 8-24h)'
        WHEN created_at >= NOW() - INTERVAL '48 hours' THEN 'C (Aging: 24-48h)'
        ELSE 'Expired (>48h)'
    END AS tier,
    COUNT(*) as count,
    ROUND(EXTRACT(EPOCH FROM (NOW() - MIN(created_at)))/60) as oldest_age_mins,
    ROUND(EXTRACT(EPOCH FROM (NOW() - MAX(created_at)))/60) as newest_age_mins
FROM content_items
WHERE type = 'ARTICLE' AND created_at >= NOW() - INTERVAL '48 hours'
GROUP BY 1
ORDER BY 1;

-- Videos by freshness tier
SELECT 
    CASE 
        WHEN created_at >= NOW() - INTERVAL '8 hours' THEN 'A (Fresh: 0-8h)'
        WHEN created_at >= NOW() - INTERVAL '24 hours' THEN 'B (Recent: 8-24h)'
        WHEN created_at >= NOW() - INTERVAL '48 hours' THEN 'C (Aging: 24-48h)'
        ELSE 'Expired (>48h)'
    END AS tier,
    COUNT(*) as count
FROM content_items
WHERE type = 'VIDEO' AND created_at >= NOW() - INTERVAL '48 hours'
GROUP BY 1
ORDER BY 1;


-- 2. SURFACE-SPECIFIC CHECKS (matches your tiered_feed_service.py config)

-- Latest surface (24h window) - expects 60% A, 30% B, 10% C
SELECT 'Latest Surface (24h)' as surface,
    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '8 hours') as tier_a,
    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND created_at < NOW() - INTERVAL '8 hours') as tier_b,
    COUNT(*) FILTER (WHERE created_at < NOW() - INTERVAL '24 hours') as tier_c
FROM content_items
WHERE type = 'ARTICLE' AND created_at >= NOW() - INTERVAL '24 hours';

-- Quick Reads surface (48h window) - expects 40% A, 40% B, 20% C
SELECT 'Quick Reads Surface (48h)' as surface,
    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '8 hours') as tier_a,
    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND created_at < NOW() - INTERVAL '8 hours') as tier_b,
    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours') as tier_c
FROM content_items
WHERE type = 'ARTICLE' AND created_at >= NOW() - INTERVAL '48 hours';


-- 3. THRESHOLD CHECK (are we below minimum?)
-- Checks if any tier is critically low (< 5 items)

SELECT 
    'ALERT: Low Inventory' as status,
    tier,
    count
FROM (
    SELECT 
        CASE 
            WHEN created_at >= NOW() - INTERVAL '8 hours' THEN 'Tier A'
            WHEN created_at >= NOW() - INTERVAL '24 hours' THEN 'Tier B'
            ELSE 'Tier C'
        END AS tier,
        COUNT(*) as count
    FROM content_items
    WHERE type = 'ARTICLE' AND created_at >= NOW() - INTERVAL '48 hours'
    GROUP BY 1
) t
WHERE count < 5;


-- 4. QUICK HEALTH SUMMARY (single row overview)
SELECT 
    (SELECT COUNT(*) FROM content_items WHERE type = 'ARTICLE' AND created_at >= NOW() - INTERVAL '8 hours') as articles_tier_a,
    (SELECT COUNT(*) FROM content_items WHERE type = 'ARTICLE' AND created_at >= NOW() - INTERVAL '24 hours') as articles_24h,
    (SELECT COUNT(*) FROM content_items WHERE type = 'ARTICLE' AND created_at >= NOW() - INTERVAL '48 hours') as articles_48h,
    (SELECT COUNT(*) FROM content_items WHERE type = 'VIDEO' AND created_at >= NOW() - INTERVAL '8 hours') as videos_tier_a,
    (SELECT COUNT(*) FROM content_items WHERE type = 'VIDEO' AND created_at >= NOW() - INTERVAL '48 hours') as videos_48h;


-- 5. DETAILED RECENT CONTENT (last 20 items with tier)
SELECT 
    id,
    title,
    created_at,
    ROUND(EXTRACT(EPOCH FROM (NOW() - created_at))/60) as age_minutes,
    CASE 
        WHEN created_at >= NOW() - INTERVAL '8 hours' THEN 'A'
        WHEN created_at >= NOW() - INTERVAL '24 hours' THEN 'B'
        ELSE 'C'
    END AS tier
FROM content_items
WHERE type = 'ARTICLE' AND created_at >= NOW() - INTERVAL '48 hours'
ORDER BY created_at DESC
LIMIT 20;


-- 6. COMBINED INVENTORY BY TYPE AND TIER
SELECT 
    type,
    CASE 
        WHEN created_at >= NOW() - INTERVAL '8 hours' THEN 'A (Fresh)'
        WHEN created_at >= NOW() - INTERVAL '24 hours' THEN 'B (Recent)'
        WHEN created_at >= NOW() - INTERVAL '48 hours' THEN 'C (Aging)'
        ELSE 'Expired'
    END AS tier,
    COUNT(*) as count
FROM content_items
WHERE created_at >= NOW() - INTERVAL '48 hours'
GROUP BY 1, 2
ORDER BY 1, 2;
