"""
Diversity Mixer for feed re-ranking.

Implements a greedy constrained re-ranking algorithm that ensures
diverse source and topic distribution in feeds without sacrificing
relevance quality.

Algorithm:
1. Start with relevance-ranked candidate list
2. Greedily select items that satisfy diversity constraints
3. If stuck (no valid candidate), relax constraints progressively
4. Return final re-ranked list

Constraints enforced:
- No consecutive items from same source (hard, unless inventory small)
- Rolling window source cap (e.g., max 2 from same source in any 5 items)
- Optional rolling window topic cap
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Set

from app.config.diversity import DiversityConstraints, get_diversity_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class ContentItemProtocol(Protocol):
    """Protocol for content items that can be diversity-mixed."""
    id: int
    source: str
    topics: Optional[List[str]]


@dataclass
class MixerResult:
    """Result of diversity mixing operation."""
    items: List[Any]
    constraints_relaxed: bool
    relaxation_level: int
    original_order_preserved: float  # 0.0 to 1.0 - how much original order preserved
    source_distribution: Dict[str, int]
    dropped_items: int
    category_distribution: Dict[str, int] = None   # topic → count in output
    source_contribution_pct: Dict[str, float] = None  # source → % of final feed

    def __post_init__(self):
        if self.category_distribution is None:
            self.category_distribution = {}
        if self.source_contribution_pct is None:
            total = sum(self.source_distribution.values()) or 1
            self.source_contribution_pct = {
                src: round(cnt / total * 100, 1)
                for src, cnt in self.source_distribution.items()
            }


class DiversityMixer:
    """
    Greedy constrained re-ranker for feed diversity.
    
    Takes a relevance-ranked list and re-ranks it to ensure
    source and topic diversity while preserving quality.
    """
    
    def __init__(self, constraints: DiversityConstraints):
        """
        Initialize mixer with constraints.

        Args:
            constraints: Diversity constraints to enforce
        """
        self.constraints = constraints
        # NOTE: _active_constraints is NOT stored as instance state; it is a
        # local variable inside mix() so that concurrent calls don't corrupt
        # each other's relaxation level.
    
    def _build_active_constraints(
        self,
        constraints: DiversityConstraints,
        relaxation_level: int = 0
    ) -> Dict[str, Any]:
        """Build active constraint set based on relaxation level."""
        active = {
            "allow_consecutive": constraints.allow_consecutive_same_source,
            "window_size": constraints.window_size,
            "max_source_per_window": constraints.max_source_per_window,
            "max_topic_per_window": constraints.max_topic_per_window,
            "disabled": False
        }
        
        # Apply relaxation steps
        if relaxation_level > 0 and relaxation_level <= len(constraints.relaxation_steps):
            for i in range(relaxation_level):
                step = constraints.relaxation_steps[i]
                if "allow_consecutive" in step:
                    active["allow_consecutive"] = step["allow_consecutive"]
                if "max_source_per_window" in step:
                    active["max_source_per_window"] = step["max_source_per_window"]
                if "max_topic_per_window" in step:
                    active["max_topic_per_window"] = step["max_topic_per_window"]
                if step.get("disable_all"):
                    active["disabled"] = True
        
        return active
    
    def _get_source(self, item: Any) -> str:
        """Extract source from item (handles both dict and object)."""
        if isinstance(item, dict):
            return item.get("source", "unknown")
        return getattr(item, "source", "unknown")
    
    def _get_primary_topic(self, item: Any) -> Optional[str]:
        """Extract primary topic from item."""
        if isinstance(item, dict):
            topics = item.get("topics", [])
        else:
            topics = getattr(item, "topics", None) or []
        
        if topics and len(topics) > 0:
            return topics[0]
        return None
    
    def _get_id(self, item: Any) -> int:
        """Extract ID from item."""
        if isinstance(item, dict):
            return item.get("id", 0)
        return getattr(item, "id", 0)
    
    def _check_consecutive_constraint(
        self,
        candidate: Any,
        selected: List[Any],
        active_constraints: dict,
    ) -> bool:
        """Check if candidate violates consecutive same-source constraint."""
        if active_constraints["allow_consecutive"]:
            return True
        
        if not selected:
            return True
        
        last_source = self._get_source(selected[-1])
        candidate_source = self._get_source(candidate)
        
        return last_source != candidate_source
    
    def _check_window_source_constraint(
        self,
        candidate: Any,
        selected: List[Any],
        active_constraints: dict,
    ) -> bool:
        """Check if candidate violates rolling window source cap.
        
        We need to check that adding the candidate doesn't cause any window
        of size `window_size` to exceed `max_source` for the candidate's source.
        
        The windows that include the candidate are:
        - [selected[-(w-1):], candidate] - most recent w items including candidate
        """
        window_size = active_constraints["window_size"]
        max_source = active_constraints["max_source_per_window"]
        
        candidate_source = self._get_source(candidate)
        
        # Look at the last (window_size - 1) selected items
        # This forms the window that ends with the candidate
        lookback = min(len(selected), window_size - 1)
        window = selected[-lookback:] if lookback > 0 else []
        
        source_count = sum(1 for item in window if self._get_source(item) == candidate_source)
        
        # +1 for the candidate itself
        return (source_count + 1) <= max_source
    
    def _check_window_topic_constraint(
        self,
        candidate: Any,
        selected: List[Any],
        active_constraints: dict,
    ) -> bool:
        """Check if candidate violates rolling window topic cap."""
        max_topic = active_constraints["max_topic_per_window"]

        if max_topic is None:
            return True

        window_size = active_constraints["window_size"]

        if len(selected) < window_size:
            window = selected
        else:
            window = selected[-(window_size - 1):]
        
        candidate_topic = self._get_primary_topic(candidate)
        if candidate_topic is None:
            return True
        
        topic_count = sum(
            1 for item in window 
            if self._get_primary_topic(item) == candidate_topic
        )
        
        return (topic_count + 1) <= max_topic
    
    def _is_valid_candidate(self, candidate: Any, selected: List[Any], active_constraints: dict) -> bool:
        """Check if candidate satisfies all active constraints."""
        if active_constraints["disabled"]:
            return True

        if not self._check_consecutive_constraint(candidate, selected, active_constraints):
            return False

        if not self._check_window_source_constraint(candidate, selected, active_constraints):
            return False

        if not self._check_window_topic_constraint(candidate, selected, active_constraints):
            return False

        return True
    
    def mix(
        self,
        candidates: List[Any],
        target_size: int,
        session_seed: Optional[str] = None
    ) -> MixerResult:
        """
        Re-rank candidates for diversity.
        
        Args:
            candidates: Relevance-ranked candidate list
            target_size: Target output size
            session_seed: Optional seed for deterministic tie-breaking
            
        Returns:
            MixerResult with re-ranked items and metadata
        """
        if not candidates:
            return MixerResult(
                items=[],
                constraints_relaxed=False,
                relaxation_level=0,
                original_order_preserved=1.0,
                source_distribution={},
                dropped_items=0,
                category_distribution={},
                source_contribution_pct={},
            )
        
        # Check if we have enough inventory to enforce constraints
        unique_sources = len(set(self._get_source(c) for c in candidates))
        if len(candidates) < self.constraints.min_inventory_for_constraints or unique_sources < 2:
            logger.info(
                f"Inventory too small ({len(candidates)} items, {unique_sources} sources), "
                "skipping diversity mixing"
            )
            sliced = candidates[:target_size]
            src_dist = self._count_sources(sliced)
            total = sum(src_dist.values()) or 1
            return MixerResult(
                items=sliced,
                constraints_relaxed=True,
                relaxation_level=len(self.constraints.relaxation_steps),
                original_order_preserved=1.0,
                source_distribution=src_dist,
                dropped_items=max(0, len(candidates) - target_size),
                category_distribution=self._count_category_distribution(sliced),
                source_contribution_pct={s: round(c / total * 100, 1) for s, c in src_dist.items()},
            )
        
        # Greedy selection with constraint checking
        selected: List[Any] = []
        remaining = list(candidates)
        used_ids: Set[int] = set()
        relaxation_level = 0
        active_constraints = self._build_active_constraints(
            self.constraints, relaxation_level
        )

        # Phase 0: identify must-include items from per_category_minimums.
        # These are the top-ranked items per category up to their minimum count.
        # We prefer them in the greedy scan so they are selected early before
        # target_size is reached.
        must_include_ids: Set[int] = set()
        if self.constraints.per_category_minimums:
            for category, min_count in self.constraints.per_category_minimums.items():
                found = 0
                for c in candidates:
                    if found >= min_count:
                        break
                    if self._get_primary_topic(c) == category:
                        must_include_ids.add(self._get_id(c))
                        found += 1

        while len(selected) < target_size and remaining:
            # Find first valid candidate
            valid_candidate = None
            valid_index = -1

            # Prefer must-include items first (guaranteed category coverage)
            if must_include_ids:
                for i, candidate in enumerate(remaining):
                    cid = self._get_id(candidate)
                    if cid in must_include_ids and cid not in used_ids:
                        if self._is_valid_candidate(candidate, selected, active_constraints):
                            valid_candidate = candidate
                            valid_index = i
                            break

            # Fall back to regular greedy scan
            if valid_candidate is None:
                for i, candidate in enumerate(remaining):
                    if self._get_id(candidate) in used_ids:
                        continue
                    if self._is_valid_candidate(candidate, selected, active_constraints):
                        valid_candidate = candidate
                        valid_index = i
                        break
            
            if valid_candidate is not None:
                # Found valid candidate
                selected.append(valid_candidate)
                used_ids.add(self._get_id(valid_candidate))
                remaining.pop(valid_index)
            else:
                # No valid candidate found - try relaxing constraints
                if relaxation_level < len(self.constraints.relaxation_steps):
                    relaxation_level += 1
                    active_constraints = self._build_active_constraints(
                        self.constraints, relaxation_level
                    )
                    logger.debug(
                        f"Relaxing constraints to level {relaxation_level}: "
                        f"{active_constraints}"
                    )
                else:
                    # Fully relaxed - take next available
                    for i, candidate in enumerate(remaining):
                        if self._get_id(candidate) not in used_ids:
                            selected.append(candidate)
                            used_ids.add(self._get_id(candidate))
                            remaining.pop(i)
                            break
                    else:
                        # No more candidates
                        break
        
        # Calculate order preservation metric
        original_order = self._calculate_order_preservation(candidates, selected)

        # ── Safety net: force-insert any must-include items not yet placed ──
        # This handles the edge case where constraints blocked every must-include
        # item during the greedy pass (e.g., all items from one source).
        for cid in must_include_ids:
            if cid in used_ids:
                continue
            item = next((c for c in candidates if self._get_id(c) == cid), None)
            if item is None:
                continue
            if len(selected) < target_size:
                selected.append(item)
                used_ids.add(cid)
            else:
                # Evict the last non-must-include item to make room
                for idx in range(len(selected) - 1, -1, -1):
                    if self._get_id(selected[idx]) not in must_include_ids:
                        removed = selected.pop(idx)
                        used_ids.discard(self._get_id(removed))
                        selected.append(item)
                        used_ids.add(cid)
                        logger.info(
                            "[diversity] Category minimum safety-net: evicted '%s' to "
                            "place must-include item (topic=%s)",
                            self._get_source(removed),
                            self._get_primary_topic(item),
                        )
                        break

        selected = selected[:target_size]

        src_dist = self._count_sources(selected)
        total_src = sum(src_dist.values()) or 1
        return MixerResult(
            items=selected,
            constraints_relaxed=relaxation_level > 0,
            relaxation_level=relaxation_level,
            original_order_preserved=original_order,
            source_distribution=src_dist,
            dropped_items=len(candidates) - len(selected),
            category_distribution=self._count_category_distribution(selected),
            source_contribution_pct={s: round(c / total_src * 100, 1) for s, c in src_dist.items()},
        )
    
    def _count_category_distribution(self, items: List[Any]) -> Dict[str, int]:
        """Count items per primary topic/category."""
        counts: Dict[str, int] = {}
        for item in items:
            topic = self._get_primary_topic(item)
            if topic:
                counts[topic] = counts.get(topic, 0) + 1
        return counts

    def _count_sources(self, items: List[Any]) -> Dict[str, int]:
        """Count items per source."""
        counts: Dict[str, int] = {}
        for item in items:
            source = self._get_source(item)
            counts[source] = counts.get(source, 0) + 1
        return counts
    
    def _calculate_order_preservation(
        self,
        original: List[Any],
        reordered: List[Any]
    ) -> float:
        """
        Calculate how much of original order is preserved.
        
        Uses Kendall tau-like metric: ratio of concordant pairs.
        """
        if len(reordered) <= 1:
            return 1.0
        
        # Build position maps
        original_pos = {self._get_id(item): i for i, item in enumerate(original)}
        
        concordant = 0
        total = 0
        
        for i in range(len(reordered)):
            for j in range(i + 1, len(reordered)):
                id_i = self._get_id(reordered[i])
                id_j = self._get_id(reordered[j])
                
                if id_i in original_pos and id_j in original_pos:
                    total += 1
                    if original_pos[id_i] < original_pos[id_j]:
                        concordant += 1
        
        if total == 0:
            return 1.0
        
        return concordant / total


def create_mixer_for_surface(surface: str) -> DiversityMixer:
    """
    Create a DiversityMixer configured for a specific surface.
    
    Args:
        surface: One of "articles", "videos", "reels"
        
    Returns:
        Configured DiversityMixer instance
    """
    settings = get_diversity_settings()
    
    if not settings.enabled:
        # Return a pass-through mixer with disabled constraints
        constraints = DiversityConstraints(
            window_size=1,
            max_source_per_window=999,
            allow_consecutive_same_source=True,
            min_inventory_for_constraints=0
        )
        return DiversityMixer(constraints)
    
    if surface == "articles":
        constraints = settings.get_article_constraints()
    elif surface == "videos":
        constraints = settings.get_video_constraints()
    elif surface == "reels":
        constraints = settings.get_reel_constraints()
    else:
        logger.warning(f"Unknown surface '{surface}', using article defaults")
        constraints = settings.get_article_constraints()
    
    return DiversityMixer(constraints)


def mix_feed(
    items: List[Any],
    surface: str,
    target_size: int,
    session_seed: Optional[str] = None
) -> List[Any]:
    """
    Convenience function to diversity-mix a feed.
    
    Args:
        items: Relevance-ranked items
        surface: Surface type ("articles", "videos", "reels")
        target_size: Target output size
        session_seed: Optional session seed for determinism
        
    Returns:
        Diversity-mixed list of items
    """
    mixer = create_mixer_for_surface(surface)
    result = mixer.mix(items, target_size, session_seed)
    
    if result.constraints_relaxed:
        logger.info(
            f"Diversity mixing for {surface}: relaxed to level {result.relaxation_level}, "
            f"order preservation: {result.original_order_preserved:.2f}"
        )
    
    return result.items
