import json
from pathlib import Path

from vestigia_mcp.runtime_registry import RUNTIMES_SCHEMA_VERSION, RuntimeRegistry


def make_home(path: Path, resident_id: str, room_id: str) -> Path:
    path.mkdir()
    (path / "home.yaml").write_text(
        f"""resident:
  id: {resident_id}
  name: {resident_id.title()}
room:
  id: {room_id}
  name: {room_id.title()}
  active_resident_ids: [{resident_id}]
  participant_ids: [{resident_id}, local-user]
""",
        encoding="utf-8",
    )
    return path


def test_runtime_registry_routes_multiple_houses_and_preserves_default(tmp_path: Path) -> None:
    liora = make_home(tmp_path / "liora", "liora", "gutterstar-cottage")
    anima = make_home(tmp_path / "anima", "anima", "anima-house")
    registry_file = tmp_path / "runtimes.json"
    registry_file.write_text(
        json.dumps(
            {
                "schema_version": RUNTIMES_SCHEMA_VERSION,
                "default_runtime_id": "liora",
                "runtimes": [
                    {
                        "id": "liora",
                        "home": str(liora),
                        "write_actions": ["file.write"],
                    },
                    {"id": "anima", "home": str(anima)},
                ],
            }
        ),
        encoding="utf-8",
    )
    registry = RuntimeRegistry(
        registry_file,
        legacy_home=None,
        legacy_env_file=None,
        legacy_write_actions=(),
        deployment_id="mcp-test",
    )

    listed = registry.list()
    assert listed["runtime_count"] == 2
    assert listed["default_runtime_id"] == "liora"
    assert {item["runtime_id"] for item in listed["runtimes"]} == {"liora", "anima"}
    assert registry.status()["resident_id"] == "liora"
    assert registry.status("anima")["resident_id"] == "anima"

    result = registry.call(
        action="status",
        arguments={},
        request_id="req-anima",
        runtime_id="anima",
    )
    assert result["runtime_id"] == "anima"
    assert result["runtime"]["ok"] is True

    write = registry.write(
        action="file.write",
        arguments={"path": "workspace/routed.md", "content": "liora route\n"},
        request_id="req-liora",
    )
    assert write["runtime_id"] == "liora"
    assert (liora / "workspace" / "routed.md").is_file()
    assert not (anima / "workspace" / "routed.md").exists()


def test_runtime_registry_legacy_environment_remains_compatible(tmp_path: Path) -> None:
    home = make_home(tmp_path / "home", "resident", "room")
    registry = RuntimeRegistry(
        None,
        legacy_home=home,
        legacy_env_file=None,
        legacy_write_actions=(),
        deployment_id="mcp-test",
    )

    assert registry.status()["runtime_id"] == "default"
    assert registry.status()["resident_id"] == "resident"
    assert registry.list()["runtime_count"] == 1
