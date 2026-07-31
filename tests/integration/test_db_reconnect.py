# pylint: disable=missing-function-docstring

"""Transparent database reconnect after the server drops an idle session, against the real mysql.connector.

The unit tests fake the driver behavior this rests on (cursor() pinging the server, so a dead session fails before
the statement is sent). This exercises it end to end: a keep-alive HTTP connection holds one database connection for
its lifetime, so restarting MariaDB under it kills that connection while it sits idle. It drives `systemctl` and
reads the journal, so it is skipped unless POWERGSLB_CONTAINER names a docker container to control.

It restarts a shared dependency: every test after it runs against a container whose MariaDB was bounced, and
powergslb-dbinit drops to inactive (its ConditionPathExists=! fails once the datadir exists).
"""

import os
import subprocess
import time
from typing import Any

import pytest
import requests

from .conftest import W2UIClient

CONTAINER = os.environ.get('POWERGSLB_CONTAINER', '')

pytestmark = pytest.mark.skipif(
    not CONTAINER, reason='POWERGSLB_CONTAINER not set; the reconnect test needs docker/mariadb control')

_RECONNECTED = 'database connection lost, reconnecting'
_PROBE_DOMAIN = 'reconnect-probe.example'


def _lookup(session: requests.Session, base_url: str) -> list[dict[str, Any]]:
    response = session.get(f'{base_url}/dns/lookup/example.com./SOA', timeout=10)
    assert response.status_code == 200, response.text
    return response.json()['result']


def _admin(session: requests.Session, admin_url: str, **params: Any) -> dict[str, Any]:
    response = session.get(f'{admin_url}/admin/w2ui', params=params, timeout=15)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _reconnect_count() -> int:
    journal = subprocess.run(['docker', 'exec', CONTAINER, 'journalctl', '-u', 'powergslb', '--no-pager'],
                             capture_output=True, text=True, check=False).stdout
    return journal.count(_RECONNECTED)


def _restart_mariadb() -> None:
    restart = subprocess.run(['docker', 'exec', CONTAINER, 'systemctl', 'restart', 'mariadb'],
                             capture_output=True, text=True, check=False)
    assert restart.returncode == 0, restart.stderr


def _wait_for_mariadb(timeout: float = 60) -> None:
    """Block until MariaDB answers a query again, so the reconnect is not racing a still-starting server."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        query = subprocess.run(['docker', 'exec', CONTAINER, 'mariadb', 'powergslb', '-N', '-B', '-e', 'SELECT 1'],
                               capture_output=True, text=True, check=False)
        if query.returncode == 0:
            return
        time.sleep(1)
    pytest.fail(f'mariadb did not accept queries again within {timeout}s')


def test_a_restart_under_an_idle_connection_is_reconnected_transparently(base_url: str) -> None:
    with requests.Session() as session:  # keep-alive, so both lookups share one worker connection
        before = _lookup(session, base_url)
        assert before, 'the SOA lookup must return a record for the restart to prove anything'
        reconnects = _reconnect_count()

        _restart_mariadb()
        _wait_for_mariadb()

        # same socket, so the same handler and the same database connection the restart killed
        assert _lookup(session, base_url) == before

    # the answer alone would also hold if the keep-alive had lapsed and a fresh connection served it
    assert _reconnect_count() > reconnects, f'no {_RECONNECTED!r} line; the connection was not reused'


def test_an_admin_write_after_the_restart_runs_on_the_healed_connection(admin_url: str, w2ui: W2UIClient,
                                                                       cleanup: list[tuple[str, int]]) -> None:
    with requests.Session() as session:  # keep-alive, so the read and the write share one worker connection
        session.auth = ('admin', 'admin')
        session.verify = False
        assert _admin(session, admin_url, cmd='get-records', data='domains')['records']
        reconnects = _reconnect_count()

        _restart_mariadb()
        _wait_for_mariadb()

        # the auth read in front of the write already heals the connection; inside the block nothing reconnects
        saved = _admin(session, admin_url, cmd='save-record', data='domains', recid=0,
                       **{'record[domain]': _PROBE_DOMAIN, 'record[description]': 'reconnect probe'})
        assert saved['status'] == 'success', saved

    cleanup.append(('domains', w2ui.find_recid('domains', domain=_PROBE_DOMAIN) or 0))
    assert _reconnect_count() > reconnects, f'no {_RECONNECTED!r} line; the connection was not reused'
