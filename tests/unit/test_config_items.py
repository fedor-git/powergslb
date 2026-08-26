#!/usr/bin/env python3
"""Test Config.items() for JWT section."""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# We need to avoid importing the entire system module
# Let's just import Config directly
import tomllib

class SimpleConfig:
    """Minimal Config implementation to test TOML loading."""
    def __init__(self, filename):
        with open(filename, 'rb') as f:
            self._data = tomllib.load(f)
    
    def items(self, section):
        return self._data.get(section, {})

# Test
config = SimpleConfig('build/powergslb.toml')

print("=== Config.items() Test ===")
print()

jwt_config = config.items('jwt') if 'jwt' in config._data else {}
print(f"jwt_config = config.items('jwt'): {jwt_config}")
print(f"type(jwt_config): {type(jwt_config)}")
print()

print("Checking values:")
print(f"  jwt_config.get('secret'): {jwt_config.get('secret', '<not found>')}")
print(f"  jwt_config.get('ttl'): {jwt_config.get('ttl', '<not found>')}")
print()

if jwt_config.get('secret'):
    print("✓ JWT config looks good!")
else:
    print("✗ JWT secret not found!")
