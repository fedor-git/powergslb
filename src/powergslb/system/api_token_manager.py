"""API token management for programmatic access to PowerGSLB.

Handles CRUD operations for JWT tokens stored in the database,
including token generation, validation, expiration, and revocation.
"""

import datetime
import logging
import secrets
from typing import Any

__all__ = ['APITokenManager']


class APITokenManager:
    """Manages API tokens stored in the database for programmatic access.

    Supports creating, validating, updating, and revoking long-lived API tokens.

    :param jwt_tokens_table: The JWTTokens database table instance.
    :param token_length: Length of generated random token (default: 64 bytes -> 128 hex chars).
    """

    def __init__(self, jwt_tokens_table: Any, token_length: int = 64) -> None:
        """Initialize the API token manager.

        :param jwt_tokens_table: Reference to the JWTTokens table from database/mysql/tables.py
        :param token_length: Number of bytes for random token generation
        """
        self.table = jwt_tokens_table
        self.token_length = token_length
        self.logger = logging.getLogger(__name__)

    def generate_token_string(self) -> str:
        """Generate a random API token string.

        :returns: A cryptographically secure random token (hex encoded).
        """
        return secrets.token_hex(self.token_length)

    def create_token(
        self,
        db: Any,
        user_id: int,
        name: str,
        expires_in_days: int | None = None
    ) -> dict[str, Any]:
        """Create a new API token for a user.

        :param db: Database executor for save operations.
        :param user_id: The user ID this token belongs to.
        :param name: Human-readable name for the token (e.g., 'CI Token', 'API Key').
        :param expires_in_days: Days until expiration; None means no expiration.
        :returns: Dict with 'token' (the generated token), 'id' (token record ID), and metadata.
        :raises ValueError: If user_id or name is invalid.
        """
        if user_id <= 0:
            raise ValueError('user_id must be a positive integer')
        if not name or not isinstance(name, str) or len(name) > 255:
            raise ValueError('name must be a non-empty string (max 255 chars)')

        token = self.generate_token_string()
        expires_at = None

        if expires_in_days:
            expires_at = (datetime.datetime.now() + datetime.timedelta(days=expires_in_days)).isoformat()

        # save() with recid=0 inserts a new row
        affected = self.table.save(
            db,
            save_recid=0,
            token=token,
            name=name,
            user_id=user_id,
            expires_at=expires_at,
            enabled=1
        )

        if affected != 1:
            raise RuntimeError(f'Failed to insert token for user {user_id}')

        token_id = db.last_insert_id()
        self.logger.info("Created API token '%s' for user %d (ID: %d)", name, user_id, token_id)

        return {
            'id': token_id,
            'token': token,
            'name': name,
            'user_id': user_id,
            'expires_at': expires_at,
            'enabled': True,
        }

    def get_token(self, db: Any, token_id: int) -> dict[str, Any] | None:
        """Retrieve a specific token by ID.

        :param db: Database executor for read operations.
        :param token_id: The token record ID.
        :returns: Token record dict if found, None otherwise.
        """
        results = db.select(
            f'SELECT * FROM `{self.table.name}` WHERE `id` = %s',
            (token_id,)
        )
        return results[0] if results else None

    def get_user_tokens(self, db: Any, user_id: int, active_only: bool = True) -> list[dict[str, Any]]:
        """Get all tokens for a user.

        :param db: Database executor for read operations.
        :param user_id: The user ID to fetch tokens for.
        :param active_only: If True, only return non-expired, enabled tokens.
        :returns: List of token records.
        """
        if active_only:
            return self.table.get_active_tokens(db, user_id)

        query = f'''
            SELECT * FROM `{self.table.name}`
            WHERE `user_id` = %s
            ORDER BY `created_at` DESC
        '''
        return db.select(query, (user_id,))

    def validate_token(self, db: Any, token: str) -> dict[str, Any] | None:
        """Check if a token is valid and active.

        :param db: Database executor for read/write operations.
        :param token: The token string to validate.
        :returns: Token record if valid, None if invalid, expired, or disabled.
        """
        results = self.table.check_token(db, token)

        if results:
            record = results[0]
            # Update last_used timestamp
            self.table.update_last_used(db, record['id'])
            return record

        return None

    def revoke_token(self, db: Any, token_id: int) -> bool:
        """Disable a token (soft delete).

        :param db: Database executor for write operations.
        :param token_id: The token ID to revoke.
        :returns: True if revoked successfully, False otherwise.
        """
        affected = self.table.revoke_token(db, token_id)

        if affected > 0:
            self.logger.info("Revoked token ID %d", token_id)
            return True

        self.logger.warning("Failed to revoke token ID %d (not found)", token_id)
        return False

    def update_token_name(self, db: Any, token_id: int, new_name: str) -> bool:
        """Update the name of a token.

        :param db: Database executor for write operations.
        :param token_id: The token ID to update.
        :param new_name: The new name for the token.
        :returns: True if updated successfully, False otherwise.
        :raises ValueError: If new_name is invalid.
        """
        if not new_name or not isinstance(new_name, str) or len(new_name) > 255:
            raise ValueError('new_name must be a non-empty string (max 255 chars)')

        # Fetch current record to preserve other fields
        token_record = self.get_token(db, token_id)
        if not token_record:
            self.logger.warning("Token ID %d not found for update", token_id)
            return False

        affected = self.table.save(
            db,
            save_recid=token_id,
            token=token_record['token'],
            name=new_name,
            user_id=token_record['user_id'],
            expires_at=token_record['expires_at'],
            enabled=token_record['enabled']
        )

        if affected > 0:
            self.logger.info("Updated token ID %d name to '%s'", token_id, new_name)
            return True

        return False

    def list_expired_tokens(self, db: Any) -> list[dict[str, Any]]:
        """List all expired tokens (for cleanup purposes).

        :param db: Database executor for read operations.
        :returns: List of expired token records.
        """
        query = f'''
            SELECT * FROM `{self.table.name}`
            WHERE expires_at IS NOT NULL AND expires_at < NOW()
            ORDER BY `expires_at` DESC
        '''
        return db.select(query)

    def cleanup_expired_tokens(self, db: Any) -> int:
        """Delete all expired tokens from the database.

        :param db: Database executor for write operations.
        :returns: Number of tokens deleted.
        """
        query = f'''
            DELETE FROM `{self.table.name}`
            WHERE expires_at IS NOT NULL AND expires_at < NOW()
        '''
        affected = db.modify(query, ())
        self.logger.info("Cleaned up %d expired tokens", affected)
        return affected
