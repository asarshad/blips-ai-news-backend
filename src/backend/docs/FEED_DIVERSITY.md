# Feed Diversity System

This document describes the Diversity Mixer, a feed re-ranking system that ensures varied source and topic distribution across all feed surfaces (articles, videos, reels).

## Problem Statement

Without diversity constraints, feeds can become dominated by a single prolific source. For example:
- The Verge might publish 20 YouTube shorts per day
- Other sources publish only 2-3
- Result: Reels feed shows 15 Verge videos in a row

This creates a poor user experience where users see the same source repeatedly.

## Solution: Greedy Constrained Re-ranking

The Diversity Mixer implements a **greedy constrained re-ranking algorithm** that:

1. Takes relevance-ranked candidate list as input
2. Applies diversity constraints during selection
3. Progressively relaxes constraints if needed
4. Returns a mixed list that balances quality and diversity

### Key Properties

- **Deterministic**: Same input always produces same output
- **Quality-preserving**: Higher-ranked items are preferred
- **Graceful degradation**: Relaxes constraints when necessary
- **Configurable per surface**: Different rules for articles/videos/reels

## Algorithm

```
function mix(candidates, target_size):
    selected = []
    remaining = candidates
    relaxation_level = 0
    
    while len(selected) < target_size and remaining not empty:
        # Find first valid candidate
        for candidate in remaining:
            if satisfies_all_constraints(candidate, selected):
                selected.append(candidate)
                remaining.remove(candidate)
                break
        else:
            # No valid candidate found
            if can_relax_further():
                relaxation_level++
                relax_constraints()
            else:
                # Take next available (fully relaxed)
                selected.append(remaining[0])
                remaining.remove(remaining[0])
    
    return selected
```

## Constraints

### 1. No Consecutive Same Source (Hard)

Items from the same source cannot appear back-to-back.

```
❌ [Verge, Verge, TC, Wired]    # Two Verge in a row
✅ [Verge, TC, Verge, Wired]    # Interleaved
```

This is the **hard constraint** - only relaxed when inventory is too small.

### 2. Rolling Window Source Cap

Within any window of N items, a source can appear at most K times.

**Example (window=5, max=2):**
```
Position:  1     2     3     4     5     6     7
Source:   [Verge, TC, Wired, Verge, Ars, TC, Verge]
                 └──────────────────┘
                 Window at position 2:
                 TC, Wired, Verge, Ars, TC
                 ✅ Each source appears ≤2 times
```

### 3. Rolling Window Topic Cap (Optional)

Within any window of N items, a topic can appear at most M times.

**Example (window=5, max_topic=2):**
```
Topics:   [AI, AI, Gaming, Mobile, AI, ...]
                        └──────────────┘
                        This window has 2 AI items - OK
```

## Configuration

### Environment Variables

```bash
# Global enable/disable
DIVERSITY_ENABLED=true

# Article constraints
DIVERSITY_ARTICLES_WINDOW_SIZE=5
DIVERSITY_ARTICLES_MAX_SOURCE=2
DIVERSITY_ARTICLES_MAX_TOPIC=3
DIVERSITY_ARTICLES_ALLOW_CONSECUTIVE=false
DIVERSITY_ARTICLES_MIN_INVENTORY=8

# Video constraints
DIVERSITY_VIDEOS_WINDOW_SIZE=5
DIVERSITY_VIDEOS_MAX_SOURCE=2
DIVERSITY_VIDEOS_MAX_TOPIC=
DIVERSITY_VIDEOS_ALLOW_CONSECUTIVE=false
DIVERSITY_VIDEOS_MIN_INVENTORY=6

# Reel constraints (stricter - this is where the problem is worst)
DIVERSITY_REELS_WINDOW_SIZE=4
DIVERSITY_REELS_MAX_SOURCE=1
DIVERSITY_REELS_MAX_TOPIC=2
DIVERSITY_REELS_ALLOW_CONSECUTIVE=false
DIVERSITY_REELS_MIN_INVENTORY=5
```

### Default Values by Surface

