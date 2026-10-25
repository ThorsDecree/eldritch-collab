from __future__ import annotations

from difflib import SequenceMatcher
import json
from pathlib import Path
import re
from typing import Any

from .context import ContextAssembler
from .db import ContinuityDB
from .models import ContextAssembly, ProviderRequest
from .providers.base import Provider
from .utils import atomic_write_json, new_id, sha256_text, stable_json, utc_now_iso


class MemoryExperimentService:
    """Run private, one-memory context comparisons against the configured provider."""

    def __init__(
        self,
        assembler: ContextAssembler,
        db: ContinuityDB,
        provider: Provider,
        home: Path,
    ) -> None:
        self.assembler = assembler
        self.db = db
        self.provider = provider
        self.home = Path(home).resolve()
        self.experiments_path = self.home / "traces" / "experiments"

    def dispatch(self, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode") or "run").strip().lower()
        if mode == "run":
            return self._run(payload, context)
        if mode == "list":
            return self._list(payload)
        experiment_id = str(payload.get("experiment_id") or "").strip()
        path = self._capsule_path(experiment_id)
        if mode == "inspect":
            return self._neutralize_tool_envelopes(
                {"mode": mode, "capsule": self._read_capsule(path, experiment_id)}
            )
        if mode == "delete":
            if not path.exists():
                raise FileNotFoundError(f"unknown memory experiment: {experiment_id}")
            path.unlink()
            return {"mode": mode, "experiment_id": experiment_id, "deleted": True}
        raise ValueError("memory.experiment mode must be run, list, inspect, or delete")

    def _run(self, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        assembly = context.get("context_assembly")
        if not isinstance(assembly, ContextAssembly):
            raise ValueError("memory experiment requires the current turn context assembly")
        if str(context.get("turn_id") or "") != assembly.turn_id:
            raise ValueError("memory experiment context does not match the current turn")
        base_messages = context.get("experiment_base_messages")
        if not isinstance(base_messages, (tuple, list)) or not base_messages:
            raise ValueError("memory experiment requires the frozen first provider prompt")
        frozen_messages = tuple(
            {"role": str(item.get("role", "user")), "content": str(item.get("content", ""))}
            for item in base_messages
            if isinstance(item, dict)
        )
        if len(frozen_messages) != len(base_messages):
            raise ValueError("frozen provider prompt contains an invalid message")
        if frozen_messages[0] != assembly.messages[0] or frozen_messages[-1] != assembly.messages[-1]:
            raise ValueError("frozen provider prompt does not match the captured context assembly")

        target_id = str(payload.get("memory_id") or "").strip()
        intervention = str(payload.get("intervention") or "").strip().lower()
        replacement_id = str(payload.get("replacement_memory_id") or "").strip() or None
        if intervention not in {"exclude", "replace"}:
            raise ValueError("intervention must be exclude or replace")
        if intervention == "replace" and not replacement_id:
            raise ValueError("replace intervention requires replacement_memory_id")
        if intervention == "exclude" and replacement_id:
            raise ValueError("exclude intervention cannot include replacement_memory_id")
        repetitions = int(payload.get("repetitions", 1))
        if not 1 <= repetitions <= 3:
            raise ValueError("repetitions must be between 1 and 3")
        retain_context = payload.get("retain_context", False)
        if not isinstance(retain_context, bool):
            raise ValueError("retain_context must be a boolean")

        variant_assembly = self.assembler.intervene_memory(
            assembly,
            target_id,
            replacement_memory_id=replacement_id,
        )
        variant_messages = list(frozen_messages)
        variant_messages[0] = variant_assembly.messages[0]
        experiment_id = new_id("experiment")
        model_route = str(context.get("model_route") or assembly.model_route)
        intervention_detail = {
            "kind": intervention,
            "memory_id": target_id,
            "replacement_memory_id": replacement_id,
        }
        arms: list[dict[str, Any]] = []
        pairs: list[dict[str, Any]] = []

        for repetition in range(1, repetitions + 1):
            order = (
                ("baseline", "intervention")
                if repetition % 2 == 1
                else ("intervention", "baseline")
            )
            pair_arms: dict[str, dict[str, Any]] = {}
            for arm_name in order:
                prompt = frozen_messages if arm_name == "baseline" else tuple(variant_messages)
                request = ProviderRequest(
                    turn_id=assembly.turn_id,
                    model_route=model_route,
                    messages=tuple(prompt),
                    metadata={
                        "memory_experiment_id": experiment_id,
                        "experimental_arm": arm_name,
                        "repetition": repetition,
                    },
                )
                try:
                    reply = self.provider.complete(request)
                    arm = {
                        "repetition": repetition,
                        "arm": arm_name,
                        "status": "completed",
                        "provider": reply.provider,
                        "model": reply.model,
                        "response_id": reply.response_id,
                        "usage": reply.usage,
                        "response_hash": sha256_text(reply.text),
                        "text": reply.text,
                    }
                except Exception as exc:
                    # Provider exception messages can echo request data; persist only
                    # the error class and make no automatic retry.
                    arm = {
                        "repetition": repetition,
                        "arm": arm_name,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": f"provider call failed ({type(exc).__name__})",
                    }
                arms.append(arm)
                pair_arms[arm_name] = arm

            baseline = pair_arms.get("baseline", {})
            variant = pair_arms.get("intervention", {})
            baseline_text = baseline.get("text")
            variant_text = variant.get("text")
            comparison = {
                "repetition": repetition,
                "call_order": list(order),
                "baseline_response_hash": baseline.get("response_hash"),
                "intervention_response_hash": variant.get("response_hash"),
                "status": (
                    "completed"
                    if baseline_text is not None and variant_text is not None
                    else "partial"
                ),
                "similarity_ratio": (
                    round(
                        SequenceMatcher(None, baseline_text, variant_text, autojunk=False).ratio(),
                        6,
                    )
                    if baseline_text is not None and variant_text is not None
                    else None
                ),
                "baseline_text": baseline_text,
                "intervention_text": variant_text,
            }
            pairs.append(comparison)

        successful_arms = sum(arm["status"] == "completed" for arm in arms)
        status = (
            "completed"
            if successful_arms == len(arms)
            else "partial"
            if successful_arms
            else "failed"
        )
        context_replay: dict[str, Any] = {
            "baseline_context_receipt_hash": sha256_text(stable_json(assembly.receipt)),
            "variant_context_receipt_hash": sha256_text(stable_json(variant_assembly.receipt)),
            "baseline_message_hashes": self._message_hashes(frozen_messages),
            "variant_message_hashes": self._message_hashes(tuple(variant_messages)),
            "baseline_layers": self._layer_receipt(assembly),
            "variant_layers": self._layer_receipt(variant_assembly),
            "retained": retain_context,
        }
        if retain_context:
            context_replay["baseline_messages"] = list(frozen_messages)
            context_replay["variant_messages"] = variant_messages
            for arm in arms:
                if "text" in arm:
                    continue
                # Keep failure details while making retained output storage explicit.
            retained_arms = [dict(arm) for arm in arms]
        else:
            retained_arms = [
                {key: value for key, value in arm.items() if key != "text"}
                for arm in arms
            ]

        created_at = utc_now_iso()
        interpretation = (
            "Measured output sensitivity under this frozen prompt and provider setup; "
            "these repetitions do not establish causality."
        )
        capsule = {
            "schema_version": "vestigia.memory-experiment.v0.1",
            "experiment_id": experiment_id,
            "created_at": created_at,
            "status": status,
            "resident_id": assembly.resident_id,
            "room_id": assembly.room_id,
            "turn_id": assembly.turn_id,
            "state": assembly.state,
            "model_route": model_route,
            "intervention": intervention_detail,
            "repetitions": repetitions,
            "interpretation": interpretation,
            "context_replay": context_replay,
            "arms": retained_arms,
            "comparisons": [
                {key: value for key, value in pair.items() if key not in {"baseline_text", "intervention_text"}}
                for pair in pairs
            ],
        }
        atomic_write_json(self._capsule_path(experiment_id), capsule)

        return self._neutralize_tool_envelopes(
            {
                "mode": "run",
                "experiment_id": experiment_id,
                "status": status,
                "intervention": intervention_detail,
                "repetitions": repetitions,
                "provider_calls": len(arms),
                "arms": arms,
                "pairs": pairs,
                "interpretation": interpretation,
                "retained_context": retain_context,
            }
        )

    def _list(self, payload: dict[str, Any]) -> dict[str, Any]:
        limit = min(100, max(1, int(payload.get("limit", 20))))
        if not self.experiments_path.exists():
            return {"mode": "list", "experiments": []}
        rows = []
        for path in self.experiments_path.glob("experiment_*.json"):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                capsule = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rows.append(
                {
                    "experiment_id": capsule.get("experiment_id"),
                    "created_at": capsule.get("created_at"),
                    "status": capsule.get("status"),
                    "turn_id": capsule.get("turn_id"),
                    "intervention": capsule.get("intervention"),
                    "repetitions": capsule.get("repetitions"),
                }
            )
        rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return {"mode": "list", "experiments": rows[:limit]}

    def _capsule_path(self, experiment_id: str) -> Path:
        if not re.fullmatch(r"experiment_[A-Za-z0-9_-]+", experiment_id):
            raise ValueError("experiment_id has an invalid format")
        root = self.experiments_path.resolve()
        path = root / f"{experiment_id}.json"
        if path.parent != root:
            raise ValueError("experiment_id leaves the experiment capsule directory")
        return path

    @staticmethod
    def _read_capsule(path: Path, experiment_id: str) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(f"unknown memory experiment: {experiment_id}")
        capsule = json.loads(path.read_text(encoding="utf-8"))
        if capsule.get("experiment_id") != experiment_id:
            raise ValueError("experiment capsule ID does not match its filename")
        return capsule

    @staticmethod
    def _message_hashes(messages: tuple[dict[str, str], ...]) -> list[dict[str, str]]:
        return [
            {"role": item["role"], "content_hash": sha256_text(item["content"])}
            for item in messages
        ]

    @staticmethod
    def _layer_receipt(assembly: ContextAssembly) -> list[dict[str, Any]]:
        return [
            {
                "name": layer.name,
                "content_hash": layer.content_hash,
                "included_item_ids": list(layer.item_ids),
                "omitted_item_ids": list(layer.omitted_item_ids),
                "budget_tokens": layer.budget_tokens,
                "used_tokens": layer.used_tokens,
            }
            for layer in assembly.layers
        ]

    @classmethod
    def _neutralize_tool_envelopes(cls, value: Any) -> Any:
        """Keep experiment model output inert when returned to the outer tool loop."""

        if isinstance(value, dict):
            return {
                str(key): cls._neutralize_tool_envelopes(child)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [cls._neutralize_tool_envelopes(item) for item in value]
        if isinstance(value, tuple):
            return [cls._neutralize_tool_envelopes(item) for item in value]
        if isinstance(value, str):
            for marker, safe_marker in (
                ("[[TOOL_ACTION", "[[QUOTED_EXPERIMENT_TOOL_ACTION"),
                ("[[HOUSE_TOOL", "[[QUOTED_EXPERIMENT_HOUSE_TOOL"),
                ("[[REACT", "[[QUOTED_EXPERIMENT_REACT"),
            ):
                value = value.replace(marker, safe_marker)
        return value
