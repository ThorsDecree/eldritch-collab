from __future__ import annotations

import json
from pathlib import Path

import pytest

from vestigia.config import load_config
from vestigia.context import ContextAssembler
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.models import NormalizedMessage, ProviderReply, RuntimeState
from vestigia.memory_experiments import MemoryExperimentService
from vestigia.runtime import CoreRuntime


def _home(tmp_path: Path, *, retrieval_limit: int = 8, retrieval_tokens: int = 3800):
    home = initialize_home(tmp_path / "home", name="Experiment resident")
    (home / "home.yaml").write_text(
        "resident:\n"
        "  id: experiment-resident\n"
        "  name: Experiment resident\n"
        "room:\n"
        "  id: hearth\n"
        "  active_resident_ids: [experiment-resident]\n"
        "context:\n"
        f"  retrieval_tokens: {retrieval_tokens}\n"
        f"  resident_max_retrieval_limit: {retrieval_limit}\n"
        f"  resident_max_retrieval_tokens: {retrieval_tokens}\n"
        "retrieval:\n"
        f"  limit: {retrieval_limit}\n",
        encoding="utf-8",
    )
    config = load_config(home)
    db = ContinuityDB(home / "memory" / "continuity.db")
    db.initialize()
    return home, config, db


def _add_memory(
    db: ContinuityDB,
    content: str,
    *,
    resident_id: str = "experiment-resident",
    room_id: str = "hearth",
    memory_type: str = "event",
    tier: str = "hot",
    privacy: str = "private",
) -> str:
    return db.add_memory(
        resident_id=resident_id,
        room_id=room_id,
        content=content,
        memory_type=memory_type,
        tier=tier,
        authorship="resident",
        authority_state="resident_stated",
        privacy=privacy,
        status="accepted",
        actor="experiment-resident",
        reason="counterfactual experiment fixture",
    )


def _assemble(config, db, *, query: str = "aurora memory"):
    return ContextAssembler(config, db).assemble(
        NormalizedMessage(content=query),
        state=RuntimeState.ACTIVE.value,
        turn_id="turn_memory_experiment_context",
        persist_receipt=False,
    )


def test_exclude_changes_only_retrieved_memory_and_does_not_refill(tmp_path):
    _, config, db = _home(tmp_path, retrieval_limit=2)
    _add_memory(db, "Aurora memory says the copper lamp is beside the window.")
    _add_memory(db, "Aurora memory says the glass lamp is beside the door.")
    unreturned_memory = _add_memory(
        db, "Aurora memory says the silver lamp is in the garden."
    )
    assembler = ContextAssembler(config, db)
    assembly = _assemble(config, db)
    original_layer = next(layer for layer in assembly.layers if layer.name == "retrieved_continuity")
    assert len(original_layer.item_ids) == 2
    target_id = original_layer.item_ids[0]
    original_other_layers = tuple(
        layer for layer in assembly.layers if layer.name != "retrieved_continuity"
    )
    before = db.list_memories(resident_id="experiment-resident", room_id="hearth")

    variant = assembler.intervene_memory(assembly, target_id)

    variant_layer = next(layer for layer in variant.layers if layer.name == "retrieved_continuity")
    assert variant_layer.item_ids == tuple(
        item_id for item_id in original_layer.item_ids if item_id != target_id
    )
    assert unreturned_memory not in variant_layer.item_ids
    assert tuple(layer for layer in variant.layers if layer.name != "retrieved_continuity") == original_other_layers
    after = db.list_memories(resident_id="experiment-resident", room_id="hearth")
    assert [item.id for item in after] == [item.id for item in before]