| Parameter | Articles | Videos | Reels |
|-----------|----------|--------|-------|
| Window Size | 5 | 5 | 4 |
| Max Source/Window | 2 | 2 | **1** |
| Max Topic/Window | 3 | None | 2 |
| Allow Consecutive | No | No | No |
| Min Inventory | 8 | 6 | 5 |

**Reels are stricter** because they're the most affected surface.

## Relaxation Steps

When constraints cannot be satisfied, the mixer relaxes them progressively:

1. **Level 1**: Allow consecutive same source
2. **Level 2**: Increase max_source_per_window to 3
3. **Level 3**: Increase max_source_per_window to 4
4. **Level 4**: Disable all constraints

The mixer tries to use the least relaxation necessary.

## Integration

The mixer is integrated into the API routes:

### Articles (`/api/v1/articles/recent`)

```python
# Fetch more candidates than needed for mixing
items = repo.get_by_type(ARTICLE, limit=fetch_limit)

# Apply diversity mixing
mixed_items = mix_feed(items, surface="articles", target_size=limit)
```

### Videos (`/api/v1/videos/recent`)

```python
items = repo.get_by_type(VIDEO, limit=fetch_limit)
mixed_items = mix_feed(items, surface="videos", target_size=limit)
```

### Reels (`/api/v1/videos/reels`)

```python
# Fetch larger pool for better diversity
items = repo.get_by_type(REEL, limit=limit * 4)
mixed_items = mix_feed(items, surface="reels", target_size=limit)
```

## Tuning Guide

### Problem: Still seeing same source too often

1. **Decrease `max_source_per_window`**:
   ```bash
   DIVERSITY_REELS_MAX_SOURCE=1
   ```

2. **Decrease window size** (makes constraint tighter):
   ```bash
   DIVERSITY_REELS_WINDOW_SIZE=3
   ```

### Problem: Feed quality decreased noticeably

1. **Increase `max_source_per_window`**:
   ```bash
   DIVERSITY_ARTICLES_MAX_SOURCE=3
   ```

2. **Allow consecutive** for less critical surfaces:
   ```bash
   DIVERSITY_VIDEOS_ALLOW_CONSECUTIVE=true
   ```

### Problem: Not enough inventory to diversify

1. **Decrease `min_inventory_for_constraints`**:
   ```bash
   DIVERSITY_REELS_MIN_INVENTORY=3
   ```
   
   Note: This may cause more relaxation.

2. **Increase the candidate pool** (in code):
   ```python
   fetch_limit = limit * 5  # More candidates
   ```

### Problem: Too many items from same topic

1. **Enable topic cap**:
   ```bash
   DIVERSITY_VIDEOS_MAX_TOPIC=2
   ```

### Problem: Relaxation happening too often

Check logs for:
```
Diversity mixing for reels: relaxed to level 2, order preservation: 0.75
```

This indicates constraint relaxation. Solutions:
1. Add more content sources
2. Relax constraints in config
3. Accept current behavior (quality vs diversity tradeoff)

## Metrics

The mixer reports:

| Metric | Description |
|--------|-------------|
| `constraints_relaxed` | Whether any relaxation occurred |
| `relaxation_level` | How many relaxation steps applied (0-4) |
| `original_order_preserved` | 0.0-1.0, how much relevance order preserved |
| `source_distribution` | Count of items per source |
| `dropped_items` | Items that couldn't be included |

## Testing

Run tests:
```bash
cd src/backend
pytest tests/test_diversity_mixer.py -v
```

Key test scenarios:
- No consecutive same source
- Window cap enforcement
- Relaxation under pressure
- Small inventory handling
- Order preservation

## Debugging

Enable debug logging to see constraint decisions:

```python
import logging
logging.getLogger("app.services.diversity_mixer").setLevel(logging.DEBUG)
```

You'll see:
```
DEBUG: Relaxing constraints to level 1: {'allow_consecutive': True, ...}
INFO: Diversity mixing for reels: relaxed to level 1, order preservation: 0.85
```

## Future Improvements

1. **User-level diversity**: Remember what user has seen in session
2. **Time-aware mixing**: Weight newer items even if from same source
3. **Collaborative filtering**: Use what similar users engaged with
4. **A/B testing hooks**: Compare different constraint configurations
