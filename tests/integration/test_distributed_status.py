"""Integration tests for Redis-backed distributed status registry."""

import time
import pytest
import redis

from powergslb.monitor.redis_status import RedisStatusRegistry, MemoryStatusRegistry


@pytest.fixture
def redis_connection():
    """Connect to Redis (assumes docker Redis on localhost:6379)."""
    client = redis.Redis(
        host='localhost',
        port=6379,
        db=1,  # Use DB 1 for tests to avoid interfering with prod data
        decode_responses=True
    )
    try:
        client.ping()
        return client
    except redis.ConnectionError:
        pytest.skip("Redis not available on localhost:6379")


class TestRedisStatusRegistryIntegration:
    """Integration tests with real Redis."""

    def test_redis_connection(self, redis_connection):
        """Verify Redis connection."""
        assert redis_connection.ping() is True

    def test_add_and_is_down_with_real_redis(self, redis_connection):
        """Test marking content as down with real Redis."""
        registry = RedisStatusRegistry(redis_connection, key_prefix='test:health:down')
        
        # Clean up before test
        registry.redis.delete(registry.key)
        
        try:
            # Mark as down
            registry.add(42)
            assert registry.is_down(42) is True
            assert registry.is_down(99) is False
            
            # Verify in Redis
            assert 42 in registry.redis.smembers(registry.key)
            assert 99 not in registry.redis.smembers(registry.key)
        finally:
            # Cleanup
            registry.redis.delete(registry.key)

    def test_remove_with_real_redis(self, redis_connection):
        """Test marking content as up with real Redis."""
        registry = RedisStatusRegistry(redis_connection, key_prefix='test:health:down')
        registry.redis.delete(registry.key)
        
        try:
            registry.add(42)
            assert registry.is_down(42) is True
            
            registry.remove(42)
            assert registry.is_down(42) is False
        finally:
            registry.redis.delete(registry.key)

    def test_snapshot_with_real_redis(self, redis_connection):
        """Test snapshot with real Redis."""
        registry = RedisStatusRegistry(redis_connection, key_prefix='test:health:down')
        registry.redis.delete(registry.key)
        
        try:
            registry.add(1)
            registry.add(2)
            registry.add(3)
            
            snapshot = registry.snapshot()
            assert set(snapshot) == {1, 2, 3}
        finally:
            registry.redis.delete(registry.key)

    def test_distributed_consistency(self, redis_connection):
        """Test that two registry instances read/write same Redis data."""
        key = 'test:distributed:health:down'
        
        # Clean up
        redis_connection.delete(key)
        
        try:
            # Pod 1: marks backend as down
            pod1_registry = RedisStatusRegistry(redis_connection, key_prefix=key)
            pod1_registry.add(42)
            
            # Pod 2: reads from Redis
            pod2_registry = RedisStatusRegistry(redis_connection, key_prefix=key)
            assert pod2_registry.is_down(42) is True
            
            # Pod 1: marks backend as up
            pod1_registry.remove(42)
            
            # Pod 2: reads updated status
            assert pod2_registry.is_down(42) is False
            
            # Verify consistency
            assert pod1_registry.snapshot() == pod2_registry.snapshot()
        finally:
            redis_connection.delete(key)

    def test_retain_with_real_redis(self, redis_connection):
        """Test retain (cleanup stale ids) with real Redis."""
        registry = RedisStatusRegistry(redis_connection, key_prefix='test:health:down')
        registry.redis.delete(registry.key)
        
        try:
            # Add some content ids
            registry.add(1)
            registry.add(2)
            registry.add(3)
            registry.add(4)
            
            # Retain only 1 and 2 (remove stale 3, 4)
            stale = registry.retain({1, 2})
            
            assert stale == {3, 4}
            assert registry.snapshot() == [1, 2] or registry.snapshot() == [2, 1]
        finally:
            registry.redis.delete(registry.key)

    def test_multiple_registries_same_key(self, redis_connection):
        """Test multiple registry instances updating same Redis key."""
        key = 'test:multi:health:down'
        redis_connection.delete(key)
        
        try:
            # Create 3 registries (simulating 3 pods)
            registries = [
                RedisStatusRegistry(redis_connection, key_prefix=key),
                RedisStatusRegistry(redis_connection, key_prefix=key),
                RedisStatusRegistry(redis_connection, key_prefix=key),
            ]
            
            # Each registry adds different content
            registries[0].add(10)
            registries[1].add(20)
            registries[2].add(30)
            
            # All registries see all data (same Redis key)
            for i, registry in enumerate(registries):
                snapshot = registry.snapshot()
                assert set(snapshot) == {10, 20, 30}, f"Registry {i} has incorrect snapshot"
                assert registry.is_down(10) is True
                assert registry.is_down(20) is True
                assert registry.is_down(30) is True
                
                # And can modify data
                registry.remove(10)
                assert registry.is_down(10) is False
                
                # Change is visible to others
                for other_registry in registries:
                    assert other_registry.is_down(10) is False
                
                # Add it back for next iteration
                registry.add(10)
        finally:
            redis_connection.delete(key)

    def test_concurrent_operations(self, redis_connection):
        """Test concurrent add/remove operations."""
        registry = RedisStatusRegistry(redis_connection, key_prefix='test:concurrent:health:down')
        registry.redis.delete(registry.key)
        
        try:
            # Simulate rapid status changes
            for i in range(100):
                registry.add(i)
                assert registry.is_down(i) is True
                registry.remove(i)
                assert registry.is_down(i) is False
            
            # All should be removed
            assert registry.snapshot() == []
        finally:
            registry.redis.delete(registry.key)

    def test_large_dataset(self, redis_connection):
        """Test registry with large number of content ids."""
        registry = RedisStatusRegistry(redis_connection, key_prefix='test:large:health:down')
        registry.redis.delete(registry.key)
        
        try:
            # Add 10000 content ids
            for i in range(10000):
                registry.add(i)
            
            # Snapshot should contain all
            snapshot = registry.snapshot()
            assert len(snapshot) == 10000
            
            # Spot check some
            assert registry.is_down(0) is True
            assert registry.is_down(5000) is True
            assert registry.is_down(9999) is True
            
            # Remove half
            for i in range(0, 5000):
                registry.remove(i)
            
            snapshot = registry.snapshot()
            assert len(snapshot) == 5000
        finally:
            registry.redis.delete(registry.key)


