"""Offline conservative token upper-bound estimation."""

from __future__ import annotations

import json

from zhiyi.application.models.contracts import (
    DocumentPart,
    ImagePart,
    InputModality,
    ModelRequest,
    ModelTarget,
    TextPart,
    thaw_json,
)
from zhiyi.application.ports.token_estimator import TokenEstimate

_MESSAGE_PROTOCOL_BYTES = 32
_CONTENT_PROTOCOL_BYTES = 16
_TOOL_PROTOCOL_BYTES = 64
_STRUCTURED_PROTOCOL_BYTES = 64


def _json_bytes(value: object) -> int:
    return len(
        json.dumps(
            thaw_json(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )


class ConservativeTokenEstimator:
    """Use UTF-8 bytes as a tokenizer-independent, non-underestimating bound."""

    def estimate(self, target: ModelTarget, request: ModelRequest) -> TokenEstimate:
        total = 0
        for message in request.messages:
            total += _MESSAGE_PROTOCOL_BYTES
            total += len(message.role.value.encode())
            total += len((message.name or "").encode())
            total += len((message.tool_call_id or "").encode())
            for part in message.content:
                total += _CONTENT_PROTOCOL_BYTES
                if isinstance(part, TextPart):
                    total += len(part.text.encode())
                elif isinstance(part, ImagePart):
                    total += target.capabilities.modality_token_upper_bounds[InputModality.IMAGE]
                elif isinstance(part, DocumentPart):
                    total += target.capabilities.modality_token_upper_bounds[InputModality.DOCUMENT]

        for tool in request.tools:
            total += _TOOL_PROTOCOL_BYTES
            total += len(tool.name.encode()) + len(tool.description.encode())
            total += _json_bytes(tool.input_schema)

        if request.structured_output is not None:
            total += _STRUCTURED_PROTOCOL_BYTES
            total += len(request.structured_output.name.encode())
            total += _json_bytes(request.structured_output.json_schema)

        return TokenEstimate(input_upper_bound=total, method="utf8-bytes-v1")
