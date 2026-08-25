#!/usr/bin/env python3
"""Test JWT configuration loading from TOML file."""

import sys
sys.path.insert(0, 'src')

from powergslb.system.config import Config

# Load config
config = Config('build/powergslb.toml')

print("=== Config Loading Test ===")
print()

# Check if jwt section exists
print(f"'jwt' in config._data: {'jwt' in config._data}")
print()

# Try to get jwt config
print("Attempting to load JWT config...")
jwt_config = config.items('jwt') if 'jwt' in config._data else {}
print(f"jwt_config type: {type(jwt_config)}")
print(f"jwt_config: {jwt_config}")
print()

# Check keys
if jwt_config:
    print(f"jwt_config.keys(): {list(jwt_config.keys())}")
    print(f"jwt_config.get('secret'): {jwt_config.get('secret')}")
    print(f"jwt_config.get('ttl'): {jwt_config.get('ttl')}")
else:
    print("ERROR: jwt_config is empty!")
    print(f"config._data keys: {list(config._data.keys())}")
