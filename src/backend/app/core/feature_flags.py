"""
Feature flags system for Blips.

Enables dynamic feature toggling without redeploy.
Checks Redis first (for instant updates), then falls back to environment variables.

Usage:
    from app.core.feature_flags import feature_flags
    
    if feature_flags.is_enabled("chat"):
        # chat feature code
    
    # Or with dependency injection in routes
    @router.get("/chat")
    def chat(flags: FeatureFlags = Depends(get_feature_flags)):
        if not flags.is_enabled("chat"):
            raise HTTPException(status_code=503, detail="Chat feature is disabled")
"""

import os
import time
from typing import Dict, Optional

import redis
from redis.exceptions import RedisError

from app.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Feature Flag Definitions
# =============================================================================

# Default values: True = enabled, False = disabled
# In production, risky features default to OFF
FEATURE_DEFAULTS = {
    # Content ingestion (articles, videos from sources)
    "ingestion": True,
    
    # AI summarization of content
    "summarization": True,
    
    # AI chat feature
    "chat": False,  # OFF by default - requires LLM costs
    
    # Reels/short video content
    "reels": True,
    
    # Long-form video content
    "videos": True,
    
    # Clustering/story grouping
    "clustering": True,
    
    # Personalization/recommendations
    "personalization": True,
}

# Production defaults - more conservative
PROD_DEFAULTS = {
    "ingestion": True,      # Core functionality, keep on
    "summarization": True,  # Core functionality — the app exists to summarize
    "chat": False,          # LLM costs - OFF until verified
    "reels": True,          # Low risk
    "videos": True,         # Low risk
    "clustering": True,     # Low risk, local computation
    "personalization": True, # Low risk, local computation
}

# Redis key prefix for feature flags
REDIS_KEY_PREFIX = "blips:feature:"

# Cache TTL for feature flag checks (seconds)
FLAG_CACHE_TTL = 10


