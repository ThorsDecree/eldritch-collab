from pathlib import Path

import pytest

from vestigia_mcp.config import Settings


def test_media_ceiling_and_runtime_write_grants_are_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("VESTIGIA_MCP_ARCHIVE_MEDIA_MAX_BYTES", "12345")
    monkeypatch.setenv(
        "VESTIGIA_MCP_RUNTIME_WRITE_ACTIONS",
        " file.write,FS.STAGE_PATCH,file.write, ,discord.react ",
    )
    monkeypatch.setenv(
        "VESTIGIA_MCP_ARCHIVE_WRITE_PREFIXES",
        " 02_Journal,Residents/Liora,02_Journal ",
    )
    monkeypatch.setenv("VESTIGIA_MCP_ARCHIVE_WRITE_MAX_BYTES", "54321")
    monkeypatch.setenv("VESTIGIA_MCP_PORCHLIGHT_SCREENSHOT_MAX_BYTES", "65432")
    monkeypatch.setenv("VESTIGIA_MCP_MOUNTS_FILE", "/tmp/vestigia-mounts.json")
    monkeypatch.setenv("VESTIGIA_MCP_RUNTIMES_FILE", "/tmp/vestigia-runtimes.json")
    monkeypatch.setenv("VESTIGIA_MCP_GAMETABLE_ENABLED", "yes")
    monkeypatch.setenv("VESTIGIA_MCP_GAMETABLE_STATE_DIR", "/tmp/vestigia-games")

    settings = Settings.from_env()

    assert settings.archive_media_max_bytes == 12345
    assert settings.runtime_write_actions == (
        "discord.react",
        "file.write",
        "fs.stage_patch",
    )
    assert settings.archive_write_prefixes == ("02_Journal", "Residents/Liora")
    assert settings.archive_write_max_bytes == 54321
    assert settings.porchlight_screenshot_max_bytes == 65432
    assert settings.mounts_file == Path("/tmp/vestigia-mounts.json")
    assert settings.runtimes_file == Path("/tmp/vestigia-runtimes.json")
    assert settings.gametable_enabled is True
    assert settings.gametable_state_dir == Path("/tmp/vestigia-games")


def test_runtime_write_grants_default_to_empty(monkeypatch) -> None:
    monkeypatch.delenv("VESTIGIA_MCP_RUNTIME_WRITE_ACTIONS", raising=False)
    monkeypatch.delenv("VESTIGIA_MCP_ARCHIVE_WRITE_PREFIXES", raising=False)
    monkeypatch.delenv("VESTIGIA_MCP_GAMETABLE_ENABLED", raising=False)
    settings = Settings.from_env()
    assert settings.runtime_write_actions == ()
    assert settings.archive_write_prefixes == ()
    assert settings.gametable_enabled is False


def test_porchlight_bridge_settings_are_bounded_and_explicit(monkeypatch) -> None:
    monkeypatch.setenv("VESTIGIA_MCP_PORCHLIGHT_BRIDGE_HOST", "127.0.0.1")
    monkeypatch.setenv("VESTIGIA_MCP_PORCHLIGHT_BRIDGE_PORT", "9123")
    monkeypatch.setenv(
        "VESTIGIA_MCP_PORCHLIGHT_BRIDGE_TOKEN_PATH", "/tmp/porchlight-token"
    )
    monkeypatch.setenv(
        "VESTIGIA_MCP_PORCHLIGHT_BRIDGE_EXTENSION_ORIGIN",
        "chrome-extension://porchlight",
    )
    monkeypatch.setenv("VESTIGIA_MCP_PORCHLIGHT_BRIDGE_MAX_BODY_BYTES", "123456")

    settings = Settings.from_env()

    assert settings.porchlight_bridge_host == "127.0.0.1"
    assert settings.porchlight_bridge_port == 9123
    assert settings.porchlight_bridge_token_path == Path("/tmp/porchlight-token")
    assert settings.porchlight_bridge_extension_origin == "chrome-extension://porchlight"
    assert settings.porchlight_bridge_max_body_bytes == 123456


