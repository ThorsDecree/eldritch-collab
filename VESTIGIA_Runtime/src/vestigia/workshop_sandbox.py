from __future__ import annotations

from typing import Any

from .capabilities import CapabilitySpec, object_schema
from .workshop_sandbox_backend import _bounded_int, backend_descriptor
from .workshop_sandbox_runner import (
    CANONICAL_SAY_HI_HASH,
    CANONICAL_SAY_HI_SOURCE,
    execute_source,
)
from .workshop_sandbox_store import (
    _inspect_execution,
    _list_executions,
    _observatory_summary,
    ensure_schema,
)


_INSTALLED = False
_SCRIPT_ID = "vestigia.canonical.say-hi"
_SCRIPT_VERSION = 1


def _handle(house: Any, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    ensure_schema(house)
    mode = str(payload.get("mode") or "describe").strip().lower()
    if mode == "describe":
        return {
            "mode": mode,
            "backend": backend_descriptor(house),
            "resident_callable_scripts": [
                {
                    "script_id": _SCRIPT_ID,
                    "version": _SCRIPT_VERSION,
                    "content_hash": CANONICAL_SAY_HI_HASH,
                    "purpose": "canonical bounded process-within-process acceptance script",
                }
            ],
            "inline_source_accepted": False,
            "imported_source_accepted": False,
            "next_stage": "resident script shelf with provenance, inspection, test, approval, and activation",
            "outward_effect": "none",
        }
    if mode == "run_acceptance":
        name = str(payload.get("name") or "friend")[:80]
        return execute_source(
            house,
            source=CANONICAL_SAY_HI_SOURCE,
            script_id=_SCRIPT_ID,
            script_version=_SCRIPT_VERSION,
            arguments={"name": name},
            context=context,
            payload=payload,
        )
    if mode == "list":
        limit = _bounded_int(payload.get("limit"), default=20, minimum=1, maximum=100)
        return {
            "mode": mode,
            "executions": _list_executions(house, limit),
            "source_included": False,
            "raw_arguments_included": False,
        }
    if mode == "inspect":
        execution_id = str(payload.get("execution_id") or "").strip()
        if not execution_id:
            raise ValueError("execution_id is required for inspect")
        return {"mode": mode, **_inspect_execution(house, execution_id)}
    raise ValueError("unsupported workshop.sandbox mode")


def _register(house: Any) -> None:
    ensure_schema(house)
    after = {"type": "string", "enum": ["continue", "finish"]}
    house.registry.register(
        CapabilitySpec(
            name="workshop.sandbox",
            description=(
                "Describe or exercise the bounded local-process Workshop backend, then inspect "
                "its private execution evidence. v0.1 runs only the bundled canonical script."
            ),
            effects=(
                "local_process:conditional",
                "filesystem:private_ephemeral",
                "database:workshop_receipt",
            ),
            cost_class="local_low",
            confirmation="none",
            default_after="continue",
            result_visibility="resident_private",
            config_key="workshop.enabled",
            group="workshop",
            input_schema=object_schema(
                {
                    "action": {"type": "string", "const": "workshop.sandbox"},
                    "mode": {
                        "type": "string",
                        "enum": ["describe", "run_acceptance", "list", "inspect"],
                    },
                    "name": {"type": "string", "maxLength": 80},
                    "execution_id": {"type": "string", "maxLength": 200},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "wall_seconds": {"type": "integer", "minimum": 1, "maximum": 30},
                    "after": after,
                },
                required=("action",),
            ),
            example_envelopes=(
                {
                    "action": "workshop.sandbox",
                    "mode": "describe",
                    "after": "continue",
                },
                {
                    "action": "workshop.sandbox",
                    "mode": "run_acceptance",
                    "name": "Jeff",
                    "wall_seconds": 3,
                    "after": "continue",
                },
                {
                    "action": "workshop.sandbox",
                    "mode": "inspect",
                    "execution_id": "workshop_exec_...",
                    "after": "continue",
                },
            ),
            next_step=(
                "Inspect the execution and receipt. Artifacts remain private; no follow-up, "
                "publication, memory adoption, or outward action occurs automatically."
            ),
        ),
        lambda payload, context: _handle(house, payload, context),
    )


def install_core() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from .house_tools import HousePort

    previous_install = HousePort._install_capabilities

    def install_with_workshop_sandbox(self: Any) -> None:
        previous_install(self)
        _register(self)

    HousePort._install_capabilities = install_with_workshop_sandbox

    try:
        from . import sensory_apparatus

        previous_observatory = sensory_apparatus._observatory

        def observatory_with_workshop(house: Any, payload: dict[str, Any]) -> dict[str, Any]:
            result = previous_observatory(house, payload)
            panels = result.get("observatory")
            if isinstance(panels, dict) and str(payload.get("section") or "all") == "all":
                panels["workshop_sandbox"] = _observatory_summary(house)
            return result

        sensory_apparatus._observatory = observatory_with_workshop
    except (ImportError, AttributeError):
        pass

    _INSTALLED = True
