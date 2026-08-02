from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any, Callable

from .config import ResolvedConfig


CapabilityHandler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class CapabilitySpec:
    """Executable resident capability metadata.

    The registry is the source of truth for both dispatch and the resident-facing
    capability panel. Documentation may explain a capability, but cannot enable one.
    """

    name: str
    description: str
    effects: tuple[str, ...] = ()
    cost_class: str = "free"
    confirmation: str = "none"
    default_after: str = "continue"
    result_visibility: str = "resident_private"
    audit: str = "full_receipt"
    schema_version: str = "v1"
    outward_facing: bool = False
    config_key: str | None = None
    forgeable: bool = False
    input_schema: dict[str, Any] = field(default_factory=dict)
    example_envelopes: tuple[dict[str, Any], ...] = ()
    group: str = "other"
    invocation_envelope: str = "TOOL_ACTION"
    related_actions: tuple[str, ...] = ()
    next_step: str = ""
    dispatchable_via_tool_action: bool = True

    def enabled(self, config: ResolvedConfig) -> bool:
        return True if not self.config_key else bool(config.get(self.config_key, True))

    def public(
        self,
        config: ResolvedConfig,
        *,
        handler_present: bool = True,
    ) -> dict[str, Any]:
        value = asdict(self)
        enabled = self.enabled(config)
        schema_complete = is_formal_object_schema(self.input_schema)
        value["registered"] = True
        value["enabled"] = enabled
        value["schema_complete"] = schema_complete
        value["callable_now"] = bool(
            enabled and schema_complete and (
                handler_present or not self.dispatchable_via_tool_action
            )
        )
        value["effects"] = list(self.effects)
        value["related_actions"] = list(self.related_actions)
        value["copyable_examples"] = [
            wrap_example(self.invocation_envelope, item)
            for item in self.example_envelopes
        ]
        value.pop("config_key", None)
        return value


