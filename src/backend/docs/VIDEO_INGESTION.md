# Video Ingestion System

## Overview

The Blips video ingestion system uses a **role-based architecture** to ensure balanced, high-quality content from diverse sources. This document explains the system design, channel configuration, and operational guidelines.

## Why Role-Based Ingestion?

The previous system had several problems:
- **Limited channel pool** (~9 channels) couldn't produce 30+ videos/day reliably
- **No shorts-native channels** led to severe reels shortage
- **Same creators repeated** causing content fatigue
- **Over-reliance on "explainer" style** content

The new system addresses these by:
1. Categorizing channels by their **content role**
2. Assigning **daily caps per channel** to prevent repetition
3. Using **quality tiers** to influence ranking
4. Separating **long-form and shorts pipelines**

## Channel Roles

Every channel is assigned exactly one role:

### EXPLAINER
Tech reviews, tutorials, and explanations.
- Examples: MKBHD, Dave2D, ShortCircuit
- Quota target: 8-10 videos/day
- Ranking weight: Standard to Premium

### NEWS
Daily tech news and market commentary.
- Examples: Bloomberg Technology, The Verge, Fireship
- Quota target: 8-10 videos/day
- Ranking weight: Premium (timely content)

### ENGINEER
Deep technical content, research summaries.
- Examples: Two Minute Papers, 3Blue1Brown, Ben Eater
- Quota target: 3-5 videos/day
- Ranking weight: Premium (high credibility)

### OFFICIAL
Company announcements and keynotes.
- Examples: OpenAI, Google Developers, Apple
- Quota target: 3-5 videos/day
- Ranking weight: **Supplemental** (down-ranked unless corroborated)

### SHORTS
Channels that primarily produce short-form content.
- Examples: Tech Vision, shorts from mixed channels
- Quota target: 30+ reels/day
- Note: Some channels with MIXED format contribute to both

## Content Formats

Each channel is classified by content format:

| Format | Description | Use Case |
|--------|-------------|----------|
| LONG_FORM | Videos > 3 minutes | Videos tab |
| SHORTS | YouTube Shorts < 60 seconds | Reels tab |
| MIXED | Channel produces both | Both tabs (auto-detected) |

## Quality Tiers

Channels are assigned quality tiers that modify ranking scores:

| Tier | Weight Modifier | Examples |
|------|-----------------|----------|
| PREMIUM | 1.15x | MKBHD, Bloomberg, Two Minute Papers |
| STANDARD | 1.0x | Austin Evans, Mental Outlaw |
| SUPPLEMENTAL | 0.85x | Official company channels |

## Daily Caps

To prevent creator fatigue, each channel has a daily cap:

- **Long-form videos**: Max 2 videos per channel per day
- **Shorts/Reels**: Max 4 reels per channel per day
- **News channels**: May have higher caps (3-4) due to daily output

## Channel Registry

The channel registry is defined in:
```
src/backend/app/integrations/youtube_channels.py
```

### Adding a New Channel

```python
ChannelConfig(
    channel_id="UCxxxxxxxxxxxxxx",  # YouTube channel ID
    name="Channel Name",
    role=ChannelRole.EXPLAINER,     # Role assignment
    content_format=ContentFormat.LONG_FORM,
    daily_cap=2,                     # Videos per day limit
    quality_tier=QualityTier.STANDARD,
    enabled=True,
    notes="Description of why this channel is included",
)
```

### Finding Channel IDs

1. Go to the channel's YouTube page
2. View page source or use a tool like [Comment Picker](https://commentpicker.com/youtube-channel-id.php)
3. The ID starts with "UC" followed by 22 characters

## Ingestion Pipeline

### Long-Form Videos Pipeline

1. Fetch from `get_long_form_channels()` 
2. Filter out shorts (by URL or duration check)
3. Apply per-channel daily caps
4. Generate AI summaries
5. Store with role metadata

### Shorts/Reels Pipeline

1. Fetch from `get_shorts_channels()`
2. Detect shorts via:
   - URL contains `/shorts/`
   - Channel format is SHORTS
   - For MIXED channels, check via API
3. Skip AI summarization (metadata only)
4. Apply per-channel caps

## Category Classification

Videos are categorized using keyword matching. Categories:

### Core Categories
- Artificial Intelligence
- Mobile
- Gaming
- Hardware
- Software
- Reviews

### Expanded Categories (New)
- Cloud & Infrastructure
- Cybersecurity
- Startups & Business
- AI Tools
- Developer & Engineering

## Deduplication Rules

1. **URL-based**: Same video URL → skip
2. **Video ID**: Same YouTube video ID → skip
3. **Per-channel caps**: > 2 videos/channel → skip
4. **Topic similarity**: Same entities within 24h → cluster (not skip)

## Operational Guidelines

### Monitoring

Check daily stats via logs:
```
Processing YouTube for UTC 2026-01-26: videos existing=15 target=30 remaining=15
  explainer: 12 videos
  news: 8 videos
  engineer: 3 videos
  official: 2 videos
```

### Common Issues

**Not enough videos**
- Check if channels are producing content
- Consider adding more channels in sparse roles
- Review daily caps

**Not enough reels**
- Ensure MIXED channels have shorts detected
- Add more SHORTS-native channels
- Increase caps for shorts channels

**Too many duplicates**
- Normal: Many videos are already ingested
- Check if daily caps are too high
- Review deduplication logs

### Quality Safety

**Never add channels that are:**
- Meme or entertainment focused
- Crypto/NFT hype
- Clickbait farms
- Non-tech content

## Configuration

Environment variables:

```bash
# Daily targets
DAILY_TARGET_VIDEOS=30
DAILY_TARGET_REELS=30

# Fetch depth (fetch more to work around duplicates)
YT_VIDEOS_PER_CHANNEL=30
```

## Statistics

Current channel pool capacity:

```python
from app.integrations.youtube_channels import get_channel_stats
print(get_channel_stats())
# {
#   'total_channels': 25,
#   'total_daily_cap': 55,
#   'long_form_daily_cap': 40,
#   'shorts_daily_cap': 30,
#   'channels_by_role': {...},
#   'premium_channels': 12
# }
```

## Future Improvements

1. **Dynamic quota adjustment**: Rebalance when roles are sparse
2. **Topic-level deduplication**: Suppress same topic from multiple sources
3. **Creator rotation**: Rotate which creators appear first
4. **Engagement-based caps**: Adjust caps based on view performance
