"""Pydantic-backed structured output contract."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import BaseModel


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


class PydanticOutputContract:
    """Export a schema and validate final values without retaining raw input."""

    __slots__ = ("_model_type", "_schema")

    def __init__(self, model_type: type[BaseModel]) -> None:
        if not isinstance(model_type, type) or not issubclass(model_type, BaseModel):
            raise TypeError("model_type must be a Pydantic BaseModel type")
        self._model_type = model_type
        schema = _freeze(model_type.model_json_schema(mode="validation"))
        if not isinstance(schema, Mapping):
            raise TypeError("Pydantic JSON schema root must be an object")
        self._schema = schema

    @property
    def name(self) -> str:
        return self._model_type.__name__

    @property
    def json_schema(self) -> Mapping[str, object]:
        return self._schema

    def validate(self, value: object) -> object:
        try:
            model = self._model_type.model_validate(value)
            return model.model_dump(mode="json")
        except Exception:
            raise ValueError(f"structured output validation failed for {self.name}") from None

    def __repr__(self) -> str:
        return f"PydanticOutputContract(model={self.name!r})"
