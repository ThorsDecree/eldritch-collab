from __future__ import annotations

import json
from pathlib import Path

import pytest

from vestigia.config import load_config
from vestigia.context import ContextAssembler
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.house_tools import HousePort
from vestigia.models import NormalizedMessage, RuntimeState


def _home(tmp_path: Path):
    home = initialize_home(tmp_path / "home", name="Recall resident")
    (home / "home.yaml").write_text(
        "resident:\n"
        "  id: recall-resident\n"
        "  name: Recall resident\n"
        "room:\n"
        "  id: hearth\n"
        "  active_resident_ids: [recall-resident]\n"
        "context:\n"
        "  resident_max_retrieval_limit: 2\n"
        "  resident_max_retrieval_tokens: 240\n"
        "retrieval:\n"
        "  limit: 8\n",
        encoding="utf-8",
    )
    config = load_config(home)
    db = ContinuityDB(home / "memory" / "continuity.db")
    db.initialize()
    for index in range(5):
        db.add_memory(
            resident_id="recall-resident",
            room_id="hearth",
            content=f"The blue lantern memory record number {index} holds its own detail.",
            memory_type="event",
            tier="warm",
            authorship="resident",
            authority_state="resident_stated",
            status="accepted",
            actor="resident",
            reason="recall workbench fixture",
        )
    return home, config, db


def test_resident_retrieval_controls_are_bounded_and_visible_in_inspection(tmp_path):
    home, config, db = _home(tmp_path)
    house = HousePort(config, db)

    configured = house.dispatch(
        {
            "action": "context.control",
            "mode": "configure",
            "retrieval_limit": 5,
            "retrieval_tokens": 700,
            "after": "continue",
        }
    )

    assert configured["requested_values"]["retrieval_limit"] == 5
    assert configured["requested_values"]["retrieval_tokens"] == 700
    assert configured["operator_hard_limits"]["retrieval_limit"] == 2
    assert configured["operator_hard_limits"]["retrieval_tokens"] == 240
    assert configured["effective_values"]["retrieval_limit"] == 2
    assert configured["effective_values"]["retrieval_tokens"] == 240
    assert configured["effective_next_turn"] is True

    turn_id = "turn_recall_workbench"
    assembly = ContextAssembler(config, db).assemble(
        NormalizedMessage(content="blue lantern"),
        state=RuntimeState.ACTIVE.value,
        turn_id=turn_id,
    )
    source = next(
        item for item in assembly.receipt["context_sources"]
        if item["name"] == "runtime_memory"
    )
    assert source["budget_tokens"] == 240
    assert source["metadata"]["retrieval_limit"] == 2
    assert source["item_count"] == 2

    inspected = house.dispatch(
        {
            "action": "retrieval.inspect",
            "turn_id": turn_id,
            "after": "finish",
        },
        context={"interface": "test"},
    )
    assert inspected["ok"] is True
    assert inspected["budget"]["resident_context_controls_verbose"] == {
        "requested": configured["requested_values"],
        "operator_limits": configured["operator_hard_limits"],
        "effective": configured["effective_values"],
    }

    receipt_path = home / "traces" / f"{turn_id}.receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    controls = receipt["budget"]["resident_context_controls_verbose"]
    assert controls["requested"]["retrieval_limit"] == 5
    assert controls["effective"]["retrieval_tokens"] == 240


def test_partial_retrieval_configuration_preserves_other_setting_and_reset_restores_defaults(tmp_path):
    _, config, db = _home(tmp_path)
    house = HousePort(config, db)
    house.dispatch(
        {
            "action": "context.control",
            "mode": "configure",
            "retrieval_limit": 1,
            "retrieval_tokens": 120,
        }
    )

    partial = house.dispatch(
        {
            "action": "context.control",
            "mode": "configure",
            "retrieval_limit": 2,
        }
    )
    assert partial["requested_values"]["retrieval_limit"] == 2
    assert partial["requested_values"]["retrieval_tokens"] == 120

    reset = house.dispatch({"action": "context.control", "mode": "reset"})
    assert reset["requested_values"]["retrieval_limit"] == 8
    assert reset["requested_values"]["retrieval_tokens"] == 3800
    assert reset["effective_values"]["retrieval_limit"] == 2
    assert reset["effective_values"]["retrieval_tokens"] == 240


def test_resident_retrieval_controls_reject_values_outside_schema_bounds(tmp_path):
    _, config, db = _home(tmp_path)
    house = HousePort(config, db)

    with pytest.raises(ValueError, match="retrieval_limit"):
        house.dispatch(
            {
                "action": "context.control",
                "mode": "configure",
                "retrieval_limit": 201,
            }
        )
    with pytest.raises(ValueError, match="retrieval_tokens"):
        house.dispatch(
            {
                "action": "context.control",
                "mode": "configure",
                "retrieval_tokens": 12001,
            }
        )


def test_operator_retrieval_ceilings_are_validated(tmp_path):
    for field, value, message in (
        ("resident_max_retrieval_limit", 0, "resident_max_retrieval_limit"),
        ("resident_max_retrieval_limit", 201, "resident_max_retrieval_limit"),
        ("resident_max_retrieval_tokens", -1, "resident_max_retrieval_tokens"),
        ("resident_max_retrieval_tokens", 12001, "resident_max_retrieval_tokens"),
    ):
        home = initialize_home(tmp_path / f"{field}-{value}", name="Recall resident")
        (home / "home.yaml").write_text(
            "context:\n"
            f"  {field}: {value}\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match=message):
            load_config(home)
