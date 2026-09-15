"""Command line entry point."""

import argparse
import logging
import threading

import redis

from powergslb.leader_election import LeaderElection
from powergslb.monitor import MonitorManager, MemoryStatusRegistry, RedisStatusRegistry, StatusRegistry
from powergslb.server import AdminRequestHandler, PowerDNSRequestHandler, ServerManager
from powergslb.system import Config, ServiceThread, SystemService
from powergslb.version import VERSION
from powergslb.view import ViewRule

__all__ = ['PowerGSLB']


class MonitorLeaderThread(threading.Thread):
    """Wrapper thread that waits for leadership before starting the actual monitor."""

    def __init__(self,
                 monitor_manager: MonitorManager,
                 leader_election: LeaderElection | None,
                 **kwargs):
        super().__init__(**kwargs)
        self.monitor_manager = monitor_manager
        self.leader_election = leader_election
        self.daemon = False

    def run(self) -> None:
        """Wait for leadership, then run the monitor."""
        try:
            if self.leader_election:
                self.leader_election.wait_for_leadership()
                logging.info('Monitor acquired leadership, starting monitor manager')
            
            self.monitor_manager.run()
            logging.info('Monitor manager finished (should not happen)')
        except Exception as e:
            logging.exception('Monitor thread crashed: %s', e)

    def is_alive(self) -> bool:
        """Check if this thread is alive (waiting for leadership or running monitor)."""
        # Use threading.Thread.is_alive() - NOT monitor_manager's, since it's not running yet
        return threading.Thread.is_alive(self)

    def shutdown(self, timeout: float = 0) -> None:
        """Shutdown: release Redis lock and stop the monitor."""
        if self.leader_election:
            self.leader_election.shutdown()
        self.monitor_manager.shutdown(timeout)

    @property
    def name(self) -> str:
        """Delegate name to monitor manager."""
        return self.monitor_manager.name


class PowerGSLB:
    """Main program: parses arguments and wires the service threads."""

    @staticmethod
    def main() -> None:
        """Parse arguments, load the config, and run the service threads under SystemService."""
        args_parser = argparse.ArgumentParser()
        args_parser.add_argument('-c', '--config', required=True)
        args_parser.add_argument('-V', '--version', action='version', version=f'PowerGSLB {VERSION}')
        args = args_parser.parse_args()

        config = Config(args.config)

        logging.basicConfig(
            format=config.get('logging', 'format'),
            level=config.get('logging', 'level')
        )
        logging.info('PowerGSLB %s', VERSION)

        database = config.items('database')
        ViewRule.configure(config.items('geoip'))
        
        # Get JWT configuration if it exists (optional)
        jwt_config = config.items('jwt') if 'jwt' in config._data else {}
        logging.debug("JWT config from TOML: %s", jwt_config)
        logging.debug("JWT config keys: %s", list(jwt_config.keys()) if jwt_config else "empty")

        # Initialize Redis client and status registry
        # Single Redis client shared between leader election and status registry
        redis_client = None
        status: StatusRegistry | MemoryStatusRegistry | RedisStatusRegistry
        
        if 'redis' in config._data:
            try:
                redis_config = config.items('redis')
                redis_client = redis.Redis(
                    host=redis_config.get('host', 'localhost'),
                    port=int(redis_config.get('port', 6379)),
                    db=int(redis_config.get('db', 0)),
                    socket_connect_timeout=float(redis_config.get('connection_timeout', 2)),
                    socket_timeout=float(redis_config.get('socket_timeout', 2)),
                    decode_responses=True
                )
                # Verify connection
                redis_client.ping()
                
                # Use Redis-backed status registry for distributed health data
                status = RedisStatusRegistry(redis_client)
                logging.info('Health status registry backed by Redis')
            except Exception as e:
                logging.error('Failed to initialize Redis: %s; falling back to memory', e)
                redis_client = None
                status = MemoryStatusRegistry()
        else:
            status = MemoryStatusRegistry()
            logging.info('Health status registry in-memory mode')

        # Initialize leader election if Redis is configured
        leader_election: LeaderElection | None = None
        if redis_client and 'leader_election' in config._data:
            try:
                le_config = config.items('leader_election')
                # Create new LeaderElection with existing redis_client
                leader_election = LeaderElection(
                    redis_config={'host': redis_client.connection_pool.connection_kwargs.get('host', 'localhost'),
                                  'port': redis_client.connection_pool.connection_kwargs.get('port', 6379),
                                  'db': redis_client.connection_pool.connection_kwargs.get('db', 0),
                                  'connection_timeout': redis_client.connection_pool.connection_kwargs.get('socket_connect_timeout', 2),
                                  'socket_timeout': redis_client.connection_pool.connection_kwargs.get('socket_timeout', 2)},
                    le_config=le_config,
                    name='LeaderElection'
                )
                logging.info('Leader election initialized')
            except Exception as e:
                logging.error('Failed to initialize leader election: %s', e)
                logging.warning('Continuing without leader election')
        else:
            if not redis_client:
                logging.info('Redis not available; leader election disabled')
            elif 'leader_election' not in config._data:
                logging.info('Leader election not configured')

        # Create monitor manager (but it will wait for leadership if enabled)
        monitor_manager = MonitorManager(config.items('monitor'), database, status, name='Monitor')
        
        # Wrap monitor in a thread that waits for leadership
        if leader_election:
            monitor_thread: ServiceThread = MonitorLeaderThread(
                monitor_manager,
                leader_election,
                name='Monitor'
            )
        else:
            monitor_thread = monitor_manager

        service_threads: list[ServiceThread] = [
            monitor_thread,
            ServerManager(config.items('admin'), database, status, AdminRequestHandler, 
                         jwt_config=jwt_config, name='Admin'),
            ServerManager(config.items('server'), database, status, PowerDNSRequestHandler, name='Server')
        ]

        service = SystemService(service_threads)
        service.start()
