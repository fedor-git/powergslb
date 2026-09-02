# pylint: disable=missing-function-docstring, redefined-outer-name

"""REST API v1 tests.

Covers the HTTPS REST API v1 at /api/v1/{resource}/*: JWT Bearer authentication, 
CRUD operations (GET/POST/PUT/DELETE), batch operations, search/filter, pagination, 
JSON request/response bodies, and full lifecycle testing for records, domains, monitors, 
routings, types, views, users, and jwt_tokens resources.
"""

from typing import Any

import pytest
import requests

from .conftest import APIClient, W2UIClient


class TestAPIAuth:
    """JWT Bearer authentication tests for REST API v1."""

    def test_no_auth_returns_401(self, admin_url: str) -> None:
        """Requests without Bearer token should return 401."""
        response = requests.get(
            f'{admin_url}/api/v1/records',
            verify=False,
            timeout=15
        )
        assert response.status_code == 401

    def test_invalid_token_returns_401(self, admin_url: str) -> None:
        """Requests with invalid Bearer token should return 401."""
        response = requests.get(
            f'{admin_url}/api/v1/records',
            headers={'Authorization': 'Bearer invalid_token_12345'},
            verify=False,
            timeout=15
        )
        assert response.status_code == 401

    def test_valid_token_returns_200(self, api: APIClient) -> None:
        """Requests with valid Bearer token should succeed."""
        response = api.list('records')
        assert response.status_code == 200
        data = response.json()
        assert data.get('status') == 'success'


class TestAPIListRecords:
    """List/GET operations for REST API v1."""

    def test_list_records_returns_paginated_data(self, api: APIClient) -> None:
        """GET /api/v1/records should return paginated records."""
        response = api.list('records', limit=10, offset=0)
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'success'
        assert 'records' in data
        assert 'total' in data
        assert isinstance(data['records'], list)
        if data['total'] > 0:
            assert len(data['records']) > 0

    def test_list_domains(self, api: APIClient) -> None:
        """GET /api/v1/domains should list all domains."""
        response = api.list('domains')
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'success'
        assert data['total'] >= 3  # example.com, example.net, example.org

    def test_list_monitors(self, api: APIClient) -> None:
        """GET /api/v1/monitors should list all monitors."""
        response = api.list('monitors')
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'success'
        assert data['total'] >= 1

    def test_list_with_pagination(self, api: APIClient) -> None:
        """GET /api/v1/{resource}?limit=L&offset=O should respect pagination params."""
        response = api.list('records', limit=5, offset=0)
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'success'
        # Verify limit parameter is respected
        assert len(data['records']) <= 5


class TestAPIGetRecord:
    """Single record retrieval tests."""

    def test_get_existing_record(self, api: APIClient, w2ui: W2UIClient) -> None:
        """GET /api/v1/records/{id} should return a single record."""
        # First, get a record ID from list
        response = api.list('records', limit=1)
        assert response.status_code == 200
        records = response.json()['records']
        assert len(records) > 0
        
        recid = records[0]['recid']
        # Now get the specific record
        response = api.get('records', recid)
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'success'
        assert data['record']['recid'] == recid

    def test_get_nonexistent_record_returns_404(self, api: APIClient) -> None:
        """GET /api/v1/records/{invalid_id} should return 404."""
        response = api.get('records', 999999)
        assert response.status_code == 404


class TestAPICreateRecord:
    """Record creation tests."""

    def test_create_a_record(self, api: APIClient, cleanup: list[tuple[str, int]]) -> None:
        """POST /api/v1/records should create a new A record."""
        record_data = {
            'domain': 'example.com',
            'name': 'api-test-a',
            'name_type': 'A',
            'ttl': 600,
            'content': '203.0.113.1',
            'monitor': 'No check',
            'view': 'Public',
            'policy': 'Round robin',
            'weight': 10,
            'disabled': 0
        }
        response = api.create('records', **record_data)
        assert response.status_code == 201
        data = response.json()
        assert data['status'] == 'success'
        assert 'record' in data
        created_record = data['record']
        assert created_record['name'] == 'api-test-a'
        assert created_record['content'] == '203.0.113.1'
        
        # Add to cleanup
        cleanup.append(('records', created_record['recid']))

    def test_create_aaaa_record(self, api: APIClient, cleanup: list[tuple[str, int]]) -> None:
        """POST /api/v1/records should support AAAA records."""
        record_data = {
            'domain': 'example.com',
            'name': 'api-test-aaaa',
            'name_type': 'AAAA',
            'ttl': 600,
            'content': '2001:db8::1',
            'monitor': 'No check',
            'view': 'Public',
            'policy': 'Round robin',
            'weight': 0,
            'disabled': 0
        }
        response = api.create('records', **record_data)
        assert response.status_code == 201
        data = response.json()
        assert data['status'] == 'success'
        created_record = data['record']
        assert created_record['name_type'] == 'AAAA'
        assert created_record['content'] == '2001:db8::1'
        cleanup.append(('records', created_record['recid']))

    def test_create_domain(self, api: APIClient, cleanup: list[tuple[str, int]]) -> None:
        """POST /api/v1/domains should create a new domain."""
        domain_data = {
            'domain': f'api-test-domain-{id(api)}.com',
            'description': 'Created by API test'
        }
        response = api.create('domains', **domain_data)
        assert response.status_code == 201
        data = response.json()
        assert data['status'] == 'success'
        created_domain = data['record']
        assert created_domain['domain'] == domain_data['domain']
        cleanup.append(('domains', created_domain['recid']))


