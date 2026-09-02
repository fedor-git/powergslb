"""Tests for JWT Token Manager."""

import time
import pytest
import jwt

from powergslb.system.jwt_token import JWTTokenManager


class TestJWTTokenManager:
    """Test suite for JWT token generation and validation."""

    @pytest.fixture
    def manager(self):
        """Create a JWT token manager for testing."""
        return JWTTokenManager('test-secret-key', expiration_hours=24)

    def test_token_generation(self, manager):
        """Test that token is generated successfully."""
        token = manager.generate_token(1, 'testuser', 'Test User')
        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_format(self, manager):
        """Test that generated token has valid JWT format."""
        token = manager.generate_token(1, 'testuser', 'Test User')
        # JWT should have three parts separated by dots
        parts = token.split('.')
        assert len(parts) == 3

    def test_token_validation(self, manager):
        """Test that valid token is validated successfully."""
        token = manager.generate_token(1, 'testuser', 'Test User')
        payload = manager.validate_token(token)
        
        assert payload is not None
        assert payload['user_id'] == 1
        assert payload['username'] == 'testuser'
        assert payload['name'] == 'Test User'

    def test_token_payload_structure(self, manager):
        """Test that token payload contains required fields."""
        token = manager.generate_token(42, 'admin', 'Administrator')
        payload = manager.validate_token(token)
        
        assert 'user_id' in payload
        assert 'username' in payload
        assert 'name' in payload
        assert 'iat' in payload  # issued at
        assert 'exp' in payload  # expiration

    def test_invalid_token_format(self, manager):
        """Test that invalid token format is rejected."""
        invalid_tokens = [
            'invalid',
            'invalid.token',
            'invalid.token.format.extra',
            'a.b.c',
            '',
            None
        ]
        
        for invalid_token in invalid_tokens:
            if invalid_token is not None:
                payload = manager.validate_token(invalid_token)
                assert payload is None

    def test_tampered_token(self, manager):
        """Test that tampered token is rejected."""
        token = manager.generate_token(1, 'testuser', 'Test User')
        # Tamper with token by changing a character
        tampered_token = token[:-5] + 'xxxxx'
        
        payload = manager.validate_token(tampered_token)
        assert payload is None

    def test_wrong_secret_key(self, manager):
        """Test that token signed with different key is rejected."""
        token = manager.generate_token(1, 'testuser', 'Test User')
        
        # Create manager with different secret
        wrong_manager = JWTTokenManager('different-secret-key')
        payload = wrong_manager.validate_token(token)
        
        assert payload is None

    def test_token_expiration(self):
        """Test that expired token is rejected."""
        # Create manager with very short expiration
        manager = JWTTokenManager('test-secret-key', expiration_hours=0)
        token = manager.generate_token(1, 'testuser', 'Test User')
        
        # Token should be valid immediately
        payload = manager.validate_token(token)
        assert payload is not None
        
        # Wait a bit and try again
        time.sleep(1)
        payload = manager.validate_token(token)
        assert payload is None  # Token should be expired

    def test_is_token_valid(self, manager):
        """Test is_token_valid convenience method."""
        token = manager.generate_token(1, 'testuser', 'Test User')
        assert manager.is_token_valid(token) is True
        
        invalid_token = 'invalid.token.here'
        assert manager.is_token_valid(invalid_token) is False

    def test_empty_secret_key(self):
        """Test that empty secret key raises error."""
        with pytest.raises(ValueError):
            JWTTokenManager('')

    def test_different_users(self, manager):
        """Test that tokens for different users have different payloads."""
        token1 = manager.generate_token(1, 'user1', 'User One')
        token2 = manager.generate_token(2, 'user2', 'User Two')
        
        payload1 = manager.validate_token(token1)
        payload2 = manager.validate_token(token2)
        
        assert payload1['user_id'] == 1
        assert payload1['username'] == 'user1'
        assert payload2['user_id'] == 2
        assert payload2['username'] == 'user2'

    def test_multiple_tokens_for_same_user(self, manager):
        """Test that multiple tokens can be issued for the same user."""
        token1 = manager.generate_token(1, 'testuser', 'Test User')
        token2 = manager.generate_token(1, 'testuser', 'Test User')
        
        # Tokens should be different (different iat timestamps)
        assert token1 != token2
        
        # But both should be valid
        payload1 = manager.validate_token(token1)
        payload2 = manager.validate_token(token2)
        
        assert payload1 is not None
        assert payload2 is not None

    def test_algorithm_hs256(self, manager):
        """Test that HS256 algorithm is used."""
        token = manager.generate_token(1, 'testuser', 'Test User')
        
        # Decode header without verification
        header = jwt.get_unverified_header(token)
        assert header['alg'] == 'HS256'

    def test_custom_expiration_hours(self):
        """Test that custom expiration hours work."""
        # 1 hour expiration
        manager = JWTTokenManager('test-secret', expiration_hours=1)
        token = manager.generate_token(1, 'testuser', 'Test User')
        
        payload = manager.validate_token(token)
        
        # Expiration should be approximately 1 hour from now
        exp_time = payload['exp']
        iat_time = payload['iat']
        
        # Should be close to 3600 seconds (1 hour)
        diff = exp_time - iat_time
        assert 3590 < diff < 3610  # Allow 10 second tolerance

    def test_unicode_usernames(self, manager):
        """Test that unicode usernames are handled correctly."""
        unicode_users = [
            ('користувач', 'Користувач'),  # Ukrainian
            ('ユーザー', 'ユーザー'),  # Japanese
            ('пользователь', 'Пользователь'),  # Russian
            ('用户', '用户'),  # Chinese
        ]
        
        for username, name in unicode_users:
            token = manager.generate_token(1, username, name)
            payload = manager.validate_token(token)
            
            assert payload is not None
            assert payload['username'] == username
            assert payload['name'] == name
