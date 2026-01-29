"""Redis-backed per-feed leases for ingestion.

Pattern:
- Claim: SET key token NX PX ttl
- Release: delete only if value matches token

If Redis isn't available, callers may fall back to Postgres advisory locks.
"""

from __future__ import annotations

from typing import Optional


LEASE_PREFIX = "blips:ingestion:lease:"


def lease_key(day_utc: str, source_type: str, feed_name: str) -> str:
    return f"{LEASE_PREFIX}{day_utc}:{source_type}:{feed_name}"


def claim_lease(redis_client, *, key: str, owner_token: str, ttl_ms: int) -> bool:
    """Attempt to claim a lease.

    Returns True if acquired, False otherwise.
    """
    if redis_client is None:
        return False

    try:
        # redis-py: set(name, value, nx=True, px=ttl_ms)
        return bool(redis_client.set(key, owner_token, nx=True, px=int(ttl_ms)))
    except Exception:
        return False


def release_lease(redis_client, *, key: str, owner_token: str) -> bool:
    """Release a lease only if the token matches."""
    if redis_client is None:
        return False

    lua = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('del', KEYS[1]) "
        "else return 0 end"
    )

    try:
        result = redis_client.eval(lua, 1, key, owner_token)
        return int(result or 0) == 1
    except Exception:
        # Some Redis test doubles (e.g., fakeredis) may not support scripting.
        pass

    # Fallback: optimistic transaction.
    try:
        try:
            from redis.exceptions import WatchError  # type: ignore
        except Exception:  # pragma: no cover
            WatchError = Exception  # type: ignore

        pipe = redis_client.pipeline()
        for _ in range(3):
            try:
                pipe.watch(key)
                current = pipe.get(key)
                if current is None:
                    pipe.reset()
                    return False

                current_str = current.decode("utf-8") if isinstance(current, (bytes, bytearray)) else str(current)
                if current_str != owner_token:
                    pipe.reset()
                    return False

                pipe.multi()
                pipe.delete(key)
                res = pipe.execute()
                deleted = int(res[0] or 0) if res else 0
                return deleted == 1
            except WatchError:
                continue
            finally:
                try:
                    pipe.reset()
                except Exception:
                    pass
    except Exception:
        # Last resort: non-atomic best-effort.
        try:
            current = redis_client.get(key)
            current_str = current.decode("utf-8") if isinstance(current, (bytes, bytearray)) else str(current)
            if current is not None and current_str == owner_token:
                return int(redis_client.delete(key) or 0) == 1
        except Exception:
            return False

    return False
