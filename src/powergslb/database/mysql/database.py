"""MySQL/MariaDB connection and statement execution."""

import contextlib
import logging
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

    A statement whose cursor cannot open because the connection is gone (server restart, failover, wait_timeout)
    reconnects once and runs on the new connection. A connection lost mid-statement and inside transaction() is not
    retried, since the statements already run are lost with it.

    :param kwargs: mysql.connector connect arguments (database, user, password, host, port, unix_socket, ...).
    """
    Error = mysql.connector.Error

    def __init__(self, **kwargs: Any) -> None:
        kwargs['autocommit'] = True
        self._connect_kwargs = kwargs
        self._connection = self._connect()
        self._last_insert_id = 0
        self._in_transaction = False

    def _connect(self) -> MySQLConnectionAbstract:
        """Open a new connection with the stored connect arguments.

        :returns: The new connection.
        """
        # connect() returns MySQLConnection or CMySQLConnection (C-extension) when the connector ships it.
        return cast(MySQLConnectionAbstract, mysql.connector.connect(**self._connect_kwargs))

    def _reconnect(self) -> None:
        """Replace a connection the server closed while idle.

        :raises mysql.connector.Error: When connecting again failed.
        """
        logging.warning('database connection lost, reconnecting')
        with contextlib.suppress(mysql.connector.Error):
            self._connection.close()  # the C extension re-raises a broken close as Error
        self._connection = self._connect()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: Any) -> None:
        with contextlib.suppress(mysql.connector.Error):
            self._connection.close()  # the C extension re-raises a broken close as Error

    @staticmethod
    def join_operation(operation: str) -> str:
        """Collapse a multiline SQL string into a single space-separated line.

        :param operation: The SQL statement text.
        :returns: The statement as one line with surrounding whitespace stripped.
        """
        return ' '.join(filter(None, (line.strip() for line in operation.splitlines())))

    def _open_cursor(self) -> Any:
        """Open a buffered cursor, reconnecting once when the connection died while idle.

        mysql.connector pings the server inside cursor(), so a session the server closed fails here.
        A failure on a connection that still pings live is re-raised.

        :returns: A buffered cursor on a live connection.
        :raises mysql.connector.Error: When cursor() failed on a live connection, inside transaction(), or
            reconnecting failed.
        """
        try:
            return self._connection.cursor(buffered=True)
        except mysql.connector.Error:
            if self._in_transaction or self._connection.is_connected():
                raise
            self._reconnect()
            return self._connection.cursor(buffered=True)

    @contextlib.contextmanager
    def _cursor(self, operation: str, params: tuple[Any, ...]) -> Iterator[Any]:
        """Run one SQL statement on a fresh buffered cursor and yield it, closing it on exit.

        The AUTO_INCREMENT value the statement generated is kept before the cursor closes, so it outlives the cursor.

        :param operation: The SQL statement to execute.
        :param params: Statement placeholder values.
        :yields: The buffered cursor with the statement executed.
        """
        operation = self.join_operation(operation)
        if params:
            logging.debug('"%s" %% %s', operation, params)
        else:
            logging.debug('"%s"', operation)

        cursor = self._open_cursor()
        try:
            cursor.execute(operation, self._unwrap_params(params))
            self._last_insert_id = cursor.lastrowid or 0
            yield cursor
        finally:
            cursor.close()

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
        the connection mid-transaction. Suspending is itself a statement, so the connection is checked first.
        A connection lost within a block is aborted rather than reconnected.

        :yields: None; the block runs its statements through the normal select and modify methods.
        """
        if not self._connection.is_connected():
            self._reconnect()

        self._connection.autocommit = False
        self._in_transaction = True
        try:
            yield
            self._connection.commit()
        except Exception:
            with contextlib.suppress(Exception):  # the C extension raises a raw MySQLInterfaceError
                self._connection.rollback()
            raise
        finally:
            self._in_transaction = False
            self._connection.autocommit = True
