# pylint: disable=missing-function-docstring

"""Tests for the seed data (database/data.sql).

The file lives at the project root, outside src/, so it is read through pytestconfig.rootpath rather than
importlib.resources.
"""

import json
import re

import pytest


def test_seed_json_matches_the_form_the_console_writes(pytestconfig: pytest.Config) -> None:
    # the admin console collapses monitor_json / policy_json to the json.dumps default separators on save,
    # so seeds already in that form make opening and saving a seed row rewrite nothing and audit nothing
    data_sql = (pytestconfig.rootpath / 'database' / 'data.sql').read_text()
    values = re.findall(r"'(\{[^']*\})'", data_sql)
    assert values
    for value in values:
        assert json.dumps(json.loads(value)) == value
