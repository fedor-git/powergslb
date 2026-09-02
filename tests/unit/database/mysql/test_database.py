# pylint: disable=missing-function-docstring, protected-access

"""Tests for MySQLDatabase.

The SQL flattener, the context-manager protocol, autocommit injection, the select (rows-as-dicts) / modify
(affected rowcount) split over a shared _cursor helper, and the one-shot reconnect when opening a cursor finds the
connection gone. MySQLDatabase holds a mysql.connector connection by composition, so instances are built with
__new__ (skipping the connecting __init__) and a fake connection whose cursor() yields a fake cursor is attached.
"""

import logging
from typing import Any

import mysql.connector
import pytest

from powergslb.database.mysql.database import MySQLDatabase
from powergslb.database.mysql.masked import Masked


class _FakeCursor:
    """Minimal stand-in for a buffered mysql.connector cursor."""

    def __init__(self, description: Any, rows: list[tuple[Any, ...]], rowcount: int,
                 raise_on_execute: Exception | None = None, lastrowid: int | None = None) -> None:
        self.description = description
        self._rows = rows
        self.rowcount = rowcount
        self._raise = raise_on_execute
        self.lastrowid = lastrowid
        self.executions: list[tuple[str, tuple[Any, ...]]] = []
        self.closed = False

    def execute(self, operation: str, params: tuple[Any, ...]) -> None:
        self.executions.append((operation, params))
        if self._raise is not None:
            raise self._raise

    def __iter__(self) -> Any:
        return iter(self._rows)

    def close(self) -> None:
        self.closed = True


class _MySQLInterfaceError(Exception):
    """Stand-in for the C extension's MySQLInterfaceError, which is not a mysql.connector.Error subclass."""


class _FakeConnection:
    """Minimal stand-in for a mysql.connector connection: yields a fixed cursor and records control calls."""

    def __init__(self, cursor: _FakeCursor, cursor_error: Exception | None = None, connected: bool = True,
                 close_error: Exception | None = None, autocommit_error: Exception | None = None,
                 rollback_error: Exception | None = None) -> None:
        self._cursor = cursor
        self._cursor_error = cursor_error
        self._close_error = close_error
        self._autocommit_error = autocommit_error
        self._rollback_error = rollback_error
        self.autocommit = True
        self.connected = connected  # public, so a test can kill the connection under an open transaction
        self.events: list[str] = []

    def cursor(self, **_kwargs: Any) -> _FakeCursor:
        if self._cursor_error is not None:
            raise self._cursor_error
        return self._cursor

    def is_connected(self) -> bool:
        return self.connected

    def __setattr__(self, name: str, value: Any) -> None:
        # the real setter is a statement (SET @@session.autocommit), so it can fail on a dead connection
        if name == 'autocommit' and hasattr(self, 'events'):
            if self._autocommit_error is not None:
                raise self._autocommit_error
            self.events.append(f'autocommit={value}')
        object.__setattr__(self, name, value)

    def commit(self) -> None:
        self.events.append('commit')

    def rollback(self) -> None:
        self.events.append('rollback')
        if self._rollback_error is not None:
            raise self._rollback_error

    def close(self) -> None:
        self.events.append('close')
        if self._close_error is not None:
            raise self._close_error


def _db_with_cursor(cursor: _FakeCursor, **connection_kwargs: Any) -> MySQLDatabase:
    database = MySQLDatabase.__new__(MySQLDatabase)
    database._connection = _FakeConnection(cursor, **connection_kwargs)  # type: ignore[assignment]
    database._last_insert_id = 0
    database._connect_kwargs = {'host': '127.0.0.1', 'autocommit': True}
    database._in_transaction = False
    return database


def test_error_alias_is_mysql_connector_error() -> None:
    assert MySQLDatabase.Error is mysql.connector.Error


def test_join_operation_collapses_whitespace() -> None:
    operation = """
                SELECT 1
                FROM t \
                """
    assert MySQLDatabase.join_operation(operation) == 'SELECT 1 FROM t'


def test_enter_returns_self() -> None:
    database = MySQLDatabase.__new__(MySQLDatabase)
    assert database.__enter__() is database  # pylint: disable=unnecessary-dunder-call


