"""Secure REST API v1: JWT authentication, JSON request/response, and batch operations.

This API provides a modern JSON-based interface for PowerGSLB management:
- JWT Bearer token authentication via Authorization: Bearer header
- RESTful CRUD operations for all resources
- Batch operations for creating/updating multiple records
- JSON request/response bodies
- Proper HTTP status codes (200, 201, 400, 401, 404, 500)
"""

import json
import logging
from http import HTTPStatus
from typing import Any, ClassVar

from powergslb.database import PageRequest, UserContext, json_default
from powergslb.server.http.handler.request import HTTPRequestHandler
from powergslb.system.jwt_token import JWTTokenManager

__all__ = ['APIRequestHandler']


class APIRequestHandler(HTTPRequestHandler):
    """Serves the secure REST API v1: JSON + JWT auth + batch operations.
    
    Endpoints:
    - GET  /api/v1/{resource}          -> List (with search, sort, paging)
    - GET  /api/v1/{resource}/{id}     -> Single record
    - POST /api/v1/{resource}          -> Create
    - PUT  /api/v1/{resource}/{id}     -> Update
    - DELETE /api/v1/{resource}/{id}   -> Delete
    - POST /api/v1/batch               -> Batch CRUD operations
    """
    route: ClassVar[str] = 'api'
    
    # Valid resource types (tables)
    _resources: ClassVar[set[str]] = {
        'domains', 'monitors', 'records', 'routings', 'types', 'views', 'users', 'jwt_tokens', 'audit'
    }
    
    # The authenticated identity of the request being served
    user: UserContext | None = None
    jwt_manager: JWTTokenManager | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the handler and set up JWT token manager."""
        import os
        
        jwt_config = kwargs.get('jwt_config', {})
        self.jwt_config = jwt_config or {}
        
        # Initialize JWT manager
        try:
            secret_key = self.jwt_config.get('secret')
            if not secret_key:
                self.jwt_manager = None
            else:
                ttl = self.jwt_config.get('ttl', 86400)
                expiration_hours = ttl // 3600
                self.jwt_manager = JWTTokenManager(secret_key, expiration_hours=expiration_hours)
                logging.info("API JWT token manager initialized with %d hour expiration", expiration_hours)
        except Exception as e:
            logging.error("Failed to initialize JWT token manager: %s", e, exc_info=True)
            self.jwt_manager = None
        
        super().__init__(*args, **kwargs)

    def _handle_route(self) -> None:
        """Route the request to the appropriate handler."""
        self.close_connection = True
        
        # Parse path: /api/v1/{endpoint}/{resource}/{id?}
        if len(self.dirs) < 2 or self.dirs[0] != 'api' or self.dirs[1] != 'v1':
            self.send_error(404)
            return
        
        try:
            # POST /api/v1/batch - Batch operations (auth required)
            if len(self.dirs) == 3 and self.dirs[2] == 'batch' and self.command == 'POST':
                if not self._is_authorized():
                    self._send_error(HTTPStatus.UNAUTHORIZED, 'JWT token required')
                else:
                    self._handle_batch()
            
            # RESTful CRUD: /api/v1/{resource} or /api/v1/{resource}/{id}
            elif len(self.dirs) >= 3 and self.dirs[2] in self._resources:
                if not self._is_authorized():
                    self._send_error(HTTPStatus.UNAUTHORIZED, 'JWT token required')
                else:
                    resource = self.dirs[2]
                    record_id = int(self.dirs[3]) if len(self.dirs) > 3 else None
                    
                    if self.command == 'GET' and record_id:
                        self._handle_get_record(resource, record_id)
                    elif self.command == 'GET':
                        self._handle_get_records(resource)
                    elif self.command == 'POST' and not record_id:
                        self._handle_create_record(resource)
                    elif self.command == 'PUT' and record_id:
                        self._handle_update_record(resource, record_id)
                    elif self.command == 'DELETE' and record_id:
                        self._handle_delete_record(resource, record_id)
                    else:
                        self._send_error(HTTPStatus.METHOD_NOT_ALLOWED, f'{self.command} not allowed for this resource')
            else:
                self.send_error(404)
        
        except ValueError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, f'Invalid request: {str(e)}')
        except Exception as e:
            logging.error("API handler error: %s", e, exc_info=True)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, 'Internal server error')

    def _is_authorized(self) -> bool:
        """Check JWT Bearer token in Authorization header (check persistent tokens from jwt_tokens table).
        
        Supports:
        - Persistent API tokens from jwt_tokens table (enabled=1, not expired)
        """
        auth_header = self.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            logging.warning("Missing or invalid Authorization header")
            return False
        
        token = auth_header[7:]  # Remove 'Bearer ' prefix
        logging.debug('Authorization scheme: Bearer, token: %s***', token[:20])
        
        try:
            # Check token in jwt_tokens table (persistent tokens)
            rows = self.database.get_data('jwt_tokens', page=None)
            if not rows or not isinstance(rows, tuple):
                logging.warning('No rows returned from jwt_tokens table or invalid format')
                return False
                
            records, _ = rows
            logging.debug('Checking token against %d jwt_tokens records', len(records))
            
            for record in records:
                record_token = record.get('token')
                record_enabled = record.get('enabled')
                logging.debug('  Comparing token (match=%s, enabled=%s)', 
                            record_token == token, record_enabled)
                
                if record.get('token') == token and record.get('enabled'):
                    # Check expiration if set
                    expires_at = record.get('expires_at')
                    if expires_at:
                        from datetime import datetime
                        try:
                            exp_time = datetime.fromisoformat(str(expires_at).replace(' ', 'T'))
                            if datetime.now() > exp_time:
                                logging.warning('Token expired at %s', expires_at)
                                return False  # Token expired
                        except (ValueError, TypeError) as e:
                            logging.error('Failed to parse token expiration date "%s": %s', expires_at, e)
                            return False  # Invalid expiration date format = invalid token
                    
                    # Token is valid - update last_used timestamp
                    try:
                        from datetime import datetime
                        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        record_id = record.get('recid')  # Note: SELECT uses 'id AS recid'
                        update_query = 'UPDATE `jwt_tokens` SET `last_used` = %s WHERE `id` = %s'
                        self.database.modify(update_query, (now, record_id))
                        logging.debug('Updated last_used timestamp for token id=%s', record_id)
                    except Exception as e:
                        logging.error('Failed to update last_used for token: %s', e)
                        # Continue anyway - token is still valid even if we can't update the timestamp
                    
                    # Create UserContext from token record
                    self.user = UserContext(
                        user_id=record.get('user_id'),
                        user_name=record.get('userName', 'api-user'),
                        roles=['api-token']
                    )
                    logging.info('Bearer token validated successfully for user_id=%s', record.get('user_id'))
                    return True
            
            logging.warning('Token not found in jwt_tokens table or not enabled')
            return False
        
        except Exception as e:
            logging.error('Bearer token verification error: %s', e, exc_info=True)
            return False

    def _handle_get_records(self, resource: str) -> None:
        """GET /api/v1/{resource} - List records with optional filtering, sorting, paging.
        
        Query params:
        - search: JSON object with field:value pairs for filtering
        - sort: JSON array of {field, direction} for sorting
        - limit: Records per page (default 50, max 1000)
        - offset: Records to skip (default 0)
        
        Response: {"status": "success", "data": [...], "total": ..., "limit": ..., "offset": ...}
        """
        try:
            # Parse query parameters
            limit = int(self.query_params.get('limit', [50])[0])
            offset = int(self.query_params.get('offset', [0])[0])
            search_json = self.query_params.get('search', ['{}'])[0]
            sort_json = self.query_params.get('sort', ['[]'])[0]
            
            limit = min(max(limit, 1), 1000)  # 1-1000
            offset = max(offset, 0)
            
            search = json.loads(search_json) if search_json else {}
            sort_data = json.loads(sort_json) if sort_json else []
            
            # Build PageRequest
            page_request = PageRequest(
                search=search or None,
                sort_data=sort_data or None,
                limit=limit,
                offset=offset
            )
            
            # Fetch from database
            records, total = self.database.get_data(resource, page_request)
            
            response = {
                'status': 'success',
                'data': records,
                'total': total,
                'limit': limit,
                'offset': offset
            }
            
            self._send_json_response(response)
        
        except json.JSONDecodeError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, f'Invalid JSON in query params: {e}')
        except Exception as e:
            logging.error("Get records error: %s", e, exc_info=True)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f'Error fetching records: {e}')

    def _handle_get_record(self, resource: str, record_id: int) -> None:
        """GET /api/v1/{resource}/{id} - Get a single record by ID.
        
        Response: {"status": "success", "data": {...}}
        """
        try:
            records, _ = self.database.get_data(resource, PageRequest())
            record_found = next((r for r in records if r.get('recid') == record_id), None)
            
            if not record_found:
                self._send_error(HTTPStatus.NOT_FOUND, f'Record {record_id} not found')
                return
            
            response = {
                'status': 'success',
                'data': record_found
            }
            
            self._send_json_response(response)
        
        except Exception as e:
            logging.error("Get record error: %s", e, exc_info=True)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f'Error fetching record: {e}')

    def _handle_create_record(self, resource: str) -> None:
        """POST /api/v1/{resource} - Create a new record.
        
        Request body: {...record fields...}
        Response: {"status": "success", "id": ..., "data": {...}}
        """
        try:
            record = self._read_json_body()
            
            # Insert record (recid=0 for new records)
            result = self.database.save_data(resource, 0, self.user, **record)
            
            if not result:
                self._send_error(HTTPStatus.BAD_REQUEST, 'Failed to create record')
                return
            
            # Fetch the created record (last inserted)
            records, _ = self.database.get_data(resource, PageRequest())
            new_record = records[-1] if records else None
            
            response = {
                'status': 'success',
                'id': new_record.get('recid') if new_record else 0,
                'data': new_record
            }
            
            self.send_response(HTTPStatus.CREATED)
            self._send_json_response_body(response)
        
        except Exception as e:
            logging.error("Create record error: %s", e, exc_info=True)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f'Error creating record: {e}')

    def _handle_update_record(self, resource: str, record_id: int) -> None:
        """PUT /api/v1/{resource}/{id} - Update an existing record.
        
        Request body: {...fields to update...}
        Response: {"status": "success", "data": {...}}
        """
        try:
            record = self._read_json_body()
            
            # Update record
            result = self.database.save_data(resource, record_id, self.user, **record)
            
            if not result:
                self._send_error(HTTPStatus.NOT_FOUND, f'Record {record_id} not found or unchanged')
                return
            
            # Fetch updated record
            records, _ = self.database.get_data(resource, PageRequest())
            updated_record = next((r for r in records if r.get('recid') == record_id), None)
            
            response = {
                'status': 'success',
                'data': updated_record
            }
            
            self._send_json_response(response)
        
        except Exception as e:
            logging.error("Update record error: %s", e, exc_info=True)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f'Error updating record: {e}')

    def _handle_delete_record(self, resource: str, record_id: int) -> None:
        """DELETE /api/v1/{resource}/{id} - Delete a record.
        
        Response: {"status": "success"}
        """
        try:
            result = self.database.delete_data(resource, [record_id], self.user)
            
            if not result:
                self._send_error(HTTPStatus.NOT_FOUND, f'Record {record_id} not found')
                return
            
            response = {'status': 'success'}
            self._send_json_response(response)
        
        except Exception as e:
            logging.error("Delete record error: %s", e, exc_info=True)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f'Error deleting record: {e}')

    def _handle_batch(self) -> None:
        """POST /api/v1/batch - Batch CRUD operations.
        """
        try:
            body = self._read_json_body()
            operations = body.get('operations', [])
            
            if not isinstance(operations, list):
                self._send_error(HTTPStatus.BAD_REQUEST, 'operations must be an array')
                return
            
            if not operations:
                self._send_error(HTTPStatus.BAD_REQUEST, 'operations array cannot be empty')
                return
            
            results = []
            succeeded = 0
            failed = 0
            
            for idx, op in enumerate(operations):
                try:
                    action = op.get('action')
                    resource = op.get('resource')
                    
                    if resource not in self._resources:
                        results.append({
                            'operation': idx,
                            'status': 'error',
                            'error': f'Invalid resource: {resource}'
                        })
                        failed += 1
                        continue
                    
                    if action == 'create':
                        record_data = op.get('data', {})
                        result = self.database.save_data(resource, 0, self.user, **record_data)
                        
                        if result:
                            records, _ = self.database.get_data(resource, PageRequest())
                            new_record = records[-1] if records else None
                            results.append({
                                'operation': idx,
                                'status': 'success',
                                'action': 'create',
                                'id': new_record.get('recid') if new_record else 0,
                                'data': new_record
                            })
                            succeeded += 1
                        else:
                            results.append({
                                'operation': idx,
                                'status': 'error',
                                'error': 'Failed to create record'
                            })
                            failed += 1
                    
                    elif action == 'update':
                        record_id = op.get('id')
                        record_data = op.get('data', {})
                        
                        if not record_id:
                            results.append({
                                'operation': idx,
                                'status': 'error',
                                'error': 'id required for update'
                            })
                            failed += 1
                            continue
                        
                        result = self.database.save_data(resource, record_id, self.user, **record_data)
                        
                        if result:
                            records, _ = self.database.get_data(resource, PageRequest())
                            updated_record = next((r for r in records if r.get('recid') == record_id), None)
                            results.append({
                                'operation': idx,
                                'status': 'success',
                                'action': 'update',
                                'id': record_id,
                                'data': updated_record
                            })
                            succeeded += 1
                        else:
                            results.append({
                                'operation': idx,
                                'status': 'error',
                                'error': f'Record {record_id} not found or unchanged'
                            })
                            failed += 1
                    
                    elif action == 'delete':
                        record_id = op.get('id')
                        
                        if not record_id:
                            results.append({
                                'operation': idx,
                                'status': 'error',
                                'error': 'id required for delete'
                            })
                            failed += 1
                            continue
                        
                        result = self.database.delete_data(resource, [record_id], self.user)
                        
                        if result:
                            results.append({
                                'operation': idx,
                                'status': 'success',
                                'action': 'delete',
                                'id': record_id
                            })
                            succeeded += 1
                        else:
                            results.append({
                                'operation': idx,
                                'status': 'error',
                                'error': f'Record {record_id} not found'
                            })
                            failed += 1
                    
                    else:
                        results.append({
                            'operation': idx,
                            'status': 'error',
                            'error': f'Invalid action: {action}'
                        })
                        failed += 1
                
                except Exception as e:
                    logging.error("Batch operation %d error: %s", idx, e, exc_info=True)
                    results.append({
                        'operation': idx,
                        'status': 'error',
                        'error': str(e)
                    })
                    failed += 1
            
            response = {
                'status': 'success' if failed == 0 else 'partial',
                'results': results,
                'total': len(operations),
                'succeeded': succeeded,
                'failed': failed
            }
            
            self._send_json_response(response)
        
        except Exception as e:
            logging.error("Batch handler error: %s", e, exc_info=True)
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f'Batch operation failed: {e}')

    def _read_json_body(self) -> dict[str, Any]:
        """Read request body as JSON."""
        if not self.body:
            self._read_body()
        
        if not self.body:
            return {}
        
        try:
            return json.loads(self.body.decode('utf-8'))
        except json.JSONDecodeError as e:
            raise ValueError(f'Invalid JSON in request body: {e}')

    def _send_json_response(self, data: dict[str, Any]) -> None:
        """Send a JSON response with 200 OK status."""
        self.send_response(HTTPStatus.OK)
        self._send_json_response_body(data)

    def _send_json_response_body(self, data: dict[str, Any]) -> None:
        """Send JSON response body."""
        body = json.dumps(data, default=json_default).encode('utf-8')
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        """Send an error response with specified status."""
        self.send_response(status)
        response = {
            'status': 'error',
            'error': message,
            'code': status.value
        }
        self._send_json_response_body(response)

    def _urlsplit(self) -> None:
        """Override to also parse query params."""
        super()._urlsplit()
        
        # For API, parse query parameters as dict of lists
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(self.raw_requestline.split(' ')[1])
        self.query_params = parse_qs(parsed.query)
