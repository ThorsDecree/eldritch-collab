from vestigia_mcp.config import Settings


def test_media_ceiling_and_runtime_write_grants_are_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("VESTIGIA_MCP_ARCHIVE_MEDIA_MAX_BYTES", "12345")
    monkeypatch.setenv(
        "VESTIGIA_MCP_RUNTIME_WRITE_ACTIONS",
        " file.write,FS.STAGE_PATCH,file.write, ,discord.react ",
    )

    settings = Settings.from_env()

    assert settings.archive_media_max_bytes == 12345
    assert settings.runtime_write_actions == (
        "discord.react",
        "file.write",
        "fs.stage_patch",
    )


def test_runtime_write_grants_default_to_empty(monkeypatch) -> None:
    monkeypatch.delenv("VESTIGIA_MCP_RUNTIME_WRITE_ACTIONS", raising=False)
    assert Settings.from_env().runtime_write_actions == ()
