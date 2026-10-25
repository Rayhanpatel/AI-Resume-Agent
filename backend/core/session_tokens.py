"""
Signed session tokens.

/session issues one after the Turnstile check; /chat and /chat/stream require it.
Before this, the chat endpoints accepted any UUID, so anyone could script the API
and spend the model budget without ever passing the bot check.

Token = "<session_id>.<expiry unix seconds>.<hmac-sha256 hex>", signed with SESSION_SECRET.
"""
import hashlib
import hmac
import logging
import secrets
import time
from typing import Optional

logger = logging.getLogger(__name__)


class SessionTokens:
    def __init__(self, secret: Optional[str], ttl_hours: int = 24):
        if not secret:
            # Tokens issued by this process die with it; fine for local runs, logged for production.
            secret = secrets.token_hex(32)
            logger.warning("SESSION_SECRET not set; using a random secret, so sessions won't survive a restart")
        self._key = secret.encode()
        self.ttl_seconds = ttl_hours * 3600

    def _sign(self, session_id: str, expires: int) -> str:
        return hmac.new(self._key, f"{session_id}.{expires}".encode(), hashlib.sha256).hexdigest()

    def issue(self, session_id: str) -> str:
        expires = int(time.time()) + self.ttl_seconds
        return f"{session_id}.{expires}.{self._sign(session_id, expires)}"

    def verify(self, token: Optional[str], session_id: str) -> bool:
        """True only if the token was issued by us for this session and hasn't expired"""
        if not token:
            return False
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != session_id:
            return False
        try:
            expires = int(parts[1])
        except ValueError:
            return False
        if expires < time.time():
            return False
        return hmac.compare_digest(parts[2], self._sign(session_id, expires))
