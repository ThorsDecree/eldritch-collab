import asyncio
import base64
import json
from pathlib import Path

from mcp import Client
from PIL import Image

from vestigia_mcp.config import Settings
from vestigia_mcp.policy import DEFAULT_CAPABILITIES, Decision, EffectClass
from vestigia_mcp.porchlight import build_snapshot
from vestigia_mcp.server import create_server


EXPECTED_TOOLS = {
    "archive.status",
    "archive.list",
    "archive.read_text",
    "archive.read_bytes",
    "archive.read_media",
    "archive.search_text",
    "archive.diff",
    "archive.diff_detail",
    "archive.registry_status",
    "archive.health",
    "archive.write_capabilities",
    "archive.stage_text",
    "archive.stage_porchlight",
    "archive.share_porchlight",
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
    "sense.list",
    "sense.show",
    "sense.can_perceive",
    "lanternslide.status",
    "lanternslide.scan",
    "lanternslide.find",
    "lanternslide.deal",
    "lanternslide.contact_sheet",
    "lanternslide.stage_catalog",
    "daemon_bridge.status",
    "daemon_bridge.residents",
    "daemon_bridge.capabilities",
    "daemon_bridge.query",
    "runtime.list",
    "runtime.status",
    "runtime.capabilities",
    "runtime.call",
    "runtime.write_capabilities",
    "runtime.write",
    "receipts.recent",
    "receipts.trace",
    "audit.show",
    "system.identity",
    "house.glance",
    "vestigia.status",
}

