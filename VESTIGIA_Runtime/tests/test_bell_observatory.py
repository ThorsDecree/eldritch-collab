import json
from pathlib import Path

import pytest

from vestigia.bell_observatory import BellObservatory, BellObservatoryError
from vestigia.config import load_config
from vestigia.db import ContinuityDB
from vestigia.home import initialize_home
from vestigia.house_tools import HousePort
from vestigia.mcp_projection import read_projection


def _context_receipt(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "vestigia.context-receipt.v0.2",
                "retrieval": {
                    "requested_policy": "auto",
                    "effective_policy": "prompt_only",
                    "semantic_source": "bell.resident_prompt",
                    "query_terms": ["notice", "attention"],
                    "control_plane_excluded": True,
                    "selected_sources": [],
                    "deferred": False,
                    "warnings": [],
                },
                "context_sources": [
                    {
                        "name": "runtime_memory",
                        "layer": "retrieved_continuity",
                        "available": True,
                        "query_terms": ["notice", "attention"],
                        "budget": {"requested_tokens": 100, "returned_tokens": 20, "included_tokens": 20, "remaining_tokens": 80},
                        "included_item_ids": ["memory_1"],
                        "omitted_item_ids": ["memory_2"],
                        "reason": "retrieved",
                    }
                ],
                "layers": [
                    {"name": "identity", "content_hash": "a" * 64, "included_item_ids": []},
                ],
                "budget": {"maximum": 1000, "used": 200},
            }
        ),
        encoding="utf-8",
    )


def test_observatory_records_inspectable_decision_inputs_and_no_change(tmp_path: Path) -> None:
    db = ContinuityDB(tmp_path / "continuity.db")
    db.initialize()
    receipt_path = tmp_path / "context.json"
    _context_receipt(receipt_path)
    observatory = BellObservatory(db, "liora", "hearth")

    run_id = observatory.start_run(
        turn_id="turn_1",
        bell_id="bell_1",
        context_receipt_path=receipt_path,
    )
    observatory.complete_run(
        run_id,
        response_state="no_change",
        curation_eligible=False,
        curation_suppression_reason="bell_no_change",
        assistant_turn_id="turn_2",
        response_hash="b" * 64,
    )

    inspected = observatory.inspect_run(run_id)
    assert inspected["run"]["bell_id"] == "bell_1"
    assert inspected["run"]["retrieval"]["control_plane_excluded"] is True
    assert inspected["run"]["retrieval"]["query_terms"] == ["notice", "attention"]
    assert inspected["run"]["response"]["state"] == "no_change"
    assert inspected["run"]["response"]["curation_eligible"] is False
    assert inspected["run"]["response"]["curation_suppression_reason"] == "bell_no_change"
    assert inspected["run"]["response"]["causal_influence"] == "unknown"

    replay = observatory.replay_run(run_id)
    assert replay["replayable"] is True
    assert replay["model_causality"] == "not_replayed"
    assert replay["outward_dispatch"] is False
    assert replay["decision_inputs"]["query_terms"] == ["notice", "attention"]


def test_observatory_lists_runs_and_fails_closed(tmp_path: Path) -> None:
    db = ContinuityDB(tmp_path / "continuity.db")
    db.initialize()
    receipt_path = tmp_path / "context.json"
    _context_receipt(receipt_path)
    observatory = BellObservatory(db, "liora", "hearth")
    observatory.start_run(turn_id="turn_1", bell_id="bell_1", context_receipt_path=receipt_path)

    listed = observatory.list_runs(limit=10)
    assert listed["matched_total"] == 1
    assert listed["runs"][0]["turn_id"] == "turn_1"
    with pytest.raises(BellObservatoryError):
        observatory.inspect_run("missing")


def test_observatory_reports_unavailable_context_receipts(tmp_path: Path) -> None:
    db = ContinuityDB(tmp_path / "continuity.db")
    db.initialize()
    observatory = BellObservatory(db, "liora", "hearth")
    run_id = observatory.start_run(
        turn_id="turn_missing",
        bell_id="bell_missing",
        context_receipt_path=tmp_path / "missing.json",
    )
    inspected = observatory.inspect_run(run_id)
    assert inspected["run"]["context_receipt"]["availability"] == "unavailable"


def test_observatory_actions_are_read_only_and_projectable(tmp_path: Path) -> None:
    home = initialize_home(tmp_path / "home", name="Test Resident", glyph="🏮")
    config = load_config(home)
    db = ContinuityDB(home / "memory" / "continuity.db")
    port = HousePort(config, db)
    receipt_path = tmp_path / "context.json"
    _context_receipt(receipt_path)
    run_id = port.bell_observatory.start_run(
        turn_id="turn_1", bell_id="bell_1", context_receipt_path=receipt_path
    )

    names = {item["name"] for item in port.registry.describe()}
    expected = {
        "bell.runs.list",
        "bell.run.inspect",
        "bell.run.replay",
        "bell.policy.preview",
    }
    assert expected <= names
    projected = read_projection(port)
    assert expected <= {item["name"] for item in projected["capabilities"]}

    listed = port.dispatch({"action": "bell.runs.list", "limit": 10})
    assert listed["matched_total"] == 1
    inspected = port.dispatch({"action": "bell.run.inspect", "run_id": run_id})
    assert inspected["run"]["run_id"] == run_id
    replay = port.dispatch({"action": "bell.run.replay", "run_id": run_id})
    assert replay["outward_dispatch"] is False
    preview = port.dispatch(
        {
            "action": "bell.policy.preview",
            "requested_policy": "auto",
            "prompt": "Notice what wants attention.",
            "purpose": "look_around",
        }
    )
    assert preview["effective_policy"] == "field_scan_v1"
