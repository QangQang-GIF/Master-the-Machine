"""Token usage metrics, billable token accounting, and normalized cross-backend credits."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


def calculate_normalized_credits(
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    reasoning_output_tokens: int = 0,
    model: str = "",
    backend: str = "",
) -> float:
    """Compute normalized model credits across Codex and Antigravity backends.

    Formula (per 1K tokens, before model tier multiplier):
    - Uncached input: 1.0 credit / 1K tokens
    - Cached input:   0.1 credit / 1K tokens
    - Output + reasoning: 3.0 credits / 1K tokens
    """
    uncached_input = max(0, int(input_tokens) - max(0, int(cached_input_tokens)))
    cached = max(0, min(int(input_tokens), int(cached_input_tokens)))
    out_total = max(0, int(output_tokens)) + max(0, int(reasoning_output_tokens))
    base_credits = (uncached_input * 1.0 + cached * 0.1 + out_total * 3.0) / 1000.0

    m = (model or "").lower()
    if any(k in m for k in ("flash-low", "luna", "lite", "mini")):
        tier_mult = 0.5
    elif any(k in m for k in ("opus", "sonnet", "argon-high")):
        tier_mult = 2.0
    else:
        tier_mult = 1.0
    return round(base_credits * tier_mult, 4)


@dataclass
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0
    billable_tokens: int = 0
    normalized_credits: float = 0.0

    @classmethod
    def calculate(
        cls,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        reasoning_output_tokens: int = 0,
        model: str = "",
        backend: str = "",
    ) -> TokenUsage:
        """Calculate billable tokens: (input - cached_input) + output, plus normalized credits."""
        uncached_input = max(0, input_tokens - cached_input_tokens)
        billable = uncached_input + output_tokens
        total = input_tokens + output_tokens
        credits = calculate_normalized_credits(
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_output_tokens=reasoning_output_tokens,
            model=model,
            backend=backend,
        )
        return cls(
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_output_tokens=reasoning_output_tokens,
            total_tokens=total,
            billable_tokens=billable,
            normalized_credits=credits,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_output_tokens": self.reasoning_output_tokens,
            "total_tokens": self.total_tokens,
            "billable_tokens": self.billable_tokens,
            "normalized_credits": self.normalized_credits,
        }
