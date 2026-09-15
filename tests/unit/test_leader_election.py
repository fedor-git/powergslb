"""Unit tests for leader election."""

import json
import time
from unittest.mock import MagicMock, Mock, patch

import pytest
import redis

from powergslb.leader_election import LeaderElection


@pytest.fixture
def redis_mock() -> Mock:
    """Mock Redis client."""
    return MagicMock(spec=redis.Redis)


@pytest.fixture
def redis_config() -> dict:
    """Default Redis config."""
    return {
        'host': 'localhost',
        'port': 6379,
        'db': 0,
        'connection_timeout': 2,
        'socket_timeout': 2,
    }


@pytest.fixture
def le_config() -> dict:
    """Default leader election config."""
    return {
        'enabled': True,
        'lock_key': 'powergslb:monitor:leader',
        'ttl': 30,
        'renewal_interval': 10,
        'acquisition_retry_interval': 5,
        'fallback_if_redis_unavailable': False,
    }


class TestLeaderElectionInitialization:
    """Test LeaderElection initialization."""

    def test_init_success(self, redis_config: dict, le_config: dict) -> None:
        """Test successful initialization with Redis available."""
        with patch('powergslb.leader_election.redis.Redis') as mock_redis_class:
            mock_client = MagicMock(spec=redis.Redis)
            mock_redis_class.return_value = mock_client

            le = LeaderElection(redis_config, le_config)

            assert le.enabled is True
            assert le.is_leader is False
            mock_client.ping.assert_called_once()

    def test_init_redis_unavailable_with_fallback(self, redis_config: dict, le_config: dict) -> None:
        """Test initialization when Redis is unavailable but fallback is enabled."""
        le_config['fallback_if_redis_unavailable'] = True
        
        with patch('powergslb.leader_election.redis.Redis') as mock_redis_class:
            mock_redis_class.side_effect = redis.ConnectionError('Connection refused')

            le = LeaderElection(redis_config, le_config)

            assert le.enabled is True
            assert le.redis_client is None

    def test_init_redis_unavailable_without_fallback(self, redis_config: dict, le_config: dict) -> None:
        """Test initialization when Redis is unavailable and fallback is disabled."""
        le_config['fallback_if_redis_unavailable'] = False
        
        with patch('powergslb.leader_election.redis.Redis') as mock_redis_class:
            mock_redis_class.side_effect = redis.ConnectionError('Connection refused')

            with pytest.raises(redis.ConnectionError):
                LeaderElection(redis_config, le_config)

    def test_init_disabled(self, redis_config: dict, le_config: dict) -> None:
        """Test initialization with leader election disabled."""
        le_config['enabled'] = False

        le = LeaderElection(redis_config, le_config)

        assert le.enabled is False
        assert le.is_leader is False

    def test_init_invalid_config_renewal_gte_ttl(self, redis_config: dict, le_config: dict) -> None:
        """Test that renewal_interval >= ttl raises ValueError."""
        le_config['renewal_interval'] = 30
        le_config['ttl'] = 30

        with pytest.raises(ValueError, match='renewal_interval'):
            LeaderElection(redis_config, le_config)


class TestLeaderElectionAcquisition:
    """Test leader lock acquisition."""

    def test_acquire_success(self, redis_config: dict, le_config: dict, redis_mock: Mock) -> None:
        """Test successful lock acquisition."""
        with patch('powergslb.leader_election.redis.Redis', return_value=redis_mock):
            redis_mock.set.return_value = True

            le = LeaderElection(redis_config, le_config)
            result = le.acquire()

            assert result is True
            assert le.is_leader is True
            redis_mock.set.assert_called_once()
            call_kwargs = redis_mock.set.call_args[1]
            assert call_kwargs['nx'] is True
            assert call_kwargs['ex'] == 30

    def test_acquire_failed_lock_held(self, redis_config: dict, le_config: dict, redis_mock: Mock) -> None:
        """Test lock acquisition fails when lock is already held."""
        with patch('powergslb.leader_election.redis.Redis', return_value=redis_mock):
            redis_mock.set.return_value = False
            redis_mock.get.return_value = json.dumps({'pod_id': 'other-pod'})

            le = LeaderElection(redis_config, le_config)
            result = le.acquire()

            assert result is False
            assert le.is_leader is False

    def test_acquire_disabled(self, redis_config: dict, le_config: dict) -> None:
        """Test acquisition when leader election is disabled."""
        le_config['enabled'] = False

        le = LeaderElection(redis_config, le_config)
        result = le.acquire()

        assert result is True
        assert le.is_leader is True

    def test_acquire_redis_unavailable_fallback(self, redis_config: dict, le_config: dict) -> None:
        """Test acquisition when Redis is unavailable with fallback."""
        le_config['fallback_if_redis_unavailable'] = True

        with patch('powergslb.leader_election.redis.Redis') as mock_redis_class:
            mock_redis_class.side_effect = redis.ConnectionError()

            le = LeaderElection(redis_config, le_config)
            result = le.acquire()

            assert result is True
            assert le.is_leader is True


