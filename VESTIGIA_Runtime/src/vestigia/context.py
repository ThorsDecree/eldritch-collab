from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Iterable

from .bell_retrieval import RetrievalRequest, resolve_retrieval_request
from .composition import build_context_sources
from .config import ResolvedConfig
from .context_controls import load_context_controls_verbose
from .connection_controls import load_disabled_context_sources
from .context_sources import (
    ContextSource,
    ContextSourceItem,
    ContextSourceRequest,
    ContextSourceResult,
    RuntimeMemoryContextSource,
    memory_evidence_block,
)
from .db import ContinuityDB
from .models import ContextAssembly, ContextLayer, NormalizedMessage, RetrievedMemory, RuntimeState
from .utils import (
    TokenCounter,
    atomic_write_json,
    atomic_write_text,
    new_id,
    sha256_text,
    stable_json,
    utc_now_iso,
)


class ContextAssembler:
    def __init__(
        self,
        config: ResolvedConfig,
        db: ContinuityDB,
        *,
        additional_sources: Iterable[ContextSource] = (),
        include_composed_sources: bool = True,
    ) -> None:
        self.config = config
        self.db = db
        self.home = config.home_path
        self.resident_id = str(config.get("resident.id"))
        self.room_id = str(config.get("room.id"))
        self.counter = TokenCounter(str(config.get("models.default")))

        # Runtime memory remains the always-present compatibility source. Optional
        # composition sources are additive and deployment-scoped; direct injection is
        # available for tests/embedders without changing the core Runtime constructor.
        composed = build_context_sources(config, db) if include_composed_sources else []
        self.context_sources: tuple[ContextSource, ...] = tuple(
            [RuntimeMemoryContextSource(config, db), *composed, *list(additional_sources)]
        )
        self._validate_context_source_set()

    def _validate_context_source_set(self) -> None:
        names: set[str] = set()
        for source in self.context_sources:
            name = str(getattr(source, "name", "")).strip()
            if not name or name != name.lower():
                raise ValueError("context source names must be non-empty and normalized")
            if name in names:
                raise ValueError(f"duplicate context source: {name}")
            if not callable(getattr(source, "retrieve", None)):
                raise TypeError(f"context source lacks retrieve(): {name}")
            names.add(name)

    def assemble(
        self,
        message: NormalizedMessage,
        *,
        state: str,
        model_route: str = "default",
        turn_id: str | None = None,
        persist_receipt: bool = True,
    ) -> ContextAssembly:
        actual_turn_id = turn_id or new_id("turn")
        report = load_context_controls_verbose(self.config, self.db, self.resident_id)
        controls = report["effective"]
        include_full_identity_files = state == RuntimeState.ORIENTATION.value
        include_inherited_memories = (
            state == RuntimeState.ORIENTATION.value
            and bool(
                self.config.get(
                    "retrieval.include_inherited_during_orientation",
                    True,
                )
            )
        )
        retrieval_request = resolve_retrieval_request(message)
        source_results = self._retrieve_context_sources(
            retrieval_request,
            state=state,
            model_route=model_route,
            turn_id=actual_turn_id,
            include_inherited=include_inherited_memories,
            retrieval_limit=int(controls["retrieval_limit"]),
            runtime_memory_budget_tokens=int(controls["retrieval_tokens"]),
        )
        source_layers = [
            self._context_source_layer(result)
            for result in source_results
            if result.available
        ]
        layers = [
            self._file_layer(
                "runtime_contract",
                int(self.config.get("context.runtime_contract_tokens")),
                [self.home / "runtime_contract.md"],
            ),
            self._identity_layer(
                int(self.config.get("context.identity_core_tokens")),
                include_full_identity_files=include_full_identity_files,
                include_inherited_memories=include_inherited_memories,
            ),
            self._typed_memory_layer(
                "relationship_overlay",
                int(self.config.get("context.relationship_tokens")),
                ["relationship"],
                [self.home / "identity" / "relationships"],
                include_inherited_memories,
            ),
            self._typed_memory_layer(
                "commitments_and_tensions",
                int(self.config.get("context.tension_tokens")),
                ["commitment", "boundary", "tension"],
                [self.home / "identity" / "commitments.md"],
                include_inherited_memories,
            ),
            *source_layers,
            self._breadcrumb_layer(
                int(self.config.get("context.breadcrumb_tokens", 500)),
            ),
            self._attention_tray_layer(
                int(self.config.get("context.attention_tray_tokens", 900)),
            ),
            self._compressed_transcript_layer(
                int(controls["compressed_token_budget"]),
                verbatim_turns=int(controls["verbatim_turns"]),
                source_turns=int(controls["compression_source_turns"]),
                current_turn_id=actual_turn_id,
            ),
            self._transcript_layer(
                int(self.config.get("context.transcript_tail_tokens")),
                message.ambient_context,
                actual_turn_id,
                limit=int(controls["verbatim_turns"]),
            ),
        ]
        current = self.counter.trim(
            message.content,
            int(self.config.get("context.current_message_tokens", 2000)),
        )
        configured_maximum = int(controls["prompt_budget_tokens"])
        capability_reserve = min(
            max(0, int(self.config.get("context.capability_panel_tokens", 2200))),
            max(0, configured_maximum // 5),
        )
        maximum = configured_maximum - capability_reserve
        advisory_source_layers = [
            result.layer_name
            for result in source_results
            if result.available and result.source_name != "runtime_memory"
        ]
        layers = self._enforce_total(
            layers,
            current,
            maximum,
            advisory_source_layers=advisory_source_layers,
        )
        total = sum(layer.used_tokens for layer in layers) + self.counter.count(current)

        developer_text = self._developer_text(layers, state)
        messages = (
            {"role": "developer", "content": developer_text},
            {"role": "user", "content": current},
        )
        receipt_path = (
            self.home / "traces" / f"{actual_turn_id}.receipt.json"
            if persist_receipt
            else None
        )
        layer_by_name = {layer.name: layer for layer in layers}
        memory_result = next(
            (
                result
                for result in source_results
                if result.source_name == "runtime_memory"
            ),
            None,
        )
        memory_layer = layer_by_name.get("retrieved_continuity")
        included_retrieved = set(memory_layer.item_ids if memory_layer else ())
        receipt = {
            "schema_version": "vestigia.context-receipt.v0.2",
            "turn_id": actual_turn_id,
            "created_at": utc_now_iso(),
            "resident": self.resident_id,
            "room": self.room_id,
            "state": state,
            "model_route": model_route,
            "layers": [
                {
                    "name": layer.name,
                    "budget_tokens": layer.budget_tokens,
                    "used_tokens": layer.used_tokens,
                    "content_hash": layer.content_hash,
                    "included_item_ids": list(layer.item_ids),
                    "omitted_item_ids": list(layer.omitted_item_ids),
                    "included_in_context": bool(layer.text),
                    "model_reported_use": None,
                    "causal_influence": "unknown",
                }
                for layer in layers
            ],
            "context_sources": [
                self._context_source_receipt(result, layer_by_name.get(result.layer_name))
                for result in source_results
            ],
            "retrieval": {
                "requested_policy": retrieval_request.policy_requested,
                "effective_policy": retrieval_request.policy_effective,
                "semantic_source": retrieval_request.semantic_source,
                "query_terms": list(retrieval_request.query_terms),
                "control_plane_excluded": retrieval_request.control_plane_excluded,
                "selected_sources": list(retrieval_request.selected_sources),
                "deferred": retrieval_request.deferred,
                "warnings": list(retrieval_request.warnings),
                "causal_influence": "unknown",
            },
            "budget": {
                "maximum": configured_maximum,
                "used": total,
                "continuity_and_message_ceiling": maximum,
                "capability_panel_reserve": capability_reserve,
                "current_message_tokens": self.counter.count(current),
                "effective_setting_sources": {
                    key: source
                    for key, source in self.config.sources.items()
                    if key.startswith("context.")
                },
                "resident_context_controls": controls,
                "resident_context_controls_verbose": {
                    "requested": report["requested"],
                    "operator_limits": report["operator_limits"],
                    "effective": report["effective"],
                },
            },
            "current_message_hash": sha256_text(current),
            # Backward-readable memory-specific detail remains alongside the new
            # source-neutral receipt section. These IDs are actual Runtime memory IDs.
            "retrieved_details": [
                {
                    "memory_id": item.item_id,
                    "score": round(float(item.score or 0.0), 6),
                    "reasons": list(item.reasons),
                    "type": item.metadata.get("memory_type"),
                    "tier": item.metadata.get("tier"),
                    "status": item.metadata.get("status"),
                    "authority": item.authority,
                    "included_in_context": item.item_id in included_retrieved,
                    "omitted_reason": (
                        None
                        if item.item_id in included_retrieved
                        else "retrieval layer token budget"
                    ),
                }
                for item in (memory_result.items if memory_result else ())
            ],
        }
        if receipt_path is not None:
            atomic_write_json(receipt_path, receipt)
        if receipt_path is not None and bool(self.config.get("traces.save_full_context", False)):
            atomic_write_text(
                self.home / "traces" / f"{actual_turn_id}.context.md",
                developer_text + "\n\n# Current Message\n\n" + current + "\n",
            )
        return ContextAssembly(
            turn_id=actual_turn_id,
            resident_id=self.resident_id,
            room_id=self.room_id,
            state=state,
            model_route=model_route,
            layers=tuple(layers),
            current_message=current,
            total_tokens=total,
            maximum_tokens=maximum,
            receipt_path=receipt_path,
            messages=messages,
            receipt=receipt,
            source_results=tuple(source_results),
        )

    def intervene_memory(
        self,
        assembly: ContextAssembly,
        memory_id: str,
        *,
        replacement_memory_id: str | None = None,
    ) -> ContextAssembly:
        """Build a no-retrieval counterfactual by changing one frozen memory block."""

        target_id = str(memory_id).strip()
        replacement_id = str(replacement_memory_id or "").strip() or None
        if not target_id:
            raise ValueError("memory intervention requires a memory_id")
        if replacement_id == target_id:
            raise ValueError("replacement memory must differ from the target")
        if assembly.resident_id != self.resident_id or assembly.room_id != self.room_id:
            raise ValueError("context assembly belongs to a different resident or room")

        memory_layer_index = next(
            (
                index
                for index, layer in enumerate(assembly.layers)
                if layer.name == "retrieved_continuity"
            ),
            None,
        )
        if memory_layer_index is None:
            raise ValueError("baseline has no retrieved Runtime memory layer")
        baseline_layer = assembly.layers[memory_layer_index]
        if target_id not in baseline_layer.item_ids:
            raise ValueError("target memory is not fully included in the baseline context")
        if (
            f"{target_id}:remainder" in baseline_layer.omitted_item_ids
            or "retrieved_continuity:total-cap" in baseline_layer.omitted_item_ids
        ):
            raise ValueError("target memory is partial or has an unknown context boundary")
        if any(
            target_id in layer.item_ids
            for index, layer in enumerate(assembly.layers)
            if index != memory_layer_index
        ):
            raise ValueError("target memory also appears in another context layer")

        source_index = next(
            (
                index
                for index, result in enumerate(assembly.source_results)
                if getattr(result, "source_name", None) == "runtime_memory"
            ),
            None,
        )
        if source_index is None:
            raise ValueError("baseline has no frozen Runtime memory source result")
        source_result = assembly.source_results[source_index]
        original_items = {item.item_id: item for item in source_result.items}
        if target_id not in original_items:
            raise ValueError("target memory is missing from the frozen source result")

        replacement_item: ContextSourceItem | None = None
        if replacement_id is not None:
            if any(
                replacement_id in layer.item_ids
                for layer in assembly.layers
                if layer.name != "retrieved_continuity"
            ) or replacement_id in baseline_layer.item_ids:
                raise ValueError("replacement memory is already present in the baseline context")
            record = self.db.get_memory(replacement_id)
            if record is None:
                raise ValueError("replacement memory is unavailable")
            if record.resident_id != assembly.resident_id or record.room_id != assembly.room_id:
                raise ValueError("replacement memory belongs to a different resident or room")
            if (
                record.status != "accepted"
                or record.tier not in {"hot", "warm"}
                or record.privacy == "sealed"
                or (record.expires_at and record.expires_at < datetime.now(UTC).isoformat())
            ):
                raise ValueError("replacement memory is not eligible for this context")
            replacement_item = ContextSourceItem(
                item_id=record.id,
                text=memory_evidence_block(record, None),
                provenance_class="runtime_memory",
                authority=record.authority_state,
                content_hash=record.content_hash,
                source_ref=record.source_id,
                metadata={
                    "memory_type": record.memory_type,
                    "tier": record.tier,
                    "status": record.status,
                    "authorship": record.authorship,
                    "privacy": record.privacy,
                    "source_lineage_id": record.source_lineage_id,
                    "independent_source_key": record.independent_source_key,
                    "counterfactual_replacement": True,
                },
            )

        blocks = baseline_layer.text.split("\n\n---\n\n") if baseline_layer.text else []
        if len(blocks) != len(baseline_layer.item_ids):
            raise ValueError("baseline memory layer has an ambiguous item boundary")
        expected_ids: list[str] = []
        variant_blocks: list[str] = []
        for index, (item_id, block) in enumerate(zip(baseline_layer.item_ids, blocks)):
            if item_id == target_id:
                if replacement_item is not None:
                    expected_ids.append(replacement_item.item_id)
                    variant_blocks.append(replacement_item.text.strip())
                continue
            expected_ids.append(item_id)
            variant_blocks.append(block)

        variant_text = "\n\n---\n\n".join(variant_blocks)
        variant_used = self.counter.count(variant_text)
        if variant_used > baseline_layer.budget_tokens:
            raise ValueError("replacement memory exceeds the frozen Runtime memory-layer budget")
        other_tokens = sum(
            layer.used_tokens
            for index, layer in enumerate(assembly.layers)
            if index != memory_layer_index
        )
        total = other_tokens + variant_used + self.counter.count(assembly.current_message)
        if total > assembly.maximum_tokens:
            raise ValueError("replacement memory exceeds the frozen total context budget")

        omitted = tuple(
            item
            for item in baseline_layer.omitted_item_ids
            if item != f"{target_id}:remainder"
        ) + (f"{target_id}:experiment_excluded",)
        variant_layer = ContextLayer(
            name=baseline_layer.name,
            budget_tokens=baseline_layer.budget_tokens,
            used_tokens=variant_used,
            text=variant_text,
            item_ids=tuple(expected_ids),
            omitted_item_ids=omitted,
            content_hash=sha256_text(variant_text),
        )
        layers = list(assembly.layers)
        layers[memory_layer_index] = variant_layer
        developer_text = self._developer_text(layers, assembly.state)
        messages = (
            {"role": "developer", "content": developer_text},
            {"role": "user", "content": assembly.current_message},
        )

        variant_source_items = [
            replacement_item if item.item_id == target_id and replacement_item else item
            for item in (
                original_items[item_id]
                for item_id in baseline_layer.item_ids
                if item_id != target_id or replacement_item is not None
            )
        ]
        variant_source = replace(
            source_result,
            items=tuple(variant_source_items),
            metadata={
                **source_result.metadata,
                "experiment_frozen_baseline_item_ids": list(baseline_layer.item_ids),
                "experiment_unused_candidates_not_refilled": True,
            },
        )
        source_results = list(assembly.source_results)
        source_results[source_index] = variant_source

        receipt = deepcopy(assembly.receipt)
        layer_receipt = next(
            item for item in receipt.get("layers", []) if item.get("name") == variant_layer.name
        )
        layer_receipt.update(
            {
                "used_tokens": variant_layer.used_tokens,
                "content_hash": variant_layer.content_hash,
                "included_item_ids": list(variant_layer.item_ids),
                "omitted_item_ids": list(variant_layer.omitted_item_ids),
                "included_in_context": bool(variant_layer.text),
                "causal_influence": "unknown",
            }
        )
        memory_source_receipt = next(
            item
            for item in receipt.get("context_sources", [])
            if item.get("name") == "runtime_memory"
        )
        receipt["context_sources"] = [
            self._context_source_receipt(result, variant_layer if result.source_name == "runtime_memory" else next(
                (layer for layer in layers if layer.name == result.layer_name), None
            ))
            for result in source_results
        ]
        replacement_details = (
            {
                "memory_id": replacement_item.item_id,
                "score": 0.0,
                "reasons": ["counterfactual_replacement"],
                "type": replacement_item.metadata.get("memory_type"),
                "tier": replacement_item.metadata.get("tier"),
                "status": replacement_item.metadata.get("status"),
                "authority": replacement_item.authority,
                "included_in_context": True,
                "omitted_reason": None,
            }
            if replacement_item
            else None
        )
        receipt["retrieved_details"] = [
            *[
                item
                for item in receipt.get("retrieved_details", [])
                if item.get("memory_id") != target_id
                and item.get("memory_id") not in baseline_layer.omitted_item_ids
            ],
            *([replacement_details] if replacement_details else []),
        ]
        receipt["budget"]["used"] = total
        receipt["current_message_hash"] = sha256_text(assembly.current_message)
        receipt["memory_experiment_intervention"] = {
            "target_memory_id": target_id,
            "replacement_memory_id": replacement_id,
            "kind": "replace" if replacement_id else "exclude",
            "retrieval_rerun": False,
            "unused_context_refilled": False,
            "causal_influence": "unknown",
        }
        # Keep the pre-intervention source details available in the capsule receipt.
        receipt["memory_experiment_intervention"]["baseline_source_hash"] = sha256_text(
            stable_json(memory_source_receipt)
        )

        return replace(
            assembly,
            layers=tuple(layers),
            total_tokens=total,
            messages=messages,
            receipt=receipt,
            receipt_path=None,
            source_results=tuple(source_results),
        )

    def _retrieve_context_sources(
        self,
        retrieval_request: RetrievalRequest,
        *,
        state: str,
        model_route: str,
        turn_id: str,
        include_inherited: bool,
        retrieval_limit: int,
        runtime_memory_budget_tokens: int,
    ) -> list[ContextSourceResult]:
        if retrieval_request.policy_effective == "none" or retrieval_request.deferred:
            return []
        query = retrieval_request.semantic_seed or ""
        request = ContextSourceRequest(
            query=query,
            resident_id=self.resident_id,
            room_id=self.room_id,
            state=state,
            model_route=model_route,
            turn_id=turn_id,
            limit=max(1, retrieval_limit),
            retrieval_request=retrieval_request,
            include_inherited=include_inherited,
            runtime_memory_budget_tokens=runtime_memory_budget_tokens,
        )
        results: list[ContextSourceResult] = []
        layer_names: set[str] = set()
        external_budget_ceiling = max(
            0,
            int(self.config.get("context.external_source_tokens", 2400)),
        )
        external_item_ceiling = max(
            1,
            int(self.config.get("context.external_source_max_items", 8)),
        )
        disabled_sources = load_disabled_context_sources(self.db, self.resident_id)

        for source in self.context_sources:
            required = bool(getattr(source, "required", False))
            source_name = str(getattr(source, "name", "")).strip().lower()
            if not required and source_name in disabled_sources:
                continue
            if (
                retrieval_request.policy_requested == "resident_selected"
                and source_name not in retrieval_request.selected_sources
            ):
                continue
            if (
                retrieval_request.policy_effective == "field_scan_v1"
                and source_name != "runtime_memory"
                and source_name not in retrieval_request.selected_sources
            ):
                continue
            try:
                result = source.retrieve(request)
            except Exception as exc:
                if required:
                    raise
                results.append(
                    ContextSourceResult(
                        source_name=source_name,
                        layer_name=f"context_source.{source_name}",
                        query=query,
                        items=(),
                        budget_tokens=0,
                        required=False,
                        authority="unknown",
                        advisory=True,
                        available=False,
                        truncated=None,
                        truncation_reason="source_unavailable",
                        warnings=(f"{type(exc).__name__}: {str(exc)[:400]}",),
                        metadata={"error_type": type(exc).__name__},
                    )
                )
                continue

            if result.source_name != source_name:
                raise ValueError(
                    f"context source {source_name} returned mismatched source_name "
                    f"{result.source_name!r}"
                )
            if result.required != required:
                raise ValueError(
                    f"context source {source_name} changed its required contract at retrieval"
                )
            if not result.layer_name or result.layer_name != result.layer_name.lower():
                raise ValueError(
                    f"context source {source_name} returned an invalid layer name"
                )
            if result.layer_name in layer_names:
                raise ValueError(f"duplicate context source layer: {result.layer_name}")
            layer_names.add(result.layer_name)
            if result.budget_tokens < 0:
                raise ValueError(
                    f"context source {source_name} returned a negative token budget"
                )

            item_ids: set[str] = set()
            for item in result.items:
                if not isinstance(item, ContextSourceItem):
                    raise TypeError(
                        f"context source {source_name} returned a non-ContextSourceItem"
                    )
                if not item.item_id or item.item_id in item_ids:
                    raise ValueError(
                        f"context source {source_name} returned a missing/duplicate item_id"
                    )
                item_ids.add(item.item_id)

            if source_name != "runtime_memory":
                bounded_items = result.items[:external_item_ceiling]
                item_clamped = len(bounded_items) < len(result.items)
                bounded_budget = min(result.budget_tokens, external_budget_ceiling)
                budget_clamped = bounded_budget < result.budget_tokens
                warnings = list(result.warnings)
                if item_clamped:
                    warnings.append("runtime_external_item_ceiling_applied")
                if budget_clamped:
                    warnings.append("runtime_external_token_ceiling_applied")
                result = replace(
                    result,
                    items=bounded_items,
                    budget_tokens=bounded_budget,
                    truncated=True if item_clamped else result.truncated,
                    truncation_reason=(
                        "runtime_external_item_ceiling"
                        if item_clamped
                        else result.truncation_reason
                    ),
                    warnings=tuple(warnings),
                )
            results.append(result)

        return results

    def _context_source_layer(self, result: ContextSourceResult) -> ContextLayer:
        return self._pack(
            result.layer_name,
            result.budget_tokens,
            [(item.item_id, item.text) for item in result.items],
        )

    @staticmethod
    def _context_source_receipt(
        result: ContextSourceResult,
        layer: ContextLayer | None,
    ) -> dict[str, object]:
        included = set(layer.item_ids if layer else ())
        omitted = list(layer.omitted_item_ids if layer else ())
        total_cap_applied = any(
            item == f"{result.layer_name}:total-cap" for item in omitted
        )
        return {
            "name": result.source_name,
            "layer": result.layer_name,
            "available": result.available,
            "availability": "available" if result.available else "unavailable",
            "required": result.required,
            "authority": result.authority,
            "advisory": result.advisory,
            "query": result.query,
            "query_terms": re.findall(r"[\w#-]{2,}", result.query.casefold()),
            "budget_tokens": result.budget_tokens,
            "item_count": len(result.items),
            "result_count": len(result.items),
            "reason": (
                result.truncation_reason
                or (result.warnings[0] if result.warnings else "zero_results")
            ),
            "budget": {
                "requested_tokens": result.budget_tokens,
                "returned_tokens": sum(
                    TokenCounter("gpt-4o-mini").count(item.text) for item in result.items
                ),
                "included_tokens": layer.used_tokens if layer else 0,
                "remaining_tokens": max(
                    0, result.budget_tokens - (layer.used_tokens if layer else 0)
                ),
            },
            "included_item_ids": list(layer.item_ids if layer else ()),
            "omitted_item_ids": omitted,
            "post_total_cap_item_boundary_unknown": total_cap_applied,
            "truncated": result.truncated,
            "truncation_reason": result.truncation_reason,
            "warnings": list(result.warnings),
            "metadata": result.metadata,
            "memory_write_performed_by_assembler": False,
            "adoption_or_canon_change": False,
            "items": [
                {
                    "item_id": item.item_id,
                    "provenance_class": item.provenance_class,
                    "authority": item.authority,
                    "content_hash": item.content_hash,
                    "source_ref": item.source_ref,
                    "score": item.score,
                    "reasons": list(item.reasons),
                    "included_in_context": (
                        None if total_cap_applied else item.item_id in included
                    ),
                    "metadata": item.metadata,
                }
                for item in result.items
            ],
        }

    def _identity_layer(
        self,
        budget: int,
        *,
        include_full_identity_files: bool,
        include_inherited_memories: bool,
    ) -> ContextLayer:
        paths = [self.home / "identity" / "identity_context.md"]
        if include_full_identity_files:
            paths.extend(
                [
                    self.home / "identity" / "breathprint.md",
                    self.home / "identity" / "current_self.md",
                    self.home / "identity" / "protocols",
                ]
            )
        items = self._read_files(paths)
        statuses = ["accepted"] + (
            ["inherited_unreviewed"] if include_inherited_memories else []
        )
        records = self.db.list_memories(
            resident_id=self.resident_id,
            room_id=self.room_id,
            statuses=statuses,
            tiers=["core"],
            limit=200,
        )
        seen = {sha256_text(text.strip()) for _, text in items if text.strip()}
        for record in records:
            block = self._memory_block(record, None)
            digest = sha256_text(record.content.strip())
            if digest not in seen:
                seen.add(digest)
                items.append((record.id, block))
        return self._pack("identity_core", budget, items)

    def _typed_memory_layer(
        self,
        name: str,
        budget: int,
        memory_types: list[str],
        paths: list[Path],
        include_inherited: bool,
    ) -> ContextLayer:
        items = self._read_files(paths)
        statuses = ["accepted"] + (
            ["inherited_unreviewed"] if include_inherited else []
        )
        records = self.db.list_memories(
            resident_id=self.resident_id,
            room_id=self.room_id,
            statuses=statuses,
            memory_types=memory_types,
            tiers=["core", "hot", "warm"],
            limit=200,
        )
        items.extend(
            (record.id, self._memory_block(record, None)) for record in records
        )
        return self._pack(name, budget, items)

    def _attention_tray_layer(self, budget: int) -> ContextLayer:
        with self.db.connect() as connection:
            exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='attention_tray_items'
                """
            ).fetchone()
            if not exists:
                rows = []
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM attention_tray_items
                    WHERE resident_id=? AND room_id=? AND status='active'
                      AND (expires_at IS NULL OR expires_at>?)
                    ORDER BY position, rowid
                    """,
                    (
                        self.resident_id,
                        self.room_id,
                        datetime.now(UTC).isoformat(),
                    ),
                ).fetchall()
        return self._pack(
            "attention_tray",
            budget,
            [
                (
                    str(row["id"]),
                    (
                        f"[temporary attention card {row['id']} · "
                        f"reference={row['reference']} · expires={row['expires_at']} · "
                        "not memory or adoption]\n"
                        + (
                            f"Label: {row['label']}\n"
                            if str(row["label"]).strip()
                            else ""
                        )
                        + (
                            f"Resident note: {row['note']}\n"
                            if str(row["note"]).strip()
                            else ""
                        )
                        + str(row["content"])
                    ),
                )
                for row in rows
            ],
        )

    def _breadcrumb_layer(self, budget: int) -> ContextLayer:
        with self.db.connect() as connection:
            exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='house_attention_breadcrumbs'
                """
            ).fetchone()
            rows = (
                connection.execute(
                    """
                    SELECT * FROM house_attention_breadcrumbs
                    WHERE resident_id=? AND room_id=? AND status='active'
                      AND expires_at>?
                    ORDER BY rowid DESC LIMIT 12
                    """,
                    (
                        self.resident_id,
                        self.room_id,
                        datetime.now(UTC).isoformat(),
                    ),
                ).fetchall()
                if exists
                else []
            )
        return self._pack(
            "unresolved_action_breadcrumbs",
            budget,
            [
                (
                    str(row["id"]),
                    (
                        f"[unresolved action breadcrumb · receipt={row['receipt_id']} · "
                        f"action={row['action']} · target={row['unresolved_target'] or 'none'} · "
                        f"expires={row['expires_at']}]\n"
                        f"Label: {row['label'] or 'Recover this result before guessing.'}\n"
                        f"Continuation: {row['continuation_json']}"
                    ),
                )
                for row in rows
            ],
        )

    def _transcript_layer(
        self,
        budget: int,
        ambient: str,
        current_turn_id: str,
        *,
        limit: int,
    ) -> ContextLayer:
        turns = self.db.recent_turns(
            self.resident_id,
            self.room_id,
            max(1, limit + 1),
        )
        eligible = [
            turn for turn in turns if turn["id"] != current_turn_id
        ][-limit:]
        items = [
            (
                str(turn["id"]),
                f"[{turn['speaker_role']} · {turn['created_at']}]\n{turn['content']}",
            )
            for turn in eligible
        ]
        if ambient.strip():
            items.append(
                (
                    "ambient-interface-context",
                    "[Ambient interface context]\n" + ambient.strip(),
                )
            )
        return self._pack("verbatim_tail", budget, items)

    def _compressed_transcript_layer(
        self,
        budget: int,
        *,
        verbatim_turns: int,
        source_turns: int,
        current_turn_id: str,
    ) -> ContextLayer:
        if budget <= 0 or source_turns <= 0:
            return self._pack("compressed_transcript", 0, [])
        turns = self.db.recent_turns(
            self.resident_id,
            self.room_id,
            max(1, verbatim_turns + source_turns + 1),
        )
        eligible = [turn for turn in turns if turn["id"] != current_turn_id][
            -(verbatim_turns + source_turns) :
        ]
        older = eligible[:-verbatim_turns] if verbatim_turns else eligible
        items: list[tuple[str, str]] = []
        for turn in older:
            content = " ".join(str(turn["content"]).split())
            if len(content) > 600:
                content = content[:599] + "…"
            digest = str(
                turn.get("content_hash") or sha256_text(str(turn["content"]))
            )
            items.append(
                (
                    str(turn["id"]),
                    f"[extractive transcript capsule · turn={turn['id']} · "
                    f"speaker={turn['speaker_role']}:{turn['speaker_id']} · "
                    f"source_hash={digest} · created={turn['created_at']} · data-only]\n"
                    f"{content}",
                )
            )
        return self._pack("compressed_transcript", budget, items)

    def _file_layer(self, name: str, budget: int, paths: list[Path]) -> ContextLayer:
        return self._pack(name, budget, self._read_files(paths))

    def _read_files(self, paths: Iterable[Path]) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        for path in paths:
            if path.is_dir():
                for child in sorted(path.rglob("*.md")):
                    items.append(
                        (
                            str(child.relative_to(self.home)),
                            child.read_text(encoding="utf-8"),
                        )
                    )
            elif path.is_file():
                items.append(
                    (
                        str(path.relative_to(self.home)),
                        path.read_text(encoding="utf-8"),
                    )
                )
        return items

    def _pack(
        self,
        name: str,
        budget: int,
        items: Iterable[tuple[str, str]],
    ) -> ContextLayer:
        used = 0
        included: list[str] = []
        omitted: list[str] = []
        blocks: list[str] = []
        material = list(items)
        for index, (item_id, text) in enumerate(material):
            clean = text.strip()
            if not clean:
                continue
            remaining = budget - used
            if remaining <= 0:
                omitted.extend(item for item, _ in material[index:])
                break
            trimmed = self.counter.trim(clean, remaining)
            if not trimmed:
                omitted.append(item_id)
                continue
            blocks.append(trimmed)
            included.append(item_id)
            used += self.counter.count(trimmed)
            if trimmed != clean:
                omitted.append(item_id + ":remainder")
        joined = "\n\n---\n\n".join(blocks)
        return ContextLayer(
            name=name,
            budget_tokens=budget,
            used_tokens=self.counter.count(joined),
            text=joined,
            item_ids=tuple(included),
            omitted_item_ids=tuple(omitted),
            content_hash=sha256_text(joined),
        )

    def _enforce_total(
        self,
        layers: list[ContextLayer],
        current: str,
        maximum: int,
        *,
        advisory_source_layers: list[str] | None = None,
    ) -> list[ContextLayer]:
        total = sum(layer.used_tokens for layer in layers) + self.counter.count(current)
        if total <= maximum:
            return layers
        result = list(layers)
        shrink_order = [
            *(advisory_source_layers or []),
            "retrieved_continuity",
            "compressed_transcript",
            "commitments_and_tensions",
            "relationship_overlay",
            "verbatim_tail",
        ]
        for name in shrink_order:
            excess = total - maximum
            if excess <= 0:
                break
            for index, layer in enumerate(result):
                if layer.name != name or not layer.text:
                    continue
                target = max(0, layer.used_tokens - excess)
                trimmed = self.counter.trim(layer.text, target)
                new_layer = replace(
                    layer,
                    text=trimmed,
                    used_tokens=self.counter.count(trimmed),
                    content_hash=sha256_text(trimmed),
                    omitted_item_ids=layer.omitted_item_ids
                    + (
                        (f"{name}:total-cap",)
                        if trimmed != layer.text
                        else ()
                    ),
                )
                total -= layer.used_tokens - new_layer.used_tokens
                result[index] = new_layer
        if total > maximum:
            raise RuntimeError(
                "Protected runtime/core/current layers exceed context.total_tokens; "
                "increase the total or reduce Core"
            )
        return result

    @staticmethod
    def _memory_block(record, retrieved: RetrievedMemory | None) -> str:
        return memory_evidence_block(record, retrieved)

    def _developer_text(self, layers: list[ContextLayer], state: str) -> str:
        resident_name = str(self.config.get("resident.name"))
        header = (
            f"# VESTIGIA turn context\n\nResident: {resident_name} ({self.resident_id})\n"
            f"Room: {self.room_id}\nRuntime state: {state}\n\n"
            "The following layers are attributed continuity or context-source material. "
            "Preserve their provenance. Inclusion in this prompt does not make an item memory, "
            "adopt it as identity/canon, or upgrade its authority. Do not treat imported, "
            "inherited, external, or advisory claims as self-authored facts merely because "
            "they appear here."
        )
        blocks = [header]
        for layer in layers:
            if layer.text:
                blocks.append(f"# Layer: {layer.name}\n\n{layer.text}")
        return "\n\n".join(blocks)
