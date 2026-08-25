# JWT Configuration Fix - Configuration File Reading

## Overview

This fix resolves two issues with the JWT authentication system in PowerGSLB:

1. **JWT Secret Configuration**: JWT secret is now read from the `[jwt]` section of `powergslb.toml` instead of the environment variable `JWT_SECRET_KEY`
2. **Login Redirect**: Users are now redirected to the login page instead of receiving a Basic Auth challenge

## Changes Made

### 1. Configuration Flow (main.py)
- Extract JWT configuration from the `[jwt]` section of the TOML config file
- Pass `jwt_config` to ServerManager for the AdminRequestHandler

```python
# Get JWT configuration if it exists (optional)
jwt_config = config.items('jwt') if 'jwt' in config._data else {}

# Pass to AdminRequestHandler
ServerManager(config.items('admin'), database, status, AdminRequestHandler, 
             jwt_config=jwt_config, name='Admin')
```

### 2. HTTPServerManager Updates (server/http/server.py)
- Accept `jwt_config` parameter in `__init__`
- Store `jwt_config` as instance variable
- Pass `jwt_config` to handler via `functools.partial`

```python
def __init__(self,
             server_config: dict[str, Any],
             database_config: dict[str, Any],
             status_registry: StatusRegistry,
             handler: type[HTTPRequestHandler],
             jwt_config: dict[str, Any] | None = None,
             **kwargs: Any) -> None:
    # ...
    self._jwt_config = jwt_config or {}
    
# In run() method:
handler = functools.partial(
    self._handler,
    directory=self.root,
    database_config=self._database_config,
    status_registry=self._status_registry,
    jwt_config=self._jwt_config,  # NEW
    timeout=self.keep_alive_timeout)
```

### 3. HTTPRequestHandler Updates (server/http/handler/request.py)
- Accept `jwt_config` parameter in `__init__`
- Store as instance variable for subclasses to use

```python
def __init__(self,
             *args: Any,
             database_config: dict[str, Any],
             status_registry: StatusRegistry,
             jwt_config: dict[str, Any] | None = None,
             timeout: float = 300,
             **kwargs: Any) -> None:
    # ...
    self.jwt_config: dict[str, Any] = jwt_config or {}
```

### 4. AdminRequestHandler JWT Initialization (server/http/handler/admin.py)
- Read JWT secret from `self.jwt_config['secret']` instead of `os.environ`
- Read TTL from `self.jwt_config['ttl']` (converts from seconds to hours)
- Log warnings if JWT section is not configured

```python
def __init__(self, *args: Any, **kwargs: Any) -> None:
    super().__init__(*args, **kwargs)
    try:
        secret_key = self.jwt_config.get('secret')
        if not secret_key:
            logging.warning("JWT_SECRET_KEY not configured in [jwt] section of config")
            self.jwt_manager = None
        else:
            expiration_hours = self.jwt_config.get('ttl', 86400) // 3600
            self.jwt_manager = JWTTokenManager(secret_key, expiration_hours=expiration_hours)
            logging.debug("JWT token manager initialized with %d hour expiration", expiration_hours)
    except Exception as e:
        logging.warning("Failed to initialize JWT token manager: %s", e)
        self.jwt_manager = None
```

### 5. Login Redirect Fix (server/http/handler/admin.py)
- `_send_authenticate()` now redirects to `/admin/login.html` instead of sending Basic Auth challenge
- This provides better UX for web-based access while maintaining JWT and Basic Auth support

```python
def _send_authenticate(self, code: int = 401) -> None:
    """Redirect to login page instead of sending Basic Auth challenge."""
    login_url = '/admin/login.html'
    self.send_response(302)  # Found (temporary redirect)
    self.send_header('Location', login_url)
    self.send_header('Content-Length', '0')
    self.end_headers()
    logging.debug("Redirecting to %s for authentication", login_url)
```

## Configuration Example

In `build/powergslb.toml`:

```toml
[jwt]
secret = "dDWQ1qdmmcSw1dpP23xs"  # Your secret key
ttl = 86400                       # Token TTL in seconds (24 hours)
```

## Benefits

✅ **Config-First Approach**: JWT secret is now stored in the config file, not environment variables  
✅ **Better Login UX**: Users see the login page instead of browser's Basic Auth dialog  
✅ **Backward Compatible**: Basic Auth still works alongside JWT  
✅ **Better Error Handling**: Clear warnings if JWT section is missing  
✅ **Optional JWT**: If `[jwt]` section is missing, service still runs without JWT support

## Testing

To verify the changes work correctly:

1. Ensure `[jwt]` section exists in your `powergslb.toml`:
   ```toml
   [jwt]
   secret = "your-secret-key"
   ttl = 86400
   ```

2. Start PowerGSLB:
   ```bash
   python3 src/powergslb/main.py -c build/powergslb.toml
   ```

3. Test login:
   - Navigate to `https://localhost:8443/admin/`
   - Should redirect to `https://localhost:8443/admin/login.html`
   - Enter credentials to receive JWT token
   - Token should be stored and used for subsequent requests

## Files Modified

- `src/powergslb/main.py` - Extract and pass JWT config
- `src/powergslb/server/http/server.py` - HTTPServerManager receives and passes JWT config
- `src/powergslb/server/http/handler/request.py` - Base handler stores JWT config
- `src/powergslb/server/http/handler/admin.py` - AdminRequestHandler reads JWT from config and handles login redirect
