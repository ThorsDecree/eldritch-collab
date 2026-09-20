from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .db import ContinuityDB
from .utils import new_id, stable_json, utc_now_iso


class BellObservatoryError(ValueError):
    pass


def _load_context_receipt(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not path.exists():
        return None, {"path": str(path), "availability": "unavailable", "reason": "missing"}
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, {
            "path": str(path),
            "availability": "unavailable",
            "reason": "malformed",
            "error_type": type(exc).__name__,
        }
    if not isinstance(value, dict):
        return None, {"path": str(path), "availability": "unavailable", "reason": "not_object"}
    return value, {
        "path": str(path),
        "availability": "available",
        "schema_version": value.get("schema_version"),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _decision_projection(receipt: dict[str, Any]) -> dict[str, Any]:
    retrieval = receipt.get("retrieval")
    retrieval = retrieval if isinstance(retrieval, dict) else {}
    sources: list[dict[str, Any]] = []
    for source in receipt.get("context_sources", ()):
        if not isinstance(source, dict):
            continue
        sources.append(
            {
                "name": source.get("name"),
                "layer": source.get("layer"),
                "available": source.get("available"),
                "query_terms": list(source.get("query_terms", ())),
                "reason": source.get("reason"),
                "budget": source.get("budget", {}),
                "included_item_ids": list(source.get("included_item_ids", ())),
                "omitted_item_ids": list(source.get("omitted_item_ids", ())),
                "truncated": source.get("truncated"),
                "truncation_reason": source.get("truncation_reason"),
            }
        )
    orientation_refs: list[dict[str, Any]] = []
    for layer in receipt.get("layers", ()):
        if not isinstance(layer, dict):
            continue
        orientation_refs.append(
            {
                "name": layer.get("name"),
                "content_hash": layer.get("content_hash"),
                "included_item_ids": list(layer.get("included_item_ids", ())),
                "omitted_item_ids": list(layer.get("omitted_item_ids", ())),
            }
        )
    return {
        "requested_policy": retrieval.get("requested_policy"),
        "effective_policy": retrieval.get("effective_policy"),
        "semantic_source": retrieval.get("semantic_source"),
        "query_terms": list(retrieval.get("query_terms", ())),
        "control_plane_excluded": bool(retrieval.get("control_plane_excluded", False)),
        "selected_sources": list(retrieval.get("selected_sources", ())),
        "deferred": bool(retrieval.get("deferred", False)),
        "warnings": list(retrieval.get("warnings", ())),
        "causal_influence": "unknown",
        "orientation_context_refs": orientation_refs,
        "sources": sources,
        "budget": receipt.get("budget", {}),
    }


class BellObservatory:
    """Runtime-owned, read-only explanations of bell attention decisions."""

    def __init__(self, db: ContinuityDB, resident_id: str, room_id: str):
        self.db = db
        self.resident_id = resident_id
        self.room_id = room_id

    def start_run(
        self,
        *,
        turn_id: str,
        bell_id: str,
        context_receipt_path: str | Path,
    ) -> str:
        run_id = new_id("bellrun")
        path = Path(context_receipt_path)
        receipt, context_info = _load_context_receipt(path)
        decision = _decision_projection(receipt or {})
        now = utc_now_iso()
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO bell_observatory_runs
                (id, resident_id, room_id, turn_id, bell_id, status,
                 context_receipt_path, retrieval_json, orientation_json,
                 sources_json, budget_json, response_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    self.resident_id,
                    self.room_id,
                    str(turn_id),
                    str(bell_id),
                    "started",
                    stable_json(context_info),
                    stable_json(
                        {
                            key: decision[key]
                            for key in (
                                "requested_policy",
                                "effective_policy",
                                "semantic_source",
                                "query_terms",
                                "control_plane_excluded",
                                "selected_sources",
                                "deferred",
                                "warnings",
                                "causal_influence",
                            )
                        }
                    ),
                    stable_json(decision["orientation_context_refs"]),
                    stable_json(decision["sources"]),
                    stable_json(decision["budget"]),
                    stable_json({"state": "pending", "causal_influence": "unknown"}),
                    now,
                    now,
                ),
            )
        return run_id

    def complete_run(
        self,
        run_id: str,
        *,
        response_state: str,
        curation_eligible: bool | None,
        curation_suppression_reason: str | None = None,
        assistant_turn_id: str | None = None,
        response_hash: str | None = None,
    ) -> None:
        self._ensure_run(run_id)
        response = {
            "state": str(response_state),
            "curation_eligible": curation_eligible,
            "curation_suppression_reason": curation_suppression_reason,
            "assistant_turn_id": assistant_turn_id,
            "response_hash": response_hash,
            "causal_influence": "unknown",
        }
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE bell_observatory_runs SET status=?, response_json=?, updated_at=? WHERE id=?",
                ("completed", stable_json(response), utc_now_iso(), run_id),
            )

    def list_runs(
        self,
        *,
        limit: int = 25,
        bell_id: str | None = None,
        response_state: str | None = None,
    ) -> dict[str, object]:
        if limit <= 0 or limit > 200:
            raise BellObservatoryError("Bell Observatory limit must be between 1 and 200")
        clauses = ["resident_id=?", "room_id=?"]
        parameters: list[Any] = [self.resident_id, self.room_id]
        if bell_id:
            clauses.append("bell_id=?")
            parameters.append(bell_id.strip())
        with self.db.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM bell_observatory_runs WHERE {' AND '.join(clauses)} ORDER BY rowid DESC",
                parameters,
            ).fetchall()
        runs = []
        for row in rows:
            response = self._parse_json(row["response_json"], {})
            state = response.get("state") if isinstance(response, dict) else None
            if response_state and state != response_state:
                continue
            runs.append(
                {
                    "run_id": str(row["id"]),
                    "turn_id": str(row["turn_id"]),
                    "bell_id": str(row["bell_id"]),
                    "status": str(row["status"]),
                    "response_state": state,
                    "created_at": str(row["created_at"]),
                    "updated_at": str(row["updated_at"]),
                }
            )
        return {
            "schema_version": "vestigia.bell-observatory.v0.1",
            "runs": runs[:limit],
            "matched_total": len(runs),
            "returned": min(len(runs), limit),
            "truncated": len(runs) > limit,
        }

    def inspect_run(self, run_id: str) -> dict[str, object]:
        row = self._row(run_id)
        return {
            "schema_version": "vestigia.bell-observatory.v0.1",
            "run": {
                "run_id": str(row["id"]),
                "resident_id": str(row["resident_id"]),
                "room_id": str(row["room_id"]),
                "turn_id": str(row["turn_id"]),
                "bell_id": str(row["bell_id"]),
                "status": str(row["status"]),
                "context_receipt": self._parse_json(row["context_receipt_path"], {}),
                "retrieval": self._parse_json(row["retrieval_json"], {}),
                "orientation_context_refs": self._parse_json(row["orientation_json"], []),
                "sources": self._parse_json(row["sources_json"], []),
                "budget": self._parse_json(row["budget_json"], {}),
                "response": self._parse_json(row["response_json"], {}),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            },
        }

    def replay_run(self, run_id: str) -> dict[str, object]:
        inspected = self.inspect_run(run_id)
        run = inspected["run"]
        retrieval = run["retrieval"]
        return {
            "schema_version": "vestigia.bell-replay.v0.1",
            "run_id": run_id,
            "replayable": True,
            "decision_inputs": {
                "bell_id": run["bell_id"],
                "turn_id": run["turn_id"],
                "requested_policy": retrieval.get("requested_policy"),
                "effective_policy": retrieval.get("effective_policy"),
                "semantic_source": retrieval.get("semantic_source"),
                "query_terms": retrieval.get("query_terms", []),
                "control_plane_excluded": retrieval.get("control_plane_excluded", False),
                "selected_sources": retrieval.get("selected_sources", []),
                "sources": run["sources"],
                "budget": run["budget"],
            },
            "model_causality": "not_replayed",
            "outward_dispatch": False,
            "limitations": [
                "replays stored decision inputs and receipts only",
                "does not replay model cognition",
                "does not send outward effects",
            ],
        }

    def _ensure_run(self, run_id: str) -> None:
        self._row(run_id)

    def _row(self, run_id: str):
        wanted = str(run_id).strip()
        if not wanted:
            raise BellObservatoryError("Bell run ID must not be blank")
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM bell_observatory_runs WHERE id=? AND resident_id=? AND room_id=?",
                (wanted, self.resident_id, self.room_id),
            ).fetchone()
        if row is None:
            raise BellObservatoryError(f"Bell Observatory run not found: {wanted}")
        return row

    @staticmethod
    def _parse_json(raw: Any, fallback: Any) -> Any:
        if isinstance(raw, (dict, list)):
            return raw
        try:
            value = json.loads(str(raw))
        except (TypeError, json.JSONDecodeError):
            return {"availability": "unavailable", "reason": "malformed_detail"}
        return value if isinstance(value, type(fallback)) else fallback
