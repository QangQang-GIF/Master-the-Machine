"""V2 model/effort routing inside the official allowlist (Plan V2 section 7).

Pure and side-effect free: it *requests* a model; whether the native runtime honored it is only
known if the runtime reports it. Telemetry therefore carries requested vs actual, and `actual` stays
None (never guessed) until a real report is recorded.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

EFFORT_ORDER = ["low", "medium", "high"]
TIER_TO_PROFILE = {"fast": "fast", "standard": "standard", "strong": "deep_reasoning"}
_FALLBACK_ORDER = {
    "deep_reasoning": ["standard", "fast"],
    "standard": ["fast", "deep_reasoning"],
    "fast": ["standard", "deep_reasoning"],
}


class ModelPolicyError(Exception):
    pass


def load_policy(path: Path | str) -> Dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "profiles" not in data or "allowlist" not in data:
        raise ModelPolicyError("model_profiles must define profiles and allowlist")
    return data


def _cap_effort(effort: str, max_effort: Optional[str]) -> str:
    if max_effort and max_effort in EFFORT_ORDER and effort in EFFORT_ORDER:
        return EFFORT_ORDER[min(EFFORT_ORDER.index(effort), EFFORT_ORDER.index(max_effort))]
    return effort


def select_model(
    policy: Dict[str, Any],
    tier: str,
    backend: str = "codex",
    unavailable_models: Iterable[str] = (),
    max_effort: Optional[str] = None,
    seconds_left: Optional[float] = None,
    estimated_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Pick a profile for a triage tier.

    Rules: never leave the allowlist; fall back to another allowed profile if the requested model is
    unavailable; never raise effort above `max_effort`; when time is tight relative to the estimate,
    lower (never raise) effort one step. Every deviation is recorded in `reasons`.
    """
    if backend not in ("codex", "agy"):
        raise ModelPolicyError(f"unknown backend {backend}")
    profiles = policy["profiles"]
    allowed_models = set(policy["allowlist"].get("models", []))
    allowed_efforts = set(policy["allowlist"].get("effort_levels", EFFORT_ORDER))
    unavailable = set(unavailable_models)
    reasons: List[str] = []

    wanted = TIER_TO_PROFILE.get(tier, policy.get("default_profile", "standard"))
    if wanted not in profiles:
        wanted = policy.get("default_profile", "standard")
        reasons.append("requested tier unknown; using default profile")

    def resolve(name: str):
        p = profiles.get(name) or {}
        return p.get(f"{backend}_model"), p.get(f"{backend}_effort")

    chosen, fallback = wanted, False
    model, effort = resolve(wanted)
    candidates = [wanted] + [c for c in _FALLBACK_ORDER.get(wanted, []) if c in profiles]
    for name in candidates:
        m, e = resolve(name)
        if m in allowed_models and m not in unavailable and e in allowed_efforts:
            chosen, model, effort = name, m, e
            fallback = name != wanted
            break
    else:
        raise ModelPolicyError("no allowed, available model profile for this request")
    if fallback:
        reasons.append(f"profile {wanted} unavailable -> fallback {chosen}")

    capped = _cap_effort(effort, max_effort)
    if capped != effort:
        reasons.append(f"effort capped {effort}->{capped} by budget")
        effort = capped
    if seconds_left is not None and estimated_seconds and seconds_left < 2 * estimated_seconds and effort != "low":
        lowered = EFFORT_ORDER[max(0, EFFORT_ORDER.index(effort) - 1)]
        reasons.append(f"tight deadline: effort {effort}->{lowered}")
        effort = lowered

    return {
        "requestedProfile": wanted, "profile": chosen, "requestedModel": resolve(wanted)[0],
        "model": model, "effort": effort, "fallbackUsed": fallback, "reasons": reasons,
        "backend": backend, "actualModel": None, "actualSource": "unreported",
    }


def record_actual(selection: Dict[str, Any], reported_model: Optional[str]) -> Dict[str, Any]:
    """Attach what the runtime reported; None means the runtime did not report (nothing is invented)."""
    out = dict(selection)
    if reported_model:
        out["actualModel"] = reported_model
        out["actualSource"] = "runtime"
        out["honored"] = reported_model == selection["model"]
    return out