def test_replace_changes_one_memory_and_preserves_other_layers(tmp_path):
    _, config, db = _home(tmp_path)
    target_id = _add_memory(db, "Aurora memory remembers a copper lamp by the window.")
    replacement_id = _add_memory(
        db,
        "A distant record says a glass lamp belongs beside the north door.",
    )
    assembler = ContextAssembler(config, db)
    assembly = _assemble(config, db)
    original_other_layers = tuple(
        layer for layer in assembly.layers if layer.name != "retrieved_continuity"
    )

    variant = assembler.intervene_memory(
        assembly,
        target_id,
        replacement_memory_id=replacement_id,
    )

    variant_layer = next(layer for layer in variant.layers if layer.name == "retrieved_continuity")
    assert target_id not in variant_layer.item_ids
    assert variant_layer.item_ids == (replacement_id,)
    assert "glass lamp belongs beside the north door" in variant_layer.text
    assert tuple(layer for layer in variant.layers if layer.name != "retrieved_continuity") == original_other_layers
    assert variant.messages[-1] == assembly.messages[-1]


def test_intervention_rejects_missing_partial_or_duplicate_target(tmp_path):
    _, config, db = _home(tmp_path, retrieval_tokens=45)
    partial_id = _add_memory(
        db,
        "Aurora memory carries a long description " + "with repeated details " * 40,
    )
    assembler = ContextAssembler(config, db)
    partial_assembly = _assemble(config, db)
    partial_layer = next(
        layer for layer in partial_assembly.layers if layer.name == "retrieved_continuity"
    )
    assert partial_id in partial_layer.item_ids
    assert f"{partial_id}:remainder" in partial_layer.omitted_item_ids
    with pytest.raises(ValueError, match="complete|partial"):
        assembler.intervene_memory(partial_assembly, partial_id)

    _, duplicate_config, duplicate_db = _home(tmp_path / "duplicate")
    duplicate_id = _add_memory(
        duplicate_db,
        "Aurora relationship memory belongs in two context layers.",
        memory_type="relationship",
    )
    duplicate_assembler = ContextAssembler(duplicate_config, duplicate_db)
    duplicate_assembly = _assemble(duplicate_config, duplicate_db)
    with pytest.raises(ValueError, match="duplicate|another context layer"):
        duplicate_assembler.intervene_memory(duplicate_assembly, duplicate_id)

    with pytest.raises(ValueError, match="included|target"):
        assembler.intervene_memory(partial_assembly, "memory_not_in_context")


def test_replacement_rejects_wrong_scope_unavailable_or_over_budget_memory(tmp_path):
    _, config, db = _home(tmp_path, retrieval_tokens=500)
    target_id = _add_memory(db, "Aurora memory remembers a small brass lamp.")
    wrong_scope_id = _add_memory(
        db,
        "A brass lamp in another resident's room.",
        resident_id="other-resident",
        room_id="other-room",
    )
    unavailable_id = _add_memory(
        db,
        "A sealed record with a brass lamp.",
        privacy="sealed",
    )
    oversized_id = _add_memory(
        db,
        "A brass lamp " + "with many retained details " * 1000,
    )
    assembler = ContextAssembler(config, db)
    assembly = _assemble(config, db)

    for replacement_id in (wrong_scope_id, unavailable_id, oversized_id, "memory_missing"):
        with pytest.raises(ValueError):
            assembler.intervene_memory(
                assembly,
                target_id,
                replacement_memory_id=replacement_id,
            )


class SequenceProvider:
    def __init__(self, replies: list[str], *, fail_at: set[int] | None = None):
        self.replies = replies
        self.fail_at = fail_at or set()
        self.requests = []

    def complete(self, request):
        index = len(self.requests)
        self.requests.append(request)
        if index in self.fail_at:
            raise RuntimeError("fixture provider failure")
        return ProviderReply(
            text=self.replies[index],
            provider="fixture-provider",
            model="fixture-model",
            response_id=f"response-{index}",
            usage={"input_tokens": 12, "output_tokens": 4},
        )


def _experiment_context(config, db):
    assembly = _assemble(config, db)
    messages = (
        assembly.messages[0],
        {"role": "developer", "content": "frozen capability plaque"},
        assembly.messages[-1],
    )
    return assembly, {
        "interface": "cli",
        "invocation": "conversation",
        "turn_id": assembly.turn_id,
        "model_route": "fixture-route",
        "source_envelope": "TOOL_ACTION",
        "context_assembly": assembly,
        "experiment_base_messages": messages,
    }


