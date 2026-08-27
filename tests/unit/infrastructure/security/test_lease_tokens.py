"""Cryptographic token generation and comparison safety tests."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from zhiyi.application.ports.lease_token_generator import LeaseTokenGenerator
from zhiyi.domain.worker_leases.identifiers import LeaseToken
from zhiyi.infrastructure.security.lease_tokens import (
    SecureLeaseTokenGenerator,
    digest_lease_token,
    lease_token_matches,
)


class DeterministicTokenGenerator:
    def __init__(self, values: list[bytes]) -> None:
        self._values = iter(values)

    def new_token(self) -> LeaseToken:
        return LeaseToken(next(self._values))


def test_deterministic_generator_can_be_injected_through_the_framework_neutral_port() -> None:
    generator: LeaseTokenGenerator = DeterministicTokenGenerator([b"a" * 32, b"b" * 32])

    assert generator.new_token().value == b"a" * 32
    assert generator.new_token().value == b"b" * 32


def test_secure_generator_requests_fresh_32_byte_csprng_material_each_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_token_bytes(size: int) -> bytes:
        calls.append(size)
        return bytes([len(calls)]) * size

    monkeypatch.setattr(
        "zhiyi.infrastructure.security.lease_tokens.secrets.token_bytes",
        fake_token_bytes,
    )
    generator = SecureLeaseTokenGenerator()

    assert generator.new_token().value == b"\x01" * 32
    assert generator.new_token().value == b"\x02" * 32
    assert calls == [32, 32]


def test_secure_generator_does_not_cache_or_deliberately_reuse_a_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def colliding_source(size: int) -> bytes:
        nonlocal calls
        calls += 1
        return b"c" * size

    monkeypatch.setattr(
        "zhiyi.infrastructure.security.lease_tokens.secrets.token_bytes",
        colliding_source,
    )
    generator = SecureLeaseTokenGenerator()

    assert generator.new_token().value == generator.new_token().value
    assert calls == 2


def test_one_hundred_thousand_secure_tokens_are_32_bytes_and_unique() -> None:
    generator = SecureLeaseTokenGenerator()

    values = [generator.new_token().value for _ in range(100_000)]

    assert all(type(value) is bytes and len(value) == 32 for value in values)
    assert len(set(values)) == 100_000


def test_digest_is_sha256_and_comparison_accepts_only_the_matching_token() -> None:
    token = LeaseToken(b"correct-token-material".ljust(32, b"!"))
    other = LeaseToken(b"wrong-token-material".ljust(32, b"!"))
    digest = digest_lease_token(token)

    assert len(digest) == 32
    assert digest.hex() == "9e8f5a38a501c31c4b6bda17b3d774815ee26fbb530f4cd8697ba78e7db20620"
    assert lease_token_matches(token, digest) is True
    assert lease_token_matches(other, digest) is False


@pytest.mark.parametrize("digest", [b"", b"x" * 31, b"x" * 33, bytearray(32), "x" * 32])
def test_digest_comparison_rejects_malformed_digest_without_printing_it(
    digest: object,
) -> None:
    sentinel = b"secret-token-and-digest-material!!"[:32]
    token = LeaseToken(sentinel)

    with pytest.raises((TypeError, ValueError), match="32 bytes") as raised:
        lease_token_matches(token, digest)  # type: ignore[arg-type]

    assert sentinel.hex() not in str(raised.value)
    assert sentinel.hex() not in repr(raised.value)


def test_token_and_digest_sentinels_never_enter_public_object_printing() -> None:
    token_sentinel = b"TOKEN-SENTINEL".ljust(32, b"!")
    digest_sentinel = digest_lease_token(LeaseToken(token_sentinel))
    generator = SecureLeaseTokenGenerator()

    printable = " ".join(
        [
            str(LeaseToken(token_sentinel)),
            repr(LeaseToken(token_sentinel)),
            str(generator),
            repr(generator),
            repr(digest_lease_token),
            repr(lease_token_matches),
        ]
    )
    assert token_sentinel.hex() not in printable
    assert digest_sentinel.hex() not in printable


def test_secure_generator_is_a_structural_port_implementation() -> None:
    factory: Callable[[], LeaseToken] = SecureLeaseTokenGenerator().new_token

    assert isinstance(SecureLeaseTokenGenerator(), LeaseTokenGenerator)
    assert len(factory().value) == 32
