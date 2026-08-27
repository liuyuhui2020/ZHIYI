"""Production Worker lease token generation and digest comparison."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from zhiyi.domain.worker_leases.identifiers import LEASE_TOKEN_BYTES, LeaseToken


class SecureLeaseTokenGenerator:
    """Create a fresh independent 256-bit capability for every ownership attempt."""

    def new_token(self) -> LeaseToken:
        return LeaseToken(secrets.token_bytes(LEASE_TOKEN_BYTES))


def digest_lease_token(token: LeaseToken) -> bytes:
    if not isinstance(token, LeaseToken):
        raise TypeError("token must be LeaseToken")
    return hashlib.sha256(token.value).digest()


def lease_token_matches(token: LeaseToken, expected_digest: bytes) -> bool:
    if not isinstance(token, LeaseToken):
        raise TypeError("token must be LeaseToken")
    if type(expected_digest) is not bytes:
        raise TypeError("expected digest must contain exactly 32 bytes")
    if len(expected_digest) != hashlib.sha256().digest_size:
        raise ValueError("expected digest must contain exactly 32 bytes")
    return hmac.compare_digest(digest_lease_token(token), expected_digest)
