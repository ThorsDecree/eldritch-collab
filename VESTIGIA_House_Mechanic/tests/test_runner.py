from pathlib import Path
import hashlib
import subprocess
import sys

from house_mechanic.model import Recipe
from house_mechanic.runner import run_recipe


def recipe(argv, *, timeout=5, stdout=4096):
    return Recipe(
        id="test.recipe",
        description="test",
        argv=tuple(argv),
        cwd=".",
        timeout_seconds=timeout,
        env_profile="python",
        expected_exit_codes=(0,),
        max_stdout_bytes=stdout,
        max_stderr_bytes=4096,
    )


def test_success_receipt(tmp_path: Path):
    r = run_recipe(recipe([sys.executable, "-c", "print('ok')"]), tmp_path, "req-1")
    assert r.request_id == "req-1"
    assert r.exit_code == 0
    assert r.expected_exit is True
    assert r.stdout.strip() == "ok"
    assert r.stdout_bytes == len(r.stdout.encode("utf-8"))
    assert r.stdout_sha256 == hashlib.sha256(r.stdout.encode("utf-8")).hexdigest()
    assert r.stderr_bytes == 0
    assert len(r.stderr_sha256) == 64
    assert r.process_id > 0
    assert r.env_profile == "python"
    assert r.timed_out is False
    assert r.process_tree_containment_proven is False


def test_timeout_is_truthfully_reported(tmp_path: Path):
    r = run_recipe(
        recipe([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.1),
        tmp_path,
    )
    assert r.timed_out is True
    assert r.expected_exit is False


def test_output_cap_stops_run(tmp_path: Path):
    r = run_recipe(
        recipe([sys.executable, "-c", "print('x' * 100000)"], stdout=1024),
        tmp_path,
    )
    assert r.output_limit_exceeded is True
    assert r.stdout_truncated is True
    assert len(r.stdout.encode("utf-8")) <= 1024


def test_python_profile_can_initialize_socket_provider(tmp_path: Path):
    r = run_recipe(
        recipe(
            [
                sys.executable,
                "-c",
                (
                    "import socket; "
                    "s = socket.socket(); "
                    "s.close(); "
                    "print('socket-ok')"
                ),
            ]
        ),
        tmp_path,
    )
    assert r.exit_code == 0, r.stderr
    assert r.stdout.strip() == "socket-ok"

def test_git_identity_includes_untracked_files(tmp_path: Path):
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_bytes(b"tracked\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "Vesti").mkdir()
    (tmp_path / "Vesti" / "new-machine.txt").write_bytes(b"untracked\n")

    expected_status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=tmp_path,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
    ).stdout.strip()

    r = run_recipe(recipe([sys.executable, "-c", "print('ok')"]), tmp_path, "req-untracked")

    assert "?? Vesti/new-machine.txt" in expected_status
    assert r.source_dirty is True
    assert r.source_status_sha256 == hashlib.sha256(expected_status.encode("utf-8")).hexdigest()


def test_run_recipe_uses_effective_cwd_without_changing_operator_recipe_digest(tmp_path: Path) -> None:
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "marker.txt").write_bytes(b"task-local\n")
    declared = Recipe(
        id="test.override",
        description="cwd override",
        argv=(
            sys.executable,
            "-c",
            "from pathlib import Path; print(Path('marker.txt').read_text().strip())",
        ),
        cwd="repo/subdir",
        timeout_seconds=5,
        env_profile="python",
        expected_exit_codes=(0,),
        max_stdout_bytes=4096,
        max_stderr_bytes=4096,
    )

    r = run_recipe(declared, tmp_path, "req-cwd", cwd_override="subdir")

    assert r.exit_code == 0, r.stderr
    assert r.stdout.strip() == "task-local"
    assert r.cwd == "subdir"
    assert r.recipe_sha256 == declared.digest()
