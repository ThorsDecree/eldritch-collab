from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading

from mcp import Client

import house_mechanic
from house_mechanic.api import HouseMechanicServer
from house_mechanic.model import load_manifest
from house_mechanic.service_model import ServiceManifest
from house_mechanic.tasking import load_repository_manifest
from vestigia_mcp.config import Settings
from vestigia_mcp.house_mechanic import DevActionFilter
from vestigia_mcp.server import create_server


TOKEN = "phase5-real-house-mechanic-token"

def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _start_house_mechanic(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "hello.txt").write_bytes(b"one\n")
    _git(repo, "add", "hello.txt")
    _git(repo, "commit", "-m", "initial")
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
    repositories_path = tmp_path / "repositories.json"
    repositories_path.write_text(
        json.dumps(
            {
                "schema_version": "vestigia.house-mechanic-repositories.v0.1",
                "repositories": [
                    {
                        "id": "fixture",
                        "path": ".",
                        "default_base_ref": "main",
                        "allowed_base_refs": ["main"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    repositories = load_repository_manifest(repositories_path, repo)
    token_path = tmp_path / "house-mechanic-token"
    token_path.write_text(TOKEN + "\n", encoding="utf-8")
    receipt_file = tmp_path / "house-mechanic-receipts.jsonl"
    mechanic = HouseMechanicServer(
        repo_root=repo,
        recipes=recipes,
        services=ServiceManifest({}),
        token_file=token_path,
        receipt_file=receipt_file,
        repositories=repositories,
        worktree_root=tmp_path / "worktrees",
        task_state_dir=tmp_path / "task-state",
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



def test_real_task_source_edit_loop_is_joined_and_allowlist_preserves_reads(
    tmp_path: Path,
) -> None:
    mechanic, thread, token_path, _ = _start_house_mechanic(tmp_path)
    state: dict[str, object] = {}

    async def exercise() -> None:
        wildcard = create_server(
            _mcp_settings(
                tmp_path,
                port=mechanic.server_address[1],
                token_path=token_path,
                action_filter=DevActionFilter(mode="wildcard", actions=()),
                suffix="source-loop",
            )
        )
        async with Client(wildcard) as client:
            caps = await client.call_tool("dev.capabilities", {})
            assert caps.is_error is False
            assert caps.structured_content is not None
            assert caps.structured_content["protocol"] == "vestigia.house-mechanic-api.v0.10"
            assert house_mechanic.__version__ == "0.12.0.dev0"
            assert "task.read" in caps.structured_content["projected_calls"]
            assert "task.diff" in caps.structured_content["projected_mutations"]
            assert "task.patch" in caps.structured_content["projected_mutations"]

            acquired = await client.call_tool(
                "dev.call",
                {
                    "action": "task.acquire",
                    "arguments": {
                        "repository_id": "fixture",
                        "holder_id": "vestigia",
                        "purpose": "prove source edit loop",
                    },
                },
            )
            assert acquired.is_error is False
            assert acquired.structured_content is not None
            task = acquired.structured_content["result"]["task"]
            task_id = task["task_id"]
            worktree = Path(task["worktree_path"])

            read = await client.call_tool(
                "dev.call",
                {
                    "action": "task.read",
                    "arguments": {
                        "task_id": task_id,
                        "holder_id": "vestigia",
                        "authority_generation": 1,
                        "paths": ["hello.txt"],
                    },
                },
            )
            assert read.is_error is False
            assert read.structured_content is not None
            read_result = read.structured_content["result"]
            assert read_result["items"][0]["text"] == "one\n"
            expected_hash = read_result["items"][0]["sha256"]
            assert expected_hash == hashlib.sha256(b"one\n").hexdigest()

            begun = await client.call_tool(
                "dev.call",
                {
                    "action": "iteration.begin",
                    "arguments": {
                        "task_id": task_id,
                        "holder_id": "vestigia",
                        "authority_generation": 1,
                    },
                },
            )
            assert begun.is_error is False
            assert begun.structured_content is not None
            iteration_id = begun.structured_content["result"]["task"]["current_iteration_id"]
            assert iteration_id

            proposed = await client.call_tool(
                "dev.call",
                {
                    "action": "task.diff",
                    "arguments": {
                        "task_id": task_id,
                        "holder_id": "vestigia",
                        "authority_generation": 1,
                        "iteration_id": iteration_id,
                        "mutations": [
                            {
                                "op": "modify",
                                "path": "hello.txt",
                                "expected_sha256": expected_hash,
                                "old": "one\n",
                                "new": "two\n",
                            },
                            {
                                "op": "create",
                                "path": "Vesti/new-machine.txt",
                                "expected_state": "absent",
                                "content": "built by the house\n",
                            },
                        ],
                    },
                },
            )
            assert proposed.is_error is False
            assert proposed.structured_content is not None
            proposal = proposed.structured_content["result"]
            assert (worktree / "hello.txt").read_text(encoding="utf-8") == "one\n"
            assert not (worktree / "Vesti" / "new-machine.txt").exists()

            patched = await client.call_tool(
                "dev.call",
                {
                    "action": "task.patch",
                    "arguments": {
                        "task_id": task_id,
                        "holder_id": "vestigia",
                        "authority_generation": 1,
                        "iteration_id": iteration_id,
                        "proposal_id": proposal["proposal_id"],
                        "proposal_digest": proposal["proposal_digest"],
                    },
                },
            )
            assert patched.is_error is False
            assert patched.structured_content is not None
            patch_body = patched.structured_content
            patch_request_id = patch_body["request_id"]
            patch_result = patch_body["result"]
            assert patch_result["request_id"] == patch_request_id
            assert (worktree / "hello.txt").read_text(encoding="utf-8") == "two\n"
            assert (worktree / "Vesti" / "new-machine.txt").read_text(
                encoding="utf-8"
            ) == "built by the house\n"

            mechanic_receipt = await client.call_tool(
                "dev.logs",
                {
                    "source": "receipts",
                    "receipt_id": patch_result["durable_receipt_id"],
                },
            )
            assert mechanic_receipt.is_error is False
            assert mechanic_receipt.structured_content is not None
            assert (
                mechanic_receipt.structured_content["receipt"]["request_id"]
                == patch_request_id
            )

            mcp_receipt = await client.call_tool(
                "receipts.recent",
                {
                    "capability": "dev.call",
                    "request_id": patch_request_id,
                },
            )
            assert mcp_receipt.is_error is False
            assert mcp_receipt.structured_content is not None
            assert mcp_receipt.structured_content["matched_total"] == 1

            checkpoint = await client.call_tool(
                "dev.call",
                {
                    "action": "iteration.checkpoint",
                    "arguments": {
                        "task_id": task_id,
                        "holder_id": "vestigia",
                        "authority_generation": 1,
                        "iteration_id": iteration_id,
                        "outcome": "pass",
                    },
                },
            )
            assert checkpoint.is_error is False
            assert checkpoint.structured_content is not None
            checkpoint_commit = checkpoint.structured_content["result"][
                "checkpoint_commit"
            ]
            assert checkpoint_commit
            assert _git(worktree, "status", "--porcelain=v1") == ""
            assert "Vesti/new-machine.txt" in _git(
                worktree, "ls-tree", "-r", "--name-only", checkpoint_commit
            ).splitlines()

            state.update(
                task_id=task_id,
                worktree=worktree,
                authority_generation=1,
            )

        exact = create_server(
            _mcp_settings(
                tmp_path,
                port=mechanic.server_address[1],
                token_path=token_path,
                action_filter=DevActionFilter(
                    mode="exact",
                    actions=("task.diff",),
                ),
                suffix="source-filter",
            )
        )
        async with Client(exact) as client:
            safe_read = await client.call_tool(
                "dev.call",
                {
                    "action": "task.read",
                    "arguments": {
                        "task_id": state["task_id"],
                        "holder_id": "vestigia",
                        "authority_generation": state["authority_generation"],
                        "paths": ["hello.txt"],
                    },
                },
            )
            assert safe_read.is_error is False

            denied_patch = await client.call_tool(
                "dev.call",
                {
                    "action": "task.patch",
                    "arguments": {
                        "task_id": state["task_id"],
                        "holder_id": "vestigia",
                        "authority_generation": state["authority_generation"],
                        "iteration_id": "not-current",
                        "proposal_id": "hm_patch_" + "0" * 32,
                        "proposal_digest": "0" * 64,
                    },
                },
            )
            assert denied_patch.is_error is True
            assert "not allowed" in str(denied_patch.content)

    try:
        asyncio.run(exercise())
    finally:
        mechanic.shutdown()
        mechanic.server_close()
        thread.join(timeout=2)