class TestMemoryStatusRegistryCompare:
    """Compare MemoryStatusRegistry behavior with Redis."""

    def test_memory_vs_redis_same_operations(self, redis_connection):
        """Verify MemoryStatusRegistry and RedisStatusRegistry produce same results for same operations."""
        memory_registry = MemoryStatusRegistry()
        redis_registry = RedisStatusRegistry(redis_connection, key_prefix='test:compare:health:down')
        redis_registry.redis.delete(redis_registry.key)
        
        try:
            operations = [
                ('add', 1), ('add', 2), ('add', 3),
                ('is_down', 1, True), ('is_down', 4, False),
                ('remove', 2),
                ('is_down', 2, False), ('is_down', 3, True),
                ('snapshot', {1, 3}),
            ]
            
            for op in operations:
                if op[0] == 'add':
                    memory_registry.add(op[1])
                    redis_registry.add(op[1])
                elif op[0] == 'remove':
                    memory_registry.remove(op[1])
                    redis_registry.remove(op[1])
                elif op[0] == 'is_down':
                    content_id, expected = op[1], op[2]
                    assert memory_registry.is_down(content_id) == expected
                    assert redis_registry.is_down(content_id) == expected
                elif op[0] == 'snapshot':
                    expected_set = op[1]
                    assert set(memory_registry.snapshot()) == expected_set
                    assert set(redis_registry.snapshot()) == expected_set
        finally:
            redis_registry.redis.delete(redis_registry.key)

    def test_memory_isolation_vs_redis_sharing(self, redis_connection):
        """Verify memory registries are isolated while redis registries share data."""
        # Two memory registries should be isolated
        mem1 = MemoryStatusRegistry()
        mem2 = MemoryStatusRegistry()
        
        mem1.add(42)
        assert mem1.is_down(42) is True
        assert mem2.is_down(42) is False  # Isolated
        
        # Two redis registries should share data
        redis1 = RedisStatusRegistry(redis_connection, key_prefix='test:isolation:health:down')
        redis2 = RedisStatusRegistry(redis_connection, key_prefix='test:isolation:health:down')
        redis1.redis.delete(redis1.key)
        
        try:
            redis1.add(42)
            assert redis1.is_down(42) is True
            assert redis2.is_down(42) is True  # Shared via Redis
        finally:
            redis1.redis.delete(redis1.key)