class FeatureFlags:
    """
    Feature flag manager with Redis-first, env-fallback strategy.
    
    Priority order:
    1. In-memory cache (TTL-based, avoids hitting Redis on every request)
    2. Redis (for instant updates without redeploy)
    3. Environment variable (FEATURE_{NAME}_ENABLED)
    4. Default value (based on ENV)
    """
    
    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self._redis = redis_client
        self._env = os.getenv("ENV", "dev").lower()
        self._cache: Dict[str, tuple] = {}  # name -> (value, expiry_timestamp)
    
    def _get_redis(self) -> Optional[redis.Redis]:
        """Get Redis client, using shared connection pool."""
        if self._redis is None:
            try:
                from app.core.dependencies import get_redis
                self._redis = get_redis()
                self._redis.ping()
            except RedisError as e:
                logger.warning(f"Redis unavailable for feature flags: {e}")
                self._redis = None
        return self._redis
    
    def _get_default(self, feature: str) -> bool:
        """Get default value for a feature based on environment."""
        if self._env == "prod":
            return PROD_DEFAULTS.get(feature, False)
        return FEATURE_DEFAULTS.get(feature, False)
    
    def _get_from_redis(self, feature: str) -> Optional[bool]:
        """Check Redis for feature flag value."""
        redis_client = self._get_redis()
        if redis_client is None:
            return None
        
        try:
            key = f"{REDIS_KEY_PREFIX}{feature}"
            value = redis_client.get(key)
            if value is not None:
                return value.decode().lower() in ("true", "1", "yes", "on")
            return None
        except RedisError as e:
            logger.warning(f"Error reading feature flag from Redis: {e}")
            return None
    
    def _get_from_env(self, feature: str) -> Optional[bool]:
        """Check environment variable for feature flag."""
        env_var = f"FEATURE_{feature.upper()}_ENABLED"
        value = os.getenv(env_var)
        if value is not None:
            return value.lower() in ("true", "1", "yes", "on")
        return None
    
    def is_enabled(self, feature: str) -> bool:
        """
        Check if a feature is enabled.
        
        Priority: Cache > Redis > Environment > Default
        
        Args:
            feature: Feature name (e.g., "chat", "ingestion")
            
        Returns:
            True if feature is enabled, False otherwise
        """
        # Normalize feature name
        feature = feature.lower().strip()
        
        # Check in-memory cache first (avoids Redis round-trip)
        cached = self._cache.get(feature)
        if cached is not None:
            value, expiry = cached
            if time.monotonic() < expiry:
                return value
            # expired — fall through
        
        # Check Redis first (dynamic, no redeploy)
        redis_value = self._get_from_redis(feature)
        if redis_value is not None:
            self._cache[feature] = (redis_value, time.monotonic() + FLAG_CACHE_TTL)
            return redis_value
        
        # Check environment variable
        env_value = self._get_from_env(feature)
        if env_value is not None:
            self._cache[feature] = (env_value, time.monotonic() + FLAG_CACHE_TTL)
            return env_value
        
        # Fall back to default
        default = self._get_default(feature)
        self._cache[feature] = (default, time.monotonic() + FLAG_CACHE_TTL)
        return default
    
    def set_flag(self, feature: str, enabled: bool) -> bool:
        """
        Set a feature flag in Redis (instant update).
        
        Args:
            feature: Feature name
            enabled: True to enable, False to disable
            
        Returns:
            True if successfully set, False otherwise
        """
        redis_client = self._get_redis()
        if redis_client is None:
            logger.error("Cannot set feature flag: Redis unavailable")
            return False
        
        try:
            key = f"{REDIS_KEY_PREFIX}{feature.lower()}"
            from app.core.config import settings as _settings
            redis_client.setex(key, _settings.CONFIG_CACHE_TTL_SECONDS, "true" if enabled else "false")
            # Invalidate local cache so next check sees the update
            self._cache.pop(feature.lower(), None)
            logger.info(f"Feature flag '{feature}' set to {enabled}")
            return True
        except RedisError as e:
            logger.error(f"Error setting feature flag: {e}")
            return False
    
    def delete_flag(self, feature: str) -> bool:
        """
        Delete a feature flag from Redis (reverts to env/default).
        
        Args:
            feature: Feature name
            
        Returns:
            True if successfully deleted, False otherwise
        """
        redis_client = self._get_redis()
        if redis_client is None:
            return False
        
        try:
            key = f"{REDIS_KEY_PREFIX}{feature.lower()}"
            redis_client.delete(key)
            self._cache.pop(feature.lower(), None)
            logger.info(f"Feature flag '{feature}' deleted from Redis")
            return True
        except RedisError as e:
            logger.error(f"Error deleting feature flag: {e}")
            return False
    
    def get_all_flags(self) -> Dict[str, dict]:
        """
        Get status of all known feature flags.
        
        Returns:
            Dict with feature names as keys and status info as values
        """
        all_features = set(FEATURE_DEFAULTS.keys()) | set(PROD_DEFAULTS.keys())
        result = {}
        
        for feature in sorted(all_features):
            redis_val = self._get_from_redis(feature)
            env_val = self._get_from_env(feature)
            default_val = self._get_default(feature)
            
            result[feature] = {
                "enabled": self.is_enabled(feature),
                "source": "redis" if redis_val is not None else ("env" if env_val is not None else "default"),
                "redis_value": redis_val,
                "env_value": env_val,
                "default_value": default_val,
            }
        
        return result
    
    def require_feature(self, feature: str, error_message: str = None):
        """
        Raise an exception if feature is disabled.
        
        Args:
            feature: Feature name to check
            error_message: Custom error message (optional)
            
        Raises:
            FeatureDisabledError if feature is disabled
        """
        if not self.is_enabled(feature):
            msg = error_message or f"Feature '{feature}' is currently disabled"
            raise FeatureDisabledError(msg)


class FeatureDisabledError(Exception):
    """Raised when attempting to use a disabled feature."""
    pass


# =============================================================================
# Global Instance and Dependency
# =============================================================================

# Singleton instance (lazy initialization)
_feature_flags: Optional[FeatureFlags] = None


def get_feature_flags() -> FeatureFlags:
    """
    Get the feature flags instance.
    
    Can be used as a FastAPI dependency or called directly.
    """
    global _feature_flags
    if _feature_flags is None:
        _feature_flags = FeatureFlags()
    return _feature_flags


# Convenience alias — lazily initialized on first property access.
class _LazyFeatureFlags:
    """Proxy that delays FeatureFlags construction until first use."""

    _instance: Optional[FeatureFlags] = None

    def __getattr__(self, name: str):  # noqa: ANN001
        if self._instance is None:
            self._instance = FeatureFlags()
        return getattr(self._instance, name)


feature_flags: FeatureFlags = _LazyFeatureFlags()  # type: ignore[assignment]