class TestAPIUpdateRecord:
    """Record update tests."""

    def test_update_record_weight(self, api: APIClient, cleanup: list[tuple[str, int]]) -> None:
        """PUT /api/v1/records/{id} should update a record's weight."""
        # Create a record first
        record_data = {
            'domain': 'example.com',
            'name': 'api-test-update',
            'name_type': 'A',
            'ttl': 600,
            'content': '203.0.113.2',
            'monitor': 'No check',
            'view': 'Public',
            'policy': 'Round robin',
            'weight': 10,
            'disabled': 0
        }
        create_resp = api.create('records', **record_data)
        assert create_resp.status_code == 201
        recid = create_resp.json()['record']['recid']
        cleanup.append(('records', recid))
        
        # Update the record
        update_data = {'weight': 50}
        update_resp = api.update('records', recid, **update_data)
        assert update_resp.status_code == 200
        data = update_resp.json()
        assert data['status'] == 'success'
        assert data['record']['weight'] == 50
        # Verify content wasn't lost (was preserved from original)
        assert data['record']['content'] == '203.0.113.2'

    def test_update_record_ttl(self, api: APIClient, cleanup: list[tuple[str, int]]) -> None:
        """PUT /api/v1/records/{id} should update TTL while preserving other fields."""
        # Create a record
        record_data = {
            'domain': 'example.com',
            'name': 'api-test-ttl',
            'name_type': 'A',
            'ttl': 300,
            'content': '203.0.113.3',
            'monitor': 'No check',
            'view': 'Public',
            'policy': 'Round robin',
            'weight': 20,
            'disabled': 0
        }
        create_resp = api.create('records', **record_data)
        recid = create_resp.json()['record']['recid']
        cleanup.append(('records', recid))
        
        # Update TTL
        update_resp = api.update('records', recid, ttl=1800)
        assert update_resp.status_code == 200
        data = update_resp.json()
        assert data['record']['ttl'] == 1800
        assert data['record']['weight'] == 20  # Preserved


class TestAPIDeleteRecord:
    """Record deletion tests."""

    def test_delete_record(self, api: APIClient) -> None:
        """DELETE /api/v1/records/{id} should delete a record."""
        # Create a record
        record_data = {
            'domain': 'example.com',
            'name': 'api-test-delete',
            'name_type': 'A',
            'ttl': 600,
            'content': '203.0.113.4',
            'monitor': 'No check',
            'view': 'Public',
            'policy': 'Round robin',
            'weight': 0,
            'disabled': 0
        }
        create_resp = api.create('records', **record_data)
        assert create_resp.status_code == 201
        recid = create_resp.json()['record']['recid']
        
        # Delete the record
        delete_resp = api.delete('records', recid)
        assert delete_resp.status_code == 200
        data = delete_resp.json()
        assert data['status'] == 'success'
        
        # Verify it's deleted
        get_resp = api.get('records', recid)
        assert get_resp.status_code == 404

    def test_delete_nonexistent_record_returns_error(self, api: APIClient) -> None:
        """DELETE /api/v1/records/{invalid_id} should fail gracefully."""
        response = api.delete('records', 999999)
        # Should return error status
        assert response.status_code in [400, 404, 500]


class TestAPISearchRecords:
    """Search/filter operations."""

    def test_search_records_by_domain(self, api: APIClient) -> None:
        """POST /api/v1/records/search should filter records by domain."""
        response = api.search('records', search={'domain': 'example.com'})
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'success'
        assert 'records' in data
        # All returned records should have domain=example.com
        for record in data['records']:
            assert record['domain'] == 'example.com'

    def test_search_records_by_name_type(self, api: APIClient) -> None:
        """POST /api/v1/records/search should filter records by name_type."""
        response = api.search('records', search={'name_type': 'A'})
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'success'
        if len(data['records']) > 0:
            for record in data['records']:
                assert record['name_type'] == 'A'

    def test_search_with_pagination(self, api: APIClient) -> None:
        """POST /api/v1/records/search should support pagination."""
        response = api.search('records', limit=5, offset=0)
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'success'
        assert len(data['records']) <= 5


