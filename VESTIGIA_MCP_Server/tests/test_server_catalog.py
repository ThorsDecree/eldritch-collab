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
    "archive.read_media",
    "archive.search_text",
    "archive.diff",
    "archive.diff_detail",
    "archive.registry_status",
    "archive.health",
    "archive.write_capabilities",
    "archive.stage_text",
    "archive.stage_directory",
    "archive.stage_list",
    "archive.stage_inspect",
    "archive.stage_discard",
    "archive.promote",
    "archive.promote_directory",
    "mount.status",
    "mount.list",
    "mount.read_text",
    "mount.read_media",
    "mount.search_text",
    "runtime.list",
    "runtime.status",
    "runtime.capabilities",
    "runtime.call",
    "runtime.write_capabilities",
    "runtime.write",
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
    (live / "portrait.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"wire-fixture"
    )
    (live / "Liora" / "breathprint.md").write_text("gutterstar", encoding="utf-8")
    external = tmp_path / "external"
    external.mkdir()
    (external / "outside.md").write_text("external lantern", encoding="utf-8")
    (external / "outside.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"external-fixture"
    )
    mounts_file = tmp_path / "mounts.json"
    mounts_file.write_text(
        json.dumps(
            {
                "schema_version": "vestigia.mounts.v0.1",
                "mounts": [
                    {"id": "outside", "root": str(external), "access": "read"}
                ],
            }
        ),
        encoding="utf-8",
    )
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
        archive_write_prefixes=("Liora",),
        mounts_file=mounts_file,
    )
    server = create_server(settings)

    async def exercise() -> None:
        async with Client(server) as client:
            listed = await client.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            assert EXPECTED_TOOLS <= set(tools)

            local_writes = {
                "runtime.write",
                "archive.stage_text",
                "archive.stage_directory",
                "archive.stage_discard",
            }
            canonical_writes = {"archive.promote", "archive.promote_directory"}
            for name in EXPECTED_TOOLS - local_writes - canonical_writes:
                annotations = tools[name].annotations
                assert annotations is not None
                assert annotations.read_only_hint is True
                assert annotations.destructive_hint is False
                assert annotations.open_world_hint is False
                assert annotations.idempotent_hint is True

            for name in local_writes:
                write_annotations = tools[name].annotations
                assert write_annotations is not None
                assert write_annotations.read_only_hint is False
                assert write_annotations.destructive_hint is False
                assert write_annotations.open_world_hint is False
                assert write_annotations.idempotent_hint is False

            for name in canonical_writes:
                promote_annotations = tools[name].annotations
                assert promote_annotations is not None
                assert promote_annotations.read_only_hint is False
                assert promote_annotations.destructive_hint is True
                assert promote_annotations.open_world_hint is False
                assert promote_annotations.idempotent_hint is True

            media_result = await client.call_tool(
                "archive.read_media",
                {"source": "live", "path": "portrait.png"},
            )
            assert media_result.is_error is False
            assert [item.type for item in media_result.content] == ["text", "image"]
            assert media_result.content[1].mime_type == "image/png"

            text_result = await client.call_tool(
                "archive.read_text",
                {"source": "live", "path": "manifest.md"},
            )
            assert text_result.is_error is False
            assert text_result.structured_content is not None
            assert text_result.structured_content["content"] == "lantern lit"
            assert text_result.structured_content["size"] == len(b"lantern lit")
            assert len(text_result.structured_content["sha256"]) == 64

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
            assert search_result.structured_content["hits"][0]["source"] == "live"
            assert (
                search_result.structured_content["hits"][0]["provenance"]
                == "configured_archive_source"
            )

            mounts_result = await client.call_tool("mount.status", {})
            assert mounts_result.is_error is False
            assert mounts_result.structured_content is not None
            assert mounts_result.structured_content["mount_count"] == 1
            assert mounts_result.structured_content["stats_included"] is False

            mount_list_result = await client.call_tool(
                "mount.list", {"mount_id": "outside", "limit": 1}
            )
            assert mount_list_result.is_error is False
            assert mount_list_result.structured_content is not None
            assert mount_list_result.structured_content["mount_id"] == "outside"
            assert mount_list_result.structured_content["page"]["has_more"] is True

            mount_text_result = await client.call_tool(
                "mount.read_text", {"mount_id": "outside", "path": "outside.md"}
            )
            assert mount_text_result.is_error is False
            assert mount_text_result.structured_content is not None
            assert mount_text_result.structured_content["content"] == "external lantern"

            mount_search_result = await client.call_tool(
                "mount.search_text", {"mount_id": "outside", "query": "lantern"}
            )
            assert mount_search_result.is_error is False
            assert mount_search_result.structured_content is not None
            assert mount_search_result.structured_content["hits"][0]["mount_id"] == "outside"

            mount_media_result = await client.call_tool(
                "mount.read_media", {"mount_id": "outside", "path": "outside.png"}
            )
            assert mount_media_result.is_error is False
            assert [item.type for item in mount_media_result.content] == ["text", "image"]

            write_surface = await client.call_tool(
                "archive.write_capabilities", {}
            )
            assert write_surface.is_error is False
            assert write_surface.structured_content is not None
            assert write_surface.structured_content["write_prefixes"] == ["Liora"]
            assert write_surface.structured_content["requires_staging"] is True

            stage_result = await client.call_tool(
                "archive.stage_text",
                {
                    "path": "Liora/new-note.md",
                    "content": "staged lantern\n",
                    "expected_base_sha256": "absent",
                    "reason": "wire test",
                },
            )
            assert stage_result.is_error is False
            assert stage_result.structured_content is not None
            stage = stage_result.structured_content
            assert stage["canonical_changed"] is False
            assert not (live / "Liora" / "new-note.md").exists()

            inspect_result = await client.call_tool(
                "archive.stage_inspect",
                {"stage_id": stage["stage_id"], "include_content": True},
            )
            assert inspect_result.is_error is False
            assert inspect_result.structured_content is not None
            assert inspect_result.structured_content["content"] == "staged lantern\n"
            assert inspect_result.structured_content["validation"]["ready"] is True

            promote_result = await client.call_tool(
                "archive.promote",
                {
                    "stage_id": stage["stage_id"],
                    "proposal_sha256": stage["proposal_sha256"],
                },
            )
            assert promote_result.is_error is False
            assert promote_result.structured_content is not None
            assert promote_result.structured_content["canonical_changed"] is True
            assert (live / "Liora" / "new-note.md").read_text(
                encoding="utf-8"
            ) == "staged lantern\n"

            directory_stage_result = await client.call_tool(
                "archive.stage_directory",
                {"path": "Liora/workbench/drafts", "reason": "wire test"},
            )
            assert directory_stage_result.is_error is False
            assert directory_stage_result.structured_content is not None
            directory_stage = directory_stage_result.structured_content
            assert directory_stage["canonical_changed"] is False
            assert directory_stage["kind"] == "directory"
            assert not (live / "Liora" / "workbench").exists()

            directory_inspect_result = await client.call_tool(
                "archive.stage_inspect",
                {"stage_id": directory_stage["stage_id"], "include_content": True},
            )
            assert directory_inspect_result.is_error is False
            assert directory_inspect_result.structured_content is not None
            assert "content" not in directory_inspect_result.structured_content
            assert directory_inspect_result.structured_content["validation"]["ready"] is True

            directory_promote_result = await client.call_tool(
                "archive.promote_directory",
                {
                    "stage_id": directory_stage["stage_id"],
                    "proposal_sha256": directory_stage["proposal_sha256"],
                },
            )
            assert directory_promote_result.is_error is False
            assert directory_promote_result.structured_content is not None
            assert directory_promote_result.structured_content["canonical_changed"] is True
            assert (live / "Liora" / "workbench" / "drafts").is_dir()

            runtime_result = await client.call_tool("runtime.status", {})
            assert runtime_result.is_error is False
            assert runtime_result.structured_content is not None
            assert runtime_result.structured_content["configured"] is False
            assert runtime_result.structured_content["available"] is False

            writes_result = await client.call_tool(
                "runtime.write_capabilities", {}
            )
            assert writes_result.is_error is True

            identity_result = await client.call_tool("system.identity", {})
            assert identity_result.is_error is False
            assert identity_result.structured_content is not None
            assert identity_result.structured_content["schema_version"] == "vestigia.system-identity.v0.1"
            assert identity_result.structured_content["archive"]["live"]["available"] is True
            assert (
                identity_result.structured_content["capability_registry"]["capability_count"]
                == 33
            )

            glance_result = await client.call_tool("house.glance", {})
            assert glance_result.is_error is False
            assert glance_result.structured_content is not None
            assert glance_result.structured_content["schema_version"] == "vestigia.house-glance.v0.1"
            assert glance_result.structured_content["meaningful_diff"]["computed"] is False
            assert glance_result.structured_content["authority"] == "descriptive_projection_only"

            status_result = await client.call_tool("vestigia.status", {})
            assert status_result.is_error is False
            assert status_result.structured_content is not None
            assert status_result.structured_content["server"]["version"] == "0.5.0.dev0"
            assert status_result.structured_content["policy"]["capability_count"] == 33
            assert status_result.structured_content["runtime"]["configured"] is False
            assert status_result.structured_content["archive"]["promotion_configured"] is True
            assert (
                "archive.promote"
                in status_result.structured_content["proprioception"]["new_native_tools"]
            )

            receipt_result = await client.call_tool(
                "receipts.recent",
                {"limit": 50},
            )
            assert receipt_result.is_error is False
            assert receipt_result.structured_content is not None
            events = receipt_result.structured_content["events"]
            capabilities = {event["capability"] for event in events}
            assert "archive.registry_status" in capabilities
            assert "archive.health" in capabilities
            assert "archive.search_text" in capabilities
            assert "archive.stage_text" in capabilities
            assert "archive.stage_directory" in capabilities
            assert "archive.stage_inspect" in capabilities
            assert "archive.promote" in capabilities
            assert "archive.promote_directory" in capabilities
            assert "archive.read_media" in capabilities
            assert "mount.status" in capabilities
            assert "mount.list" in capabilities
            assert "mount.read_text" in capabilities
            assert "mount.read_media" in capabilities
            assert "mount.search_text" in capabilities
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
