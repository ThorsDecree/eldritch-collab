from pathlib import Path
from dataclasses import replace
import json
import sys

import pytest

from house_mechanic.model import ManifestError, load_manifest
from house_mechanic.runner import run_recipe


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "recipes.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_rejects_unknown_fields(tmp_path: Path):
    p = _write(tmp_path, {
        "schema_version": "vestigia.house-mechanic.v0.1",
        "recipes": [{"id":"x","argv":["python","-V"],"cwd":".","shell":True}],
    })
    with pytest.raises(ManifestError, match="unknown recipe fields"):
        load_manifest(p, tmp_path)


def test_rejects_path_escape(tmp_path: Path):
    p = _write(tmp_path, {
        "schema_version": "vestigia.house-mechanic.v0.1",
        "recipes": [{"id":"x","argv":["python","-V"],"cwd":"../outside"}],
    })
    with pytest.raises(ManifestError, match="escapes repository root"):
        load_manifest(p, tmp_path)


def test_digest_is_stable(tmp_path: Path):
    p = _write(tmp_path, {
        "schema_version": "vestigia.house-mechanic.v0.1",
        "recipes": [{"id":"x","argv":["python","-V"],"cwd":"."}],
    })
    a = load_manifest(p, tmp_path).recipes["x"].digest()
    b = load_manifest(p, tmp_path).recipes["x"].digest()
    assert a == b and len(a) == 64


def test_example_manifest_includes_runtime_verification_recipes() -> None:
    house_mechanic_root = Path(__file__).parents[1]
    repository_root = house_mechanic_root.parent
    manifest = load_manifest(
        house_mechanic_root / "recipes.example.json",
        repository_root,
    )
    focused = manifest.recipes["runtime.test.mcp_context_source"]
    full = manifest.recipes["runtime.test"]

    assert focused.argv == (
        "python", "-m", "pytest", "-o", "pythonpath=src", "-q", "tests/test_mcp_context_source.py"
    )
    assert focused.cwd == "VESTIGIA_Runtime"
    assert full.argv == ("python", "-m", "pytest", "-o", "pythonpath=src", "-q")
    assert full.cwd == "VESTIGIA_Runtime"


@pytest.mark.parametrize("recipe_id", ["runtime.test.mcp_context_source", "runtime.test"])
def test_runtime_recipe_imports_source_from_selected_worktree(tmp_path: Path, recipe_id: str) -> None:
    house_mechanic_root = Path(__file__).parents[1]
    manifest = load_manifest(house_mechanic_root / "recipes.example.json", house_mechanic_root.parent)
    runtime = tmp_path / "VESTIGIA_Runtime"
    package = runtime / "src" / "vestigia"
    package.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b"TASK_SOURCE = True\n")
    tests = runtime / "tests"
    tests.mkdir()
    (tests / "test_mcp_context_source.py").write_bytes(
        b"from pathlib import Path\n"
        b"import vestigia\n"
        b"def test_import_is_from_task():\n"
        b"    expected = Path(__file__).parents[1] / 'src' / 'vestigia' / '__init__.py'\n"
        b"    assert Path(vestigia.__file__).resolve() == expected.resolve()\n"
        b"    assert vestigia.TASK_SOURCE is True\n"
    )
    declared = manifest.recipes[recipe_id]
    recipe = replace(declared, argv=(sys.executable, *declared.argv[1:]), timeout_seconds=30)
    receipt = run_recipe(recipe, tmp_path)
    assert receipt.exit_code == 0, receipt.stdout + receipt.stderr
