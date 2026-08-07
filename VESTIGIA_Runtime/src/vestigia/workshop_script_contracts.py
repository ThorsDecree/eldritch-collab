from __future__ import annotations

import math
from typing import Any


CONTRACT_SUBSET_VERSION = "vestigia.value-contract.v0.1"
_ALLOWED_SCHEMA_KEYS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "const",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "description",
}
_ALLOWED_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


def supported_schema_keywords() -> tuple[str, ...]:
    return tuple(sorted(_ALLOWED_SCHEMA_KEYS))


def supported_json_types() -> tuple[str, ...]:
    return tuple(sorted(_ALLOWED_TYPES))


def default_input_schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": True}


def default_output_schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": True}


def validate_schema(schema: Any, *, label: str = "schema", depth: int = 0) -> dict[str, Any]:
    if depth > 6:
        raise ValueError(f"{label} exceeded the supported nesting depth")
    if not isinstance(schema, dict):
        raise ValueError(f"{label} must be an object")
    unsupported = sorted(set(schema) - _ALLOWED_SCHEMA_KEYS)
    if unsupported:
        raise ValueError(f"{label} uses unsupported contract fields: {', '.join(unsupported)}")
    schema_type = schema.get("type")
    if schema_type is None:
        raise ValueError(f"{label}.type is required in the v0.1 contract subset")
    types = [schema_type] if isinstance(schema_type, str) else schema_type
    if not isinstance(types, list) or not types or any(item not in _ALLOWED_TYPES for item in types):
        raise ValueError(f"{label}.type contains an unsupported JSON type")
    if "object" in types:
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError(f"{label}.properties must be an object")
        if len(properties) > 128:
            raise ValueError(f"{label}.properties exceeded 128 entries")
        for name, child in properties.items():
            if not isinstance(name, str) or not name or len(name) > 120:
                raise ValueError(f"{label} has an invalid property name")
            validate_schema(child, label=f"{label}.properties.{name}", depth=depth + 1)
        required = schema.get("required", [])
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            raise ValueError(f"{label}.required must be an array of strings")
        if len(set(required)) != len(required):
            raise ValueError(f"{label}.required contains duplicates")
        unknown_required = sorted(set(required) - set(properties))
        if unknown_required:
            raise ValueError(f"{label}.required names unknown properties: {', '.join(unknown_required)}")
        additional = schema.get("additionalProperties", True)
        if not isinstance(additional, bool):
            raise ValueError(f"{label}.additionalProperties must be boolean in v0.1")
    if "array" in types and "items" in schema:
        validate_schema(schema["items"], label=f"{label}.items", depth=depth + 1)
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (not isinstance(schema[key], int) or isinstance(schema[key], bool) or schema[key] < 0):
            raise ValueError(f"{label}.{key} must be a non-negative integer")
    for key in ("minimum", "maximum"):
        if key in schema and (not isinstance(schema[key], (int, float)) or isinstance(schema[key], bool)):
            raise ValueError(f"{label}.{key} must be numeric")
        if key in schema and isinstance(schema[key], float) and not math.isfinite(schema[key]):
            raise ValueError(f"{label}.{key} must be a finite JSON number")
    if "enum" in schema and (not isinstance(schema["enum"], list) or len(schema["enum"]) > 128):
        raise ValueError(f"{label}.enum must be an array of at most 128 values")
    return schema


def _matches_type(value: Any, name: str) -> bool:
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    return False


def validate_value(value: Any, schema: dict[str, Any], *, path: str = "$", depth: int = 0) -> None:
    validate_schema(schema, label="value schema")
    if depth > 6:
        raise ValueError(f"{path} exceeded the supported nesting depth")
    schema_type = schema["type"]
    types = [schema_type] if isinstance(schema_type, str) else list(schema_type)
    if not any(_matches_type(value, name) for name in types):
        raise ValueError(f"{path} did not match declared type {schema_type!r}")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path} did not match the declared constant")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} was not one of the declared enum values")
    if isinstance(value, dict) and "object" in types:
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                raise ValueError(f"{path}.{required} is required")
        if schema.get("additionalProperties", True) is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise ValueError(f"{path} contains undeclared properties: {', '.join(extras)}")
        for key, child in properties.items():
            if key in value:
                validate_value(value[key], child, path=f"{path}.{key}", depth=depth + 1)
    if isinstance(value, list) and "array" in types:
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ValueError(f"{path} has fewer than minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError(f"{path} exceeds maxItems")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                validate_value(item, item_schema, path=f"{path}[{index}]", depth=depth + 1)
    if isinstance(value, str) and "string" in types:
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ValueError(f"{path} is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"{path} exceeds maxLength")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and ({"integer", "number"} & set(types)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{path} must be a finite JSON number")
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path} is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path} exceeds maximum")
