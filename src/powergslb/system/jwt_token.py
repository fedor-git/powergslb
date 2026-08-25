"""JWT token generation and validation for admin authentication."""

import datetime
import json
import logging
from typing import Any

import jwt

__all__ = ['JWTTokenManager']


class JWTTokenManager:
    """Manages JWT token creation and validation for admin sessions.

    :param secret_key: The secret key for encoding/decoding tokens.
    :param algorithm: The algorithm to use for encoding (default: HS256).
    :param expiration_hours: Token expiration time in hours (default: 24).
    """

    def __init__(
        self,
        secret_key: str,
        algorithm: str = 'HS256',
        expiration_hours: int = 24
    ) -> None:
        """Initialize the JWT token manager.

        :param secret_key: The secret key for encoding/decoding tokens.
        :param algorithm: The algorithm to use for encoding.
        :param expiration_hours: Token expiration time in hours.
        :raises ValueError: When the secret_key is empty.
        """
        if not secret_key:
            raise ValueError('secret_key cannot be empty')

        self.secret_key = secret_key
        self.algorithm = algorithm
        self.expiration_hours = expiration_hours
        self.logger = logging.getLogger(__name__)

    def generate_token(self, user_id: int, username: str, name: str) -> str:
        """Generate a JWT token for a user.

        :param user_id: The user's database ID.
        :param username: The user's login name.
        :param name: The user's display name.
        :returns: The encoded JWT token.
        """
        payload: dict[str, Any] = {
            'user_id': user_id,
            'username': username,
            'name': name,
            'iat': datetime.datetime.utcnow(),
            'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=self.expiration_hours),
        }

        try:
            token = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
            self.logger.debug("Generated JWT token for user '%s'", username)
            return token
        except Exception as e:
            self.logger.error("Failed to generate JWT token for user '%s': %s", username, e)
            raise

    def validate_token(self, token: str) -> dict[str, Any] | None:
        """Validate and decode a JWT token.

        :param token: The JWT token to validate.
        :returns: The decoded payload if valid, None if invalid or expired.
        """
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            self.logger.debug("JWT token validated for user '%s'", payload.get('username'))
            return payload
        except jwt.ExpiredSignatureError:
            self.logger.warning("JWT token has expired")
            return None
        except jwt.InvalidTokenError as e:
            self.logger.warning("Invalid JWT token: %s", e)
            return None
        except Exception as e:
            self.logger.error("Error validating JWT token: %s", e)
            return None

    def is_token_valid(self, token: str) -> bool:
        """Check if a JWT token is valid.

        :param token: The JWT token to check.
        :returns: True if token is valid and not expired, False otherwise.
        """
        return self.validate_token(token) is not None
