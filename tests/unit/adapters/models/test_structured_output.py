from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict, Field

from zhiyi.adapters.models.structured_output import PydanticOutputContract


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


def test_pydantic_contract_exports_schema_and_returns_json_safe_value() -> None:
    contract = PydanticOutputContract(Answer)

    assert contract.name == "Answer"
    assert contract.json_schema["additionalProperties"] is False
    assert contract.validate({"summary": "ok", "confidence": 0.75}) == {
        "summary": "ok",
        "confidence": 0.75,
    }


def test_pydantic_contract_sanitizes_validation_errors_and_repr() -> None:
    contract = PydanticOutputContract(Answer)
    sensitive = "never-echo-this-value"

    with pytest.raises(ValueError, match="Answer") as caught:
        contract.validate({"summary": sensitive, "confidence": 7})

    assert sensitive not in str(caught.value)
    assert sensitive not in repr(caught.value)
    assert "never-echo" not in repr(contract)


def test_pydantic_contract_rejects_non_model_types() -> None:
    with pytest.raises(TypeError, match="BaseModel"):
        PydanticOutputContract(dict)  # type: ignore[arg-type]
