from pathlib import Path

import pytest

from vestigia_mcp.config import Settings
from vestigia_mcp.sense_registry import SenseOrganRegistry
from vestigia_mcp.server import create_server


def test_porchlight_manifest_is_bounded_and_deterministic() -> None:
    registry = SenseOrganRegistry()

    listed = registry.list()
    assert [item["organ_id"] for item in listed["organs"]] == ["porchlight"]
    manifest = registry.show("porchlight")["organ"]
    assert manifest["activation_topology"] == "explicit_invocation"
    assert manifest["consent_basis"] == "explicit_user_invocation"
    assert "raw_html" in manifest["prohibited_payloads"]
    assert manifest["digest"] == registry.show("porchlight")["organ"]["digest"]


def test_porchlight_can_perceive_only_declared_payloads() -> None:
    registry = SenseOrganRegistry()

    allowed = registry.can_perceive(
        "porchlight",
        {
            "capture_mode": "selection",
            "payloads": ["readable_text", "page_metadata"],
            "include_screenshot": False,
            "consent": "explicit_user_invocation",
        },
    )
    assert allowed["allowed"] is True
    assert allowed["matched_scope"] == "selection"

    denied = registry.can_perceive(
        "porchlight",
        {
            "capture_mode": "page",
            "payloads": ["readable_text", "raw_html", "cookies"],
            "include_screenshot": False,
            "consent": "explicit_user_invocation",
        },
    )
    assert denied["allowed"] is False
    assert "raw_html" in denied["rejected_payloads"]
    assert "cookies" in denied["rejected_payloads"]


def test_unknown_or_unconsented_organ_request_fails_closed() -> None:
    registry = SenseOrganRegistry()
    assert registry.can_perceive("missing", {})["allowed"] is False
    assert registry.can_perceive(
        "porchlight",
        {"capture_mode": "selection", "payloads": ["readable_text"]},
    )["allowed"] is False


@pytest.mark.parametrize("tool_name", ["sense.list", "sense.show", "sense.can_perceive"])
def test_sense_tools_are_exposed_as_read_only(tmp_path: Path, tool_name: str) -> None:
    settings = Settings(
        live_archive_root=tmp_path / "live",
        snapshot_archive_root=None,
        state_dir=tmp_path / "state",
        deployment_id="sense-test",
    )
    server = create_server(settings)

    async def exercise() -> None:
        from mcp import Client

        async with Client(server) as client:
            tools = {item.name: item for item in (await client.list_tools()).tools}
            assert tool_name in tools
            assert tools[tool_name].annotations.read_only_hint is True

    import asyncio

    asyncio.run(exercise())
