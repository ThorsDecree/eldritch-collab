from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows launcher checks")
REPO = Path(__file__).resolve().parents[2]


def test_suite_requires_a_profile_before_preparing_environments(tmp_path):
    script = REPO / "Start-VESTIGIA-Suite.ps1"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "Supply an existing tunnel profile" in result.stderr


def test_tunnel_suite_preflight_requires_explicit_pilot_routing(tmp_path):
    server = tmp_path / "MCP pilot!"
    server.mkdir()
    launcher = server / "Start VESTIGIA MCP Tunnel.bat"
    shutil.copyfile(REPO / "VESTIGIA_MCP_Server" / launcher.name, launcher)
    tunnel = server / "tunnel-client-v0.0.14-windows-amd64" / "tunnel-client.exe"
    tunnel.parent.mkdir()
    tunnel.touch()

    runtime_home = tmp_path / "Runtime pilot!"
    runtime_home.mkdir()
    (runtime_home / "home.yaml").touch()
    runtime_env = tmp_path / "runtime pilot.env"
    runtime_env.touch()
    archive = tmp_path / "Isolated archive"
    archive.mkdir()
    snapshot = tmp_path / "archive snapshot.zip"
    snapshot.touch()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("VESTIGIA_")}
    environment.update(
        CONTROL_PLANE_API_KEY="synthetic-only",
        VESTIGIA_SUITE_RUNTIME_HOME=str(runtime_home),
        VESTIGIA_SUITE_RUNTIME_ENV_FILE=str(runtime_env),
        VESTIGIA_MCP_RUNTIME_HOME=str(tmp_path / "wrong home"),
    )
    command = f'"{os.environ["ComSpec"]}" /d /v:off /s /c ""{launcher}" "Vex-local" "--check-config""'

    def check():
        return subprocess.run(
            command, cwd=server, env=environment, capture_output=True, text=True, timeout=30,
        )

    missing = check()
    assert missing.returncode != 0
    assert "needs explicit MCP live Archive" in missing.stdout
    environment.update(
        VESTIGIA_MCP_LIVE_ARCHIVE_ROOT=str(archive),
        VESTIGIA_MCP_SNAPSHOT_ARCHIVE_ROOT=str(snapshot),
        VESTIGIA_MCP_STATE_DIR=str(tmp_path / "MCP state"),
        VESTIGIA_MCP_DEPLOYMENT_ID="synthetic-pilot",
    )
    # These files are auto-discovered in standalone tunnel mode.
    for registry in ("mounts.local.json", "runtimes.local.json"):
        (server / registry).touch()
    ready = check()
    assert ready.returncode == 0, ready.stdout + ready.stderr
    environment["VESTIGIA_MCP_RUNTIMES_FILE"] = str(server / "runtimes.local.json")
    conflict = check()
    assert conflict.returncode != 0
    assert "requires a single-home MCP configuration" in conflict.stdout