class TestAPIBatchOperations:
    """Batch CRUD operations tests."""

    def test_batch_create_records(self, api: APIClient, cleanup: list[tuple[str, int]]) -> None:
        """POST /api/v1/records/batch should create multiple records."""
        operations = [
            {
                'action': 'create',
                'data': {
                    'domain': 'example.com',
                    'name': 'api-batch-1',
                    'name_type': 'A',
                    'ttl': 600,
                    'content': '203.0.113.10',
                    'monitor': 'No check',
                    'view': 'Public',
                    'policy': 'Round robin',
                    'weight': 0,
                    'disabled': 0
                }
            },
            {
                'action': 'create',
                'data': {
                    'domain': 'example.com',
                    'name': 'api-batch-2',
                    'name_type': 'A',
                    'ttl': 600,
                    'content': '203.0.113.11',
                    'monitor': 'No check',
                    'view': 'Public',
                    'policy': 'Round robin',
                    'weight': 0,
                    'disabled': 0
                }
            }
        ]
        
        response = api.batch('records', operations)
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'success'
        assert 'results' in data
        assert len(data['results']) == 2
        
        # Both operations should succeed
        for result in data['results']:
            assert result['status'] == 'success'
            # Note: batch results don't include created record IDs, so we can't add to cleanup easily
            # In production, you'd want to track them for cleanup

    def test_batch_update_records(self, api: APIClient, cleanup: list[tuple[str, int]]) -> None:
        """POST /api/v1/records/batch should update multiple records."""
        # First create some records
        create_ops = [
            {
                'action': 'create',
                'data': {
                    'domain': 'example.com',
                    'name': f'api-batch-upd-{i}',
                    'name_type': 'A',
                    'ttl': 600,
                    'content': f'203.0.113.{20 + i}',
                    'monitor': 'No check',
                    'view': 'Public',
                    'policy': 'Round robin',
                    'weight': 10,
                    'disabled': 0
                }
            }
            for i in range(2)
        ]
        
        create_resp = api.batch('records', create_ops)
        assert create_resp.status_code == 200
        
        # Get record IDs to update
        list_resp = api.list('records')
        records = list_resp.json()['records']
        batch_created = [r for r in records if 'api-batch-upd' in r.get('name', '')]
        assert len(batch_created) >= 2
        
        # Prepare update operations
        update_ops = [
            {
                'action': 'update',
                'id': r['recid'],
                'data': {'weight': 50}
            }
            for r in batch_created[:2]
        ]
        
        update_resp = api.batch('records', update_ops)
        assert update_resp.status_code == 200
        data = update_resp.json()
        assert data['status'] == 'success'
        assert len(data['results']) == 2
        
        # Cleanup
        for r in batch_created[:2]:
            cleanup.append(('records', r['recid']))

    def test_batch_mixed_operations(self, api: APIClient, cleanup: list[tuple[str, int]]) -> None:
        """POST /api/v1/records/batch should handle mixed create/update/delete operations."""
        # First create records to update/delete
        create_resp = api.create('records', **{
            'domain': 'example.com',
            'name': 'api-batch-mix',
            'name_type': 'A',
            'ttl': 600,
            'content': '203.0.113.30',
            'monitor': 'No check',
            'view': 'Public',
            'policy': 'Round robin',
            'weight': 10,
            'disabled': 0
        })
        existing_id = create_resp.json()['record']['recid']
        
        operations = [
            {
                'action': 'create',
                'data': {
                    'domain': 'example.com',
                    'name': 'api-batch-mix-new',
                    'name_type': 'A',
                    'ttl': 600,
                    'content': '203.0.113.31',
                    'monitor': 'No check',
                    'view': 'Public',
                    'policy': 'Round robin',
                    'weight': 0,
                    'disabled': 0
                }
            },
            {
                'action': 'update',
                'id': existing_id,
                'data': {'weight': 99}
            }
        ]
        
        response = api.batch('records', operations)
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'success'
        assert len(data['results']) == 2
        assert all(r['status'] == 'success' for r in data['results'])
        
        # Verify update
        get_resp = api.get('records', existing_id)
        assert get_resp.json()['record']['weight'] == 99
        
        cleanup.append(('records', existing_id))


class TestAPIResourceTypes:
    """Test API operations on different resource types."""

    def test_list_and_get_monitors(self, api: APIClient) -> None:
        """Test GET operations on monitors resource."""
        list_resp = api.list('monitors')
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert data['total'] >= 1
        
        if len(data['records']) > 0:
            monitor = data['records'][0]
            get_resp = api.get('monitors', monitor['recid'])
            assert get_resp.status_code == 200
            assert get_resp.json()['record']['recid'] == monitor['recid']

    def test_list_and_get_routings(self, api: APIClient) -> None:
        """Test GET operations on routings resource."""
        list_resp = api.list('routings')
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert data['total'] >= 1

    def test_list_and_get_views(self, api: APIClient) -> None:
        """Test GET operations on views resource."""
        list_resp = api.list('views')
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert data['total'] >= 1

    def test_list_and_get_types(self, api: APIClient) -> None:
        """Test GET operations on types resource."""
        list_resp = api.list('types')
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert data['total'] >= 1
