"""
Interest boost computation.

Adds an additive score nudge when content matches a user's declared
category interests (stored in user_category_selections).

Formula (per item in the personalised re-rank pass):
    interest_boost = INTEREST_WEIGHT * (1 - engagement_decay_factor)

Design constraints:
- INTEREST_WEIGHT is calibrated so the max boost is <= 20% of the average
  base_score (~0.50), i.e. INTEREST_WEIGHT <= 0.10.
- engagement_decay_factor (0.0–1.0) erodes the declared-interest boost as
  the user accumulates engagement-derived preference weights.  When a user
  has been actively interacting, their behaviour overrides their stated
  preferences.

This ensures:
1. Selected categories surface more prominently in the feed.
2. Non-selected categories are NEVER removed (soft nudge only).
3. Heavy users get an organic, behaviour-driven feed rather than a
   permanently locked-in onboarding selection.
"""

import os
from typing import List

# Max additive boost for a matched category.
# Must be <= 20% of average base_score.
# Average global_score empirically ~0.50 → 20% cap → 0.10.
INTEREST_WEIGHT: float = float(os.getenv("INTEREST_WEIGHT", "0.10"))

# Total learned-preference weight above which declared interests are fully overridden.
# E.g. if a user has accumulated preference weights summing to DECAY_SATURATION,
# engagement_decay_factor → 1.0 and the declared-interest boost → 0.
DECAY_SATURATION: float = float(os.getenv("INTEREST_DECAY_SATURATION", "50.0"))


def compute_interest_boost(
    item_topics: List[str],
    selected_categories: List[str],
    engagement_decay_factor: float = 0.0,
) -> float:
    """
    Compute the interest boost for a content item.

    Args:
        item_topics: Topics extracted from the content (first element = primary).
        selected_categories: Categories the user opted into at onboarding.
        engagement_decay_factor: 0.0 = full boost; 1.0 = no boost.
            Derived from total accumulated learned-preference weight.

    Returns:
        Additive score nudge (0.0 when no match or fully decayed).
    """
    if not selected_categories or not item_topics:
        return 0.0

    # Primary topic match only – avoids over-boosting multi-topic articles.
    primary_topic = item_topics[0]
    if primary_topic not in selected_categories:
        return 0.0

    # Clamp decay factor to [0, 1]
    decay = max(0.0, min(1.0, engagement_decay_factor))
    return INTEREST_WEIGHT * (1.0 - decay)


def compute_engagement_decay_factor(total_learned_weight: float) -> float:
    """
    Derive the decay factor from the user's total accumulated preference weight.

    As the user interacts more, their learned preferences accumulate weight.
    Once the total reaches DECAY_SATURATION, declared interests are fully
    overridden by engagement-driven signals.

    Args:
        total_learned_weight: Sum of all UserPreference.weight values for
            the user (any pref_type).

    Returns:
        Float in [0.0, 1.0]; 0.0 = new user, 1.0 = fully engaged user.
    """
    if DECAY_SATURATION <= 0:
        return 0.0
    return min(1.0, total_learned_weight / DECAY_SATURATION)
