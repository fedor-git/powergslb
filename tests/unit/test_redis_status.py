"""Tests for RedisStatusRegistry and MemoryStatusRegistry."""

import pytest
from unittest.mock import MagicMock, patch, call
import redis

from powergslb.monitor.redis_status import RedisStatusRegistry, MemoryStatusRegistry, StatusWriter


class TestMemoryStatusRegistry:
    """Test MemoryStatusRegistry (single-pod mode)."""

    def test_init(self):
        """Test initialization."""
        registry = MemoryStatusRegistry()
        assert registry._status == set()

    def test_add_and_is_down(self):
        """Test marking content as down."""
        registry = MemoryStatusRegistry()
        registry.add(1)
        assert registry.is_down(1) is True
        assert registry.is_down(2) is False

    def test_remove(self):
        """Test marking content as up."""
        registry = MemoryStatusRegistry()
        registry.add(1)
        registry.remove(1)
        assert registry.is_down(1) is False

    def test_snapshot(self):
        """Test snapshot returns all down ids."""
        registry = MemoryStatusRegistry()
        registry.add(1)
        registry.add(2)
        registry.add(3)
        snapshot = registry.snapshot()
        assert set(snapshot) == {1, 2, 3}

    def test_get_writer(self):
        """Test StatusWriter creation."""
        registry = MemoryStatusRegistry()
        writer = registry.get_writer(1)
        assert isinstance(writer, StatusWriter)
        assert writer.content_id == 1

    def test_retain_removes_invalid_ids(self):
        """Test retain removes ids not in valid set."""
        registry = MemoryStatusRegistry()
        registry.add(1)
        registry.add(2)
        registry.add(3)
        stale = registry.retain({1, 2})
        assert stale == {3}
        assert registry.snapshot() == [1, 2] or registry.snapshot() == [2, 1]

    def test_close_is_noop(self):
        """Test close() does nothing."""
        registry = MemoryStatusRegistry()
        registry.close()  # Should not raise


class TestRedisStatusRegistry:
    """Test RedisStatusRegistry (distributed mode)."""

    @pytest.fixture
    def redis_mock(self):
        """Mock Redis client."""
        mock = MagicMock(spec=redis.Redis)
        mock.ping.return_value = True
        return mock

    def test_init_success(self, redis_mock):
        """Test initialization with successful Redis connection."""
        registry = RedisStatusRegistry(redis_mock)
        assert registry.redis == redis_mock
        assert registry.key == 'powergslb:health:down'
        redis_mock.ping.assert_called_once()

    def test_init_custom_key(self, redis_mock):
        """Test initialization with custom key prefix."""
        registry = RedisStatusRegistry(redis_mock, key_prefix='custom:health')
        assert registry.key == 'custom:health'

    def test_init_redis_unavailable(self, redis_mock):
        """Test initialization fails if Redis not available."""
        redis_mock.ping.side_effect = redis.ConnectionError('Connection refused')
        with pytest.raises(redis.ConnectionError):
            RedisStatusRegistry(redis_mock)

    def test_add_content_to_redis(self, redis_mock):
        """Test marking content as down adds to Redis Set."""
        registry = RedisStatusRegistry(redis_mock)
        registry.add(1)
        redis_mock.sadd.assert_called_once_with('powergslb:health:down', 1)

    def test_add_handles_redis_error(self, redis_mock):
        """Test add() handles Redis errors gracefully."""
        redis_mock.sadd.side_effect = redis.ConnectionError('Connection lost')
        registry = RedisStatusRegistry(redis_mock)
        with pytest.raises(redis.ConnectionError):
            registry.add(1)

    def test_remove_from_redis(self, redis_mock):
        """Test marking content as up removes from Redis Set."""
        registry = RedisStatusRegistry(redis_mock)
        registry.remove(1)
        redis_mock.srem.assert_called_once_with('powergslb:health:down', 1)

    def test_is_down_checks_redis_set(self, redis_mock):
        """Test is_down() checks Redis Set membership."""
        redis_mock.sismember.return_value = True
        registry = RedisStatusRegistry(redis_mock)
        assert registry.is_down(1) is True
        redis_mock.sismember.assert_called_once_with('powergslb:health:down', 1)

    def test_is_down_fails_open(self, redis_mock):
        """Test is_down() returns False if Redis fails (fail open)."""
        redis_mock.sismember.side_effect = redis.ConnectionError('Connection lost')
        registry = RedisStatusRegistry(redis_mock)
        # Should return False (fail open - don't block DNS)
        assert registry.is_down(1) is False

    def test_snapshot_from_redis(self, redis_mock):
        """Test snapshot retrieves all down ids from Redis Set."""
        redis_mock.smembers.return_value = {'1', '2', '3'}
        registry = RedisStatusRegistry(redis_mock)
        snapshot = registry.snapshot()
        assert set(snapshot) == {1, 2, 3}
        redis_mock.smembers.assert_called_once_with('powergslb:health:down')

    def test_snapshot_redis_error(self, redis_mock):
        """Test snapshot returns empty list if Redis fails."""
        redis_mock.smembers.side_effect = redis.ConnectionError('Connection lost')
        registry = RedisStatusRegistry(redis_mock)
        snapshot = registry.snapshot()
        assert snapshot == []

    def test_get_writer(self, redis_mock):
        """Test StatusWriter creation."""
        registry = RedisStatusRegistry(redis_mock)
        writer = registry.get_writer(1)
        assert isinstance(writer, StatusWriter)
        assert writer.content_id == 1

    def test_retain_removes_stale_ids(self, redis_mock):
        """Test retain removes ids not in valid set."""
        redis_mock.smembers.return_value = {'1', '2', '3'}
        registry = RedisStatusRegistry(redis_mock)
        stale = registry.retain({1, 2})
        assert stale == {3}
        redis_mock.srem.assert_called_once_with('powergslb:health:down', 3)

    def test_retain_redis_error(self, redis_mock):
        """Test retain returns empty set if Redis fails."""
        redis_mock.smembers.side_effect = redis.ConnectionError('Connection lost')
        registry = RedisStatusRegistry(redis_mock)
        stale = registry.retain({1, 2})
        assert stale == set()

    def test_close_is_noop(self, redis_mock):
        """Test close() does nothing (connection managed elsewhere)."""
        registry = RedisStatusRegistry(redis_mock)
        registry.close()  # Should not raise