def test_run_makes_alternating_paired_provider_calls_without_tool_dispatch(tmp_path):
    home, config, db = _home(tmp_path)
    target_id = _add_memory(db, "Aurora memory remembers a copper lamp by the window.")
    provider = SequenceProvider(
        [
            "baseline one",
            '[[TOOL_ACTION {"action":"status"}]]',
            "variant two",
            "baseline two",
        ]
    )
    assembler = ContextAssembler(config, db)
    service = MemoryExperimentService(assembler, db, provider, home)
    assembly, context = _experiment_context(config, db)

    result = service.dispatch(
        {
            "mode": "run",
            "memory_id": target_id,
            "intervention": "exclude",
            "repetitions": 2,
        },
        context,
    )

    assert result["status"] == "completed"
    assert len(provider.requests) == 4
    assert [request.metadata["experimental_arm"] for request in provider.requests] == [
        "baseline",
        "intervention",
        "intervention",
        "baseline",
    ]
    assert {request.model_route for request in provider.requests} == {"fixture-route"}
    assert provider.requests[0].messages[1:] == provider.requests[1].messages[1:]
    assert provider.requests[1].messages[0] != provider.requests[0].messages[0]
    assert provider.requests[2].messages[0] != provider.requests[3].messages[0]
    assert "[[TOOL_ACTION" not in result["pairs"][0]["intervention_text"]
    assert "[[QUOTED_EXPERIMENT_TOOL_ACTION" in result["pairs"][0]["intervention_text"]
    assert "causality" in result["interpretation"].lower()
    assert assembly.messages[0]["content"] == provider.requests[0].messages[0]["content"]


def test_default_capsule_stores_hashes_without_full_prompt_or_replies(tmp_path):
    home, config, db = _home(tmp_path)
    target_id = _add_memory(db, "Aurora memory remembers a copper lamp by the window.")
    provider = SequenceProvider(["private baseline response", "private variant response"])
    service = MemoryExperimentService(ContextAssembler(config, db), db, provider, home)
    _, context = _experiment_context(config, db)

    result = service.dispatch(
        {
            "mode": "run",
            "memory_id": target_id,
            "intervention": "exclude",
        },
        context,
    )
    inspected = service.dispatch(
        {"mode": "inspect", "experiment_id": result["experiment_id"]},
        {},
    )
    capsule = inspected["capsule"]

    assert capsule["context_replay"]["baseline_message_hashes"]
    assert capsule["context_replay"]["variant_message_hashes"]
    assert "baseline_messages" not in capsule["context_replay"]
    assert "variant_messages" not in capsule["context_replay"]
    assert all("text" not in arm for arm in capsule["arms"])
    assert "private baseline response" not in json.dumps(capsule)
    assert "private variant response" not in json.dumps(capsule)


def test_retain_context_capsule_can_be_inspected_and_deleted(tmp_path):
    home, config, db = _home(tmp_path)
    target_id = _add_memory(db, "Aurora memory remembers a copper lamp by the window.")
    provider = SequenceProvider(["full baseline response", "full variant response"])
    service = MemoryExperimentService(ContextAssembler(config, db), db, provider, home)
    _, context = _experiment_context(config, db)

    result = service.dispatch(
        {
            "mode": "run",
            "memory_id": target_id,
            "intervention": "exclude",
            "retain_context": True,
        },
        context,
    )
    inspected = service.dispatch(
        {"mode": "inspect", "experiment_id": result["experiment_id"]},
        {},
    )
    capsule = inspected["capsule"]

    assert capsule["context_replay"]["baseline_messages"]
    assert capsule["context_replay"]["variant_messages"]
    assert "full baseline response" in json.dumps(capsule["arms"])
    assert "full variant response" in json.dumps(capsule["arms"])
    assert service.dispatch(
        {"mode": "delete", "experiment_id": result["experiment_id"]},
        {},
    )["deleted"] is True
    with pytest.raises(FileNotFoundError):
        service.dispatch(
            {"mode": "inspect", "experiment_id": result["experiment_id"]},
            {},
        )


