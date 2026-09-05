from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from vestigia.bootstrap import bootstrap_runtime, installation_plan
from vestigia.composition import OrderedRegistry, composition_plan
from vestigia.house_tools import HousePort
from vestigia.runtime import CoreRuntime


def test_package_import_does_not_bootstrap_runtime() -> None:
    code = (
        "import vestigia; "
        "from vestigia.composition import composition_plan; "
        "assert composition_plan()['frozen'] is False"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_runtime_bootstrap_plan_is_explicit_and_unique() -> None:
    plan = installation_plan()
    assert plan == (
        ("sensory_apparatus", "register_composition"),
        ("attention_apparatus", "register_composition"),
        ("attention_keyring", "register_composition"),
        ("image_drawer_continuation", "register_composition"),
        ("workshop_sandbox", "register_composition"),
        ("workshop_script_shelf", "register_composition"),
        ("workshop_microscope", "register_composition"),
        ("document_formats", "register_composition"),
        ("navigation_hardening", "register_composition"),
        ("navigation_bookmark_compat", "register_composition"),
        ("library_window", "register_composition"),
        ("library_window_lifecycle", "register_composition"),
        ("gaming_tools", "register_composition"),
        ("workbench", "register_composition"),
        ("mcp_context_source", "register_composition"),
        ("context_introspection", "register_composition"),
    )
    assert len(plan) == len(set(plan))


def test_runtime_bootstrap_is_idempotent_without_class_replacement() -> None:
    before = (
        HousePort._install_capabilities,
        HousePort._image_drawer,
        CoreRuntime.chat,
        CoreRuntime._run_curation_if_due,
        CoreRuntime._format_resident_receipts,
    )
    bootstrap_runtime()
    first = composition_plan()
    bootstrap_runtime()
    second = composition_plan()
    after = (
        HousePort._install_capabilities,
        HousePort._image_drawer,
        CoreRuntime.chat,
        CoreRuntime._run_curation_if_due,
        CoreRuntime._format_resident_receipts,
    )
    assert after == before
    assert first == second
    assert first["frozen"] is True
    assert first["capability_installers"]


def test_registry_rejects_collisions_and_late_registration() -> None:
    registry = OrderedRegistry("fixture")
    registry.register("alpha", lambda: None, order=10)
    with pytest.raises(ValueError, match="duplicate fixture"):
        registry.register("alpha", lambda: None, order=20)
    registry.freeze()
    with pytest.raises(RuntimeError, match="frozen"):
        registry.register("beta", lambda: None)


def test_production_feature_modules_do_not_assign_private_runtime_methods() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "vestigia"
    names = (
        "sensory_apparatus.py",
        "attention_apparatus.py",
        "attention_keyring.py",
        "image_drawer_continuation.py",
        "workshop_sandbox.py",
        "library_window.py",
        "library_window_lifecycle.py",
        "navigation_hardening.py",
        "navigation_bookmark_compat.py",
    )
    forbidden = (
        "HousePort._install_capabilities =",
        "HousePort._image_drawer =",
        "HousePort._read =",
        "HousePort._continue =",
        "HousePort._bookmark_open =",
        "CoreRuntime.chat =",
        "CoreRuntime._run_curation_if_due =",
        "CoreRuntime._format_resident_receipts =",
        "MemoryService.extract_from_participant_turn =",
        "sensory_apparatus._observatory =",
        "sensory_apparatus.explain =",
        "registry._specs",
        "registry._handlers",
        "contracts.FIELDS[",
        "contracts.EXAMPLES[",
    )
    for name in names:
        text = (root / name).read_text(encoding="utf-8")
        assert "def install_core" not in text
        for marker in forbidden:
            assert marker not in text, f"{name} still contains {marker}"


def test_image_drawer_extension_delegates_legacy_core_modes() -> None:
    from vestigia.image_drawer_continuation import _drawer_mode_handler

    class House:
        def _require_images(self):
            return object()

        def _image_drawer_core(self, payload, context):
            return {
                "delegated": True,
                "mode": payload["mode"],
                "context": context,
            }

    result = _drawer_mode_handler(House(), {"mode": "get"}, {"source": "test"})
    assert result == {
        "delegated": True,
        "mode": "get",
        "context": {"source": "test"},
    }
