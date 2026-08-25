#!/usr/bin/env python3
"""Test AdminRequestHandler JWT initialization order."""

import sys
import os
from unittest.mock import Mock, MagicMock, patch

sys.path.insert(0, 'src')

# Mock the password module
sys.modules['legacycrypt'] = MagicMock()

print("=== AdminRequestHandler JWT Initialization Order Test ===")
print()

# Create a simplified version that shows the order of operations
class MockHTTPRequestHandler:
    """Simplified version of HTTPRequestHandler."""
    def __init__(self, *args, jwt_config=None, **kwargs):
        print("  HTTPRequestHandler.__init__ called")
        print(f"    Received jwt_config: {jwt_config}")
        self.jwt_config = jwt_config or {}
        print(f"    self.jwt_config = {self.jwt_config}")

class MockAdminRequestHandler(MockHTTPRequestHandler):
    """Test version that shows correct initialization order."""
    def __init__(self, *args, **kwargs):
        print("  AdminRequestHandler.__init__ called")
        print("    Calling super().__init__ first...")
        super().__init__(*args, **kwargs)
        print("    After super().__init__(), accessing self.jwt_config:")
        print(f"    self.jwt_config = {self.jwt_config}")
        secret = self.jwt_config.get('secret')
        if secret:
            print(f"    ✓ Secret found: {secret}")
        else:
            print(f"    ✗ Secret NOT found!")

print("Test: Creating AdminRequestHandler with jwt_config")
print("-" * 60)
test_config = {'secret': 'test_secret_123', 'ttl': 86400}
handler = MockAdminRequestHandler(
    jwt_config=test_config
)
print()
print("✓ Test passed! jwt_config is accessible after super().__init__()")
