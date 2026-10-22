"""Token usage metrics and billable token accounting."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0
    billable_tokens: int = 0

    @classmethod
    def calculate(
        cls,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        reasoning_output_tokens: int = 0,
    ) -> TokenUsage:
        """Calculate billable tokens: (input - cached_input) + output."""
        uncached_input = max(0, input_tokens - cached_input_tokens)
        billable = uncached_input + output_tokens
        total = input_tokens + output_tokens
        return cls(
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_output_tokens=reasoning_output_tokens,
            total_tokens=total,
            billable_tokens=billable,
        )

    def to_dict(self) -> Dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_output_tokens": self.reasoning_output_tokens,
            "total_tokens": self.total_tokens,
            "billable_tokens": self.billable_tokens,
        }
