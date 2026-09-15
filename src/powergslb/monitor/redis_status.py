"""Redis-backed health status registry for distributed deployments."""

import logging
from typing import Any

__all__ = ['RedisStatusRegistry', 'MemoryStatusRegistry']


class MemoryStatusRegistry:
    """In-memory health status registry (single-pod mode)."""

    def __init__(self) -> None:
        self._status: set[int] = set()

    def add(self, content_id: int) -> None:
        """Mark content as down."""
        self._status.add(content_id)

    def remove(self, content_id: int) -> None:
        """Mark content as up."""
        self._status.discard(content_id)

    def is_down(self, content_id: int) -> bool:
        """Check if content is down."""
        return content_id in self._status

    def snapshot(self) -> list[int]:
        """Return all down content IDs."""
        return list(self._status)

    def get_writer(self, content_id: int) -> 'StatusWriter':
        """Return a StatusWriter for the given content id."""
        return StatusWriter(self, content_id)

    def retain(self, valid_ids: set[int]) -> set[int]:
        """Drop invalid content ids; return stale ids."""
        stale = self._status - valid_ids
        self._status &= valid_ids
        return stale

    def close(self) -> None:
        """Cleanup (no-op for memory backend)."""
        pass


class RedisStatusRegistry:
    """Redis-backed health status registry for distributed deployments.
    
    Stores down content IDs in a Redis Set: "powergslb:health:down"
    All pods read from the same Redis, ensuring consistent health data.
    
    :param redis_client: redis.Redis client instance.
    :param key_prefix: Redis key prefix; default "powergslb:health:down".
    """

    def __init__(self, redis_client: Any, key_prefix: str = 'powergslb:health:down') -> None:
        self.redis = redis_client
        self.key = key_prefix
        self._logger = logging.getLogger(__name__)
        
        # Verify Redis connectivity
        try:
            self.redis.ping()
            self._logger.info('Redis status registry initialized with key: %s', self.key)
        except Exception as e:
            self._logger.error('Redis connectivity check failed: %s', e)
            raise

    def add(self, content_id: int) -> None:
        """Mark content as down in Redis Set.
        
        :param content_id: The content id to mark down.
        """
        try:
            self.redis.sadd(self.key, content_id)
        except Exception as e:
            self._logger.error('Failed to mark content %d as down: %s', content_id, e)
            raise

    def remove(self, content_id: int) -> None:
        """Mark content as up (remove from Redis Set).
        
        :param content_id: The content id to mark up.
        """
        try:
            self.redis.srem(self.key, content_id)
        except Exception as e:
            self._logger.error('Failed to mark content %d as up: %s', content_id, e)
            raise

    def is_down(self, content_id: int) -> bool:
        """Check if content is down (exists in Redis Set).
        
        :param content_id: The content id to test.
        :returns: True if content is marked down.
        """
        try:
            return bool(self.redis.sismember(self.key, content_id))
        except Exception as e:
            self._logger.error('Failed to check status of content %d: %s', content_id, e)
            # Fail open: if Redis is unavailable, treat as UP (don't block DNS)
            return False

    def snapshot(self) -> list[int]:
        """Return all down content IDs from Redis Set.
        
        :returns: List of content ids currently marked down.
        """
        try:
            down_ids = self.redis.smembers(self.key)
            return [int(content_id) for content_id in down_ids]
        except Exception as e:
            self._logger.error('Failed to snapshot health status: %s', e)
            return []

    def get_writer(self, content_id: int) -> 'StatusWriter':
        """Return a StatusWriter for the given content id.
        
        :param content_id: The content id the writer owns.
        :returns: The writer bound to this registry and content id.
        """
        return StatusWriter(self, content_id)

    def retain(self, valid_ids: set[int]) -> set[int]:
        """Remove content ids not in valid_ids from Redis; return stale ids.
        
        :param valid_ids: The content ids allowed to stay.
        :returns: The stale ids that were removed.
        """
        try:
            current_ids = {int(cid) for cid in self.redis.smembers(self.key)}
            stale = current_ids - valid_ids
            if stale:
                self.redis.srem(self.key, *stale)
            return stale
        except Exception as e:
            self._logger.error('Failed to retain valid content ids: %s', e)
            return set()

    def close(self) -> None:
        """Cleanup: close Redis connection if it owns the client.
        
        Note: Called from MonitorManager.shutdown() to ensure graceful cleanup.
        """
        # Don't close Redis here - it may be shared with leader election
        # The application layer owns the Redis connection lifecycle
        pass


class StatusWriter:
    """Write access to a single content id in the StatusRegistry.

    :param registry: The registry the writes go to (Memory or Redis).
    :param content_id: The content id this writer owns.
    """

    def __init__(self, registry: Any, content_id: int) -> None:
        self._registry = registry
        self.content_id = content_id

    def set_down(self) -> None:
        """Mark the content as down."""
        self._registry.add(self.content_id)

    def set_up(self) -> None:
        """Mark the content as up."""
        self._registry.remove(self.content_id)

    def is_down(self) -> bool:
        """Return True if the content is down."""
        return self._registry.is_down(self.content_id)