def test_exit_closes_connection() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0)
    database = _db_with_cursor(cursor)
    database.__exit__(None, None, None)
    assert database._connection.events == ['close']  # type: ignore[attr-defined]


def test_exit_ignores_a_broken_close() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0)
    database = _db_with_cursor(cursor, close_error=mysql.connector.Error('Lost connection'))
    # a connection the server already closed has nothing left to lose, and raising here would reach the handler
    database.__exit__(None, None, None)
    assert database._connection.events == ['close']  # type: ignore[attr-defined]


def test_init_connects_with_autocommit(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_connect(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    # The C-vs-pure choice is delegated to the factory; __init__ only injects autocommit and holds the result.
    monkeypatch.setattr(mysql.connector, 'connect', fake_connect)
    MySQLDatabase(host='127.0.0.1', user='u')
    assert captured == {'host': '127.0.0.1', 'user': 'u', 'autocommit': True}


def test_select_returns_list_of_dicts() -> None:
    cursor = _FakeCursor(description=[('a',), ('b',)], rows=[(1, 2), (3, 4)], rowcount=2)
    database = _db_with_cursor(cursor)
    result = database.select('SELECT a, b FROM t')
    assert result == [{'a': 1, 'b': 2}, {'a': 3, 'b': 4}]
    assert cursor.closed is True


def test_select_shapes_rows_by_description_regardless_of_keyword() -> None:
    # A row-returning statement that does not start with 'SELECT' (a CTE, SHOW, lowercase) still yields row dicts:
    # select reads the columns from cursor.description, not from the SQL's leading keyword.
    cursor = _FakeCursor(description=[('a',), ('b',)], rows=[(1, 2)], rowcount=1)
    database = _db_with_cursor(cursor)
    result = database.select('WITH t AS (SELECT 1) SELECT a, b FROM t')
    assert result == [{'a': 1, 'b': 2}]


def test_modify_returns_rowcount() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=7)
    database = _db_with_cursor(cursor)
    result = database.modify('UPDATE t SET x = %s', (1,))
    assert result == 7
    assert cursor.executions == [('UPDATE t SET x = %s', (1,))]
    assert cursor.closed is True


def test_last_insert_id_outlives_the_closed_cursor() -> None:
    # the generated key is kept before the cursor closes, so it is readable after the statement returns
    cursor = _FakeCursor(description=None, rows=[], rowcount=1, lastrowid=42)
    database = _db_with_cursor(cursor)
    database.modify('INSERT INTO t (x) VALUES (%s)', (1,))
    assert cursor.closed is True
    assert database.last_insert_id() == 42


def test_last_insert_id_is_zero_when_the_statement_generated_none() -> None:
    # a statement that generates no key reports None, which reads back as 0 rather than a stale id
    cursor = _FakeCursor(description=None, rows=[], rowcount=1, lastrowid=None)
    database = _db_with_cursor(cursor)
    database._last_insert_id = 42
    database.modify('UPDATE t SET x = %s', (1,))
    assert database.last_insert_id() == 0


def test_modify_flattens_operation_before_running() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0)
    database = _db_with_cursor(cursor)
    database.modify('DELETE FROM t\n  WHERE id = %s', (1,))
    assert cursor.executions[0][0] == 'DELETE FROM t WHERE id = %s'


def test_unwrap_params_unwraps_masked_values() -> None:
    unwrapped = MySQLDatabase._unwrap_params(('test', Masked('$6$salt$hash'), 7))
    assert unwrapped == ('test', '$6$salt$hash', 7)


def test_unwrap_params_leaves_ordinary_params_unchanged() -> None:
    assert MySQLDatabase._unwrap_params(('example.com', 1)) == ('example.com', 1)


def test_cursor_masks_only_the_masked_param_in_debug_log_but_still_binds_it(
        caplog: pytest.LogCaptureFixture) -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=1)
    database = _db_with_cursor(cursor)
    operation = 'INSERT INTO `users` (`user`, `name`, `password`) VALUES (%s, %s, %s)'
    with caplog.at_level(logging.DEBUG):
        database.modify(operation, ('test', 'Test User', Masked('$6$salt$hash')))
    # the hash reaches execute untouched, unwrapped from Masked
    assert cursor.executions == [(operation, ('test', 'Test User', '$6$salt$hash'))]
    # only the hash is masked; user and name stay visible in the log
    assert '$6$salt$hash' not in caplog.text
    assert "'*****'" in caplog.text
    assert 'test' in caplog.text
    assert 'Test User' in caplog.text


