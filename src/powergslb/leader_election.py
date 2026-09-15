"""Distributed leader election using Redis for multi-instance deployments."""

import json
import logging
import socket
import threading
import time
from typing import Any

import redis

__all__ = ['LeaderElection']


class LeaderElection:
    """
    Redis-based leader election using Redlock algorithm.
    
    Only one instance acquires the leader lock and runs the monitor.
    Other instances wait for leadership. On leader failure, standby instances
    automatically promote. Graceful degradation if Redis is unavailable.
    """

    def __init__(self,
                 redis_config: dict[str, Any],
                 le_config: dict[str, Any],
                 **kwargs: Any) -> None:
        """
        Initialize leader election.
        
        :param redis_config: Redis connection parameters (host, port, db, etc.)
        :param le_config: Leader election config (ttl, lock_key, renewal_interval, etc.)
        :raises Exception: If Redis is required but unavailable.
        """
        self.enabled = le_config.get('enabled', True)
        self.lock_key = le_config.get('lock_key', 'powergslb:monitor:leader')
        self.ttl = le_config.get('ttl', 30)
        self.renewal_interval = le_config.get('renewal_interval', 10)
        self.acquisition_retry = le_config.get('acquisition_retry_interval', 5)
        self.fallback_if_unavailable = le_config.get('fallback_if_redis_unavailable', True)
        
        # Validate config
        if self.renewal_interval >= self.ttl:
            raise ValueError(f'renewal_interval ({self.renewal_interval}) must be < ttl ({self.ttl})')
        
        self.pod_id = socket.gethostname()
        self.is_leader = False
        self.redis_client: redis.Redis[str] | None = None
        self.__renewal_stop = threading.Event()
        self.__renewal_thread: threading.Thread | None = None
        
        # Initialize Redis connection
        if self.enabled:
            try:
                self.redis_client = redis.Redis(
                    host=redis_config.get('host', 'localhost'),
                    port=redis_config.get('port', 6379),
                    db=redis_config.get('db', 0),
                    password=redis_config.get('password', None),
                    socket_connect_timeout=redis_config.get('connection_timeout', 2),
                    socket_timeout=redis_config.get('socket_timeout', 2),
                    decode_responses=True,
                )
                # Test connection
                self.redis_client.ping()
                logging.info('Leader election: Redis connected (pod=%s)', self.pod_id)
            except (redis.ConnectionError, redis.ResponseError) as e:
                if self.fallback_if_unavailable:
                    logging.warning('Leader election: Redis unavailable, fallback enabled: %s', e)
                    self.redis_client = None
                else:
                    logging.error('Leader election: Redis unavailable and fallback disabled')
                    raise
        
        # Start the periodic renewal thread
        self.__renewal_thread = threading.Thread(target=self.__renewal_loop, daemon=True)
        self.__renewal_thread.start()

    def __renewal_loop(self) -> None:
        """Background thread that periodically renews the leader lock."""
        while not self.__renewal_stop.is_set():
            if self.is_leader and self.redis_client:
                try:
                    success = self.renew()
                    if not success:
                        logging.error('Leader election: lost leadership during renewal (pod=%s)', self.pod_id)
                except Exception as e:
                    logging.error('Leader election: renewal thread crashed: %s', e)
                    self.is_leader = False
            
            # Sleep for renewal_interval, but break early if stop is signaled
            self.__renewal_stop.wait(self.renewal_interval)

    def acquire(self) -> bool:
        """
        Try to acquire the leader lock.
        
        Uses Redis SET with NX (only if not exists) for atomic acquisition.
        Returns True if this instance is now the leader.
        
        :returns: True if leader lock acquired, False otherwise.
        """
        try:
            if not self.enabled or self.redis_client is None:
                # No leader election or Redis unavailable → always return True (local mode)
                if not self.enabled:
                    logging.debug('Leader election: disabled, acting as leader')
                else:
                    logging.warning('Leader election: Redis unavailable, fallback mode enabled')
                self.is_leader = True
                return True
            
            # OPTIMIZATION: First, check if a leader already exists via GET (cheaper than SET NX)
            # This avoids unnecessary SET NX attempts when a healthy leader is present
            current_leader = self._get_current_leader()
            if current_leader and current_leader != 'unknown':
                # A leader exists in Redis
                if current_leader == self.pod_id:
                    # I am the current leader → still leader
                    self.is_leader = True
                    return True
                else:
                    # Someone else is the leader → I'm standby
                    if self.is_leader:
                        # Lost leadership!
                        logging.warning('Leader election: lost leadership to %s (pod=%s)', current_leader, self.pod_id)
                    self.is_leader = False
                    return False
            
            # Lock doesn't exist → try to acquire it atomically via SET NX
            result = self.redis_client.set(
                self.lock_key,
                json.dumps({
                    'pod_id': self.pod_id,
                    'acquired_at': time.time(),
                }),
                nx=True,  # Only set if key doesn't exist
                ex=self.ttl,  # Expiry in seconds
            )
            if result:
                # I just became leader!
                if not self.is_leader:
                    logging.info('Leader election: became leader (pod=%s)', self.pod_id)
                self.is_leader = True
                return True
            else:
                # SET NX failed - lock was just created (race condition)
                # Check if it's ours (shouldn't happen, but verify)
                current_leader = self._get_current_leader()
                if current_leader == self.pod_id:
                    if not self.is_leader:
                        logging.info('Leader election: acquired existing lock (pod=%s)', self.pod_id)
                    self.is_leader = True
                    return True
                else:
                    if self.is_leader:
                        logging.warning('Leader election: lost leadership to %s (pod=%s)', current_leader, self.pod_id)
                    self.is_leader = False
                    return False
        except Exception as e:
            logging.error('Leader election: acquire() failed with %s: %s', type(e).__name__, e)
            self.is_leader = False
            return False

    def renew(self) -> bool:
        """
        Renew the leader lock if we still own it.
        
        Called periodically by the leader to maintain leadership.
        If renewal fails, the leader should shut down.
        
        :returns: True if lock renewed, False if we lost leadership.
        """
        if not self.enabled or self.redis_client is None:
            return True  # No Redis → assume lock is held
        
        if not self.is_leader:
            return False  # Not leader → nothing to renew
        
        try:
            # Atomically check ownership and extend TTL
            # Use Lua script to ensure atomicity: only extend if we own it
            # The script checks if the stored pod_id matches our pod_id, then extends TTL
            script = """
                local raw = redis.call('GET', KEYS[1])
                if not raw then
                    return 0  -- Key doesn't exist (lost lock)
                end
                local ok, data = pcall(cjson.decode, raw)
                if not ok then
                    return 0  -- JSON decode failed
                end
                if data.pod_id == ARGV[1] then
                    return redis.call('EXPIRE', KEYS[1], ARGV[2])
                else
                    return 0
                end
            """
            result = self.redis_client.eval(script, 1, self.lock_key, self.pod_id, self.ttl)
            
            if result:
                return True
            else:
                logging.error('Leader election: lost leadership during renewal (pod=%s)', self.pod_id)
                self.is_leader = False
                return False
        except redis.RedisError as e:
            logging.error('Leader election: renew failed: %s', e)
            if self.fallback_if_unavailable:
                logging.warning('Leader election: Redis error during renew, fallback enabled')
                return True
            else:
                # Critical: lost Redis connection while leading
                logging.error('Leader election: critical failure, stepping down')
                self.is_leader = False
                return False

    def _get_current_leader(self) -> str:
        """Get the current leader pod ID from Redis (for logging)."""
        try:
            data = self.redis_client.get(self.lock_key)
            if data:
                parsed = json.loads(data)
                return parsed.get('pod_id', 'unknown')
        except (redis.RedisError, json.JSONDecodeError, KeyError):
            pass
        return 'unknown'

    def wait_for_leadership(self) -> None:
        """
        Block until this instance becomes the leader.
        
        Standbys call this; they poll for leadership with acquisition_retry_interval.
        Once acquired, returns immediately on subsequent calls.
        """
        if self.is_leader:
            return
        
        # Poll for leadership until acquired
        while not self.acquire():
            time.sleep(self.acquisition_retry)

    def shutdown(self) -> None:
        """Clean shutdown: stop renewal thread and release the lock if we hold it."""
        # Stop the renewal thread
        self.__renewal_stop.set()
        if self.__renewal_thread and self.__renewal_thread.is_alive():
            self.__renewal_thread.join(timeout=2)
        
        if self.is_leader and self.redis_client is not None:
            try:
                self.redis_client.delete(self.lock_key)
                logging.info('Leader election: released lock on shutdown (pod=%s)', self.pod_id)
            except redis.RedisError as e:
                logging.warning('Leader election: failed to release lock: %s', e)
        
        self.is_leader = False
