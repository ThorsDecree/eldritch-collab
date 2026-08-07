from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "VESTIGIA_Runtime" / "src" / "vestigia"
TESTS = ROOT / "VESTIGIA_Runtime" / "tests"


def replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected text not found in {path}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


composition = SRC / "composition.py"
replace(
    composition,
    '''    for mode in modes:\n        normalized = str(mode).strip().lower()\n        _drawer_modes.register(f"{normalized}:{name}", callback, order=order)\n''',
    '''    del name\n    for mode in modes:\n        normalized = str(mode).strip().lower()\n        if not normalized:\n            raise ValueError("drawer mode names must not be empty")\n        _drawer_modes.register(normalized, callback, order=order)\n''',
)
replace(
    composition,
    '''    matches = [entry for entry in _drawer_modes.entries() if entry.name.startswith(mode + ":")]\n    if not matches:\n        return False, None\n    if len(matches) != 1:\n        raise RuntimeError(f"drawer mode collision: {mode}")\n    result = matches[0].callback(house, payload, context)\n''',
    '''    entry = next(\n        (candidate for candidate in _drawer_modes.entries() if candidate.name == mode),\n        None,\n    )\n    if entry is None:\n        return False, None\n    result = entry.callback(house, payload, context)\n''',
)

drawer = SRC / "image_drawer_continuation.py"
replace(
    drawer,
    '''    raise ValueError(f"unsupported registered image drawer mode: {mode}")\n''',
    '''    return house._image_drawer_core(payload, _context)\n''',
)

tests = TESTS / "test_runtime_bootstrap.py"
text = tests.read_text(encoding="utf-8")
addition = '''\n\ndef test_image_drawer_extension_delegates_legacy_core_modes() -> None:\n    from vestigia.image_drawer_continuation import _drawer_mode_handler\n\n    class House:\n        def _require_images(self):\n            return object()\n\n        def _image_drawer_core(self, payload, context):\n            return {\n                "delegated": True,\n                "mode": payload["mode"],\n                "context": context,\n            }\n\n    result = _drawer_mode_handler(House(), {"mode": "get"}, {"source": "test"})\n    assert result == {\n        "delegated": True,\n        "mode": "get",\n        "context": {"source": "test"},\n    }\n'''
if "test_image_drawer_extension_delegates_legacy_core_modes" not in text:
    tests.write_text(text.rstrip() + addition + "\n", encoding="utf-8")

Path(__file__).unlink()
