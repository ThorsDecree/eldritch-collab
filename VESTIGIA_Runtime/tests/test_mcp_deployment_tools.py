from __future__ import annotations

import importlib.util
import json

import pytest
from PIL import Image

from vestigia.config import load_config
from vestigia.home import initialize_home
from vestigia.mcp_stdio import McpStdioClient, McpToolRejected
from vestigia.runtime import CoreRuntime


PRIVATE_TURN = {"interface": "web", "invocation": "conversation"}
REQUIRES_MCP = pytest.mark.skipif(
    any(importlib.util.find_spec(name) is None for name in ("mcp", "vestigia_mcp")),
    reason="requires the sibling MCP server and the mcp-context extra",
)


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    archive = tmp_path / "Archive"
    (archive / "pics").mkdir(parents=True)
    Image.new("RGB", (64, 48), "green").save(archive / "pics" / "canary.png")
    profile = tmp_path / "Pilot MCP settings.env"
    profile.write_text(
        f"VESTIGIA_MCP_LIVE_ARCHIVE_ROOT={archive}\n"
        f"VESTIGIA_MCP_STATE_DIR={tmp_path / 'mcp-state'}\n"
        "VESTIGIA_MCP_DEPLOYMENT_ID=synthetic-solo-pilot\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VESTIGIA_CONTEXT_MCP_DEPLOYMENT_ENV_FILE", str(profile))
    monkeypatch.setenv("VESTIGIA_CONTEXT_MCP_ENABLED", "false")
    home = initialize_home(tmp_path / "Home", name="Pilot")
    return CoreRuntime(load_config(home), fake=True).house, profile


def test_discovery_and_calls_require_a_private_turn(deployment, monkeypatch):
    house, _ = deployment

    def unexpectedly_called(*args):
        pytest.fail("A non-private turn reached MCP")

    monkeypatch.setattr(McpStdioClient, "list_tools", unexpectedly_called)
    monkeypatch.setattr(McpStdioClient, "call_tool", unexpectedly_called)
    for context in (
        {},
        {"interface": "bell", "invocation": "conversation"},
        {
            "interface": "discord",
            "invocation": "conversation",
            "delivery_target": {"kind": "discord_channel", "id": "guild-room"},
        },
        {
            "interface": "discord",
            "invocation": "conversation",
            "delivery_target": {"kind": "discord_dm", "id": "group"},
            "is_group_dm": True,
        },
    ):
        for payload in (
            {"action": "mcp.tools", "prefix": "game."},
            {"action": "mcp.call", "tool": "game.create"},
        ):
            with pytest.raises(PermissionError):
                house.dispatch(payload, turn_id="blocked", context=context)
    assert "mcp.call" not in house.registry.forgeable_names()


def dispatch(house, action, **fields):
    return house.dispatch(
        {"action": action, **fields}, turn_id="synthetic-turn", context=PRIVATE_TURN,
    )


def call(house, tool, **arguments):
    return dispatch(house, "mcp.call", tool=tool, arguments=arguments)


@REQUIRES_MCP
def test_discovery_and_lanternslide_share_catalog_and_import_contact_sheet(deployment):
    house, profile = deployment
    direct = McpStdioClient(30, args=("--deployment-env", str(profile)))
    advertised = {tool["name"] for tool in direct.list_tools()}
    published = dispatch(house, "mcp.tools", limit=100)
    assert set(published["tools"]) == advertised
    assert {"game.create", "game.mulligan", "lanternslide.contact_sheet", "system.identity"} <= advertised
    first = dispatch(house, "mcp.tools", prefix="lanternslide.", limit=3)
    assert len(first["tools"]) == 3 and first["next_offset"] == 3
    contract = dispatch(house, "mcp.tools", tool="lanternslide.contact_sheet")
    assert contract["tool_contract"]["inputSchema"]["required"] == ["image_ids"]
    assert direct.call_tool("lanternslide.scan", {})["structured_content"]["complete"]

    deal = call(house, "lanternslide.deal", count=1, seed="synthetic")["mcp_result"]
    image_id = deal["entries"][0]["image_id"]
    sheet = call(house, "lanternslide.contact_sheet", image_ids=[image_id])
    [private_image] = sheet["images"]
    asset = house.images.get_asset(private_image["image_id"])
    assert asset["privacy"] == "private"
    assert asset["content_hash"] == private_image["sha256"]
    assert asset["media_type"] == "image/png"
    assert list((profile.parent / "Archive" / "pics").iterdir()) == [
        profile.parent / "Archive" / "pics" / "canary.png"
    ]


@REQUIRES_MCP
def test_gametable_shares_revisions_and_keeps_private_results_out_of_receipts(deployment):
    house, profile = deployment
    direct = McpStdioClient(30, args=("--deployment-env", str(profile)))
    response = call(
        house,
        "game.create",
        title="Synthetic table",
        seats=[
            {"seat_id": "resident", "display_name": "Resident"},
            {"seat_id": "guest", "display_name": "Guest"},
        ],
    )
    created = response["mcp_result"]
    game_id = created["game_id"]
    tokens = created["seat_tokens"]
    receipt = house.legible.inspect_receipt(response["receipt_id"])
    assert receipt["target"]["tool"] == "game.create"
    assert receipt["result"]["mcp"]["request_id"] == created["request_id"]
    assert "mcp_result" not in receipt["result"]
    assert not any(token in json.dumps(receipt) for token in tokens.values())
    with house.db.connect() as connection:
        audit = connection.execute("SELECT payload_json FROM house_events WHERE action = 'mcp.call'").fetchall()
    assert not any(token in row["payload_json"] for token in tokens.values() for row in audit)

    resident = {"game_id": game_id, "seat_token": tokens["resident"]}
    guest = {"game_id": game_id, "seat_token": tokens["guest"]}
    direct.call_tool(
        "game.load_deck",
        {**guest, "expected_revision": 0, "deck": ["Guest card"] * 8},
    )
    assert call(house, "game.view", game_id=game_id)["mcp_result"]["game"]["revision"] == 1
    with pytest.raises(McpToolRejected, match="[Rr]evision"):
        call(house, "game.load_deck", **resident, expected_revision=0, deck=["Resident card"] * 8)
    call(house, "game.load_deck", **resident, expected_revision=1, deck=["Resident card"] * 8)
    call(house, "game.start", **resident, expected_revision=2)
    private_view = json.dumps(call(house, "game.view", **resident)["mcp_result"])
    assert "Resident card" in private_view and "Guest card" not in private_view
    public_view = json.dumps(direct.call_tool("game.view", {"game_id": game_id})["structured_content"])
    assert "Resident card" not in public_view and "Guest card" not in public_view


@REQUIRES_MCP
def test_profile_controls_gametable_instead_of_ambient_mcp_environment(deployment, monkeypatch):
    house, profile = deployment

    monkeypatch.setattr(
        McpStdioClient,
        "_child_env",
        lambda self: {
            "VESTIGIA_MCP_STATE_DIR": str(profile.parent / "ambient-mcp-state"),
            "VESTIGIA_MCP_GAMETABLE_ENABLED": "0",
        },
    )
    assert "game.status" in dispatch(house, "mcp.tools", prefix="game.")["tools"]
    call(house, "game.status")
    assert not (profile.parent / "ambient-mcp-state").exists()

    with profile.open("a", encoding="utf-8") as stream:
        stream.write("VESTIGIA_MCP_GAMETABLE_ENABLED=0\n")
    assert dispatch(house, "mcp.tools", prefix="game.")["tools"] == []
    with pytest.raises(McpToolRejected, match="game.status"):
        call(house, "game.status")
