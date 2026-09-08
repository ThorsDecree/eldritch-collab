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

    settings = Settings.from_env()

    assert settings.archive_media_max_bytes == 12345
    assert settings.runtime_write_actions == (
        "discord.react",
        "file.write",
        "fs.stage_patch",
    )
    assert settings.archive_write_prefixes == ("02_Journal", "Residents/Liora")
    assert settings.archive_write_max_bytes == 54321


def test_runtime_write_grants_default_to_empty(monkeypatch) -> None:
    monkeypatch.delenv("VESTIGIA_MCP_RUNTIME_WRITE_ACTIONS", raising=False)
    monkeypatch.delenv("VESTIGIA_MCP_ARCHIVE_WRITE_PREFIXES", raising=False)
    settings = Settings.from_env()
    assert settings.runtime_write_actions == ()
    assert settings.archive_write_prefixes == ()
