from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
import subprocess
import sys
import threading

from house_mechanic.api import HouseMechanicServer, PROTOCOL
from house_mechanic.model import Manifest, Recipe
from house_mechanic.service_model import ServiceManifest
from house_mechanic.tasking import load_repository_manifest


TOKEN = "house-mechanic-task-api-token"


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


def _request(port: int, method: str, path: str, payload: dict | None = None):
    body = None
    headers = {"Authorization": f"Bearer {TOKEN}", "Connection": "close"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        conn.close()


def _request_headers_only(port: int, method: str, path: str, *, content_length: int):
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Connection": "close",
        "Content-Type": "application/json",
        "Content-Length": str(content_length),
    }
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.putrequest(method, path)
        for key, value in headers.items():
            conn.putheader(key, value)
        conn.endheaders()
        response = conn.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        conn.close()


def _server(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "hello.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "hello.txt")
    _git(repo, "commit", "-m", "initial")

    repositories_path = tmp_path / "repositories.json"
    repositories_path.write_text(
        json.dumps(
            {
                "schema_version": "vestigia.house-mechanic-repositories.v0.1",
                "repositories": [
                    {
                        "id": "fixture",
                        "path": "repo",
                        "default_base_ref": "main",
                        "allowed_base_refs": ["main"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    repositories = load_repository_manifest(repositories_path, tmp_path)
    token = tmp_path / "token"
    token.write_text(TOKEN + "\n", encoding="utf-8")
    receipt_file = tmp_path / "receipts.jsonl"

    server = HouseMechanicServer(
        repo_root=tmp_path,
        recipes=Manifest({}),
        services=ServiceManifest({}),
        token_file=token,
        receipt_file=receipt_file,
        repositories=repositories,
        worktree_root=tmp_path / "worktrees",
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, receipt_file


def test_authenticated_task_api_acquire_iterate_checkpoint_and_receipt(tmp_path: Path) -> None:
    server, thread, receipt_file = _server(tmp_path)
    port = server.server_address[1]
    try:
        status, caps = _request(port, "GET", "/v1/capabilities")
        assert status == 200
        assert caps["protocol"] == PROTOCOL
        assert caps["operations"]["task.acquire"]["enabled"] is True
        assert caps["operations"]["task.acquire"]["caller_supplies_worktree_path"] is False
        acquire_meta = caps["operations"]["task.acquire"]
        assert acquire_meta["mutation"] is True
        assert acquire_meta["method"] == "POST"
        assert acquire_meta["path"] == "/v1/task-acquire"
        assert acquire_meta["input_schema"]["type"] == "object"
        assert acquire_meta["input_schema"]["additionalProperties"] is False
        assert set(acquire_meta["input_schema"]["required"]) == {
            "repository_id",
            "holder_id",
            "purpose",
        }

        status, acquired = _request(
            port,
            "POST",
            "/v1/task-acquire",
            {
                "repository_id": "fixture",
                "holder_id": "liora",
                "purpose": "repair the thing",
                "iteration_limit": 2,
            },
        )
        assert status == 200
        assert acquired["receipt_persisted"] is True
        task = acquired["task"]
        task_id = task["task_id"]
        assert task["authority_generation"] == 1
        assert task["state"] == "active"
        worktree = Path(task["worktree_path"])
        assert worktree.is_dir()

        status, listed = _request(port, "GET", "/v1/tasks")
        assert status == 200
        assert [row["task_id"] for row in listed["tasks"]] == [task_id]

        status, stale = _request(
            port,
            "POST",
            "/v1/iteration-begin",
            {
                "task_id": task_id,
                "holder_id": "liora",
                "authority_generation": 2,
            },
        )
        assert status == 409
        assert stale["error"]["code"] == "stale_authority"

        status, begun = _request(
            port,
            "POST",
            "/v1/iteration-begin",
            {
                "task_id": task_id,
                "holder_id": "liora",
                "authority_generation": 1,
            },
        )
        assert status == 200
        iteration_id = begun["task"]["current_iteration_id"]
        assert iteration_id

        (worktree / "hello.txt").write_text("changed\n", encoding="utf-8")

        status, checkpoint = _request(
            port,
            "POST",
            "/v1/iteration-checkpoint",
            {
                "task_id": task_id,
                "holder_id": "liora",
                "authority_generation": 1,
                "iteration_id": iteration_id,
                "outcome": "fail",
            },
        )
        assert status == 200
        assert checkpoint["receipt_persisted"] is True
        assert checkpoint["checkpoint_commit"]
        assert checkpoint["git"]["dirty"] is False

        rows = [
            json.loads(line)
            for line in receipt_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        kinds = [row["kind"] for row in rows]
        assert "dev_task_transition" in kinds
        assert "dev_iteration_checkpoint" in kinds
        checkpoint_rows = [row for row in rows if row["kind"] == "dev_iteration_checkpoint"]
        assert checkpoint_rows[-1]["evidence"]["outcome"] == "fail"
        assert checkpoint_rows[-1]["evidence"]["checkpoint_commit"] == checkpoint["checkpoint_commit"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_task_api_handoff_requires_acceptance(tmp_path: Path) -> None:
    server, thread, _ = _server(tmp_path)
    port = server.server_address[1]
    try:
        _, acquired = _request(
            port,
            "POST",
            "/v1/task-acquire",
            {
                "repository_id": "fixture",
                "holder_id": "liora",
                "purpose": "handoff",
            },
        )
        task = acquired["task"]

        status, offered = _request(
            port,
            "POST",
            "/v1/handoff-offer",
            {
                "task_id": task["task_id"],
                "holder_id": "liora",
                "authority_generation": 1,
                "recipient_id": "vestigia",
            },
        )
        assert status == 200
        assert offered["task"]["holder_id"] == "liora"
        assert offered["task"]["state"] == "handoff_pending"

        status, accepted = _request(
            port,
            "POST",
            "/v1/handoff-respond",
            {
                "task_id": task["task_id"],
                "recipient_id": "vestigia",
                "accept": True,
            },
        )
        assert status == 200
        assert accepted["task"]["holder_id"] == "vestigia"
        assert accepted["task"]["authority_generation"] == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_task_routes_fail_closed_when_not_configured(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text(TOKEN + "\n", encoding="utf-8")
    server = HouseMechanicServer(
        repo_root=tmp_path,
        recipes=Manifest({}),
        services=ServiceManifest({}),
        token_file=token,
        receipt_file=tmp_path / "receipts.jsonl",
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, response = _request(server.server_address[1], "GET", "/v1/tasks")
        assert status == 503
        assert response["error"]["code"] == "tasking_not_configured"

        status, caps = _request(server.server_address[1], "GET", "/v1/capabilities")
        assert status == 200
        assert caps["operations"]["task.run_recipe"]["enabled"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_task_api_renews_extends_budget_and_refreshes_base(tmp_path: Path) -> None:
    server, thread, receipt_file = _server(tmp_path)
    port = server.server_address[1]
    repo = tmp_path / "repo"
    try:
        status, acquired = _request(
            port,
            "POST",
            "/v1/task-acquire",
            {
                "repository_id": "fixture",
                "holder_id": "liora",
                "purpose": "refresh api",
                "iteration_limit": 1,
            },
        )
        assert status == 200
        task = acquired["task"]
        task_id = task["task_id"]
        worktree = Path(task["worktree_path"])

        status, renewed = _request(
            port,
            "POST",
            "/v1/task-renew",
            {
                "task_id": task_id,
                "holder_id": "liora",
                "authority_generation": 1,
                "lease_seconds": 7200,
            },
        )
        assert status == 200
        assert renewed["receipt_persisted"] is True
        assert renewed["task"]["authority_generation"] == 1

        status, begun = _request(
            port,
            "POST",
            "/v1/iteration-begin",
            {
                "task_id": task_id,
                "holder_id": "liora",
                "authority_generation": 1,
            },
        )
        assert status == 200
        status, checkpoint = _request(
            port,
            "POST",
            "/v1/iteration-checkpoint",
            {
                "task_id": task_id,
                "holder_id": "liora",
                "authority_generation": 1,
                "iteration_id": begun["task"]["current_iteration_id"],
                "outcome": "not_run",
            },
        )
        assert status == 200
        assert checkpoint["task"]["state"] == "paused_budget_exhausted"

        status, extended = _request(
            port,
            "POST",
            "/v1/task-extend-budget",
            {
                "task_id": task_id,
                "holder_id": "liora",
                "authority_generation": 1,
                "additional_iterations": 2,
            },
        )
        assert status == 200
        assert extended["task"]["state"] == "active"
        assert extended["task"]["iteration_limit"] == 3

        (worktree / "task.txt").write_text("task\n", encoding="utf-8")
        _git(worktree, "add", "task.txt")
        _git(worktree, "commit", "-m", "task change")
        (repo / "upstream.txt").write_text("upstream\n", encoding="utf-8")
        _git(repo, "add", "upstream.txt")
        _git(repo, "commit", "-m", "upstream change")
        new_base = _git(repo, "rev-parse", "HEAD")

        status, refreshed = _request(
            port,
            "POST",
            "/v1/task-refresh-base",
            {
                "task_id": task_id,
                "holder_id": "liora",
                "authority_generation": 1,
                "base_ref": "main",
            },
        )
        assert status == 200
        assert refreshed["refresh_succeeded"] is True
        assert refreshed["candidate_base_commit"] == new_base
        assert refreshed["task"]["current_base_commit"] == new_base
        assert refreshed["task"]["authority_generation"] == 2
        assert refreshed["git"]["dirty"] is False

        rows = [
            json.loads(line)
            for line in receipt_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        operations = [
            row["evidence"].get("operation")
            for row in rows
            if row["kind"] == "dev_task_transition"
        ]
        assert "renew" in operations
        assert "extend_budget" in operations
        assert "refresh_base" in operations
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_task_source_operations_are_fixed_typed_routes_with_receipts(tmp_path: Path) -> None:
    server, thread, receipt_file = _server(tmp_path)
    port = server.server_address[1]
    try:
        status, caps = _request(port, "GET", "/v1/capabilities")
        assert status == 200
        operations = caps["operations"]

        read_meta = operations["task.read"]
        assert read_meta["enabled"] is True
        assert read_meta["mutation"] is False
        assert read_meta["effect"] == "bounded_worktree_read"
        assert read_meta["method"] == "POST"
        assert read_meta["path"] == "/v1/task-read"
        assert read_meta["input_schema"]["additionalProperties"] is False
        assert set(read_meta["input_schema"]["required"]) == {
            "task_id", "holder_id", "authority_generation", "paths"
        }

        diff_meta = operations["task.diff"]
        assert diff_meta["enabled"] is True
        assert diff_meta["mutation"] is True
        assert diff_meta["effect"] == "bounded_worktree_patch_proposal"
        assert diff_meta["path"] == "/v1/task-diff"
        assert diff_meta["input_schema"]["additionalProperties"] is False

        patch_meta = operations["task.patch"]
        assert patch_meta["enabled"] is True
        assert patch_meta["mutation"] is True
        assert patch_meta["effect"] == "bounded_worktree_source_mutation"
        assert patch_meta["path"] == "/v1/task-patch"
        assert patch_meta["input_schema"]["additionalProperties"] is False

        _, acquired = _request(
            port,
            "POST",
            "/v1/task-acquire",
            {
                "repository_id": "fixture",
                "holder_id": "vestigia",
                "purpose": "source api",
                "iteration_limit": 2,
            },
        )
        task = acquired["task"]
        task_id = task["task_id"]
        worktree = Path(task["worktree_path"])

        status, read = _request(
            port,
            "POST",
            "/v1/task-read",
            {
                "task_id": task_id,
                "holder_id": "vestigia",
                "authority_generation": 1,
                "paths": ["hello.txt"],
            },
        )
        assert status == 200
        assert read["receipt_persisted"] is True
        assert read["items"][0]["path"] == "hello.txt"
        assert read["items"][0]["text"] == (worktree / "hello.txt").read_bytes().decode("utf-8")

        status, begun = _request(
            port,
            "POST",
            "/v1/iteration-begin",
            {
                "task_id": task_id,
                "holder_id": "vestigia",
                "authority_generation": 1,
            },
        )
        assert status == 200
        iteration_id = begun["task"]["current_iteration_id"]
        before = (worktree / "hello.txt").read_bytes()
        before_text = before.decode("utf-8")
        import hashlib

        status, diff = _request(
            port,
            "POST",
            "/v1/task-diff",
            {
                "task_id": task_id,
                "holder_id": "vestigia",
                "authority_generation": 1,
                "iteration_id": iteration_id,
                "mutations": [
                    {
                        "op": "modify",
                        "path": "hello.txt",
                        "expected_sha256": hashlib.sha256(before).hexdigest(),
                        "old": before_text,
                        "new": before_text.replace("one", "ONE", 1),
                    },
                    {
                        "op": "create",
                        "path": "Vesti/api_created.txt",
                        "expected_state": "absent",
                        "content": "created through task.diff\n",
                    },
                ],
            },
        )
        assert status == 200
        assert diff["receipt_persisted"] is True
        assert diff["state"] == "ready"
        assert (worktree / "hello.txt").read_bytes() == before
        assert not (worktree / "Vesti" / "api_created.txt").exists()

        status, bad = _request(
            port,
            "POST",
            "/v1/task-patch",
            {
                "task_id": task_id,
                "holder_id": "vestigia",
                "authority_generation": 1,
                "iteration_id": iteration_id,
                "proposal_id": diff["proposal_id"],
                "proposal_digest": "0" * 64,
            },
        )
        assert status == 409
        assert bad["error"]["code"] == "proposal_digest_mismatch"

        status, patched = _request(
            port,
            "POST",
            "/v1/task-patch",
            {
                "task_id": task_id,
                "holder_id": "vestigia",
                "authority_generation": 1,
                "iteration_id": iteration_id,
                "proposal_id": diff["proposal_id"],
                "proposal_digest": diff["proposal_digest"],
            },
        )
        assert status == 200
        assert patched["receipt_persisted"] is True
        assert patched["state"] == "consumed"
        assert (worktree / "Vesti" / "api_created.txt").is_file()

        rows = [
            json.loads(line)
            for line in receipt_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        source_rows = [row for row in rows if row["kind"] == "dev_source_operation"]
        assert [row["evidence"]["operation"] for row in source_rows] == [
            "task.read", "task.diff", "task.patch"
        ]
        read_evidence = source_rows[0]["evidence"]
        assert read_evidence["paths"] == ["hello.txt"]
        assert "text" not in json.dumps(read_evidence)
        diff_evidence = source_rows[1]["evidence"]
        assert diff_evidence["proposal_id"] == diff["proposal_id"]
        assert diff_evidence["proposal_digest"] == diff["proposal_digest"]
        patch_evidence = source_rows[2]["evidence"]
        assert patch_evidence["proposal_id"] == diff["proposal_id"]
        assert all("post_sha256" in item for item in patch_evidence["mutations"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_task_diff_request_ceiling_covers_escaped_patch_payload() -> None:
    from house_mechanic.api import MAX_SOURCE_DIFF_REQUEST_BYTES

    assert MAX_SOURCE_DIFF_REQUEST_BYTES >= (6 * 4_194_304) + 1_048_576


def test_task_diff_has_larger_route_specific_request_ceiling(tmp_path: Path) -> None:
    server, thread, _ = _server(tmp_path)
    port = server.server_address[1]
    try:
        _, acquired = _request(
            port,
            "POST",
            "/v1/task-acquire",
            {
                "repository_id": "fixture",
                "holder_id": "vestigia",
                "purpose": "large diff api",
            },
        )
        task = acquired["task"]
        _, begun = _request(
            port,
            "POST",
            "/v1/iteration-begin",
            {
                "task_id": task["task_id"],
                "holder_id": "vestigia",
                "authority_generation": 1,
            },
        )
        iteration_id = begun["task"]["current_iteration_id"]

        status, large_diff = _request(
            port,
            "POST",
            "/v1/task-diff",
            {
                "task_id": task["task_id"],
                "holder_id": "vestigia",
                "authority_generation": 1,
                "iteration_id": iteration_id,
                "mutations": [
                    {
                        "op": "create",
                        "path": "Vesti/large-but-bounded.txt",
                        "expected_state": "absent",
                        "content": "x" * 20_000,
                    }
                ],
            },
        )
        assert status == 200
        assert large_diff["state"] == "ready"

        status, ordinary = _request(
            port,
            "POST",
            "/v1/task-show",
            {"task_id": task["task_id"], "padding": "x" * 20_000},
        )
        assert status == 413
        assert ordinary["error"]["code"] == "request_too_large"

        status, oversized = _request_headers_only(
            port,
            "POST",
            "/v1/task-diff",
            content_length=(32 * 1024 * 1024) + 1,
        )
        assert status == 413
        assert oversized["error"]["code"] == "request_too_large"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_task_run_recipe_capability_is_fixed_and_closed(tmp_path: Path) -> None:
    server, thread, _ = _server(tmp_path)
    try:
        status, caps = _request(server.server_address[1], "GET", "/v1/capabilities")
        assert status == 200
        meta = caps["operations"]["task.run_recipe"]
        assert meta["enabled"] is True
        assert meta["mutation"] is True
        assert meta["effect"] == "bounded_task_local_process"
        assert meta["method"] == "POST"
        assert meta["path"] == "/v1/task-run-recipe"
        assert meta["input_schema"]["additionalProperties"] is False
        assert set(meta["input_schema"]["required"]) == {
            "task_id", "holder_id", "authority_generation", "recipe_id"
        }
        assert meta["requires_open_iteration"] is False
        assert meta["caller_supplies_argv"] is False
        assert meta["caller_supplies_cwd"] is False
        assert meta["caller_supplies_env"] is False
        assert meta["caller_supplies_worktree_path"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_task_run_recipe_uses_task_worktree_without_open_iteration(tmp_path: Path) -> None:
    server, thread, receipt_file = _server(tmp_path)
    server.recipes = Manifest(
        {
            "fixture.read": Recipe(
                id="fixture.read",
                description="read task-local hello",
                argv=(
                    sys.executable,
                    "-c",
                    "from pathlib import Path; print(Path('hello.txt').read_text().strip())",
                ),
                cwd="repo",
                timeout_seconds=5,
                env_profile="python",
                expected_exit_codes=(0,),
                max_stdout_bytes=4096,
                max_stderr_bytes=4096,
            )
        }
    )
    port = server.server_address[1]
    try:
        status, acquired = _request(
            port,
            "POST",
            "/v1/task-acquire",
            {
                "repository_id": "fixture",
                "holder_id": "vestigia",
                "purpose": "task recipe root isolation",
            },
        )
        assert status == 200
        task = acquired["task"]
        worktree = Path(task["worktree_path"])
        (worktree / "hello.txt").write_bytes(b"two\n")

        status, task_run = _request(
            port,
            "POST",
            "/v1/task-run-recipe",
            {
                "task_id": task["task_id"],
                "holder_id": "vestigia",
                "authority_generation": 1,
                "recipe_id": "fixture.read",
            },
        )
        assert status == 200
        assert task_run["execution_occurred"] is True
        assert task_run["receipt_persisted"] is True
        assert task_run["iteration_id"] is None
        assert task_run["receipt"]["stdout"].strip() == "two"

        status, generic = _request(
            port, "POST", "/v1/run", {"recipe_id": "fixture.read"}
        )
        assert status == 200
        assert generic["receipt"]["stdout"].strip() == "one"

        rows = [json.loads(line) for line in receipt_file.read_text(encoding="utf-8").splitlines()]
        task_receipt = next(row for row in reversed(rows) if row["kind"] == "task_recipe_run")
        assert task_receipt["evidence"]["iteration_id"] is None
        assert task_receipt["evidence"]["source_dirty"] is True
        assert "worktree_path" not in task_receipt["evidence"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _set_task_recipe(server: HouseMechanicServer, *, cwd: str = "repo", recipe_id: str = "fixture.read", code: str | None = None) -> None:
    command = code or "from pathlib import Path; print(Path('hello.txt').read_text().strip())"
    server.recipes = Manifest(
        {
            recipe_id: Recipe(
                id=recipe_id,
                description="task recipe fixture",
                argv=(sys.executable, "-c", command),
                cwd=cwd,
                timeout_seconds=5,
                env_profile="python",
                expected_exit_codes=(0,),
                max_stdout_bytes=4096,
                max_stderr_bytes=4096,
            )
        }
    )


def _acquire_task(port: int, purpose: str = "task recipe fixture") -> dict:
    status, acquired = _request(
        port,
        "POST",
        "/v1/task-acquire",
        {
            "repository_id": "fixture",
            "holder_id": "vestigia",
            "purpose": purpose,
        },
    )
    assert status == 200
    return acquired["task"]


def test_task_run_recipe_records_iteration_and_clean_checkpoint_state(tmp_path: Path) -> None:
    server, thread, receipt_file = _server(tmp_path)
    _set_task_recipe(server)
    port = server.server_address[1]
    try:
        task = _acquire_task(port, "task recipe checkpoint provenance")
        worktree = Path(task["worktree_path"])
        status, begun = _request(
            port,
            "POST",
            "/v1/iteration-begin",
            {
                "task_id": task["task_id"],
                "holder_id": "vestigia",
                "authority_generation": 1,
            },
        )
        assert status == 200
        iteration_id = begun["task"]["current_iteration_id"]
        (worktree / "hello.txt").write_bytes(b"two\n")

        status, dirty_run = _request(
            port,
            "POST",
            "/v1/task-run-recipe",
            {
                "task_id": task["task_id"],
                "holder_id": "vestigia",
                "authority_generation": 1,
                "recipe_id": "fixture.read",
            },
        )
        assert status == 200
        assert dirty_run["iteration_id"] == iteration_id
        dirty_receipt = next(
            row
            for row in reversed([json.loads(line) for line in receipt_file.read_text(encoding="utf-8").splitlines()])
            if row["receipt_id"] == dirty_run["durable_receipt_id"]
        )
        assert dirty_receipt["evidence"]["iteration_id"] == iteration_id
        assert dirty_receipt["evidence"]["source_dirty"] is True

        status, checkpoint = _request(
            port,
            "POST",
            "/v1/iteration-checkpoint",
            {
                "task_id": task["task_id"],
                "holder_id": "vestigia",
                "authority_generation": 1,
                "iteration_id": iteration_id,
                "outcome": "pass",
            },
        )
        assert status == 200
        checkpoint_commit = checkpoint["checkpoint_commit"]

        status, clean_run = _request(
            port,
            "POST",
            "/v1/task-run-recipe",
            {
                "task_id": task["task_id"],
                "holder_id": "vestigia",
                "authority_generation": 1,
                "recipe_id": "fixture.read",
            },
        )
        assert status == 200
        assert clean_run["iteration_id"] is None
        assert clean_run["receipt"]["source_dirty"] is False
        assert clean_run["receipt"]["source_commit"] == checkpoint_commit
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_task_run_recipe_rejects_wrong_holder_before_execution(tmp_path: Path) -> None:
    server, thread, _ = _server(tmp_path)
    _set_task_recipe(
        server,
        recipe_id="fixture.write",
        code="from pathlib import Path; Path('ran.txt').write_text('ran')",
    )
    port = server.server_address[1]
    try:
        task = _acquire_task(port, "wrong holder")
        worktree = Path(task["worktree_path"])
        status, response = _request(
            port, "POST", "/v1/task-run-recipe",
            {
                "task_id": task["task_id"],
                "holder_id": "anima",
                "authority_generation": 1,
                "recipe_id": "fixture.write",
            },
        )
        assert status == 403
        assert response["error"]["code"] == "wrong_holder"
        assert not (worktree / "ran.txt").exists()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_task_run_recipe_rejects_stale_generation_before_execution(tmp_path: Path) -> None:
    server, thread, _ = _server(tmp_path)
    _set_task_recipe(server, recipe_id="fixture.write", code="from pathlib import Path; Path('ran.txt').write_text('ran')")
    port = server.server_address[1]
    try:
        task = _acquire_task(port, "stale generation")
        worktree = Path(task["worktree_path"])
        status, response = _request(
            port, "POST", "/v1/task-run-recipe",
            {
                "task_id": task["task_id"], "holder_id": "vestigia",
                "authority_generation": 9, "recipe_id": "fixture.write",
            },
        )
        assert status == 409
        assert response["error"]["code"] == "stale_authority"
        assert not (worktree / "ran.txt").exists()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_task_run_recipe_rejects_expired_lease_before_execution(tmp_path: Path) -> None:
    server, thread, _ = _server(tmp_path)
    _set_task_recipe(server, recipe_id="fixture.write", code="from pathlib import Path; Path('ran.txt').write_text('ran')")
    port = server.server_address[1]
    try:
        task = _acquire_task(port, "expired lease")
        record = server.tasks.ledger.get(task["task_id"])
        record.lease_expires_at = "2000-01-01T00:00:00+00:00"
        server.tasks.ledger.save(record)
        worktree = Path(task["worktree_path"])
        status, response = _request(
            port, "POST", "/v1/task-run-recipe",
            {
                "task_id": task["task_id"], "holder_id": "vestigia",
                "authority_generation": 1, "recipe_id": "fixture.write",
            },
        )
        assert status == 409
        assert response["error"]["code"] == "lease_expired"
        assert not (worktree / "ran.txt").exists()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_task_run_recipe_rejects_non_active_task_before_execution(tmp_path: Path) -> None:
    server, thread, _ = _server(tmp_path)
    _set_task_recipe(server, recipe_id="fixture.write", code="from pathlib import Path; Path('ran.txt').write_text('ran')")
    port = server.server_address[1]
    try:
        task = _acquire_task(port, "non active")
        record = server.tasks.ledger.get(task["task_id"])
        record.state = "succeeded"
        server.tasks.ledger.save(record)
        worktree = Path(task["worktree_path"])
        status, response = _request(
            port, "POST", "/v1/task-run-recipe",
            {
                "task_id": task["task_id"], "holder_id": "vestigia",
                "authority_generation": 1, "recipe_id": "fixture.write",
            },
        )
        assert status == 409
        assert response["error"]["code"] == "task_not_active"
        assert not (worktree / "ran.txt").exists()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_task_run_recipe_rejects_handoff_pending_before_execution(tmp_path: Path) -> None:
    server, thread, _ = _server(tmp_path)
    _set_task_recipe(server, recipe_id="fixture.write", code="from pathlib import Path; Path('ran.txt').write_text('ran')")
    port = server.server_address[1]
    try:
        task = _acquire_task(port, "handoff pending")
        record = server.tasks.ledger.get(task["task_id"])
        record.state = "handoff_pending"
        record.pending_handoff_to = "anima"
        server.tasks.ledger.save(record)
        worktree = Path(task["worktree_path"])
        status, response = _request(
            port, "POST", "/v1/task-run-recipe",
            {
                "task_id": task["task_id"], "holder_id": "vestigia",
                "authority_generation": 1, "recipe_id": "fixture.write",
            },
        )
        assert status == 409
        assert response["error"]["code"] == "task_not_active"
        assert not (worktree / "ran.txt").exists()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_task_run_recipe_rejects_branch_mismatch_before_execution(tmp_path: Path) -> None:
    server, thread, _ = _server(tmp_path)
    _set_task_recipe(server, recipe_id="fixture.write", code="from pathlib import Path; Path('ran.txt').write_text('ran')")
    port = server.server_address[1]
    try:
        task = _acquire_task(port, "branch mismatch")
        record = server.tasks.ledger.get(task["task_id"])
        record.branch_name = "hm/not-the-issued-branch"
        server.tasks.ledger.save(record)
        worktree = Path(task["worktree_path"])
        status, response = _request(
            port, "POST", "/v1/task-run-recipe",
            {
                "task_id": task["task_id"], "holder_id": "vestigia",
                "authority_generation": 1, "recipe_id": "fixture.write",
            },
        )
        assert status == 409
        assert response["error"]["code"] == "branch_mismatch"
        assert not (worktree / "ran.txt").exists()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_task_run_recipe_rejects_unknown_and_lifecycle_recipes(tmp_path: Path) -> None:
    server, thread, _ = _server(tmp_path)
    _set_task_recipe(server, recipe_id="fixture.lifecycle")
    server.lifecycle_recipe_ids.add("fixture.lifecycle")
    port = server.server_address[1]
    try:
        task = _acquire_task(port, "recipe admission")
        base = {
            "task_id": task["task_id"], "holder_id": "vestigia", "authority_generation": 1
        }
        status, unknown = _request(
            port, "POST", "/v1/task-run-recipe", {**base, "recipe_id": "missing"}
        )
        assert status == 404
        assert unknown["error"]["code"] == "unknown_recipe"
        status, lifecycle = _request(
            port, "POST", "/v1/task-run-recipe", {**base, "recipe_id": "fixture.lifecycle"}
        )
        assert status == 403
        assert lifecycle["error"]["code"] == "lifecycle_recipe_reserved"
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_task_run_recipe_rejects_recipe_cwd_outside_selected_repository(tmp_path: Path) -> None:
    server, thread, _ = _server(tmp_path)
    _set_task_recipe(server, cwd=".")
    port = server.server_address[1]
    try:
        task = _acquire_task(port, "cwd boundary")
        status, response = _request(
            port, "POST", "/v1/task-run-recipe",
            {
                "task_id": task["task_id"], "holder_id": "vestigia",
                "authority_generation": 1, "recipe_id": "fixture.read",
            },
        )
        assert status == 409
        assert response["error"]["code"] == "path_outside_repository"
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_task_run_recipe_rejects_busy_slot(tmp_path: Path) -> None:
    server, thread, _ = _server(tmp_path)
    _set_task_recipe(server)
    port = server.server_address[1]
    try:
        task = _acquire_task(port, "busy slot")
        assert server.run_slots.acquire(blocking=False) is True
        try:
            status, response = _request(
                port, "POST", "/v1/task-run-recipe",
                {
                    "task_id": task["task_id"], "holder_id": "vestigia",
                    "authority_generation": 1, "recipe_id": "fixture.read",
                },
            )
        finally:
            server.run_slots.release()
        assert status == 409
        assert response["error"]["code"] == "busy"
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_task_run_recipe_rejects_caller_supplied_execution_fields(tmp_path: Path) -> None:
    server, thread, _ = _server(tmp_path)
    _set_task_recipe(server)
    port = server.server_address[1]
    try:
        task = _acquire_task(port, "closed schema")
        status, response = _request(
            port, "POST", "/v1/task-run-recipe",
            {
                "task_id": task["task_id"], "holder_id": "vestigia",
                "authority_generation": 1, "recipe_id": "fixture.read",
                "cwd": ".",
            },
        )
        assert status == 400
        assert response["error"]["code"] == "invalid_request"
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_task_run_recipe_receipt_failure_reports_completed_execution(tmp_path: Path) -> None:
    server, thread, _ = _server(tmp_path)
    _set_task_recipe(
        server,
        recipe_id="fixture.count",
        code=(
            "from pathlib import Path; "
            "p=Path('count.txt'); "
            "n=int(p.read_text()) if p.exists() else 0; "
            "p.write_text(str(n+1)); print('ok')"
        ),
    )
    port = server.server_address[1]
    try:
        task = _acquire_task(port, "receipt failure")
        worktree = Path(task["worktree_path"])

        def fail_receipt(*args, **kwargs):
            raise RuntimeError("receipt write failed")

        server.receipts.append_task_run = fail_receipt
        status, response = _request(
            port, "POST", "/v1/task-run-recipe",
            {
                "task_id": task["task_id"], "holder_id": "vestigia",
                "authority_generation": 1, "recipe_id": "fixture.count",
            },
        )
        assert status == 500
        assert response["execution_occurred"] is True
        assert response["receipt_persisted"] is False
        assert response["receipt"]["exit_code"] == 0
        assert response["error"]["code"] == "receipt_persistence_failed"
        assert (worktree / "count.txt").read_text(encoding="utf-8") == "1"
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
