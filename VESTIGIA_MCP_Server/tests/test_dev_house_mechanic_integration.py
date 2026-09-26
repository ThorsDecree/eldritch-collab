from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import threading

from mcp import Client

from house_mechanic.api import HouseMechanicServer
from house_mechanic.model import load_manifest
from house_mechanic.service_model import ServiceManifest
from vestigia_mcp.config import Settings
from vestigia_mcp.house_mechanic import DevActionFilter
from vestigia_mcp.server import create_server


TOKEN = "phase5-real-house-mechanic-token"


def _start_house_mechanic(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    recipes_path = tmp_path / "recipes.json"
    recipes_path.write_text(
        json.dumps(
            {
                "schema_version": "vestigia.house-mechanic.v0.1",
                "recipes": [
                    {
                        "id": "phase5.echo",
                        "description": "Phase 5 integration fixture",
                        "argv": [
                            sys.executable,
                            "-c",
                            "print('house-building-house')",
                        ],
                        "cwd": ".",
                        "timeout_seconds": 5,
                        "env_profile": "python",
                        "expected_exit_codes": [0],
                        "max_stdout_bytes": 4096,
                        "max_stderr_bytes": 4096,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    recipes = load_manifest(recipes_path, repo)
    token_path = tmp_path / "house-mechanic-token"
    token_path.write_text(TOKEN + "\n", encoding="utf-8")
    receipt_file = tmp_path / "house-mechanic-receipts.jsonl"
    mechanic = HouseMechanicServer(
        repo_root=repo,
        recipes=recipes,
        services=ServiceManifest({}),
        token_file=token_path,
        receipt_file=receipt_file,
        port=0,
    )
    thread = threading.Thread(target=mechanic.serve_forever, daemon=True)
    thread.start()
    return mechanic, thread, token_path, receipt_file


def _mcp_settings(
    tmp_path: Path,
    *,
    port: int,
    token_path: Path,
    action_filter: DevActionFilter,
    suffix: str,
) -> Settings:
    return Settings(
        live_archive_root=None,
        snapshot_archive_root=None,
        state_dir=tmp_path / f"mcp-state-{suffix}",
        deployment_id=f"phase5-{suffix}",
        house_mechanic_enabled=True,
        house_mechanic_host="127.0.0.1",
        house_mechanic_port=port,
        house_mechanic_token_path=token_path,
        house_mechanic_timeout_seconds=5,
        house_mechanic_max_response_bytes=65_536,
        dev_actions=action_filter,
    )


def test_real_house_mechanic_mutation_joins_receipts_and_allowlist_can_narrow(
    tmp_path: Path,
) -> None:
    mechanic, thread, token_path, receipt_file = _start_house_mechanic(tmp_path)

    async def exercise() -> None:
        wildcard = create_server(
            _mcp_settings(
                tmp_path,
                port=mechanic.server_address[1],
                token_path=token_path,
                action_filter=DevActionFilter(mode="wildcard", actions=()),
                suffix="wildcard-a",
            )
        )
        async with Client(wildcard) as client:
            caps = await client.call_tool("dev.capabilities", {})
            assert caps.is_error is False
            assert caps.structured_content is not None
            projected = caps.structured_content["projected_mutations"]
            assert "recipe.run" in projected
            assert projected["recipe.run"]["path"] == "/v1/run"
            assert projected["recipe.run"]["input_schema"]["required"] == [
                "recipe_id"
            ]

            called = await client.call_tool(
                "dev.call",
                {
                    "action": "recipe.run",
                    "arguments": {"recipe_id": "phase5.echo"},
                },
            )
            assert called.is_error is False
            assert called.structured_content is not None
            body = called.structured_content
            request_id = body["request_id"]
            result = body["result"]
            assert result["request_id"] == request_id
            assert result["execution_occurred"] is True
            assert result["receipt_persisted"] is True
            assert result["receipt"]["request_id"] == request_id
            assert result["receipt"]["stdout"].strip() == "house-building-house"
            receipt_id = result["durable_receipt_id"]

            mechanic_receipt = await client.call_tool(
                "dev.logs",
                {
                    "source": "receipts",
                    "receipt_id": receipt_id,
                },
            )
            assert mechanic_receipt.is_error is False
            assert mechanic_receipt.structured_content is not None
            receipt = mechanic_receipt.structured_content["receipt"]
            assert receipt["receipt_id"] == receipt_id
            assert receipt["request_id"] == request_id

            mcp_receipt = await client.call_tool(
                "receipts.recent",
                {
                    "capability": "dev.call",
                    "request_id": request_id,
                },
            )
            assert mcp_receipt.is_error is False
            assert mcp_receipt.structured_content is not None
            assert mcp_receipt.structured_content["matched_total"] == 1

        exact = create_server(
            _mcp_settings(
                tmp_path,
                port=mechanic.server_address[1],
                token_path=token_path,
                action_filter=DevActionFilter(
                    mode="exact",
                    actions=("service.start",),
                ),
                suffix="exact",
            )
        )
        async with Client(exact) as client:
            denied = await client.call_tool(
                "dev.call",
                {
                    "action": "recipe.run",
                    "arguments": {"recipe_id": "phase5.echo"},
                },
            )
            assert denied.is_error is True
            assert "not allowed" in str(denied.content)

        wildcard_again = create_server(
            _mcp_settings(
                tmp_path,
                port=mechanic.server_address[1],
                token_path=token_path,
                action_filter=DevActionFilter(mode="wildcard", actions=()),
                suffix="wildcard-b",
            )
        )
        async with Client(wildcard_again) as client:
            admitted = await client.call_tool(
                "dev.call",
                {
                    "action": "recipe.run",
                    "arguments": {"recipe_id": "phase5.echo"},
                },
            )
            assert admitted.is_error is False

    try:
        asyncio.run(exercise())
        rows = [
            json.loads(line)
            for line in receipt_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        run_request_ids = [
            row["request_id"]
            for row in rows
            if row.get("kind") == "recipe_run"
        ]
        assert len(run_request_ids) == 2
        assert all(value.startswith("mcp_req_") for value in run_request_ids)
    finally:
        mechanic.shutdown()
        mechanic.server_close()
        thread.join(timeout=2)
