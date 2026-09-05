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
    "runtime.status",
    "runtime.capabilities",
    "runtime.call",
    "receipts.recent",
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

            status_result = await client.call_tool("vestigia.status", {})
            assert status_result.is_error is False
            assert status_result.structured_content is not None
            assert status_result.structured_content["server"]["version"] == "0.2.0.dev0"
            assert status_result.structured_content["policy"]["capability_count"] == 12
            assert status_result.structured_content["runtime"]["configured"] is False

            receipt_result = await client.call_tool(
                "receipts.recent",
                {"limit": 10},
            )
            assert receipt_result.is_error is False
            assert receipt_result.structured_content is not None
            capabilities = {
                event["capability"]
                for event in receipt_result.structured_content["events"]
            }
            assert "archive.registry_status" in capabilities
            assert "archive.search_text" in capabilities
            assert "runtime.status" in capabilities
            assert "vestigia.status" in capabilities

    asyncio.run(exercise())
