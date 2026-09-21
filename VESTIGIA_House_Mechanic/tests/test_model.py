from pathlib import Path
import json

import pytest

from house_mechanic.model import ManifestError, load_manifest


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
