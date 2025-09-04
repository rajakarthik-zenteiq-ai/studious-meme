"""
OAuth / Authentication utilities for mongo_server.
Uses JWT validation via Authlib when OAUTH_ENABLED=true, otherwise falls back to anonymous mode.
Environment variables:
  OAUTH_ENABLED=true|false
  OAUTH_ISSUER=<issuer base url>
  OAUTH_AUDIENCE=<expected audience>
  OAUTH_JWKS_URL=<jwks endpoint> (optional if issuer exposes /.well-known/jwks.json)
  OAUTH_ALGORITHMS=RS256,ES256 (comma list)
"""
import os
import logging
import json
import time
from typing import Dict, Any, Optional
from dataclasses import dataclass
from urllib.request import urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)

try:
    from authlib.jose import JsonWebToken, JsonWebKey
    _AUTHLIB_AVAILABLE = True
except Exception:
    _AUTHLIB_AVAILABLE = False

@dataclass
class AuthResult:
    user_id: str
    authenticated: bool
    oauth_enabled: bool
    claims: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

class OAuthAuthenticator:
    """JWT validator using Authlib (preferred when OAuth enabled)."""
    def __init__(self):
        self.oauth_enabled = os.getenv("OAUTH_ENABLED", "false").lower() == "true"
        self.issuer = os.getenv("OAUTH_ISSUER", "").rstrip('/')
        self.audience = os.getenv("OAUTH_AUDIENCE", "")
        self.jwks_url = os.getenv("OAUTH_JWKS_URL") or (f"{self.issuer}/.well-known/jwks.json" if self.issuer else None)
        self.algorithms = [a.strip() for a in os.getenv("OAUTH_ALGORITHMS", "RS256").split(',') if a.strip()]
        self._jwks_cache: Optional[Dict[str, Any]] = None
        self._jwks_fetched_at: float = 0.0
        self._jwks_ttl = int(os.getenv("OAUTH_JWKS_TTL", "3600"))
        if self.oauth_enabled:
            if not _AUTHLIB_AVAILABLE:
                logger.warning("OAuth enabled but authlib not installed; falling back to anonymous mode")
            elif not self.jwks_url:
                logger.warning("OAuth enabled but no JWKS URL / issuer provided")
            else:
                logger.info(f"OAuth JWT validation active (issuer={self.issuer}, audience={self.audience})")
        else:
            logger.info("OAuth disabled – using anonymous fallback")
        self.jwt = JsonWebToken(self.algorithms) if _AUTHLIB_AVAILABLE else None

    def _fetch_jwks(self) -> Optional[Dict[str, Any]]:
        if not self.jwks_url:
            return None
        now = time.time()
        if self._jwks_cache and (now - self._jwks_fetched_at) < self._jwks_ttl:
            return self._jwks_cache
        try:
            with urlopen(self.jwks_url, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                self._jwks_cache = data
                self._jwks_fetched_at = now
                return data
        except URLError as e:
            logger.error(f"Failed to fetch JWKS: {e}")
        except Exception as e:
            logger.error(f"Unexpected JWKS fetch error: {e}")
        return None

    def _extract_token(self, headers: Any) -> Optional[str]:
        if not headers:
            return None
        auth = headers.get('authorization') or headers.get('Authorization')
        if not auth:
            return None
        if auth.lower().startswith('bearer '):
            return auth.split(' ', 1)[1].strip()
        return None

    def _validate_jwt(self, token: str) -> Optional[Dict[str, Any]]:
        if not self.jwt:
            return None
        jwks = self._fetch_jwks()
        if not jwks:
            return None
        try:
            key_set = JsonWebKey.import_key_set(jwks)
            claims = self.jwt.decode(token, key_set)
            # Basic claim checks
            if self.issuer and claims.get('iss') != self.issuer:
                raise ValueError("Issuer mismatch")
            if self.audience:
                aud = claims.get('aud')
                if isinstance(aud, list):
                    if self.audience not in aud:
                        raise ValueError("Audience mismatch")
                elif aud != self.audience:
                    raise ValueError("Audience mismatch")
            claims.validate()
            return dict(claims)
        except Exception as e:
            logger.warning(f"JWT validation failed: {e}")
            return None

    def validate_request(self, request: Any = None) -> Dict[str, Any]:
        if not self.oauth_enabled:
            return AuthResult(user_id="anonymous", authenticated=False, oauth_enabled=False).__dict__
        if not (_AUTHLIB_AVAILABLE and self.jwks_url):
            return AuthResult(user_id="anonymous", authenticated=False, oauth_enabled=True, error="Authlib/JWKS unavailable").__dict__
        headers = getattr(request, 'headers', {}) if request else {}
        token = self._extract_token(headers)
        if not token:
            return AuthResult(user_id="anonymous", authenticated=False, oauth_enabled=True, error="Missing bearer token").__dict__
        claims = self._validate_jwt(token)
        if not claims:
            return AuthResult(user_id="anonymous", authenticated=False, oauth_enabled=True, error="Invalid token").__dict__
        user_id = str(claims.get('sub') or claims.get('user_id') or 'unknown')
        return AuthResult(user_id=user_id, authenticated=True, oauth_enabled=True, claims=claims).__dict__

    def extract_user_from_context(self, context: Any = None) -> Dict[str, Any]:
        return self.validate_request(getattr(context, 'request', None))

class FallbackAuthenticator:
    """Legacy fallback authenticator (anonymous)."""
    def __init__(self):
        self.oauth_enabled = False
    def validate_request(self, request: Any = None) -> Dict[str, Any]:
        return AuthResult(user_id="anonymous", authenticated=False, oauth_enabled=False).__dict__
    def extract_user_from_context(self, context: Any = None) -> Dict[str, Any]:
        return self.validate_request()

# Factory selection
if os.getenv("OAUTH_ENABLED", "false").lower() == "true":
    authenticator = OAuthAuthenticator()
else:
    authenticator = FallbackAuthenticator()