def test_select_closes_cursor_even_on_error() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0, raise_on_execute=RuntimeError('boom'))
    database = _db_with_cursor(cursor)
    with pytest.raises(RuntimeError):
        database.select('SELECT 1')
    assert cursor.closed is True


def test_transaction_commits_the_block_as_a_unit() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=3)
    database = _db_with_cursor(cursor)
    with database.transaction():
        assert database.modify('INSERT INTO t VALUES (%s)', (1,)) == 3
        assert database.modify('UPDATE t SET x = %s', (2,)) == 3
    # autocommit is suspended for the transaction, the statements commit as a unit, then autocommit is restored.
    assert database._connection.events == [  # type: ignore[attr-defined]
        'autocommit=False', 'commit', 'autocommit=True']
    assert database._in_transaction is False


def test_transaction_rolls_back_and_reraises_on_error() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0, raise_on_execute=RuntimeError('boom'))
    database = _db_with_cursor(cursor)
    with pytest.raises(RuntimeError):
        with database.transaction():
            database.modify('INSERT INTO t VALUES (%s)', (1,))
    # rolled back, never committed, and autocommit restored even on the error path
    assert database._connection.events == [  # type: ignore[attr-defined]
        'autocommit=False', 'rollback', 'autocommit=True']
    assert database._in_transaction is False


def test_a_failing_rollback_does_not_mask_the_error() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0,
                         raise_on_execute=mysql.connector.Error('Lost connection during query'))
    # the raw MySQLInterfaceError is not a mysql.connector.Error, so only suppressing Exception catches it
    database = _db_with_cursor(cursor, rollback_error=_MySQLInterfaceError('Lost connection to MySQL server'))
    # the rollback of a connection already gone cannot succeed; the statement error must survive it
    with pytest.raises(mysql.connector.Error, match='Lost connection during query'):
        with database.transaction():
            database.modify('INSERT INTO t VALUES (%s)', (1,))
    assert database._connection.events == [  # type: ignore[attr-defined]
        'autocommit=False', 'rollback', 'autocommit=True']


def test_a_transaction_that_cannot_suspend_autocommit_leaves_reconnect_armed() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0)
    database = _db_with_cursor(cursor, autocommit_error=mysql.connector.Error('MySQL Connection not available'))
    with pytest.raises(mysql.connector.Error):
        with database.transaction():
            pytest.fail('the block must not run when autocommit could not be suspended')
    # the block was never entered, so the flag must not be left set against the next statement
    assert database._in_transaction is False


def _never_called(**_kwargs: Any) -> object:
    """Stand in for mysql.connector.connect where reconnecting would be wrong."""
    raise AssertionError('connect must not be called')


def _dead_database(cursor: _FakeCursor, **connection_kwargs: Any) -> MySQLDatabase:
    """Build a database whose connection the server closed: cursor() raises and is_connected() answers False."""
    return _db_with_cursor(cursor, cursor_error=mysql.connector.Error('MySQL Connection not available'),
                           connected=False, **connection_kwargs)


def test_select_reconnects_once_when_the_connection_died_while_idle(
        monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    cursor = _FakeCursor(description=[('a',)], rows=[(1,)], rowcount=1)
    database = _dead_database(cursor)
    dead, fresh = database._connection, _FakeConnection(cursor)
    captured: dict[str, Any] = {}

    def fake_connect(**kwargs: Any) -> object:
        captured.update(kwargs)
        return fresh

    monkeypatch.setattr(mysql.connector, 'connect', fake_connect)
    with caplog.at_level(logging.WARNING):
        assert database.select('SELECT a FROM t') == [{'a': 1}]
    # the dead connection is closed and replaced, and the statement runs on the new one
    assert captured == {'host': '127.0.0.1', 'autocommit': True}
    assert dead.events == ['close']  # type: ignore[attr-defined]
    assert database._connection is fresh
    assert 'database connection lost' in caplog.text


def test_modify_reconnects_too_since_the_statement_was_never_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=1)
    database = _dead_database(cursor)
    monkeypatch.setattr(mysql.connector, 'connect', lambda **_kwargs: _FakeConnection(cursor))
    # cursor() fails before execute(), so the write reaches the server exactly once
    assert database.modify('INSERT INTO t VALUES (%s)', (1,)) == 1
    assert cursor.executions == [('INSERT INTO t VALUES (%s)', (1,))]


