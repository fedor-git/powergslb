"""MySQL/MariaDB connection and statement execution."""

import contextlib
import logging
import re
from collections.abc import Iterator
from typing import Any, Self, cast

import mysql.connector
from mysql.connector.abstracts import MySQLConnectionAbstract

from powergslb.database.mysql.masked import Masked
from powergslb.database.mysql.powerdns import PowerDNSMixIn
from powergslb.database.mysql.w2ui import W2UIMixIn

__all__ = ['MySQLDatabase']


class MySQLDatabase(PowerDNSMixIn, W2UIMixIn):
    """MySQL/MariaDB query facade over a mysql.connector connection; usable as a context manager.

    Runs with autocommit on (not user-configurable), so every single-statement write persists on its own;
    only transaction() suspends autocommit to group statements and commit() them as a unit.

    Automatically detects and reconnects on connection failure using connection.ping().

    :param kwargs: mysql.connector connect arguments (database, user, password, host, port, unix_socket, ...)
                   plus optional ping settings: ping_reconnect (bool, default False), ping_attempts (int, default 1),
                   ping_delay (int, default 0).
    """
    Error = mysql.connector.Error

    def __init__(self, **kwargs: Any) -> None:
        # Extract ping configuration (not mysql.connector arguments)
        self._ping_reconnect = kwargs.pop('ping_reconnect', False)
        self._ping_attempts = kwargs.pop('ping_attempts', 1)
        self._ping_delay = kwargs.pop('ping_delay', 0)
        
        self._connect_kwargs = kwargs.copy()
        self._connect_kwargs['autocommit'] = True
        # connect() returns MySQLConnection or CMySQLConnection (C-extension) when the connector ships it.
        self._connection = cast(MySQLConnectionAbstract, mysql.connector.connect(**self._connect_kwargs))
        self._last_insert_id = 0
        self._infrastructure_error_occurred = False  # Track if last query had 2003/2005 error

    def _reconnect(self) -> None:
        """Close existing connection and establish a new one.

        Logs a warning and raises if reconnection fails.
        """
        try:
            self._connection.close()
        except Exception:
            pass  # Connection already broken, ignore close errors
        
        logging.debug('Reconnecting to MySQL database...')
        self._connection = cast(MySQLConnectionAbstract, mysql.connector.connect(**self._connect_kwargs))
        logging.info('Successfully reconnected to MySQL database')

    def _check_health(self) -> None:
        """Check database health: connection status only.

        Uses ping() for fast connection check. Galera cluster state (wsrep_local_state)
        is checked only after ping fails, to avoid overhead on every query.

        :raises: mysql.connector.Error if connection dead or cluster not synced.
        """
        try:
            self._connection.ping(
                reconnect=self._ping_reconnect,
                attempts=self._ping_attempts,
                delay=self._ping_delay
            )
        except mysql.connector.Error as e:
            # Connection failed; check if it's a Galera cluster issue.
            # _check_galera_state() will only raise if cluster is not synced AND connection is OK.
            # If connection is broken, it returns silently to preserve the original error code.
            self._check_galera_state()
            # Re-raise the original connection error with its error code intact
            raise

    def _check_galera_state(self) -> None:
        """Check Galera cluster state (wsrep_local_state) after connection failure.

        Only called when connection check fails, to avoid performance overhead.
        If database is truly unreachable (error code 2003/2005), skip this check
        to preserve the original error code for infrastructure error detection.

        Galera states:
        - 4 (Synced): cluster healthy, queries OK
        - 3 (Joined): joining cluster, operations may be blocked
        - 2 (Donor/Desynced): in maintenance, queries blocked
        - 1 (Joining): syncing state
        - 0 (Undefined): not part of cluster or not initialized

        :raises: mysql.connector.Error if cluster not synced.
        """
        cursor = None
        try:
            cursor = self._connection.cursor(buffered=True)
            cursor.execute('SHOW STATUS LIKE %s', ('wsrep_local_state',))
            result = cursor.fetchone()
            if result:
                state_value = int(result[1])
                if state_value != 4:  # 4 = Synced
                    state_names = {
                        0: 'Undefined',
                        1: 'Joining',
                        2: 'Donor/Desynced',
                        3: 'Joined',
                        4: 'Synced'
                    }
                    state_name = state_names.get(state_value, 'Unknown')
        except mysql.connector.Error as e:
            # Only suppress errors coming from querying Galera state; if we raised the
            # "cluster not synced" error above, propagate it to the caller.
            if 'Galera cluster not synced' in str(e):
                raise
            # If we can't query Galera state, it means the connection itself is broken.
            # Don't wrap the error; let the caller handle the original exception which may
            # have the infrastructure error code (2003, 2005).
            logging.debug('Could not check Galera state (connection unavailable): %s', e)
            # Don't raise; let the original error from _check_health propagate with its code
            return
        finally:
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass

    @staticmethod
    def _error_code(error: mysql.connector.Error) -> int | None:
        """Extract error code from mysql.connector.Error.

        Handles both direct MySQL errors and wrapped errors from reconnect attempts.
        Examples:
        - "2003 (HY000): Can't connect..." → 2003
        - "Can not reconnect...: 2003 (HY000)..." → 2003
        - "Unknown MySQL server host 'mariadb' (-2)" → 2005 (from message pattern)

        :param error: The database error.
        :returns: The MySQL error code (e.g. 2003), or None if not found.
        """
        # Try errno attribute first (works for direct MySQL errors)
        if hasattr(error, 'errno'):
            errno = error.errno
            # errno might be 2003/2005 (MySQL codes) or system errno like 111 (Connection refused)
            # MySQL codes are in range 1000-9999, system errno much lower
            if isinstance(errno, int) and 1000 <= errno <= 9999:
                return errno
        
        # Parse from error message: look for "2003" or "2005" pattern
        # Handles both direct ("2003 (HY000):...") and wrapped ("Can not reconnect: 2003...")
        msg = str(error)
        # Match MySQL error codes: "2003 (HY000)" or just "2003"
        match = re.search(r'\b(200[35])\b', msg)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, IndexError):
                pass
        
        return None

    @staticmethod
    def _is_infrastructure_error(error: mysql.connector.Error) -> bool:
        """Check if error is infrastructure-level (DNS, network, unreachable server).

        These errors indicate the database is unreachable due to infrastructure problems,
        not connection state issues. Reconnecting won't help until infrastructure recovers.

        Error codes:
        - 2003: Can't connect to MySQL server (port unreachable, server down)
        - 2005: Unknown MySQL server host (DNS failure)

        :param error: The database error.
        :returns: True if this is an infrastructure error that won't be fixed by reconnect.
        """
        code = MySQLDatabase._error_code(error)
        return code in (2003, 2005)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: Any) -> None:
        self._connection.close()

    @staticmethod
    def join_operation(operation: str) -> str:
        """Collapse a multiline SQL string into a single space-separated line.

        :param operation: The SQL statement text.
        :returns: The statement as one line with surrounding whitespace stripped.
        """
        return ' '.join(filter(None, (line.strip() for line in operation.splitlines())))

    @contextlib.contextmanager
    def _cursor(self, operation: str, params: tuple[Any, ...]) -> Iterator[Any]:
        """Run one SQL statement on a fresh buffered cursor and yield it, closing it on exit.

        The AUTO_INCREMENT value the statement generated is kept before the cursor closes, so it outlives the cursor.
        Uses connection.ping() to detect broken connections and automatically reconnects.

        :param operation: The SQL statement to execute.
        :param params: Statement placeholder values.
        :yields: The buffered cursor with the statement executed.
        :raises: mysql.connector.Error on connection failures.
        """
        operation = self.join_operation(operation)
        if params:
            logging.debug('"%s" %% %s', operation, params)
        else:
            logging.debug('"%s"', operation)

        unwrapped_params = self._unwrap_params(params)

        # If last query had infrastructure error, try reconnect once
        if self._infrastructure_error_occurred:
            logging.debug('Attempting reconnect after infrastructure error...')
            try:
                self._reconnect()
                self._infrastructure_error_occurred = False
                logging.info('Successfully recovered from infrastructure error')
            except mysql.connector.Error as e:
                # Reconnect failed; keep the flag set so future queries keep attempting
                # reconnect before running health checks/logging again.
                logging.debug('Reconnect failed: %s. Will retry on next query.', e)
                self._infrastructure_error_occurred = True
                raise

        # Check connection and cluster health before executing query
        try:
            self._check_health()
        except mysql.connector.Error as e:
            # Infrastructure errors (DNS, network unreachable): mark for next query, fail fast
            if self._is_infrastructure_error(e):
                if not getattr(self, '_infrastructure_error_occurred', False):
                    logging.error('Infrastructure error (2003/2005): database unavailable: %s', e)
                self._infrastructure_error_occurred = True
                raise
            
            error_msg = str(e).lower()
            # Galera cluster issues need waiting, not immediate reconnect
            if 'galera' in error_msg or 'wsrep' in error_msg:
                logging.warning('Galera cluster issue: %s. Waiting for recovery...', e)
                raise  # Let caller decide retry strategy
            
            # Connection issues: attempt reconnect
            logging.warning('Connection lost: %s. Reconnecting...', e)
            try:
                self._reconnect()
            except mysql.connector.Error as reconnect_error:
                logging.error('Failed to reconnect: %s', reconnect_error)
                raise

        cursor = None
        try:
            cursor = self._connection.cursor(buffered=True)
            cursor.execute(operation, unwrapped_params)
            self._last_insert_id = cursor.lastrowid or 0
            yield cursor
        finally:
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass

    def last_insert_id(self) -> int:
        """Return the AUTO_INCREMENT value the most recent statement generated, or 0 when it generated none.

        :returns: The generated key, or 0.
        """
        return self._last_insert_id

    @staticmethod
    def _unwrap_params(params: tuple[Any, ...]) -> tuple[Any, ...]:
        """Unwrap any Masked parameter to its real value before execution.

        :param params: The statement placeholder values, some possibly Masked.
        :returns: The parameters with every Masked replaced by its value.
        """
        return tuple(param.value if isinstance(param, Masked) else param for param in params)

    def select(self, operation: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Execute a result-set statement (SELECT, WITH...SELECT, SHOW, ...) and return its rows as dicts.

        :param operation: The SQL statement to execute.
        :param params: Statement placeholder values.
        :returns: The result rows, each keyed by column name.
        """
        with self._cursor(operation, params) as cursor:
            logging.debug('%s rows returned', cursor.rowcount)
            column_names = [column[0] for column in cursor.description]
            return [dict(zip(column_names, row)) for row in cursor]

    def modify(self, operation: str, params: tuple[Any, ...] = ()) -> int:
        """Execute a write statement (INSERT, UPDATE, DELETE, ...) and return the affected row count.

        :param operation: The SQL statement to execute.
        :param params: Statement placeholder values.
        :returns: The number of rows the statement affected.
        """
        with self._cursor(operation, params) as cursor:
            logging.debug('%s rows affected', cursor.rowcount)
            return cursor.rowcount

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """Group every statement run inside the block into one committed transaction.

        Suspends autocommit for the block's duration and restores it in finally, so an exception cannot leave
        the connection mid-transaction.

        :yields: None; the block runs its statements through the normal select and modify methods.
        """
        self._connection.autocommit = False
        try:
            yield
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        finally:
            self._connection.autocommit = True
