import asyncio
from pathlib import Path

from mcp import Client

from vestigia.home import initialize_home
from vestigia_mcp.config import Settings
from vestigia_mcp.server import create_server


def test_mcp_reaches_runtime_observatory_without_copying_runtime_state(tmp_path: Path) -> None:
    runtime_home = initialize_home(
        tmp_path / "runtime-home", name="Test Resident", glyph="🏮"
    )
    live = tmp_path / "live"
    live.mkdir()
    server = create_server(
        Settings(
            live_archive_root=live,
            snapshot_archive_root=None,
            state_dir=tmp_path / "mcp-state",
            deployment_id="integration",
            runtime_home=runtime_home,
        )
    )

    async def exercise() -> None:
        async with Client(server) as client:
            projected = await client.call_tool(
                "runtime.capabilities", {"target": "bell.policy.preview"}
            )
            assert projected.is_error is False
            assert projected.structured_content is not None
            assert projected.structured_content["capabilities"][0]["name"] == "bell.policy.preview"
            assert projected.structured_content["authority"] == "runtime_capability_registry"

            called = await client.call_tool(
                "runtime.call",
                {
                    "action": "bell.policy.preview",
                    "arguments": {
                        "requested_policy": "auto",
                        "prompt": "Notice what wants attention.",
                        "purpose": "look_around",
                    },
                },
            )
            assert called.is_error is False
            assert called.structured_content is not None
            request_id = called.structured_content["request_id"]
            assert request_id.startswith("mcp_req_")
            assert called.structured_content["runtime"]["effective_policy"] == "field_scan_v1"

            trace = await client.call_tool("receipts.trace", {"request_id": request_id})
            assert trace.is_error is False
            assert trace.structured_content is not None
            assert trace.structured_content["matched_total"] == 1
            assert trace.structured_content["included_does_not_mean_caused"] is True

    asyncio.run(exercise())
