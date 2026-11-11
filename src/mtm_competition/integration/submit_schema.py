"""Check answers/<key>/submit.json against the item's published submit schema before SUBMIT.

39PSWT5C: the grader validates submit.json against challenge/<key>-submit.schema.json first and a schema
failure scores 0 for the whole item (CR-0x submitted "auditSummary": {} where the schema required fields).
Nothing checked the schema locally. This module

* finds the schema (``challenge/<key>-submit.schema.json`` or a ``schemaFile`` named by the item),
* lists precise errors ({path, keyword, message}),
* and, only when asked (near the deadline / at a flush), fills the minimum needed to make the answer valid:
  missing required fields, wrong types, empty arrays/strings, enums. Values that are already valid are kept.

Usage (workers, before finishing):  python -m mtm_competition.integration.submit_schema <match_root> <KEY>
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PLACEHOLDER = "not determined before the deadline"


def find_schema(match_root: str | Path, key: str, hints: Optional[Dict[str, Any]] = None) -> Tuple[Optional[dict], Optional[Path]]:
    root = Path(match_root)
    names: List[str] = []
    for source in (hints or {},):
        for field in ("schemaFile", "submitSchemaFile", "answerSchemaFile"):
            value = source.get(field)
            if isinstance(value, str) and value:
                names.append(value if "/" in value else f"challenge/{value}")
        for field in ("submitSchema", "answerSchema"):
            if isinstance(source.get(field), dict):
                return source[field], None
    names.append(f"challenge/{key}-submit.schema.json")
    challenge = root / "challenge"
    if challenge.is_dir():
        names.extend(f"challenge/{p.name}" for p in sorted(challenge.glob(f"{key}*schema*.json")))
    for name in dict.fromkeys(names):
        path = root / name
        try:
            if path.is_file() and path.resolve().is_relative_to(root.resolve()):
                schema = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(schema, dict):
                    return schema, path
        except (OSError, ValueError):
            continue
    return None, None


def _pointer(path) -> str:
    return "/" + "/".join(str(p) for p in path) if path else "/"


def schema_errors(answer: Any, schema: dict, limit: int = 30) -> List[Dict[str, str]]:
    try:
        from jsonschema import validators
    except ImportError:                                   # pragma: no cover - jsonschema ships with the pack
        return _fallback_errors(answer, schema, schema, [], limit)
    cls = validators.validator_for(schema)
    try:
        cls.check_schema(schema)
    except Exception:
        return []                                         # an unusable schema is not the worker's fault
    out = []
    for err in sorted(cls(schema).iter_errors(answer), key=lambda e: list(map(str, e.absolute_path))):
        out.append({"path": _pointer(err.absolute_path), "keyword": str(err.validator),
                    "message": err.message[:300]})
        if len(out) >= limit:
            break
    return out


def _resolve(node: Any, root: dict) -> dict:
    seen = 0
    while isinstance(node, dict) and isinstance(node.get("$ref"), str) and node["$ref"].startswith("#") and seen < 20:
        target: Any = root
        for part in node["$ref"][1:].split("/"):
            if part:
                part = part.replace("~1", "/").replace("~0", "~")
                target = target.get(part, {}) if isinstance(target, dict) else {}
        node = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
        seen += 1
    return node if isinstance(node, dict) else {}


def _first_branch(node: dict, root: dict) -> dict:
    for comb in ("allOf",):
        if isinstance(node.get(comb), list):
            merged = dict(node)
            for sub in node[comb]:
                sub = _resolve(sub, root)
                merged.setdefault("properties", {}).update(sub.get("properties", {}))
                merged["required"] = list(dict.fromkeys([*merged.get("required", []), *sub.get("required", [])]))
                for k, v in sub.items():
                    if k not in ("properties", "required"):
                        merged.setdefault(k, v)
            return merged
    for comb in ("oneOf", "anyOf"):
        if isinstance(node.get(comb), list) and node[comb]:
            branches = [_resolve(b, root) for b in node[comb]]
            non_null = [b for b in branches if b.get("type") != "null"]
            return {**{k: v for k, v in node.items() if k != comb}, **(non_null or branches)[0]}
    return node


def default_for(node: Any, root: dict, depth: int = 0) -> Any:
    node = _first_branch(_resolve(node, root), root)
    if depth > 12:
        return None
    if "const" in node:
        return node["const"]
    if "default" in node:
        return copy.deepcopy(node["default"])
    if isinstance(node.get("enum"), list) and node["enum"]:
        return "PARTIAL" if "PARTIAL" in node["enum"] else node["enum"][0]
    kind = node.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), kind[0] if kind else None)
    if kind is None:
        kind = "object" if "properties" in node or "required" in node else ("array" if "items" in node else "string")
    if kind == "object":
        props = node.get("properties", {}) if isinstance(node.get("properties"), dict) else {}
        return {name: default_for(props.get(name, {}), root, depth + 1) for name in node.get("required", [])}
    if kind == "array":
        item = node.get("items") if isinstance(node.get("items"), dict) else {}
        return [default_for(item, root, depth + 1) for _ in range(int(node.get("minItems", 0) or 0))]
    if kind in ("integer", "number"):
        low = node.get("minimum", node.get("exclusiveMinimum"))
        value = 0 if low is None else (low + (1 if "exclusiveMinimum" in node and "minimum" not in node else 0))
        high = node.get("maximum")
        if high is not None and value > high:
            value = high
        return int(value) if kind == "integer" else value
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    text = PLACEHOLDER
    if node.get("format") == "date-time":
        text = "1970-01-01T00:00:00Z"
    elif node.get("format") == "date":
        text = "1970-01-01"
    min_len, max_len = int(node.get("minLength", 0) or 0), node.get("maxLength")
    if len(text) < min_len:
        text = text + "." * (min_len - len(text))
    if max_len is not None and len(text) > int(max_len):
        text = text[: int(max_len)] if int(max_len) > 0 else ""
    return text


def _schema_at(schema: dict, path: List[Any], root: dict) -> dict:
    node = _first_branch(_resolve(schema, root), root)
    for part in path:
        if isinstance(part, int):
            items = node.get("items")
            node = _first_branch(_resolve(items if isinstance(items, dict) else {}, root), root)
        else:
            props = node.get("properties", {})
            if isinstance(props, dict) and part in props:
                node = _first_branch(_resolve(props[part], root), root)
            elif isinstance(node.get("additionalProperties"), dict):
                node = _first_branch(_resolve(node["additionalProperties"], root), root)
            else:
                node = {}
    return node


def _get(doc: Any, path: List[Any]) -> Any:
    for part in path:
        doc = doc[part]
    return doc


def _set(doc: Any, path: List[Any], value: Any) -> Any:
    if not path:
        return value
    _get(doc, path[:-1])[path[-1]] = value
    return doc


def _coerce(value: Any, node: dict, root: dict) -> Any:
    kind = node.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    if isinstance(value, str):
        stripped = value.strip().replace(",", "")
        try:
            if "integer" in kinds and stripped.lstrip("-").isdigit():
                return int(stripped)
            if "number" in kinds:
                return float(stripped)
        except ValueError:
            pass
        if "boolean" in kinds and stripped.lower() in ("true", "false"):
            return stripped.lower() == "true"
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "string" in kinds:
        return str(value)
    if isinstance(value, float) and value.is_integer() and "integer" in kinds:
        return int(value)
    if not isinstance(value, list) and "array" in kinds and value not in (None, "", {}):
        return [value]
    return default_for(node, root)


def autofill(answer: Any, schema: dict, rounds: int = 8) -> Tuple[Any, List[Dict[str, str]]]:
    """Return (fixed answer, changes). Only locations reported invalid are touched."""
    try:
        from jsonschema import validators
    except ImportError:                                   # pragma: no cover
        return answer, []
    cls = validators.validator_for(schema)
    try:
        cls.check_schema(schema)
    except Exception:
        return answer, []
    doc = copy.deepcopy(answer) if isinstance(answer, dict) else default_for(schema, schema)
    changes: List[Dict[str, str]] = []
    for _ in range(rounds):
        errors = list(cls(schema).iter_errors(doc))
        if not errors:
            break
        progressed = False
        # deepest first, so a parent replacement does not invalidate a child path
        for err in sorted(errors, key=lambda e: -len(e.absolute_path)):
            path = list(err.absolute_path)
            try:
                current = _get(doc, path)
            except (KeyError, IndexError, TypeError):
                continue
            node = _first_branch(_resolve(err.schema if isinstance(err.schema, dict) else {}, schema), schema)
            kw = err.validator
            new: Any = current
            if kw == "required" and isinstance(current, dict):
                props = node.get("properties", {}) if isinstance(node.get("properties"), dict) else {}
                for name in node.get("required", []):
                    if name not in current:
                        current[name] = default_for(props.get(name, {}), schema)
                        changes.append({"path": _pointer(path + [name]), "change": "added required field"})
                        progressed = True
                continue
            if kw == "additionalProperties" and isinstance(current, dict) and node.get("additionalProperties") is False:
                allowed = set(node.get("properties", {}))
                for name in [n for n in current if n not in allowed]:
                    if not any(__import__("re").search(p, name) for p in node.get("patternProperties", {})):
                        current.pop(name)
                        changes.append({"path": _pointer(path + [name]), "change": "removed field not allowed"})
                        progressed = True
                continue
            if kw == "minItems" and isinstance(current, list):
                item = node.get("items") if isinstance(node.get("items"), dict) else {}
                while len(current) < int(node.get("minItems", 0)):
                    current.append(default_for(item, schema))
                changes.append({"path": _pointer(path), "change": "padded to minItems"})
                progressed = True
                continue
            if kw == "maxItems" and isinstance(current, list):
                del current[int(node.get("maxItems", len(current))):]
                progressed = True
                continue
            if kw == "type":
                new = _coerce(current, node, schema)
            elif kw in ("enum", "const", "minLength", "maxLength", "format", "pattern", "oneOf", "anyOf"):
                new = default_for(node, schema)
                if kw == "minLength" and isinstance(current, str) and current.strip():
                    new = current + "." * (int(node.get("minLength", 0)) - len(current))
            elif kw in ("minimum", "exclusiveMinimum", "maximum", "exclusiveMaximum") and isinstance(current, (int, float)):
                new = default_for(node, schema)
            elif kw == "minProperties" and isinstance(current, dict):
                props = node.get("properties", {}) if isinstance(node.get("properties"), dict) else {}
                for name, sub in props.items():
                    if len(current) >= int(node.get("minProperties", 0)):
                        break
                    current.setdefault(name, default_for(sub, schema))
                progressed = True
                continue
            else:
                continue
            if new != current:
                doc = _set(doc, path, new)
                changes.append({"path": _pointer(path), "change": f"{kw}: replaced with a minimal valid value"})
                progressed = True
        if not progressed:
            break
    return doc, changes


def _fallback_errors(value: Any, node: Any, root: dict, path: List[Any], limit: int) -> List[Dict[str, str]]:
    node = _first_branch(_resolve(node, root), root)
    out: List[Dict[str, str]] = []
    kind = node.get("type")
    kinds = kind if isinstance(kind, list) else ([kind] if kind else [])
    py = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool}
    if kinds and not any(k == "null" and value is None or k in py and isinstance(value, py[k]) for k in kinds):
        return [{"path": _pointer(path), "keyword": "type", "message": f"expected {kind}"}]
    if isinstance(value, dict):
        for name in node.get("required", []):
            if name not in value:
                out.append({"path": _pointer(path), "keyword": "required", "message": f"'{name}' is a required property"})
        for name, sub in (node.get("properties") or {}).items():
            if name in value:
                out.extend(_fallback_errors(value[name], sub, root, path + [name], limit))
    if isinstance(value, list):
        if len(value) < int(node.get("minItems", 0) or 0):
            out.append({"path": _pointer(path), "keyword": "minItems", "message": "too few items"})
        if isinstance(node.get("items"), dict):
            for i, v in enumerate(value):
                out.extend(_fallback_errors(v, node["items"], root, path + [i], limit))
    if isinstance(value, str) and len(value) < int(node.get("minLength", 0) or 0):
        out.append({"path": _pointer(path), "keyword": "minLength", "message": "too short"})
    if "enum" in node and value not in node["enum"]:
        out.append({"path": _pointer(path), "keyword": "enum", "message": f"must be one of {node['enum']}"})
    return out[:limit]


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m mtm_competition.integration.submit_schema <match_root> <KEY>")
        return 2
    root, key = Path(argv[0]), argv[1]
    schema, where = find_schema(root, key)
    answer_path = root / "answers" / key / "submit.json"
    try:
        answer = json.loads(answer_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "errors": [{"path": "/", "keyword": "json", "message": str(exc)}]}))
        return 1
    if schema is None:
        print(json.dumps({"valid": None, "note": f"no schema found for {key}"}))
        return 0
    errors = schema_errors(answer, schema)
    print(json.dumps({"valid": not errors, "schema": str(where), "errors": errors}, indent=1))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
