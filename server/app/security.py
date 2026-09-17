"""Node-token and admin-key handling.

Node tokens are shown to the user exactly once (at creation/regeneration
time) and stored server-side only as a SHA-256 hash, the same pattern as a
password hash - a leaked database does not leak usable tokens.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_node_token() -> str:
    return "nmt_" + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