def test_porchlight_bridge_rejects_non_loopback_env(monkeypatch) -> None:
    monkeypatch.setenv("VESTIGIA_MCP_PORCHLIGHT_BRIDGE_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="loopback"):
        Settings.from_env()


def test_lanternslide_settings_are_bounded_and_source_scoped(monkeypatch) -> None:
    monkeypatch.setenv("VESTIGIA_MCP_LANTERNSLIDE_SOURCE_PREFIX", "images")
    monkeypatch.setenv(
        "VESTIGIA_MCP_LANTERNSLIDE_CATALOG_PATH",
        "images/Lanternslide/catalog.json",
    )
    monkeypatch.setenv("VESTIGIA_MCP_LANTERNSLIDE_SCAN_BATCH_MAX", "17")
    monkeypatch.setenv("VESTIGIA_MCP_LANTERNSLIDE_IMAGE_MAX_BYTES", "7654321")
    monkeypatch.setenv(
        "VESTIGIA_MCP_LANTERNSLIDE_CONTACT_SHEET_MAX_BYTES", "2345678"
    )

    settings = Settings.from_env()

    assert settings.lanternslide_source_prefix == "images"
    assert settings.lanternslide_catalog_path == "images/Lanternslide/catalog.json"
    assert settings.lanternslide_scan_batch_max == 17
    assert settings.lanternslide_image_max_bytes == 7654321
    assert settings.lanternslide_contact_sheet_max_bytes == 2345678


def test_lanternslide_catalog_must_be_json_inside_source_prefix(monkeypatch) -> None:
    monkeypatch.setenv("VESTIGIA_MCP_LANTERNSLIDE_SOURCE_PREFIX", "pics")
    monkeypatch.setenv("VESTIGIA_MCP_LANTERNSLIDE_CATALOG_PATH", "other/catalog.json")
    with pytest.raises(ValueError, match="source prefix"):
        Settings.from_env()

    monkeypatch.setenv("VESTIGIA_MCP_LANTERNSLIDE_CATALOG_PATH", "pics/catalog.txt")
    with pytest.raises(ValueError, match=r"\.json"):
        Settings.from_env()


def test_daemon_bridge_settings_are_loopback_and_explicit(monkeypatch) -> None:
    monkeypatch.setenv("VESTIGIA_MCP_DAEMON_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("VESTIGIA_MCP_DAEMON_BRIDGE_HOST", "localhost")
    monkeypatch.setenv("VESTIGIA_MCP_DAEMON_BRIDGE_PORT", "9876")
    monkeypatch.setenv(
        "VESTIGIA_MCP_DAEMON_BRIDGE_TOKEN_PATH",
        "/tmp/daemon-bridge-token",
    )
    monkeypatch.setenv("VESTIGIA_MCP_DAEMON_BRIDGE_TIMEOUT_SECONDS", "33")
    monkeypatch.setenv(
        "VESTIGIA_MCP_DAEMON_BRIDGE_MAX_RESPONSE_BYTES",
        "77777",
    )

    settings = Settings.from_env()

    assert settings.daemon_bridge_enabled is True
    assert settings.daemon_bridge_host == "127.0.0.1"
    assert settings.daemon_bridge_port == 9876
    assert settings.daemon_bridge_token_path == Path("/tmp/daemon-bridge-token")
    assert settings.daemon_bridge_timeout_seconds == 33
    assert settings.daemon_bridge_max_response_bytes == 77777


def test_daemon_bridge_rejects_non_loopback_env(monkeypatch) -> None:
    monkeypatch.setenv("VESTIGIA_MCP_DAEMON_BRIDGE_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="loopback"):
        Settings.from_env()


def test_house_mechanic_dev_actions_distinguish_unset_wildcard_empty_and_exact(
    monkeypatch,
) -> None:
    monkeypatch.delenv("VESTIGIA_MCP_DEV_ACTIONS", raising=False)
    assert Settings.from_env().dev_actions.mode == "wildcard"

    monkeypatch.setenv("VESTIGIA_MCP_DEV_ACTIONS", "*")
    wildcard = Settings.from_env().dev_actions
    assert wildcard.mode == "wildcard"
    assert wildcard.actions == ()

    monkeypatch.setenv("VESTIGIA_MCP_DEV_ACTIONS", "")
    denied = Settings.from_env().dev_actions
    assert denied.mode == "deny_all"
    assert denied.actions == ()

    monkeypatch.setenv(
        "VESTIGIA_MCP_DEV_ACTIONS",
        " task.acquire, deployment.candidate,task.acquire ",
    )
    exact = Settings.from_env().dev_actions
    assert exact.mode == "exact"
    assert exact.actions == ("deployment.candidate", "task.acquire")


def test_house_mechanic_settings_are_loopback_and_explicit(monkeypatch) -> None:
    monkeypatch.setenv("VESTIGIA_MCP_HOUSE_MECHANIC_ENABLED", "true")
    monkeypatch.setenv("VESTIGIA_MCP_HOUSE_MECHANIC_HOST", "localhost")
    monkeypatch.setenv("VESTIGIA_MCP_HOUSE_MECHANIC_PORT", "9988")
    monkeypatch.setenv(
        "VESTIGIA_MCP_HOUSE_MECHANIC_TOKEN_PATH",
        "/tmp/house-mechanic-token",
    )
    monkeypatch.setenv("VESTIGIA_MCP_HOUSE_MECHANIC_TIMEOUT_SECONDS", "21")
    monkeypatch.setenv(
        "VESTIGIA_MCP_HOUSE_MECHANIC_MAX_RESPONSE_BYTES",
        "123456",
    )

    settings = Settings.from_env()

    assert settings.house_mechanic_enabled is True
    assert settings.house_mechanic_host == "127.0.0.1"
    assert settings.house_mechanic_port == 9988
    assert settings.house_mechanic_token_path == Path("/tmp/house-mechanic-token")
    assert settings.house_mechanic_timeout_seconds == 21
    assert settings.house_mechanic_max_response_bytes == 123456


def test_house_mechanic_rejects_non_loopback_env(monkeypatch) -> None:
    monkeypatch.setenv("VESTIGIA_MCP_HOUSE_MECHANIC_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="loopback"):
        Settings.from_env()
