-- Non-English Content Cleanup Script
-- Purpose: Suppress existing non-English content that leaked through before language filter was deployed
-- Date: February 2026
-- 
-- INSTRUCTIONS:
-- 1. Run this against your production database
-- 2. Review the SELECT first to see what will be affected
-- 3. Run the UPDATE to suppress the content
-- 4. Content is soft-deleted (is_suppressed=true), not hard-deleted

-- ============================================================================
-- STEP 1: Review what will be affected (DRY RUN)
-- ============================================================================

-- Find Spanish content
SELECT 
    id, 
    title, 
    source, 
    content_type,
    created_at
FROM content_items 
WHERE 
    (title ILIKE '%en español%' OR title ILIKE '%español%')
    AND is_suppressed = false
ORDER BY created_at DESC;

-- Count affected items
SELECT 
    content_type,
    COUNT(*) as count
FROM content_items 
WHERE 
    (title ILIKE '%en español%' OR title ILIKE '%español%')
    AND is_suppressed = false
GROUP BY content_type;

-- ============================================================================
-- STEP 2: Suppress the content (RUN THIS AFTER REVIEWING STEP 1)
-- ============================================================================

-- Uncomment and run this to suppress the content:
-- UPDATE content_items 
-- SET 
--     is_suppressed = true,
--     updated_at = NOW()
-- WHERE 
--     (title ILIKE '%en español%' OR title ILIKE '%español%')
--     AND is_suppressed = false;

-- ============================================================================
-- STEP 3: Verify the cleanup
-- ============================================================================

-- After running the UPDATE, verify no unsuppressed non-English content remains:
-- SELECT COUNT(*) FROM content_items 
-- WHERE 
--     (title ILIKE '%en español%' OR title ILIKE '%español%')
--     AND is_suppressed = false;
-- Expected result: 0

-- ============================================================================
-- ADDITIONAL PATTERNS (optional - uncomment if needed)
-- ============================================================================

-- French content
-- UPDATE content_items SET is_suppressed = true, updated_at = NOW()
-- WHERE (title ILIKE '%en français%' OR title ILIKE '%français%')
-- AND is_suppressed = false;

-- German content  
-- UPDATE content_items SET is_suppressed = true, updated_at = NOW()
-- WHERE (title ILIKE '%auf deutsch%' OR title ILIKE '%deutschen%')
-- AND is_suppressed = false;

-- Portuguese content
-- UPDATE content_items SET is_suppressed = true, updated_at = NOW()
-- WHERE (title ILIKE '%em português%' OR title ILIKE '%português%')
-- AND is_suppressed = false;