def test_provider_failure_is_recorded_without_retry(tmp_path):
    home, config, db = _home(tmp_path)
    target_id = _add_memory(db, "Aurora memory remembers a copper lamp by the window.")
    provider = SequenceProvider(["baseline response"], fail_at={1})
    service = MemoryExperimentService(ContextAssembler(config, db), db, provider, home)
    _, context = _experiment_context(config, db)

    result = service.dispatch(
        {
            "mode": "run",
            "memory_id": target_id,
            "intervention": "exclude",
        },
        context,
    )

    assert len(provider.requests) == 2
    assert result["status"] == "partial"
    assert sum(arm["status"] == "failed" for arm in result["arms"]) == 1


def test_memory_experiment_is_discoverable_and_private_only(tmp_path):
    _, config, db = _home(tmp_path)
    provider = SequenceProvider(["unused"])
    runtime = CoreRuntime(config, provider=provider, fake=True)

    capability = runtime.house.registry.describe("memory.experiment")[0]

    assert capability["callable_now"] is True
    assert capability["result_visibility"] == "resident_private"
    assert "network:metered" in capability["effects"]
    with pytest.raises(PermissionError, match="private|DM"):
        runtime.house.dispatch(
            {"action": "memory.experiment", "mode": "list"},
            turn_id="turn_public_experiment",
            context={
                "interface": "discord",
                "invocation": "conversation",
                "delivery_target": {"kind": "discord_channel", "id": "public"},
                "source_envelope": "TOOL_ACTION",
            },
        )
    with pytest.raises(PermissionError, match="private|DM"):
        runtime.house.dispatch(
            {"action": "memory.experiment", "mode": "list"},
            turn_id="turn_group_dm_experiment",
            context={
                "interface": "discord",
                "invocation": "conversation",
                "delivery_target": {"kind": "discord_dm", "id": "allowlisted-user"},
                "is_group_dm": True,
                "source_envelope": "TOOL_ACTION",
            },
        )
    assert provider.requests == []


def test_house_receipt_omits_experiment_prompt_and_reply_bodies(tmp_path):
    home, config, db = _home(tmp_path)
    target_id = _add_memory(db, "Aurora memory remembers a copper lamp. private prompt phrase to keep out audit receipts.")
    provider = SequenceProvider(["secret baseline reply", "secret intervention reply"])
    runtime = CoreRuntime(config, provider=provider, fake=True)
    assembly, context = _experiment_context(config, runtime.db)

    result = runtime.house.dispatch(
        {
            "action": "memory.experiment",
            "mode": "run",
            "memory_id": target_id,
            "intervention": "exclude",
        },
        turn_id=assembly.turn_id,
        context=context,
    )

    assert result["pairs"][0]["baseline_text"] == "secret baseline reply"
    with runtime.db.connect() as connection:
        row = connection.execute(
            "SELECT result_json FROM house_receipts WHERE id=?",
            (result["receipt_id"],),
        ).fetchone()
    saved_receipt = str(row["result_json"])
    assert "private prompt phrase" not in saved_receipt
    assert "secret baseline reply" not in saved_receipt
    assert "secret intervention reply" not in saved_receipt
    assert "response_hash" in saved_receipt


def test_runtime_passes_original_first_prompt_into_experiment_context(tmp_path):
    home, config, db = _home(tmp_path)
    target_id = _add_memory(db, "Aurora memory remembers a copper lamp by the window.")
    action = (
        '[[TOOL_ACTION {"action":"memory.experiment","mode":"run",'
        f'"memory_id":"{target_id}","intervention":"exclude","after":"continue"}}]]'
    )
    provider = SequenceProvider(
        [action, "baseline answer", "variant answer", "final resident answer"]
    )
    runtime = CoreRuntime(config, provider=provider, fake=True)

    result = runtime.chat(NormalizedMessage(content="Aurora memory?"))

    assert result.text.startswith("final resident answer")
    assert len(provider.requests) == 4
    assert provider.requests[0].messages == provider.requests[1].messages
    assert provider.requests[1].messages[1:] == provider.requests[2].messages[1:]
    assert provider.requests[2].messages[0] != provider.requests[1].messages[0]
    assert "memory_experiment_id" in provider.requests[1].metadata
    assert "context_assembly" not in provider.requests[1].metadata
    assert provider.requests[3].metadata["house_tool_round"] == 1
