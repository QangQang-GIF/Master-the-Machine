"""Semantic mojibake detection for JSON answers.

Raw-byte searches are not enough: ``json.dumps`` defaults to ``ensure_ascii=True`` and turns a wrongly
decoded string into ``\\uXXXX`` escapes, so a literal search for the garbled text finds nothing. The check
therefore works on the *decoded* object: every string is tested for the classic "UTF-8 bytes decoded as a
legacy single-byte code page" signature and can be compared with a trusted reference (CSV, fixture).
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Iterator

_LEGACY_CODECS = ("cp1252", "latin-1")


class MojibakeError(ValueError):
    """Raised when an answer contains text that looks like mis-decoded UTF-8."""


def repair_mojibake(text: str) -> str | None:
    """Return the repaired string if ``text`` looks like UTF-8 decoded as cp1252/latin-1, else ``None``."""
    if not isinstance(text, str) or text.isascii():
        return None
    for codec in _LEGACY_CODECS:
        try:
            repaired = text.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if repaired != text and not repaired.isascii():
            return repaired
    return None


def _walk(obj: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str):
                yield f"{path}.<key:{key}>", key
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(obj, (list, tuple)):
        for index, value in enumerate(obj):
            yield from _walk(value, f"{path}[{index}]")


def find_mojibake(obj: Any) -> list[dict[str, str]]:
    """List every string in ``obj`` that looks mis-decoded, with its JSON path and suggested repair."""
    findings = []
    for path, text in _walk(obj):
        repaired = repair_mojibake(text)
        if repaired is not None:
            findings.append({"path": path, "found": text, "repaired": repaired})
    return findings


def find_mojibake_in_json_bytes(raw: bytes) -> list[dict[str, str]]:
    """Decode JSON bytes (UTF-8, optional BOM) into an object first, then inspect the decoded strings."""
    return find_mojibake(json.loads(raw.decode("utf-8-sig")))


def assert_no_mojibake(obj: Any, label: str = "answer") -> None:
    findings = find_mojibake(obj)
    if findings:
        sample = "; ".join(f"{f['path']}: {f['found']!r} -> {f['repaired']!r}" for f in findings[:3])
        raise MojibakeError(f"{label} contains {len(findings)} mis-decoded string(s) (UTF-8 read as a legacy code page): {sample}")


def missing_reference_strings(obj: Any, reference: Iterable[str]) -> list[str]:
    """Reference strings (for example category names from the source CSV) that do not occur in ``obj``."""
    present = {text for _, text in _walk(obj)}
    return [ref for ref in reference if ref not in present]