class TestLeaderElectionRenewal:
    """Test leader lock renewal."""

    def test_renew_success(self, redis_config: dict, le_config: dict, redis_mock: Mock) -> None:
        """Test successful lock renewal."""
        with patch('powergslb.leader_election.redis.Redis', return_value=redis_mock):
            # First: acquire
            redis_mock.set.return_value = True
            le = LeaderElection(redis_config, le_config)
            le.acquire()

            # Then: renew
            redis_mock.eval.return_value = 1  # Lua script returns 1 if renewal succeeded
            result = le.renew()

            assert result is True
            assert le.is_leader is True
            redis_mock.eval.assert_called_once()

    def test_renew_failed_lost_leadership(self, redis_config: dict, le_config: dict, redis_mock: Mock) -> None:
        """Test renewal fails when leadership is lost."""
        with patch('powergslb.leader_election.redis.Redis', return_value=redis_mock):
            # First: acquire
            redis_mock.set.return_value = True
            le = LeaderElection(redis_config, le_config)
            le.acquire()

            # Then: renewal fails (lost leadership)
            redis_mock.eval.return_value = 0
            result = le.renew()

            assert result is False
            assert le.is_leader is False

    def test_renew_not_leader(self, redis_config: dict, le_config: dict, redis_mock: Mock) -> None:
        """Test renewal when not leader returns False."""
        with patch('powergslb.leader_election.redis.Redis', return_value=redis_mock):
            redis_mock.set.return_value = False
            redis_mock.get.return_value = json.dumps({'pod_id': 'other-pod'})

            le = LeaderElection(redis_config, le_config)
            le.acquire()

            result = le.renew()

            assert result is False
            redis_mock.eval.assert_not_called()

    def test_renew_disabled(self, redis_config: dict, le_config: dict) -> None:
        """Test renewal when leader election is disabled."""
        le_config['enabled'] = False

        le = LeaderElection(redis_config, le_config)
        le.is_leader = True

        result = le.renew()

        assert result is True


class TestLeaderElectionShutdown:
    """Test leader election shutdown."""

    def test_shutdown_as_leader(self, redis_config: dict, le_config: dict, redis_mock: Mock) -> None:
        """Test shutdown releases the lock when leader."""
        with patch('powergslb.leader_election.redis.Redis', return_value=redis_mock):
            redis_mock.set.return_value = True

            le = LeaderElection(redis_config, le_config)
            le.acquire()

            le.shutdown()

            redis_mock.delete.assert_called_once_with(le.lock_key)
            assert le.is_leader is False

    def test_shutdown_as_standby(self, redis_config: dict, le_config: dict, redis_mock: Mock) -> None:
        """Test shutdown does nothing when not leader."""
        with patch('powergslb.leader_election.redis.Redis', return_value=redis_mock):
            redis_mock.set.return_value = False
            redis_mock.get.return_value = json.dumps({'pod_id': 'other-pod'})

            le = LeaderElection(redis_config, le_config)
            le.acquire()

            le.shutdown()

            redis_mock.delete.assert_not_called()

    def test_shutdown_disabled(self, redis_config: dict, le_config: dict) -> None:
        """Test shutdown when leader election is disabled."""
        le_config['enabled'] = False

        le = LeaderElection(redis_config, le_config)
        le.is_leader = True

        le.shutdown()  # Should not raise

        assert le.is_leader is False


class TestLeaderElectionWaitForLeadership:
    """Test waiting for leadership."""

    def test_wait_for_leadership_already_leader(self, redis_config: dict, le_config: dict, redis_mock: Mock) -> None:
        """Test wait_for_leadership returns immediately if already leader."""
        with patch('powergslb.leader_election.redis.Redis', return_value=redis_mock):
            redis_mock.set.return_value = True

            le = LeaderElection(redis_config, le_config)
            le.acquire()

            # Should not sleep, should return immediately
            with patch('time.sleep') as mock_sleep:
                le.wait_for_leadership()
                mock_sleep.assert_not_called()

    def test_wait_for_leadership_becomes_leader(self, redis_config: dict, le_config: dict, redis_mock: Mock) -> None:
        """Test wait_for_leadership blocks until leadership acquired."""
        with patch('powergslb.leader_election.redis.Redis', return_value=redis_mock):
            # Simulate: first call fails, second call succeeds
            redis_mock.set.side_effect = [False, True]

            le = LeaderElection(redis_config, le_config)

            with patch('time.sleep') as mock_sleep:
                le.wait_for_leadership()

                # Should have slept once (waiting for first attempt to fail, then acquiring)
                mock_sleep.assert_called()
                assert le.is_leader is True