class TestStatusWriter:
    """Test StatusWriter with both registry types."""

    def test_writer_with_memory_registry(self):
        """Test StatusWriter with MemoryStatusRegistry."""
        registry = MemoryStatusRegistry()
        writer = registry.get_writer(1)
        
        writer.set_down()
        assert writer.is_down() is True
        
        writer.set_up()
        assert writer.is_down() is False

    def test_writer_with_redis_registry(self):
        """Test StatusWriter with RedisStatusRegistry."""
        redis_mock = MagicMock(spec=redis.Redis)
        redis_mock.ping.return_value = True
        registry = RedisStatusRegistry(redis_mock)
        writer = registry.get_writer(1)
        
        # Test set_down
        writer.set_down()
        redis_mock.sadd.assert_called_with('powergslb:health:down', 1)
        
        # Test set_up
        writer.set_up()
        redis_mock.srem.assert_called_with('powergslb:health:down', 1)
        
        # Test is_down
        redis_mock.sismember.return_value = True
        assert writer.is_down() is True


class TestDistributedConsistency:
    """Test distributed consistency across multiple pods."""

    def test_all_pods_read_same_redis_key(self):
        """Verify all pods read from same Redis key."""
        redis_mock = MagicMock(spec=redis.Redis)
        redis_mock.ping.return_value = True
        
        # Simulate 3 pods
        pod1_registry = RedisStatusRegistry(redis_mock, key_prefix='powergslb:health:down')
        pod2_registry = RedisStatusRegistry(redis_mock, key_prefix='powergslb:health:down')
        pod3_registry = RedisStatusRegistry(redis_mock, key_prefix='powergslb:health:down')
        
        # All use same key
        assert pod1_registry.key == pod2_registry.key == pod3_registry.key
        
        # Pod 1 marks content as down
        redis_mock.sadd.return_value = 1
        pod1_registry.add(42)
        
        # Pod 2 checks same Redis
        redis_mock.sismember.return_value = True
        result = pod2_registry.is_down(42)
        
        # Pod 2 should see Pod 1's changes (same Redis key)
        assert result is True
        redis_mock.sismember.assert_called_with('powergslb:health:down', 42)

    def test_memory_registry_isolated_per_pod(self):
        """Verify MemoryStatusRegistry is isolated per pod."""
        pod1_registry = MemoryStatusRegistry()
        pod2_registry = MemoryStatusRegistry()
        
        # Pod 1 marks content as down
        pod1_registry.add(42)
        assert pod1_registry.is_down(42) is True
        
        # Pod 2 has separate memory - doesn't see Pod 1's state
        assert pod2_registry.is_down(42) is False