def test_an_error_during_execute_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0,
                         raise_on_execute=mysql.connector.Error('Lost connection to MySQL server during query'))
    # the cursor opens on a live connection; the server drops it while the statement runs
    database = _db_with_cursor(cursor, connected=False)
    monkeypatch.setattr(mysql.connector, 'connect', _never_called)
    # the statement was already sent, so re-sending it would duplicate a records / audit row
    with pytest.raises(mysql.connector.Error, match='during query'):
        database.modify('INSERT INTO t VALUES (%s)', (1,))


def test_cursor_error_is_reraised_when_the_connection_is_still_live(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0)
    database = _db_with_cursor(cursor, cursor_error=mysql.connector.Error('unread result found'), connected=True)
    monkeypatch.setattr(mysql.connector, 'connect', _never_called)
    with pytest.raises(mysql.connector.Error, match='unread result found'):
        database.select('SELECT 1')


def test_no_reconnect_inside_a_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0,
                         raise_on_execute=mysql.connector.Error('Lost connection to MySQL server during query'))
    database = _db_with_cursor(cursor)
    connection = database._connection
    monkeypatch.setattr(mysql.connector, 'connect', _never_called)
    # reconnecting would lose the statements already run in the block, so the block is aborted instead
    with pytest.raises(mysql.connector.Error):
        with database.transaction():
            connection.connected = False  # type: ignore[attr-defined]  # the server dies under the open block
            database.modify('INSERT INTO t VALUES (%s)', (1,))
    assert connection.events == [  # type: ignore[attr-defined]
        'autocommit=False', 'rollback', 'autocommit=True']


def test_transaction_reconnects_when_the_connection_died_before_the_block(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=1)
    database = _db_with_cursor(cursor, connected=False)
    dead, fresh = database._connection, _FakeConnection(cursor)
    monkeypatch.setattr(mysql.connector, 'connect', lambda **_kwargs: fresh)
    # suspending autocommit is a statement of its own, and none of the block's has run yet, so entry can reconnect
    with database.transaction():
        database.modify('INSERT INTO t VALUES (%s)', (1,))
    assert dead.events == ['close']  # type: ignore[attr-defined]
    assert fresh.events == ['autocommit=False', 'commit', 'autocommit=True']


def test_the_reconnect_is_not_retried_a_second_time(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0)
    database = _dead_database(cursor)
    attempts: list[int] = []

    def fake_connect(**_kwargs: Any) -> object:
        attempts.append(1)
        return _FakeConnection(cursor, cursor_error=mysql.connector.Error('MySQL Connection not available'),
                               connected=False)

    monkeypatch.setattr(mysql.connector, 'connect', fake_connect)
    # a fresh connection that fails too is the server being down, not a stale session: no second reconnect
    with pytest.raises(mysql.connector.Error):
        database.select('SELECT 1')
    assert attempts == [1]


def test_a_failing_reconnect_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0)
    database = _dead_database(cursor)

    def fake_connect(**_kwargs: Any) -> object:
        raise mysql.connector.Error('Can not connect to MySQL server')

    monkeypatch.setattr(mysql.connector, 'connect', fake_connect)
    with pytest.raises(mysql.connector.Error, match='Can not connect'):
        database.select('SELECT 1')


def test_a_broken_close_does_not_stop_the_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=1)
    database = _dead_database(cursor, close_error=mysql.connector.Error('Lost connection'))
    fresh = _FakeConnection(cursor)
    monkeypatch.setattr(mysql.connector, 'connect', lambda **_kwargs: fresh)
    # the C extension re-raises a broken close as Error; the connection is being thrown away either way
    assert database.modify('DELETE FROM t') == 1
    assert database._connection is fresh
