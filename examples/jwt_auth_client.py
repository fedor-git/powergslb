#!/usr/bin/env python3
"""
Example usage of PowerGSLB JWT Authentication API.

This script demonstrates:
1. How to login and get a JWT token
2. How to use the token to make authenticated requests
3. How to handle token expiration
"""

import json
import requests
import sys
from typing import Optional


class PowerGSLBClient:
    """Simple client for PowerGSLB admin API with JWT authentication."""

    def __init__(self, base_url: str = 'http://localhost:8000'):
        """
        Initialize the client.

        :param base_url: Base URL of PowerGSLB admin interface
        """
        self.base_url = base_url.rstrip('/')
        self.token: Optional[str] = None
        self.session = requests.Session()

    def login(self, username: str, password: str) -> bool:
        """
        Login with username and password, get JWT token.

        :param username: Admin username
        :param password: Admin password
        :return: True if login successful, False otherwise
        """
        url = f'{self.base_url}/admin/login'
        payload = {
            'username': username,
            'password': password
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get('status') == 'success':
                self.token = data.get('token')
                print(f"✓ Login successful for user '{username}'")
                print(f"✓ Token: {self.token[:50]}...")
                return True
            else:
                print(f"✗ Login failed: {data.get('message')}")
                return False

        except requests.exceptions.RequestException as e:
            print(f"✗ Login error: {e}")
            return False

    def _get_headers(self) -> dict:
        """Get headers with JWT token."""
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        return headers

    def get_records(self, table: str, limit: int = 10) -> bool:
        """
        Get records from a table using w2ui protocol.

        :param table: Table name (e.g., 'users', 'records', 'domains')
        :param limit: Maximum number of records to fetch
        :return: True if successful, False otherwise
        """
        if not self.token:
            print("✗ Not authenticated. Call login() first.")
            return False

        url = f'{self.base_url}/admin/w2ui'
        data = {
            'cmd': 'get-records',
            'data': table,
            'limit': str(limit),
            'offset': '0'
        }

        try:
            response = requests.post(
                url,
                data=data,
                headers=self._get_headers(),
                timeout=10
            )
            response.raise_for_status()

            result = response.json()
            if result.get('status') == 'success':
                records = result.get('records', [])
                total = result.get('total', 0)
                print(f"✓ Retrieved {len(records)} out of {total} records from '{table}'")
                return True
            else:
                print(f"✗ Error: {result.get('message')}")
                return False

        except requests.exceptions.RequestException as e:
            print(f"✗ Request error: {e}")
            return False

    def get_record(self, table: str, record_id: int) -> Optional[dict]:
        """
        Get a single record by ID.

        :param table: Table name
        :param record_id: Record ID
        :return: Record dict if successful, None otherwise
        """
        if not self.token:
            print("✗ Not authenticated. Call login() first.")
            return None

        url = f'{self.base_url}/admin/w2ui'
        data = {
            'cmd': 'get-record',
            'data': table,
            'recid': str(record_id)
        }

        try:
            response = requests.post(
                url,
                data=data,
                headers=self._get_headers(),
                timeout=10
            )
            response.raise_for_status()

            result = response.json()
            if result.get('status') == 'success':
                record = result.get('record')
                print(f"✓ Retrieved record {record_id} from '{table}'")
                return record
            else:
                print(f"✗ Error: {result.get('message')}")
                return None

        except requests.exceptions.RequestException as e:
            print(f"✗ Request error: {e}")
            return None

    def list_tables(self) -> list[str]:
        """
        List available tables (for demonstration).

        :return: List of table names
        """
        # This is a demonstration - actual tables depend on your setup
        return ['users', 'records', 'domains', 'monitors', 'views', 'status']

    def print_info(self) -> None:
        """Print client information."""
        print(f"\nPowerGSLB Admin Client")
        print(f"  URL: {self.base_url}")
        print(f"  Authenticated: {'Yes' if self.token else 'No'}")
        if self.token:
            print(f"  Token: {self.token[:50]}...")


def main():
    """Main example usage."""
    # Create client
    client = PowerGSLBClient('http://localhost:8000')

    print("\n=== PowerGSLB JWT Authentication Example ===\n")

    # Example 1: Login
    print("Step 1: Logging in...")
    if not client.login('admin', 'admin'):
        print("Login failed. Please check your credentials and server URL.")
        sys.exit(1)

    # Example 2: Print client info
    print("\nStep 2: Client information:")
    client.print_info()

    # Example 3: List available tables
    print("\nStep 3: Available tables:")
    for table in client.list_tables():
        print(f"  - {table}")

    # Example 4: Get records from different tables
    print("\nStep 4: Fetching records from different tables:")
    for table in ['users', 'records']:
        print(f"\n  Fetching from '{table}'...")
        client.get_records(table, limit=5)

    # Example 5: Get a specific record
    print("\nStep 5: Fetching a specific record:")
    record = client.get_record('users', 1)
    if record:
        print(f"  Record content: {json.dumps(record, indent=2)}")

    print("\n=== Example Completed ===\n")


if __name__ == '__main__':
    main()
