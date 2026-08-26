from __future__ import annotations

import pytest

from zhiyi.application.ports.run_repository import (
    RunRepositoryError,
    RunRepositoryErrorCode,
    safe_repository_error_message,
)
from zhiyi.domain.runs.identifiers import CorrelationId

EXPECTED_MESSAGES = {
    RunRepositoryErrorCode.STORAGE_UNAVAILABLE: "Run storage is unavailable",
    RunRepositoryErrorCode.COMMIT_OUTCOME_UNKNOWN: "Run storage commit outcome is unknown",
    RunRepositoryErrorCode.DATA_CORRUPTION: "Run storage data is invalid",
    RunRepositoryErrorCode.SCHEMA_INCOMPATIBLE: "Run storage schema is incompatible",
}


@pytest.mark.parametrize(("code", "message"), EXPECTED_MESSAGES.items())
def test_repository_errors_have_stable_codes_and_constant_messages(
    code: RunRepositoryErrorCode,
    message: str,
) -> None:
    error = RunRepositoryError(code)

    assert safe_repository_error_message(code) == message
    assert str(error) == message
    assert repr(error) == f"RunRepositoryError(code={code.value!r}, correlation_id=None)"


def test_optional_correlation_id_is_the_only_public_diagnostic() -> None:
    error = RunRepositoryError(
        RunRepositoryErrorCode.DATA_CORRUPTION,
        correlation_id=CorrelationId("corr-safe"),
    )

    assert str(error) == "Run storage data is invalid (correlation_id=corr-safe)"
    assert repr(error) == ("RunRepositoryError(code='data_corruption', correlation_id='corr-safe')")


def test_exception_chaining_does_not_leak_internal_details() -> None:
    secret = "postgresql://user:password@database/private tenant-42 SELECT payload"
    try:
        raise RuntimeError(secret)
    except RuntimeError as cause:
        error = RunRepositoryError(RunRepositoryErrorCode.STORAGE_UNAVAILABLE)
        error.__cause__ = cause

    assert secret not in str(error)
    assert secret not in repr(error)


@pytest.mark.parametrize(
    "invalid",
    ["storage_unavailable", object(), None],
)
def test_error_code_type_is_strict(invalid: object) -> None:
    with pytest.raises(TypeError, match="code must be RunRepositoryErrorCode"):
        RunRepositoryError(invalid)  # type: ignore[arg-type]


def test_correlation_id_type_is_strict() -> None:
    with pytest.raises(TypeError, match="correlation_id must be CorrelationId"):
        RunRepositoryError(
            RunRepositoryErrorCode.STORAGE_UNAVAILABLE,
            correlation_id="tenant-42",  # type: ignore[arg-type]
        )