GAME_TOOLS = {
    "game.profiles",
    "game.status",
    "game.create",
    "game.load_deck",
    "game.start",
    "game.mulligan",
    "game.keep",
    "game.view",
    "game.events",
    "game.act",
    "game.effect_declare",
    "game.effect_resolve",
    "game.repair_state",
    "game.pass_priority",
    "game.yield",
    "game.shortcut_propose",
    "game.shortcut_respond",
    "game.concede",
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
            assert not (GAME_TOOLS & set(tools))

            local_writes = {
                "runtime.write",
                "archive.stage_text",
                "archive.stage_porchlight",
                "archive.stage_directory",
                "archive.stage_discard",
                "lanternslide.scan",
                "lanternslide.stage_catalog",
            }
            direct_writes = {"archive.share_porchlight"}
            canonical_writes = {"archive.promote", "archive.promote_directory"}
            metered_reads = {"daemon_bridge.query"}
            for name in (
                EXPECTED_TOOLS
                - local_writes
                - direct_writes
                - canonical_writes
                - metered_reads
            ):
                annotations = tools[name].annotations
                assert annotations is not None
                assert annotations.read_only_hint is True
                assert annotations.destructive_hint is False
                assert annotations.open_world_hint is False
                assert annotations.idempotent_hint is True

            for name in metered_reads:
                read_annotations = tools[name].annotations
                assert read_annotations is not None
                assert read_annotations.read_only_hint is True
                assert read_annotations.destructive_hint is False
                assert read_annotations.open_world_hint is False
                assert read_annotations.idempotent_hint is False

            for name in local_writes:
                write_annotations = tools[name].annotations
                assert write_annotations is not None
                assert write_annotations.read_only_hint is False
                assert write_annotations.destructive_hint is False
                assert write_annotations.open_world_hint is False
                assert write_annotations.idempotent_hint is False

            for name in direct_writes:
                write_annotations = tools[name].annotations
                assert write_annotations is not None
                assert write_annotations.read_only_hint is False
                assert write_annotations.destructive_hint is True
                assert write_annotations.open_world_hint is False
                assert write_annotations.idempotent_hint is True

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
            assert len(text_result.structured_content["content_sha256"]) == 64

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
                == 50
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
            assert status_result.structured_content["server"]["version"] == "0.9.0.dev0"
            assert status_result.structured_content["policy"]["capability_count"] == 50
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


def test_archive_paging_server_contract_and_byte_capability(tmp_path: Path) -> None:
    live = tmp_path / "live"
    (live / "logs").mkdir(parents=True)
    (live / "db").mkdir()
    (live / "logs" / "huge.md").write_text("entry\n" * 250_000, encoding="utf-8")
    fixture = b"SQLite format 3\x00" + bytes(range(256)) * 4
    (live / "db" / "runtime.sqlite").write_bytes(fixture)
    settings = Settings(
        live_archive_root=live,
        snapshot_archive_root=None,
        state_dir=tmp_path / "state",
        deployment_id="paging-contract",
        archive_text_max_bytes=100,
        archive_page_max_bytes=1_024,
    )
    server = create_server(settings)

    async def exercise() -> None:
        async with Client(server) as client:
            text_result = await client.call_tool(
                "archive.read_text",
                {"source": "live", "path": "logs/huge.md", "page_bytes": 512},
            )
            assert text_result.is_error is False
            assert text_result.structured_content is not None
            assert text_result.structured_content["budget"]["requested_bytes"] == 512
            assert text_result.structured_content["snapshot_status"] == "same_snapshot"

            byte_result = await client.call_tool(
                "archive.read_bytes",
                {"source": "live", "path": "db/runtime.sqlite", "page_bytes": 512},
            )
            assert byte_result.is_error is False
            assert byte_result.structured_content is not None
            assert base64.b64decode(byte_result.structured_content["data"]) == fixture[:512]

    asyncio.run(exercise())

    capabilities = [
        capability
        for capability in DEFAULT_CAPABILITIES
        if capability.name == "archive.read_bytes"
    ]
    assert len(capabilities) == 1
    assert capabilities[0].effect is EffectClass.PERCEIVE
    assert capabilities[0].default is Decision.ALLOW


def test_porchlight_stage_creates_latest_history_and_receipt_stages(tmp_path: Path) -> None:
    live = tmp_path / "live"
    live.mkdir()
    for directory in (
        live / "Porchlight" / "latest",
        live / "Porchlight" / "history",
        live / "Porchlight" / "receipts",
    ):
        directory.mkdir(parents=True)
    settings = Settings(
        live_archive_root=live,
        snapshot_archive_root=None,
        state_dir=tmp_path / "state",
        deployment_id="porchlight-stage",
        archive_write_prefixes=("Porchlight",),
    )
    server = create_server(settings)

    async def exercise() -> None:
        async with Client(server) as client:
            result = await client.call_tool(
                "archive.stage_porchlight",
                {
                    "url": "https://example.test/thread",
                    "title": "Thread",
                    "content": "A readable capture.",
                    "mode": "page",
                    "captured_at": "2026-09-19T12:00:00+00:00",
                },
            )
            assert result.is_error is False
            assert result.structured_content is not None
            data = result.structured_content
            assert data["canonical_changed"] is False
            assert {item["path"].split("/", 2)[1] for item in data["artifacts"]} == {
                "latest",
                "history",
                "receipts",
            }
            assert all(item["status"] == "staged" for item in data["artifacts"])

    asyncio.run(exercise())


def test_wire_lanternslide_scans_deals_sheets_and_stages_catalog(tmp_path: Path) -> None:
    live = tmp_path / "live"
    (live / "pics").mkdir(parents=True)
    for name, color in (("one.png", (255, 0, 0)), ("two.png", (0, 0, 255))):
        image = Image.new("RGB", (24, 16), color)
        image.save(live / "pics" / name, format="PNG")
    settings = Settings(
        live_archive_root=live,
        snapshot_archive_root=None,
        state_dir=tmp_path / "state",
        deployment_id="lanternslide-wire",
        archive_write_prefixes=("pics",),
    )
    server = create_server(settings)

    async def exercise() -> None:
        async with Client(server) as client:
            scan = await client.call_tool("lanternslide.scan", {})
            assert scan.is_error is False
            assert scan.structured_content is not None
            assert scan.structured_content["complete"] is True
            assert scan.structured_content["indexed_total"] == 2
            request_id = scan.structured_content["request_id"]

            status = await client.call_tool("lanternslide.status", {})
            assert status.is_error is False
            assert status.structured_content["complete"] is True

            found = await client.call_tool(
                "lanternslide.find", {"query": "ONE.PNG"}
            )
            assert found.is_error is False
            assert found.structured_content["entries"][0]["path"] == "pics/one.png"
            deal = await client.call_tool(
                "lanternslide.deal", {"count": 2, "seed": "wire-seed"}
            )
            assert deal.is_error is False
            ids = deal.structured_content["image_ids"]
            sheet = await client.call_tool(
                "lanternslide.contact_sheet", {"image_ids": ids}
            )
            assert sheet.is_error is False
            assert [item.type for item in sheet.content] == ["text", "image"]
            assert sheet.content[1].mime_type == "image/png"

            trace = await client.call_tool("receipts.trace", {"request_id": request_id})
            assert trace.is_error is False
            records = trace.structured_content["receipts"]
            scan_receipt = next(
                item
                for item in records
                if "raw_image_bytes" in item["omitted"]
            )
            assert scan_receipt["omitted"] == [
                "raw_image_bytes",
                "source_pixels",
                "thumbnails",
            ]

            first_stage = await client.call_tool("lanternslide.stage_catalog", {})
            assert first_stage.is_error is False
            directory_stage = first_stage.structured_content["directory_stage"]
            assert directory_stage["kind"] == "directory"
            assert not (live / "pics" / "Lanternslide").exists()
            promoted = await client.call_tool(
                "archive.promote_directory",
                {
                    "stage_id": directory_stage["stage_id"],
                    "proposal_sha256": directory_stage["proposal_sha256"],
                },
            )
            assert promoted.is_error is False
            second_stage = await client.call_tool("lanternslide.stage_catalog", {})
            assert second_stage.is_error is False
            stage = second_stage.structured_content["stage"]
            assert stage["status"] == "staged"
            assert not (live / "pics" / "Lanternslide" / "catalog.json").exists()

    asyncio.run(exercise())


def test_porchlight_rejects_existing_history_before_staging(tmp_path: Path) -> None:
    live = tmp_path / "live"
    for directory in (
        live / "Porchlight" / "latest",
        live / "Porchlight" / "history",
        live / "Porchlight" / "receipts",
    ):
        directory.mkdir(parents=True)
    artifact = build_snapshot(
        "https://example.test/thread",
        "Thread",
        "A readable capture.",
        "page",
        "2026-09-19T12:00:00+00:00",
    )
    (live / artifact.history_path).write_text("already promoted", encoding="utf-8")
    server = create_server(
        Settings(
            live_archive_root=live,
            snapshot_archive_root=None,
            state_dir=tmp_path / "state",
            deployment_id="porchlight-history-collision",
            archive_write_prefixes=("Porchlight",),
        )
    )

    async def exercise() -> None:
        async with Client(server) as client:
            result = await client.call_tool(
                "archive.stage_porchlight",
                {
                    "url": "https://example.test/thread",
                    "title": "Thread",
                    "content": "A readable capture.",
                    "mode": "page",
                    "captured_at": "2026-09-19T12:00:00+00:00",
                },
            )
            assert result.is_error is True
            stages = await client.call_tool("archive.stage_list", {})
            assert stages.is_error is False
            assert stages.structured_content is not None
            assert stages.structured_content["stages"] == []

    asyncio.run(exercise())


def test_wire_gametable_keeps_opponent_cards_out_of_a_seat_projection(
    tmp_path: Path,
) -> None:
    settings = Settings(
        live_archive_root=None,
        snapshot_archive_root=None,
        state_dir=tmp_path / "state",
        deployment_id="test-deployment",
        gametable_enabled=True,
    )
    server = create_server(settings)
    liora_deck = [f"Liora secret {number}" for number in range(1, 9)]
    jeff_deck = [f"Jeff secret {number}" for number in range(1, 9)]

    async def exercise() -> None:
        async with Client(server) as client:
            listed = await client.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            assert GAME_TOOLS <= set(tools)
            assert tools["game.view"].annotations is not None
            assert tools["game.view"].annotations.read_only_hint is True
            assert tools["game.create"].annotations is not None
            assert tools["game.create"].annotations.read_only_hint is False
            for name in {"game.effect_declare", "game.effect_resolve", "game.repair_state", "game.yield"}:
                assert tools[name].annotations is not None
                assert tools[name].annotations.read_only_hint is False

            profiles_result = await client.call_tool("game.profiles", {})
            assert profiles_result.is_error is False
            assert profiles_result.structured_content is not None
            assert "magic.commander.v0.1" in {
                profile["profile_id"]
                for profile in profiles_result.structured_content["profiles"]
            }

            create_result = await client.call_tool(
                "game.create",
                {
                    "title": "Wire Commander",
                    "seats": [
                        {"seat_id": "liora", "display_name": "Liora"},
                        {"seat_id": "jeff", "display_name": "Jeff"},
                    ],
                },
            )
            assert create_result.is_error is False
            assert create_result.structured_content is not None
            created = create_result.structured_content
            game_id = created["game_id"]
            liora_token = created["seat_tokens"]["liora"]
            jeff_token = created["seat_tokens"]["jeff"]

            public_before_start = await client.call_tool("game.view", {"game_id": game_id})
            assert public_before_start.is_error is False
            assert public_before_start.structured_content is not None
            assert "secret" not in json.dumps(public_before_start.structured_content)

            liora_load = await client.call_tool(
                "game.load_deck",
                {
                    "game_id": game_id,
                    "seat_token": liora_token,
                    "expected_revision": 0,
                    "deck": liora_deck,
                },
            )
            assert liora_load.is_error is False
            jeff_load = await client.call_tool(
                "game.load_deck",
                {
                    "game_id": game_id,
                    "seat_token": jeff_token,
                    "expected_revision": 1,
                    "deck": jeff_deck,
                },
            )
            assert jeff_load.is_error is False

            start_result = await client.call_tool(
                "game.start",
                {"game_id": game_id, "seat_token": liora_token, "expected_revision": 2},
            )
            assert start_result.is_error is False
            assert start_result.structured_content is not None
            assert "Liora secret" in json.dumps(start_result.structured_content)
            assert "Jeff secret" not in json.dumps(start_result.structured_content)

            liora_view = await client.call_tool(
                "game.view", {"game_id": game_id, "seat_token": liora_token}
            )
            jeff_view = await client.call_tool(
                "game.view", {"game_id": game_id, "seat_token": jeff_token}
            )
            assert liora_view.structured_content is not None
            assert jeff_view.structured_content is not None
            assert "Jeff secret" not in json.dumps(liora_view.structured_content)
            assert "Liora secret" not in json.dumps(jeff_view.structured_content)

            events_result = await client.call_tool("game.events", {"game_id": game_id})
            assert events_result.is_error is False
            assert events_result.structured_content is not None
            assert "secret" not in json.dumps(events_result.structured_content)

            receipts = await client.call_tool("receipts.recent", {"capability": "game.start"})
            assert receipts.is_error is False
            assert receipts.structured_content is not None
            assert liora_token not in json.dumps(receipts.structured_content)
            assert jeff_token not in json.dumps(receipts.structured_content)

    asyncio.run(exercise())