class CapabilityPolicyEngine:
    def __init__(self, config: ResolvedConfig) -> None:
        self.config = config
        self.recognized_policies = {
            "none",
            "configured_budget",
            "resident_only_if_private_or_legacy_two_breath",
            "later_resident_hash_bound_claim",
            "hash_bound_for_claim",
        }

    def authorize(
        self,
        spec: CapabilitySpec,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> None:
        if spec.confirmation not in self.recognized_policies:
            raise PermissionError(
                f"Capability {spec.name} specifies unrecognized confirmation policy: {spec.confirmation}"
            )
        if spec.outward_facing:
            interface = context.get("interface")
            if not interface or interface not in {"discord", "bell"}:
                raise PermissionError(
                    f"Outward facing capability {spec.name} must be executed from an authorized Discord/Bell interface, got: {interface or '(none)'}"
                )


class CapabilityRegistry:
    def __init__(self, config: ResolvedConfig) -> None:
        self.config = config
        self.policy = CapabilityPolicyEngine(config)
        self._specs: dict[str, CapabilitySpec] = {}
        self._handlers: dict[str, CapabilityHandler] = {}

    def register(self, spec: CapabilitySpec, handler: CapabilityHandler) -> None:
        name = spec.name.strip().lower()
        if not name or name != spec.name:
            raise ValueError("capability names must already be normalized")
        if name in self._specs:
            raise ValueError(f"duplicate capability: {name}")
        if spec.default_after not in {"continue", "finish"}:
            raise ValueError(f"invalid default continuation for {name}")
        if spec.confirmation not in self.policy.recognized_policies:
            raise ValueError(
                f"Capability {spec.name} specifies unrecognized confirmation policy: {spec.confirmation}"
            )
        if spec.outward_facing and spec.confirmation == "none":
            raise ValueError(
                f"Outward facing capability {spec.name} must declare a non-none confirmation policy."
            )
        self._specs[name] = spec
        self._handlers[name] = handler

    def register_contract(self, spec: CapabilitySpec) -> None:
        """Register a discoverable non-TOOL_ACTION envelope such as BELL_DRAFT."""

        name = spec.name.strip().lower()
        if not name or name != spec.name:
            raise ValueError("capability names must already be normalized")
        if name in self._specs:
            raise ValueError(f"duplicate capability: {name}")
        if spec.dispatchable_via_tool_action:
            raise ValueError("contract-only capabilities must name another invocation envelope")
        if spec.confirmation not in self.policy.recognized_policies:
            raise ValueError(
                f"Capability {spec.name} specifies unrecognized confirmation policy: {spec.confirmation}"
            )
        if spec.outward_facing and spec.confirmation == "none":
            raise ValueError(
                f"Outward facing capability {spec.name} must declare a non-none confirmation policy."
            )
        self._specs[name] = spec

    def spec(self, name: str) -> CapabilitySpec:
        normalized = name.strip().lower()
        if normalized not in self._specs:
            raise ValueError(f"unknown resident capability: {normalized or '(missing)'}")
        return self._specs[normalized]

    def dispatch(
        self,
        payload: dict[str, Any],
        *,
        turn_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], CapabilitySpec, str]:
        if not isinstance(payload, dict):
            raise ValueError("tool payload must be an object")
        spec = self.spec(str(payload.get("action", "")))
        if spec.name not in self._handlers:
            raise ValueError(
                f"{spec.name} uses {spec.invocation_envelope}, not TOOL_ACTION; "
                "request its focused capability contract for a copyable example"
            )
        if not spec.enabled(self.config):
            raise PermissionError(f"resident capability is disabled: {spec.name}")
        after = str(payload.get("after", spec.default_after)).strip().lower()
        if after not in {"continue", "finish"}:
            raise ValueError("after must be continue or finish")
        clean = dict(payload)
        clean["action"] = spec.name
        clean["after"] = after
        validate_instance(clean, spec.input_schema)
        execution_context = dict(context or {})
        execution_context["turn_id"] = turn_id
        self.policy.authorize(spec, clean, execution_context)
        result = self._handlers[spec.name](clean, execution_context)
        return result, spec, after

    def describe(self, target: str | None = None) -> list[dict[str, Any]]:
        if target:
            spec = self.spec(target)
            return [
                spec.public(
                    self.config,
                    handler_present=spec.name in self._handlers,
                )
            ]
        return [
            self._specs[name].public(
                self.config,
                handler_present=name in self._handlers,
            )
            for name in sorted(self._specs)
        ]

    def grouped_index(self) -> dict[str, list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for name in sorted(self._specs):
            spec = self._specs[name]
            public = spec.public(
                self.config,
                handler_present=name in self._handlers,
            )
            groups.setdefault(spec.group, []).append(
                {
                    key: public[key]
                    for key in (
                        "name",
                        "registered",
                        "enabled",
                        "schema_complete",
                        "callable_now",
                        "invocation_envelope",
                        "cost_class",
                        "confirmation",
                    )
                }
            )
        return groups

    def forgeable_names(self) -> set[str]:
        return {
            name
            for name, spec in self._specs.items()
            if spec.forgeable and spec.enabled(self.config)
        }


def object_schema(
    properties: dict[str, dict[str, Any]] | None = None,
    *,
    required: tuple[str, ...] = (),
    additional: bool = False,
    all_of: list[dict[str, Any]] | None = None,
    description: str = "",
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": additional,
    }
    if all_of:
        schema["allOf"] = all_of
    if description:
        schema["description"] = description
    return schema


def is_formal_object_schema(schema: dict[str, Any]) -> bool:
    return bool(
        isinstance(schema, dict)
        and schema.get("type") == "object"
        and isinstance(schema.get("properties"), dict)
        and isinstance(schema.get("required", []), list)
        and isinstance(schema.get("additionalProperties"), bool)
    )


def wrap_example(envelope: str, payload: dict[str, Any]) -> str:
    return f"[[{envelope} {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}]]"


def validate_instance(instance: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate the JSON-Schema subset used by resident action contracts.

    This intentionally small validator is the same release-gated path used before
    dispatch. Contracts do not claim support for arbitrary third-party schemas.
    """

    if not is_formal_object_schema(schema):
        raise ValueError("capability contract is not a complete object schema")
    _validate_node(instance, schema, "$")


def _validate_node(value: Any, schema: dict[str, Any], path: str) -> None:
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                raise ValueError(f"{path}.{required} is required")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise ValueError(f"{path} has unsupported fields: {', '.join(unknown)}")
        for key, child in value.items():
            if key in properties:
                _validate_node(child, properties[key], f"{path}.{key}")
    elif expected == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} must be an array")
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise ValueError(f"{path} must contain at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise ValueError(f"{path} may contain at most {schema['maxItems']} items")
        for index, item in enumerate(value):
            _validate_node(item, schema.get("items", {}), f"{path}[{index}]")
    elif expected == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path} must be a string")
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            raise ValueError(f"{path} is too short")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise ValueError(f"{path} is too long")
        if schema.get("pattern") and not re.fullmatch(str(schema["pattern"]), value):
            raise ValueError(f"{path} has an invalid format")
    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{path} must be an integer")
        if "minimum" in schema and value < int(schema["minimum"]):
            raise ValueError(f"{path} must be at least {schema['minimum']}")
        if "maximum" in schema and value > int(schema["maximum"]):
            raise ValueError(f"{path} may be at most {schema['maximum']}")
    elif expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{path} must be a number")
    elif expected == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{path} must be a boolean")
    elif expected == "null":
        if value is not None:
            raise ValueError(f"{path} must be null")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path} must equal {schema['const']!r}")
    for condition in schema.get("allOf", []):
        if_condition = condition.get("if", {})
        if _matches(value, if_condition):
            _validate_node(value, {**schema, **condition.get("then", {}), "allOf": []}, path)


def _matches(value: Any, schema: dict[str, Any]) -> bool:
    if not isinstance(value, dict):
        return False
    for key, child in schema.get("properties", {}).items():
        if key not in value:
            return False
        if "const" in child and value[key] != child["const"]:
            return False
        if "enum" in child and value[key] not in child["enum"]:
            return False
    return all(key in value for key in schema.get("required", []))
