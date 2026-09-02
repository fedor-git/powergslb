#!/usr/bin/env python3
"""Test TOML parsing."""

import tomllib

with open('build/powergslb.toml', 'rb') as f:
    data = tomllib.load(f)

print("=== TOML Parsing Test ===")
print(f"Sections in TOML: {list(data.keys())}")
print()

if 'jwt' in data:
    print("✓ 'jwt' section found")
    print(f"  Content: {data['jwt']}")
    print(f"  Keys: {list(data['jwt'].keys())}")
else:
    print("✗ 'jwt' section NOT found")
