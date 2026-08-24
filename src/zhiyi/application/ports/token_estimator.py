"""Conservative token estimation port used before provider calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from zhiyi.application.models.contracts import ModelRequest, ModelTarget


@dataclass(frozen=True, slots=True)
class TokenEstimate:
    input_upper_bound: int
    method: str

    def __post_init__(self) -> None:
        if self.input_upper_bound < 0:
            raise ValueError("token estimate must be non-negative")
        if not self.method:
            raise ValueError("token estimate method must not be empty")


class TokenEstimator(Protocol):
    def estimate(self, target: ModelTarget, request: ModelRequest) -> TokenEstimate: ...
