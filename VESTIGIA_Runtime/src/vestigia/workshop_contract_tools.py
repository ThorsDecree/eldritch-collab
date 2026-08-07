from __future__ import annotations

from typing import Any

from .workshop_script_contracts import (
    CONTRACT_SUBSET_VERSION,
    supported_json_types,
    supported_schema_keywords,
    validate_schema,
    validate_value,
)


DIAGNOSTIC_SCHEMA_VERSION = "vestigia.contract-diagnostic.v0.1"
_EXAMPLE_TEXT_LIMIT = 256
_EXAMPLE_ITEM_LIMIT = 16


def _bounded_text(value: Any, limit: int = 500) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _schema_types(schema: dict[str, Any]) -> list[str]:
    raw = schema.get("type")
    return [raw] if isinstance(raw, str) else list(raw or [])


def _unsupported(schema: Any, *, path: str = "$", depth: int = 0) -> list[dict[str, str]]:
    if depth > 6 or not isinstance(schema, dict):
        return []
    allowed = set(supported_schema_keywords())
    items: list[dict[str, str]] = []
    for key in sorted(set(schema) - allowed):
        items.append({"path": path, "keyword": str(key)[:120]})
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, child in list(properties.items())[:128]:
            items.extend(_unsupported(child, path=f"{path}.properties.{name}", depth=depth + 1))
    if isinstance(schema.get("items"), dict):
        items.extend(_unsupported(schema["items"], path=f"{path}.items", depth=depth + 1))
    return items[:64]


def _summary(schema: dict[str, Any], *, depth: int = 0) -> dict[str, Any]:
    types = _schema_types(schema)
    summary: dict[str, Any] = {"type": types[0] if len(types) == 1 else types}
    constraints = {
        key: schema[key]
        for key in ("minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems")
        if key in schema
    }
    if constraints:
        summary["constraints"] = constraints
    if "const" in schema:
        summary["const_present"] = True
    if "enum" in schema:
        summary["enum_count"] = len(schema.get("enum") or [])
    if "object" in types:
        properties = schema.get("properties") or {}
        summary.update(
            {
                "property_count": len(properties),
                "property_names": list(properties)[:20],
                "required": list(schema.get("required") or [])[:20],
                "additional_properties": bool(schema.get("additionalProperties", True)),
            }
        )
        if depth < 2:
            summary["properties"] = {
                name: _summary(child, depth=depth + 1)
                for name, child in list(properties.items())[:12]
                if isinstance(child, dict)
            }
            summary["properties_truncated"] = len(properties) > 12
    if "array" in types and isinstance(schema.get("items"), dict) and depth < 2:
        summary["items"] = _summary(schema["items"], depth=depth + 1)
    return summary


def _valid_example(schema: dict[str, Any], *, depth: int = 0) -> Any:
    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    types = _schema_types(schema)
    chosen = types[0]
    if chosen == "object":
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        result: dict[str, Any] = {}
        for name, child in properties.items():
            if name in required and isinstance(child, dict):
                result[name] = _valid_example(child, depth=depth + 1)
        return result
    if chosen == "array":
        count = int(schema.get("minItems") or 0)
        if count > _EXAMPLE_ITEM_LIMIT:
            return None
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return [_valid_example(item_schema, depth=depth + 1) for _ in range(count)]
        return []
    if chosen == "string":
        minimum = int(schema.get("minLength") or 0)
        maximum = int(schema.get("maxLength") or max(1, minimum))
        if minimum > _EXAMPLE_TEXT_LIMIT or maximum < minimum:
            return None
        length = min(max(1, minimum), maximum, _EXAMPLE_TEXT_LIMIT)
        return "x" * length
    if chosen == "integer":
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None:
            return int(minimum)
        if maximum is not None and maximum < 0:
            return int(maximum)
        return 0
    if chosen == "number":
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None:
            return float(minimum)
        if maximum is not None and maximum < 0:
            return float(maximum)
        return 0.0
    if chosen == "boolean":
        return False
    if chosen == "null":
        return None
    return None


def _invalid_example(schema: dict[str, Any], valid: Any) -> tuple[Any | None, str | None]:
    types = _schema_types(schema)
    if "object" in types and isinstance(valid, dict):
        required = list(schema.get("required") or [])
        if required:
            broken = dict(valid)
            broken.pop(required[0], None)
            return broken, f"omit required property {required[0]}"
        if schema.get("additionalProperties", True) is False:
            broken = dict(valid)
            broken["__unexpected__"] = True
            return broken, "add an undeclared property"
    if "string" in types:
        if "maxLength" in schema:
            maximum = int(schema["maxLength"])
            if maximum < _EXAMPLE_TEXT_LIMIT:
                return "x" * (maximum + 1), "exceed maxLength"
            return None, None
        if int(schema.get("minLength") or 0) > 0:
            return "", "violate minLength"
    if "array" in types and "maxItems" in schema:
        maximum = int(schema["maxItems"])
        if maximum < _EXAMPLE_ITEM_LIMIT:
            item_schema = (
                schema.get("items")
                if isinstance(schema.get("items"), dict)
                else {"type": "null"}
            )
            return [
                _valid_example(item_schema) for _ in range(maximum + 1)
            ], "exceed maxItems"
        return None, None
    if "integer" in types or "number" in types:
        if "minimum" in schema:
            return schema["minimum"] - 1, "fall below minimum"
        if "maximum" in schema:
            return schema["maximum"] + 1, "exceed maximum"
    enum = schema.get("enum")
    if isinstance(enum, list):
        candidate = "__not_in_enum__"
        if candidate not in enum:
            return candidate, "use a value outside enum"
    wrong = [] if "array" not in types else {}
    return wrong, "use a value of the wrong JSON type"


def diagnose_contract(schema: Any, *, label: str = "schema") -> dict[str, Any]:
    unsupported = _unsupported(schema)
    try:
        validated = validate_schema(schema, label=label)
    except ValueError as exc:
        return {
            "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
            "contract_subset": CONTRACT_SUBSET_VERSION,
            "valid": False,
            "error": _bounded_text(exc),
            "unsupported_keywords": unsupported,
            "supported_keywords": list(supported_schema_keywords()),
            "supported_types": list(supported_json_types()),
            "summary": None,
            "example_valid": None,
            "example_invalid": None,
            "examples_are_proofs": False,
            "example_generation_bounded": True,
        }
    valid = _valid_example(validated)
    invalid, invalid_reason = _invalid_example(validated, valid)
    try:
        validate_value(valid, validated, path="$.example_valid")
        valid_ok = True
    except ValueError:
        valid_ok = False
    invalid_fails = False
    if invalid is not None:
        try:
            validate_value(invalid, validated, path="$.example_invalid")
        except ValueError:
            invalid_fails = True
    return {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "contract_subset": CONTRACT_SUBSET_VERSION,
        "valid": True,
        "error": None,
        "unsupported_keywords": unsupported,
        "supported_keywords": list(supported_schema_keywords()),
        "supported_types": list(supported_json_types()),
        "summary": _summary(validated),
        "example_valid": valid if valid_ok else None,
        "example_invalid": (
            {"value": invalid, "intended_failure": invalid_reason} if invalid_fails else None
        ),
        "examples_are_proofs": False,
        "example_generation_bounded": True,
    }
