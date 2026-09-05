from __future__ import annotations

from pathlib import Path

import pytest

from vestigia_mcp.adapters.runtime import RuntimeBridge, RuntimeBridgeError


def make_home(path: Path) -> Path:
    path.mkdir()
    (path / "home.yaml").write_text(
        """resident:\n  id: test-resident\n  name: Test Resident\nroom:\n  id: test-room\n  name: Test Room\n  active_resident_ids: [test-resident]\n  participant_ids: [test-resident, local-user]\n""",
        encoding="utf-8",
    )
    return path


def test_configured_runtime_bridge_projects_and_dispatches_reads(tmp_path: Path) -> None:
    bridge = RuntimeBridge(
        make_home(tmp_path / "home"),
        None,
        deployment_id="mcp-test",
    )

    status = bridge.status()
    assert status["configured"] is True
    assert status["available"] is True
    assert status["resident_id"] == "test-resident"
    assert status["room_id"] == "test-room"
    assert int(status["projected_capability_count"]) > 0
    assert len(str(status["capability_digest_sha256"])) == 64

    capabilities = bridge.capabilities()
    names = {item["name"] for item in capabilities["capabilities"]}
    assert "status" in names
    assert "file.write" not in names
    assert "discord.react" not in names

    result = bridge.call(
        action="status",
        arguments={},
        request_id="req_integration",
    )
    assert result["request_id"] == "req_integration"
    assert result["runtime"]["ok"] is True
    assert result["runtime"]["action"] == "status"
    assert result["runtime"]["receipt_id"]

    with pytest.raises(RuntimeBridgeError):
        bridge.call(
            action="file.write",
            arguments={"path": "workspace/nope.md", "content": "nope"},
            request_id="req_denied",
        )
