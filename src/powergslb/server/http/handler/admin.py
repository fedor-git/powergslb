"""Admin interface handler: Basic Auth, JWT, w2ui CRUD protocol, and static assets."""

import base64
import datetime
import email.utils
import gzip
import io
import json
import logging
import os
import urllib.parse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from typing import Any, Callable, ClassVar, BinaryIO

import brotli

from powergslb.database import PageRequest, UserContext, json_default
from powergslb.monitor import MonitorManager
from powergslb.routing import RoutingPolicy
from powergslb.server.http.handler.queryparser import QueryParserError, parse_query
from powergslb.server.http.handler.request import HTTPRequestHandler
from powergslb.system.jwt_token import JWTTokenManager
from powergslb.system.password import verify_password
from powergslb.view import ViewRule

__all__ = ['AdminRequestHandler']


class AdminRequestHandler(HTTPRequestHandler):
    """Serves the admin interface: Basic Auth/JWT, w2ui grid CRUD at /admin/w2ui, and static assets.

    Passes the w2ui query to the database get_data/save_data/delete_data dispatchers. Search, sort, and paging run in
    SQL: the handler translates the query into a PageRequest and the database composes it into the SQL read.
    
    Supports two authentication methods:
    1. Basic Auth (traditional username:password in Authorization header)
    2. JWT Token (Bearer token in Authorization header, obtained from /admin/login)
    """
    route: ClassVar[str] = 'admin'

    # The authenticated identity of the request being served, set per request by _is_authorized().
    user: UserContext | None = None
    
    # JWT token manager, initialized in __init__
    jwt_manager: JWTTokenManager | None = None

    _cache_control: ClassVar[str | None] = 'no-store'

    _commands: ClassVar[dict[str, str]] = {
        'delete-records': '_delete_records',
        'get-items': '_get_items',
        'get-record': '_get_record',
        'get-records': '_get_records',
        'login': '_login',
        'save-record': '_save_record'
    }

    # (Content-Encoding token, precompressed sibling suffix, per-request compressor), most preferred first.
    _encodings: ClassVar[tuple[tuple[str, str, Callable[[bytes], bytes]], ...]] = (
        ('br', '.br', lambda data: brotli.compress(data, quality=5)),
        ('gzip', '.gz', lambda data: gzip.compress(data, compresslevel=6)),
    )
    # Below this size a dynamic response is sent uncompressed.
    _min_encode_size: ClassVar[int] = 256
    
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the handler and set up JWT token manager BEFORE processing request.
        
        CRITICAL: JWT manager MUST be initialized BEFORE super().__init__() because
        SimpleHTTPRequestHandler.__init__() immediately starts processing the request
        (calls do_POST, do_GET, etc) and we need jwt_manager available during that.
        """
        import logging
        
        # Check if we're in DEVELOPMENT mode - disable compression for live reload
        development_mode = os.environ.get('DEVELOPMENT', '').lower() == 'true'
        if development_mode:
            logging.info("DEVELOPMENT mode enabled - disabling compression for static assets")
            self._encodings = ()  # Disable all compression in dev mode
        
        jwt_config = kwargs.get('jwt_config', {})
        
        # Store jwt_config so HTTPRequestHandler can access it
        self.jwt_config: dict[str, Any] = jwt_config or {}
        
        # Initialize JWT manager BEFORE super().__init__() because parent class
        # immediately processes the request and we need jwt_manager available
        try:
            
            secret_key = self.jwt_config.get('secret')
            
            if not secret_key:
                self.jwt_manager = None
            else:
                ttl = self.jwt_config.get('ttl', 86400)
                expiration_hours = ttl // 3600
                self.jwt_manager = JWTTokenManager(secret_key, expiration_hours=expiration_hours)
                logging.info("JWT token manager initialized with %d hour expiration BEFORE super().__init__()", expiration_hours)
        except Exception as e:
            logging.error("Failed to initialize JWT token manager: %s", e, exc_info=True)
            self.jwt_manager = None
        
        # NOW call super().__init__() - at this point jwt_manager is already initialized
        super().__init__(*args, **kwargs)

    def _handle_route(self) -> None:
        """Authenticate (if needed), then serve the login, w2ui CRUD endpoint, or fall through to static admin assets."""
        self.close_connection = True
        
        # Block direct access to /admin/login.html - redirect to /login
        if self.path == '/admin/login.html':
            self.send_response(302)
            self.send_header('Location', '/login')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        
        # /public/* - Public assets (no auth required): login page, favicon, CSS
        if self.command == 'GET' and len(self.dirs) >= 1 and self.dirs[0] == 'public':
            SimpleHTTPRequestHandler.do_GET(self)
        # GET /login - Login page (no auth required, rewrite to /public/login.html)
        elif self.path == '/login' and self.command == 'GET':
            self.path = '/public/login.html'  # Rewrite path to serve login.html
            SimpleHTTPRequestHandler.do_GET(self)
        # POST /login - JWT login endpoint (no auth required)
        elif self.path == '/login' and self.command == 'POST':
            login_response = self._handle_login()
            # If login was successful, set JWT token as HTTP-only cookie
            if login_response.get('status') == 'success' and login_response.get('token'):
                self._send_json_response_with_cookie(login_response, login_response['token'])
            else:
                self._send_json_response(login_response)
        # DELETE /logout - Clear JWT cookie and redirect to login
        elif self.path == '/logout' and self.command == 'DELETE':
            self._handle_logout()
        # w2ui CRUD endpoint - auth required (MUST check BEFORE generic GET to avoid static file fallback)
        elif len(self.dirs) == 2 and self.dirs[1] == 'w2ui':
            if not self._is_authorized():
                self._send_authenticate()
            elif self.command in ('GET', 'POST'):
                self._send_content(self.content(), debug=self.command == 'GET')
            else:
                self.send_error(404)
        # GET requests (static admin files) - auth required
        elif self.command == 'GET':
            if not self._is_authorized():
                self._send_authenticate()
            else:
                SimpleHTTPRequestHandler.do_GET(self)
        # HEAD requests - auth required
        elif self.command == 'HEAD':
            if not self._is_authorized():
                self._send_authenticate()
            else:
                SimpleHTTPRequestHandler.do_HEAD(self)
        # Everything else requires auth
        elif not self._is_authorized():
            self._send_authenticate()
        else:
            self.send_error(404)

    def send_head(self) -> io.BytesIO | BinaryIO | None:
        """Serve a static asset, preferring a precompressed sibling the client accepts.

        A resolved file or a directory index is served from here; everything else stays pure stdlib.
        
        In DEVELOPMENT mode (when _encodings is empty), always serve the identity (uncompressed) file.

        :returns: An open file object for the caller to stream and close, or None when nothing further remains.
        """
        path = self._static_file_path()
        if path is None:
            return super().send_head()

        # DEVELOPMENT mode: _encodings is empty, always serve uncompressed
        if not self._encodings:
            return self._send_static_head(path, path, None)

        accepted = self._accepted_encodings()
        for encoding, suffix, _ in self._encodings:
            encoded_path = path + suffix
            if encoding in accepted and os.path.isfile(encoded_path):
                return self._send_static_head(path, encoded_path, encoding)

        return self._send_static_head(path, path, None)

    def _static_file_path(self) -> str | None:
        """Resolve the request to the on-disk regular file stdlib would serve.

        A directory URL resolves to its index page; a directory without a trailing slash or index page returns None.

        :returns: The file path to negotiate, or None.
        """
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            if not urllib.parse.urlsplit(self.path).path.endswith('/'):
                return None

            for index in self.index_pages:
                index_path = os.path.join(path, index)
                if os.path.isfile(index_path):
                    return index_path
            return None

        return path if os.path.isfile(path) else None

    def _accepted_encodings(self) -> set[str]:
        """Parse Accept-Encoding into the set of codings the client accepts (dropping any q=0 token).

        :returns: The lowercased coding tokens with a non-zero q-value.
        """
        accepted = set()
        for part in self.headers.get('Accept-Encoding', '').split(','):
            tokens = part.split(';')
            coding = tokens[0].strip().lower()
            if not coding:
                continue
            quality = 1.0
            for param in tokens[1:]:
                param = param.strip()
                if param.startswith('q='):
                    try:
                        quality = float(param[2:])
                    except ValueError:
                        quality = 0.0  # unparseable q counts as a refusal (nginx-style)
            if quality > 0:
                accepted.add(coding)
        return accepted

    def _encode_body(self, content_bytes: bytes) -> tuple[bytes, str | None]:
        """Compress a dynamic response, negotiating brotli then gzip against Accept-Encoding.

        Bodies under _min_encode_size go out identity (the CPU is not worth the tiny reply).

        :param content_bytes: The identity response body.
        :returns: The (possibly compressed) body and its Content-Encoding token, or None for identity.
        """
        if len(content_bytes) < self._min_encode_size:
            return content_bytes, None

        accepted = self._accepted_encodings()
        for encoding, _, compress in self._encodings:
            if encoding in accepted:
                return compress(content_bytes), encoding

        return content_bytes, None

    def _send_static_head(self, path: str, disk_path: str, encoding: str | None) -> io.BytesIO | BinaryIO | None:
        """Send headers for a static asset, mirroring the stdlib static path.

        The Content-Type is guessed from the original file, not the .br/.gz twin; Content-Length, Last-Modified and
        the If-Modified-Since comparison all use the file actually served; Vary: Accept-Encoding rides every
        representation so a shared cache keys the identity and precompressed bodies apart.

        :param path: The original file path, used only to guess the Content-Type.
        :param disk_path: The identity file or a precompressed sibling to open and serve.
        :param encoding: The Content-Encoding token for a sibling, or None to serve the identity file.
        :returns: The open file object, or None on a 304.
        """
        ctype = self.guess_type(path)
        try:
            f = open(disk_path, 'rb')  # pylint: disable=consider-using-with
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None

        try:
            fs = os.fstat(f.fileno())
            if 'If-Modified-Since' in self.headers and 'If-None-Match' not in self.headers:
                try:
                    ims = email.utils.parsedate_to_datetime(self.headers['If-Modified-Since'])
                except (TypeError, IndexError, OverflowError, ValueError):
                    pass
                else:
                    if ims.tzinfo is None:
                        ims = ims.replace(tzinfo=datetime.timezone.utc)
                    if ims.tzinfo is datetime.timezone.utc:
                        last_modif = datetime.datetime.fromtimestamp(fs.st_mtime, datetime.timezone.utc)
                        last_modif = last_modif.replace(microsecond=0)
                        if last_modif <= ims:
                            self.send_response(HTTPStatus.NOT_MODIFIED)
                            self.end_headers()
                            f.close()
                            return None

            self.send_response(HTTPStatus.OK)
            self.send_header('Content-Type', ctype)
            if encoding is not None:
                self.send_header('Content-Encoding', encoding)
            self.send_header('Content-Length', str(fs[6]))
            self.send_header('Last-Modified', self.date_time_string(fs.st_mtime))
            self.send_header('Vary', 'Accept-Encoding')
            self.end_headers()
            return f

        except BaseException:
            f.close()
            raise

    def _is_authorized(self) -> bool:
        """Validate credentials (JWT cookie, Basic Auth, or JWT bearer token) against the database.
        
        Tries in order:
        1. JWT token from powergslb_token cookie (HTTP-only, set by login)
        2. Authorization header (Bearer or Basic)
        
        On success the identity row (id, user, name) and the client address are stored on self.user as the
        request's UserContext; a fresh or failed request resets it first.

        :returns: True when the request carries valid credentials.
        """
        self.user = None
        
        # Try JWT token from cookie first (HTTP-only cookie set by login)
        cookie_header = self.headers.get('Cookie')
        if cookie_header:
            logging.debug(f'_is_authorized: Cookie header found: {cookie_header[:50]}...')
            token = self._extract_cookie_value(cookie_header, 'powergslb_token')
            if token:
                logging.debug(f'_is_authorized: Found powergslb_token cookie: {token[:20]}...')
                if self._verify_jwt_token(token):
                    logging.debug('_is_authorized: JWT token verified successfully')
                    return True
                else:
                    logging.warning('_is_authorized: JWT token verification failed')
            else:
                logging.debug('_is_authorized: powergslb_token cookie not found in Cookie header')
        else:
            logging.debug('_is_authorized: No Cookie header in request')
        
        # Fall back to Authorization header (Bearer or Basic)
        authorization_header = self.headers.get('Authorization')
        if not authorization_header:
            logging.debug('_is_authorized: No Authorization header, returning False')
            return False

        try:
            scheme, credentials = authorization_header.split(' ', 1)
            
            # Try Bearer token (JWT) authentication
            if scheme.lower() == 'bearer':
                return self._verify_jwt_token(credentials)
            
            # Try Basic authentication
            elif scheme.lower() == 'basic':
                user, password = base64.b64decode(credentials).decode('utf-8').split(':', 1)
                return self._verify_basic_auth(user, password)
            
            else:
                logging.warning("Unknown authorization scheme: %s", scheme)
                return False
                
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.error('authorization error: %s', e)
            return False

    @staticmethod
    def _extract_cookie_value(cookie_header: str, cookie_name: str) -> str | None:
        """Extract a cookie value by name from the Cookie header.

        :param cookie_header: The value of the Cookie header (e.g., "cookie1=val1; cookie2=val2").
        :param cookie_name: The name of the cookie to extract.
        :returns: The cookie value, or None if not found.
        """
        for cookie_pair in cookie_header.split(';'):
            cookie_pair = cookie_pair.strip()
            if '=' in cookie_pair:
                name, value = cookie_pair.split('=', 1)
                if name == cookie_name:
                    return value
        return None

    def _verify_basic_auth(self, user: str, password: str) -> bool:
        """Verify Basic Auth credentials against the database.

        :param user: The login name.
        :param password: The plaintext password.
        :returns: True if credentials are valid, False otherwise.
        """
        rows = self.database.check_user(user, password)
        if rows:
            self.user = UserContext(rows[0]['id'], rows[0]['user'], rows[0]['name'], self._client_ip())
            return True
        else:
            logging.error("user '%s' not authorized", user)
            return False

    def _verify_jwt_token(self, token: str) -> bool:
        """Verify JWT token and extract user information.

        :param token: The JWT token string.
        :returns: True if token is valid, False otherwise.
        """
        if not self.jwt_manager:
            return False

        payload = self.jwt_manager.validate_token(token)
        if not payload:
            return False

        try:
            # Create user context from JWT payload
            self.user = UserContext(
                id=payload['user_id'],
                user=payload['username'],
                name=payload['name'],
                client_ip=self._client_ip()
            )
            return True
        except KeyError as e:
            logging.error("Missing required field in JWT payload: %s", e)
            return False

    def _send_authenticate(self, code: int = 401) -> None:
        """Redirect to login page instead of sending Basic Auth challenge.

        :param code: HTTP status code (used for logging, but always redirects to login).
        """
        # For admin interface, redirect to login page instead of Basic Auth challenge
        # This allows both JWT and Basic Auth while providing a better user experience
        login_url = '/login'
        self.send_response(302)
        self.send_header('Location', login_url)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _send_json_response(self, data: dict[str, Any], status_code: int = 200) -> None:
        """Send a JSON response with appropriate headers.

        :param data: The data to encode as JSON.
        :param status_code: HTTP status code (default: 200 OK).
        """
        response_json = json.dumps(data, separators=(',', ':'))
        response_bytes = response_json.encode('utf-8')
        
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(response_bytes)))
        self.send_header('Cache-Control', self._cache_control or 'no-cache')
        self.end_headers()
        self.wfile.write(response_bytes)

    def _send_json_response_with_cookie(self, data: dict[str, Any], token: str, 
                                       status_code: int = 200, ttl_hours: int = 24) -> None:
        """Send a JSON response with JWT token set as HTTP-only cookie.

        :param data: The data to encode as JSON.
        :param token: JWT token to set as cookie.
        :param status_code: HTTP status code (default: 200 OK).
        :param ttl_hours: Cookie TTL in hours (default: 24).
        """
        response_json = json.dumps(data, separators=(',', ':'))
        response_bytes = response_json.encode('utf-8')
        
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(response_bytes)))
        self.send_header('Cache-Control', self._cache_control or 'no-cache')
        
        # Set JWT token as HTTP-only cookie
        # Note: SameSite removed to allow cross-site cookie transmission during login redirect
        cookie_value = f'powergslb_token={token}; Max-Age={ttl_hours * 3600}; Path=/; HttpOnly'
        self.send_header('Set-Cookie', cookie_value)
        
        self.end_headers()
        self.wfile.write(response_bytes)

    def _handle_login(self) -> dict[str, Any]:
        """Handle login request and return JWT token.

        :returns: JSON response with token or error message.
        """
        if not self.jwt_manager:
            logging.error("JWT token manager is None! jwt_config=%s, secret=%s", 
                         self.jwt_config, self.jwt_config.get('secret') if self.jwt_config else 'N/A')
            return {
                'status': 'error',
                'message': 'JWT token manager not configured'
            }

        try:
            self._read_body()
            
            login_data = json.loads(self.body.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logging.error('Failed to parse login request: %s', e, exc_info=True)
            return {
                'status': 'error',
                'message': 'Invalid request format'
            }

        username = login_data.get('username', '').strip()
        password = login_data.get('password', '')

        if not username or not password:
            logging.warning("_handle_login: Missing username or password")
            return {
                'status': 'error',
                'message': 'Username and password are required'
            }

        # Verify credentials in database
        rows = self.database.check_user(username, password)
        if not rows:
            logging.warning("Failed login attempt for user '%s' from %s", username, self._client_ip())
            return {
                'status': 'error',
                'message': 'Invalid username or password'
            }

        user_data = rows[0]
        
        try:
            # Generate JWT token
            token = self.jwt_manager.generate_token(
                user_id=user_data['id'],
                username=user_data['user'],
                name=user_data['name']
            )
            
            logging.info("User '%s' logged in successfully from %s", username, self._client_ip())
            
            return {
                'status': 'success',
                'message': 'Login successful',
                'token': token,
                'user': {
                    'id': user_data['id'],
                    'username': user_data['user'],
                    'name': user_data['name']
                }
            }
        except Exception as e:
            logging.error('Failed to generate JWT token for user %s: %s', username, e)
            return {
                'status': 'error',
                'message': 'Failed to generate authentication token'
            }

    def _handle_logout(self) -> None:
        """Handle logout request: clear JWT cookie by setting Max-Age=0 and return JSON response.
        
        Browser will automatically delete the HTTP-only cookie when Max-Age=0.
        """
        response = {
            'status': 'success',
            'message': 'Logged out successfully'
        }
        
        # Send JSON response with cookie cleared (Max-Age=0)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        # Clear cookie by setting Max-Age=0
        self.send_header('Set-Cookie', 'powergslb_token=; Max-Age=0; Path=/; HttpOnly')
        body = json.dumps(response).encode('utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        
        logging.info("User logged out from %s", self._client_ip())

    def _delete_records(self) -> dict[str, Any]:
        """Handle the delete-records command: delete the selected rows from the database.

        A scalar selection is wrapped, so the selected is always a list. Requires an authorized request (self.user set).

        :returns: The w2ui status reply.
        """
        data = self.query.get('data')
        selected = self.query.get('selected')
        
        if not isinstance(selected, list):
            selected = [selected]

        assert self.user is not None
        
        if not self.database.delete_data(data, selected, self.user):
            logging.error("_delete_records: Failed to delete records from '%s'", data)
            return {'status': 'error', 'message': 'records not deleted'}
        
        return {'status': 'success'}

    def _get_data(self, data: Any, recid: int = 0,
                  page: PageRequest | None = None) -> tuple[list[dict[str, Any]], int]:
        """Read a token's table from the database; the status table needs the down-id snapshot.

        :param data: The table token from the query.
        :param recid: The key value to fetch; 0 fetches every row.
        :param page: The search/sort/paging request; None returns every matching row.
        :returns: The matching rows and the total match count.
        """
        if data == 'status':
            return self.database.get_data(data, recid, page, down_ids=self.status_registry.snapshot())
        return self.database.get_data(data, recid, page)

    def _get_items(self) -> dict[str, Any]:
        """Handle the get-items command: collect one field's values from the database for a combo dropdown.

        For users table, returns list of {id, text} objects so combo can store numeric ID and display username.
        For other tables, returns simple list of field values.

        :returns: The w2ui reply with the collected items.
        """
        data = self.query.get('data')
        field = self.query.get('field')
        records, _ = self._get_data(data, page=PageRequest.from_query(self.query))
        
        # For users table, return {id, text} format for proper ID storage
        if data == 'users' and field == 'user':
            items = [{'id': record.get('recid'), 'text': record.get(field)} 
                     for record in records if record.get(field) is not None]
        else:
            items = [record.get(field) for record in records if record.get(field) is not None]
        
        return {'status': 'success', 'items': items}

    def _get_record(self) -> dict[str, Any]:
        """Handle the get-record command: fetch one row from the database by recid.

        :returns: The w2ui reply with the record, or an error when the recid does not exist.
        """
        data = self.query.get('data')
        recid = int(self.query.get('recid'))

        records, _ = self._get_data(data, recid)
        if not records:
            return {'status': 'error', 'message': f"get-record '{data}' id {recid} not found"}
        
        record = records[0]
        # No field renaming needed - use user_id consistently
        
        return {'status': 'success', 'record': record}

    def _get_records(self) -> dict[str, Any]:
        """Handle the get-records command: read the database records searched, sorted and paged in SQL.

        :returns: The w2ui reply with the total match count and the requested page.
        """
        data = self.query.get('data')
        records, total = self._get_data(data, page=PageRequest.from_query(self.query))
        if data == 'status':
            self._style_status(records)
        return {'status': 'success', 'total': total, 'records': records}

    def _parse_query(self) -> None:
        """Parse the query string (GET) or request body (POST) into self.query; a parse error yields an empty query."""
        try:
            if self.query:
                logging.debug("_parse_query: Using query string: %s", self.query)
                parsed = parse_query(self.query)
                
                # w2ui sends GET requests with all data in a 'request' URL parameter as JSON string
                # e.g., ?request={"cmd":"get-records","data":"status",...}
                # We need to extract and parse that JSON
                if 'request' in parsed and isinstance(parsed['request'], str):
                    try:
                        request_json = json.loads(parsed['request'])
                        logging.debug("_parse_query: Extracted w2ui request JSON: %s", request_json)
                        self.query = request_json
                    except (json.JSONDecodeError, ValueError) as e:
                        logging.warning("_parse_query: Could not parse 'request' as JSON (%s), using raw params", e)
                        self.query = parsed
                else:
                    self.query = parsed
                    
            elif self.body:
                body_str = self.body.decode('utf-8')
                logging.debug("_parse_query: Using body: %s", body_str)
                
                content_type = self.headers.get('Content-Type', '').lower()
                if 'application/json' in content_type:
                    try:
                        self.query = json.loads(body_str)
                    except json.JSONDecodeError as e:
                        logging.error('JSON parse error: %s', e)
                        self.query = {}
                else:
                    self.query = parse_query(body_str)
            else:
                logging.warning("_parse_query: No query string or body to parse!")
                self.query = {}
        except QueryParserError as e:
            logging.error('query parse error: %s', e)
            self.query = {}

    def _masked_query(self) -> Any:
        """Return the query with any posted record password masked for safe logging.

        :returns: A shallow copy with record password masked, or self.query unchanged when there is none.
        """
        record = self.query.get('record') if isinstance(self.query, dict) else None
        if isinstance(record, dict) and 'password' in record:
            return {**self.query, 'record': {**record, 'password': self._mask}}
        return self.query

    def _save_record(self) -> dict[str, Any]:
        """Handle the save-record command: validate the posted record, then insert or update it.

        An invalid record is rejected before the write. Requires an authorized request (self.user set).
        
        Special handling for jwt_tokens: if user_id is a username string, lookup the user ID.

        :returns: The w2ui status reply.
        """
        data = self.query.get('data')
        recid = int(self.query.get('recid'))
        record = self.query.get('record')

        self._validate_record(data, record)

        # Special handling: for jwt_tokens, if user_id is a non-numeric string, lookup the user ID
        if data == 'jwt_tokens' and isinstance(record.get('user_id'), str):
            user_id_str = record['user_id'].strip()
            # Check if it's a numeric ID already
            if not user_id_str.isdigit():
                # It's a username, lookup the user ID
                users_data, _ = self._get_data('users', page=PageRequest())
                user_found = None
                for user in users_data:
                    if user.get('user') == user_id_str:
                        user_found = user.get('recid')
                        break
                if user_found is None:
                    raise ValueError(f"User '{user_id_str}' not found")
                record['user_id'] = user_found

        assert self.user is not None
        try:
            result = self.database.save_data(data, recid, self.user, **record)
            # For jwt_tokens, accept 0 rows affected as success (record unchanged)
            # MySQL returns 0 when UPDATE doesn't actually change any values
            if data == 'jwt_tokens':
                return {'status': 'success'}
            # For other tables, treat 0 as error  (record not changed)
            if not result:
                return {'status': 'error', 'message': 'record not changed'}
            return {'status': 'success'}
        except Exception as e:
            logging.error(f"JWT Token save: ERROR in save_data: {type(e).__name__}: {e}", exc_info=True)
            raise

    @staticmethod
    def _validate_record(data: Any, record: dict[str, Any]) -> None:
        """Reject an invalid record before the database write.

        :param data: The table token from the query.
        :param record: The record fields posted by the admin form.
        :raises ValueError: When validation failed.
        """
        if data == 'monitors':
            # The record content is unknown at monitor-definition time; validate a placeholder IP.
            MonitorManager.build_check({'content': '127.0.0.1', 'monitor_json': record['monitor_json']})

        elif data == 'routings':
            RoutingPolicy.resolve(record['policy_json'])

        elif data == 'views':
            ViewRule.resolve(record['rule'])
        
        elif data == 'jwt_tokens':
            # Validate jwt_tokens record
            expires_at = record.get('expires_at', '')
            
            # Handle various input types
            if expires_at is None:
                expires_at = ''
            elif not isinstance(expires_at, str):
                expires_at = str(expires_at).strip()
            else:
                expires_at = expires_at.strip()
            
            # Validate format if not empty
            if expires_at:
                try:
                    from datetime import datetime
                    # Try YYYY-MM-DD HH:MM:SS format first
                    if ' ' in expires_at:
                        datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S')
                    else:
                        # Try YYYY-MM-DD format
                        datetime.strptime(expires_at, '%Y-%m-%d')
                except (ValueError, TypeError) as e:
                    raise ValueError("Expires At must be in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format or empty")

    @staticmethod
    def _style_status(records: list[dict[str, Any]]) -> None:
        """Annotate each status row with its display style from the SQL-computed status value.

        :param records: The status rows to annotate in place.
        """
        for record in records:
            record['style'] = 'color: red' if record['status'] == 'Off' else 'color: green'

    def content(self) -> str:
        """Dispatch the w2ui cmd to its handler; an unknown command or a handler error becomes an error reply.

        A deliberate validation error (ValueError) is surfaced to the UI; any other exception is logged and
        answered with a generic message, so an internal failure (database, type) is not disclosed to the client.

        :returns: The JSON-encoded w2ui reply.
        """
        self._parse_query()
        command = self.query.get('cmd') if isinstance(self.query, dict) else None
        method_name = self._commands.get(command) if isinstance(command, str) else None
        
        logging.debug("content: query=%s, cmd=%s, method=%s", self.query, command, method_name)

        if method_name is None:
            content: dict[str, Any] = {'status': 'error', 'message': f"command '{command}' not implemented"}
        else:
            try:
                content = getattr(self, method_name)()
            except ValueError as e:
                logging.error('%s: %s', type(e).__name__, e)
                content = {'status': 'error', 'message': str(e)}
            except Exception as e:  # pylint: disable=broad-exception-caught
                logging.error('%s: %s', type(e).__name__, e)
                content = {'status': 'error', 'message': 'internal error'}

        return json.dumps(content, separators=(',', ':'), default=json_default)
