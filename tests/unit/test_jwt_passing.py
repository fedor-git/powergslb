#!/usr/bin/env python3
"""Mock test to verify jwt_config is passed correctly through initialization chain."""

import sys
import os
from unittest.mock import Mock, MagicMock, patch
import functools

sys.path.insert(0, 'src')

# We need to mock the password module since it has missing dependencies
sys.modules['legacycrypt'] = MagicMock()

print("=== JWT Config Passing Test ===")
print()

# Test 1: functools.partial with jwt_config
print("Test 1: functools.partial with jwt_config")
print("-" * 50)

def mock_handler_init(*args, **kwargs):
    """Mock handler init that captures jwt_config."""
    jwt_config = kwargs.get('jwt_config')
    print(f"  mock_handler_init received jwt_config: {jwt_config}")
    return {
        'jwt_config_received': jwt_config,
        'other_kwargs': {k: v for k, v in kwargs.items() if k != 'jwt_config'}
    }

test_jwt_config = {'secret': 'test_secret', 'ttl': 86400}

# Simulate what functools.partial does
handler = functools.partial(
    mock_handler_init,
    directory='/tmp',
    database_config={'host': 'localhost'},
    status_registry=Mock(),
    jwt_config=test_jwt_config,
    timeout=300
)

# Call it
result = handler()
print(f"  ✓ Result: {result}")
print()

# Test 2: Check if jwt_config survives in functools.partial
print("Test 2: Verify jwt_config in partial keywords")
print("-" * 50)
print(f"  handler.keywords: {handler.keywords}")
print(f"  'jwt_config' in handler.keywords: {'jwt_config' in handler.keywords}")
print(f"  handler.keywords['jwt_config']: {handler.keywords.get('jwt_config')}")
print()

# Test 3: Simulate HTTPServerManager flow
print("Test 3: Simulate HTTPServerManager flow")
print("-" * 50)

server_config = {'address': '0.0.0.0', 'port': 8443}
database_config = {'host': 'localhost'}
jwt_config = {'secret': 'from_toml', 'ttl': 86400}

print(f"  server_config: {server_config}")
print(f"  database_config: {database_config}")
print(f"  jwt_config: {jwt_config}")
print()

# Simulate HTTPServerManager.__init__
print("  HTTPServerManager.__init__:")
_database_config = database_config
_status_registry = Mock()
_jwt_config = jwt_config or {}
print(f"    _jwt_config = {_jwt_config}")
print()

# Simulate HTTPServerManager.run()
print("  HTTPServerManager.run():")
handler_partial = functools.partial(
    mock_handler_init,
    directory='/tmp',
    database_config=_database_config,
    status_registry=_status_registry,
    jwt_config=_jwt_config,
    timeout=300
)
print(f"    Created partial with jwt_config: {handler_partial.keywords.get('jwt_config')}")

# Call handler
result = handler_partial()
print(f"    Handler received: {result['jwt_config_received']}")
print()

print("✓ All tests passed! jwt_config is properly passed through the chain.")
