"""Password hashing, session tokens and a simple login rate limiter.

No extra dependency (passlib/bcrypt/itsdangerous) is pulled in for this:
stdlib `hashlib.pbkdf2_hmac` is a real, currently-recommended KDF and is
plenty for a single-tenant admin panel; sessions are opaque random tokens
looked up server-side (app/database.py sessions table), not signed
cookies, so revocation is a straight DELETE rather than a crypto problem.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone

_PBKDF2_ITERATIONS = 200_000
SESSION_COOKIE_NAME = "session_id"
SESSION_TTL = timedelta(days=7)
CSRF_HEADER = "X-CSRF-Token"


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, password_hash)


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


def session_expiry() -> datetime:
    return datetime.now(timezone.utc) + SESSION_TTL


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


class LoginRateLimiter:
    """Fixed-window limiter per source IP: `max_attempts` failures within
    `window_seconds` locks that IP out until the window rolls over. In
    memory only - fine for a single-process admin panel, and resets on
    restart, which is an acceptable tradeoff for simplicity."""

    def __init__(self, max_attempts: int = 5, window_seconds: int = 300) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = {}

    def is_locked_out(self, key: str) -> bool:
        self._prune(key)
        return len(self._attempts.get(key, [])) >= self.max_attempts

    def record_failure(self, key: str) -> None:
        self._prune(key)
        self._attempts.setdefault(key, []).append(time.monotonic())

    def record_success(self, key: str) -> None:
        self._attempts.pop(key, None)

    def _prune(self, key: str) -> None:
        cutoff = time.monotonic() - self.window_seconds
        attempts = self._attempts.get(key)
        if attempts:
            self._attempts[key] = [t for t in attempts if t >= cutoff]
