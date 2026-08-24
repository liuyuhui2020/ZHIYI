from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping

import pytest

from zhiyi.adapters.secrets.environment import EnvironmentSecretProvider
from zhiyi.application.ports.secret_provider import SecretReference, SecretResolutionError


class NonEnumerableEnvironment(Mapping[str, str]):
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values
        self.lookups: list[str] = []

    def __getitem__(self, key: str) -> str:
        self.lookups.append(key)
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("environment enumeration is forbidden")

    def __len__(self) -> int:
        raise AssertionError("environment enumeration is forbidden")


@pytest.mark.asyncio
async def test_environment_provider_reads_only_allowlisted_reference() -> None:
    environment = NonEnumerableEnvironment(
        {"OPENAI_API_KEY": "sentinel-openai", "UNRELATED_SECRET": "must-not-read"}
    )
    provider = EnvironmentSecretProvider(
        allowed_references=(SecretReference("OPENAI_API_KEY"),),
        environment=environment,
    )

    value = await provider.resolve(SecretReference("OPENAI_API_KEY"))

    assert value.reveal() == "sentinel-openai"
    assert str(value) == "********"
    assert repr(value) == "SecretValue(********)"
    assert environment.lookups == ["OPENAI_API_KEY"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reference", "environment"),
    [
        ("ANTHROPIC_API_KEY", {"ANTHROPIC_API_KEY": "not-allowlisted"}),
        ("OPENAI_API_KEY", {}),
        ("OPENAI_API_KEY", {"OPENAI_API_KEY": "   "}),
    ],
)
async def test_unavailable_secret_has_one_non_enumerating_safe_error(
    reference: str, environment: dict[str, str]
) -> None:
    provider = EnvironmentSecretProvider(
        allowed_references=(SecretReference("OPENAI_API_KEY"),),
        environment=NonEnumerableEnvironment(environment),
    )

    with pytest.raises(SecretResolutionError) as caught:
        await provider.resolve(SecretReference(reference))

    assert str(caught.value) == "Secret is unavailable"
    assert "not-allowlisted" not in repr(caught.value)


@pytest.mark.asyncio
async def test_concurrent_resolution_never_crosses_credentials() -> None:
    provider = EnvironmentSecretProvider(
        allowed_references=(
            SecretReference("OPENAI_API_KEY"),
            SecretReference("ANTHROPIC_API_KEY"),
        ),
        environment={
            "OPENAI_API_KEY": "sentinel-openai",
            "ANTHROPIC_API_KEY": "sentinel-anthropic",
        },
    )
    references = [
        SecretReference("OPENAI_API_KEY" if index % 2 == 0 else "ANTHROPIC_API_KEY")
        for index in range(1_000)
    ]

    values = await asyncio.gather(*(provider.resolve(reference) for reference in references))

    assert all(
        value.reveal()
        == ("sentinel-openai" if reference.name == "OPENAI_API_KEY" else "sentinel-anthropic")
        for reference, value in zip(references, values, strict=True)
    )
