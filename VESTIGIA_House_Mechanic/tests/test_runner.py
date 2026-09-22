from pathlib import Path
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
    assert r.stdout_bytes == len(b"ok\n")
    assert len(r.stdout_sha256) == 64
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
