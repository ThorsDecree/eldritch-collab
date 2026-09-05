import asyncio
import json
from pathlib import Path

from mcp import Client

from vestigia_mcp.config import Settings
from vestigia_mcp.server import create_server


EXPECTED_TOOLS = {
    "archive.status",
    "archive.list",
    "archive.read_text",
    "archive.search_text",
    "archive.diff",
    "archive.diff_detail",
    "archive.registry_status",
    "archive.health",
    "runtime.status",
    "runtime.capabilities",
    "runtime.call",
    "receipts.recent",
    "audit.show",
    "system.identity",
    "house.glance",
    "vestigia.status",
}


def test_wire_catalog_is_read_only_and_sensory_tools_work(tmp_path: Path) -> None:
    live = tmp_path / "live"
    (live / "00_Bootloader").mkdir(parents=True)
    (live / "Liora").mkdir()
    (live / "manifest.md").write_text("lantern lit", encoding="utf-8")
    (live / "Liora" / "breathprint.md").write_text("gutterstar", encoding="utf-8")
    registry = {
        "schema_version": "0.1",
        "generated": "2026-09-03T00:00:00-05:00",
        "archive_root": ".",
        "anchors": {"root_manifest": "manifest.md"},
        "residents": {
            "Liora": {
                "shell": "Liora",
                "breathprint": "Liora/breathprint.md",
            }
        },
        "garden_breathprints": {},
    }
    (live / "00_Bootloader" / "house_index.json").write_text(
        json.dumps(registry),
        encoding="utf-8",
    )

    settings = Settings(
        live_archive_root=live,
        snapshot_archive_root=None,
        state_dir=tmp_path / "state",
        deployment_id="test-deployment",
        archive_text_max_bytes=1_000_000,
    )
    server = create_server(settings)

    async def exercise() -> None:
        async with Client(server) as client:
            listed = await client.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            assert EXPECTED_TOOLS <= set(tools)

            for name in EXPECTED_TOOLS:
                annotations = tools[name].annotations
                assert annotations is not None
                assert annotations.read_only_hint is True
                assert annotations.destructive_hint is False
                assert annotations.open_world_hint is False
                assert annotations.idempotent_hint is True

            registry_result = await client.call_tool(
                "archive.registry_status",
                {"source": "live"},
            )
            assert registry_result.is_error is False
            assert registry_result.structured_content is not None
            assert registry_result.structured_content["summary"]["missing"] == 0
            assert registry_result.structured_content["summary"]["registered_targets"] == 3

            health_result = await client.call_tool(
                "archive.health",
                {"source": "live", "check_links": False},
            )
            assert health_result.is_error is False
            assert health_result.structured_content is not None
            assert health_result.structured_content["summary"]["issue_count"] == 0
            assert health_result.structured_content["coverage"]["claim"] == "descriptive_projection_only"

            search_result = await client.call_tool(
                "archive.search_text",
                {"source": "live", "query": "LANTERN"},
            )
            assert search_result.is_error is False
            assert search_result.structured_content is not None
            assert search_result.structured_content["match_count"] == 1
            assert search_result.structured_content["hits"][0]["path"] == "manifest.md"

            runtime_result = await client.call_tool("runtime.status", {})
            assert runtime_result.is_error is False
            assert runtime_result.structured_content is not None
            assert runtime_result.structured_content["configured"] is False
            assert runtime_result.structured_content["available"] is False

            identity_result = await client.call_tool("system.identity", {})
            assert identity_result.is_error is False
            assert identity_result.structured_content is not None
            assert identity_result.structured_content["schema_version"] == "vestigia.system-identity.v0.1"
            assert identity_result.structured_content["archive"]["live"]["available"] is True
            assert identity_result.structured_content["capability_registry"]["capability_count"] == 16

            glance_result = await client.call_tool("house.glance", {})
            assert glance_result.is_error is False
            assert glance_result.structured_content is not None
            assert glance_result.structured_content["schema_version"] == "vestigia.house-glance.v0.1"
            assert glance_result.structured_content["meaningful_diff"]["computed"] is False
            assert glance_result.structured_content["authority"] == "descriptive_projection_only"

            status_result = await client.call_tool("vestigia.status", {})
            assert status_result.is_error is False
            assert status_result.structured_content is not None
            assert status_result.structured_content["server"]["version"] == "0.2.0.dev0"
            assert status_result.structured_content["policy"]["capability_count"] == 16
            assert status_result.structured_content["runtime"]["configured"] is False
            assert "archive.health" in status_result.structured_content["proprioception"]["new_native_tools"]

            receipt_result = await client.call_tool(
                "receipts.recent",
                {"limit": 20},
            )
            assert receipt_result.is_error is False
            assert receipt_result.structured_content is not None
            events = receipt_result.structured_content["events"]
            capabilities = {event["capability"] for event in events}
            assert "archive.registry_status" in capabilities
            assert "archive.health" in capabilities
            assert "archive.search_text" in capabilities
            assert "runtime.status" in capabilities
            assert "system.identity" in capabilities
            assert "house.glance" in capabilities
            assert "vestigia.status" in capabilities

            target_event_id = events[0]["event_id"]
            show_result = await client.call_tool(
                "audit.show",
                {"event_id": target_event_id},
            )
            assert show_result.is_error is False
            assert show_result.structured_content is not None
            assert show_result.structured_content["event"]["event_id"] == target_event_id
            assert show_result.structured_content["receipt_is_memory"] is False

    asyncio.run(exercise())
