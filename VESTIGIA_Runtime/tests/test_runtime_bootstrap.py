from __future__ import annotations

from vestigia.bootstrap import bootstrap_runtime, installation_plan
from vestigia.house_tools import HousePort
from vestigia.runtime import CoreRuntime


def test_runtime_bootstrap_plan_is_explicit_and_unique() -> None:
    plan = installation_plan()
    assert plan == (
        ("sensory_apparatus", "install_core"),
        ("attention_apparatus", "install_core"),
        ("attention_keyring", "install_core"),
        ("image_drawer_continuation", "install_core"),
        ("workshop_sandbox", "install_core"),
    )
    assert len(plan) == len(set(plan))


def test_runtime_bootstrap_is_idempotent() -> None:
    before = (
        HousePort._install_capabilities,
        HousePort._image_drawer,
        CoreRuntime.chat,
        CoreRuntime._run_curation_if_due,
        CoreRuntime._format_resident_receipts,
    )

    bootstrap_runtime()
    bootstrap_runtime()

    after = (
        HousePort._install_capabilities,
        HousePort._image_drawer,
        CoreRuntime.chat,
        CoreRuntime._run_curation_if_due,
        CoreRuntime._format_resident_receipts,
    )
    assert after == before
