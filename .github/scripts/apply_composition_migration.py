from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "VESTIGIA_Runtime" / "src" / "vestigia"
TESTS = ROOT / "VESTIGIA_Runtime" / "tests"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def must_replace(path: Path, old: str, new: str, *, count: int = 1) -> None:
    text = read(path)
    actual = text.count(old)
    if actual < count:
        raise RuntimeError(f"{path}: expected at least {count} occurrence(s) of {old!r}, found {actual}")
    write(path, text.replace(old, new, count))


def replace_from(path: Path, marker: str, replacement: str) -> None:
    text = read(path)
    index = text.find(marker)
    if index < 0:
        raise RuntimeError(f"{path}: marker not found: {marker!r}")
    write(path, text[:index] + replacement)


COMPOSITION = '''from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable


Callback = Callable[..., Any]


@dataclass(frozen=True)
class CompositionEntry:
    name: str
    order: int
    callback: Callback


class OrderedRegistry:
    """Collision-checked, deterministic registry used by built-in composition."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._entries: dict[str, CompositionEntry] = {}
        self._frozen = False
        self._lock = RLock()

    @property
    def frozen(self) -> bool:
        return self._frozen

    def register(self, name: str, callback: Callback, *, order: int = 100) -> None:
        normalized = str(name).strip().lower()
        if not normalized or normalized != name:
            raise ValueError(f"{self.kind} names must already be normalized")
        if not callable(callback):
            raise TypeError(f"{self.kind} callback must be callable: {name}")
        with self._lock:
            if self._frozen:
                raise RuntimeError(f"{self.kind} registry is frozen")
            if normalized in self._entries:
                raise ValueError(f"duplicate {self.kind}: {normalized}")
            self._entries[normalized] = CompositionEntry(normalized, int(order), callback)

    def freeze(self) -> None:
        with self._lock:
            self._frozen = True

    def entries(self) -> tuple[CompositionEntry, ...]:
        with self._lock:
            return tuple(sorted(self._entries.values(), key=lambda item: (item.order, item.name)))

    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.entries())


_capabilities = OrderedRegistry("capability installer")
_observatory = OrderedRegistry("observatory panel")
_source_explain = OrderedRegistry("source explain enricher")
_chat = OrderedRegistry("chat middleware")
_memory_veto = OrderedRegistry("memory extraction veto")
_curation_veto = OrderedRegistry("curation veto")
_receipt_filter = OrderedRegistry("receipt filter")
_drawer_modes = OrderedRegistry("drawer mode")
_contracts = OrderedRegistry("contract contribution")

_REGISTRIES = (
    _capabilities,
    _observatory,
    _source_explain,
    _chat,
    _memory_veto,
    _curation_veto,
    _receipt_filter,
    _drawer_modes,
    _contracts,
)


def register_capability_installer(name: str, callback: Callback, *, order: int) -> None:
    _capabilities.register(name, callback, order=order)


def register_observatory_panel(name: str, callback: Callback, *, order: int) -> None:
    _observatory.register(name, callback, order=order)


def register_source_explain_enricher(name: str, callback: Callback, *, order: int) -> None:
    _source_explain.register(name, callback, order=order)


def register_chat_middleware(name: str, callback: Callback, *, order: int) -> None:
    _chat.register(name, callback, order=order)


def register_memory_extract_veto(name: str, callback: Callback, *, order: int) -> None:
    _memory_veto.register(name, callback, order=order)


def register_curation_veto(name: str, callback: Callback, *, order: int) -> None:
    _curation_veto.register(name, callback, order=order)


def register_receipt_filter(name: str, callback: Callback, *, order: int) -> None:
    _receipt_filter.register(name, callback, order=order)


def register_drawer_modes(
    name: str,
    modes: tuple[str, ...],
    callback: Callback,
    *,
    order: int,
) -> None:
    for mode in modes:
        normalized = str(mode).strip().lower()
        _drawer_modes.register(f"{normalized}:{name}", callback, order=order)


def register_contract_contribution(
    name: str,
    action: str,
    callback: Callback,
    *,
    order: int,
) -> None:
    _contracts.register(f"{action}:{name}", callback, order=order)


def freeze_composition() -> None:
    for registry in _REGISTRIES:
        registry.freeze()


def install_capability_contributions(house: Any) -> None:
    for entry in _capabilities.entries():
        entry.callback(house)


def render_observatory(
    house: Any,
    payload: dict[str, Any],
    core: Callback,
) -> dict[str, Any]:
    result = core(house, payload)
    for entry in _observatory.entries():
        updated = entry.callback(house, payload, result)
        if not isinstance(updated, dict):
            raise TypeError(f"observatory panel {entry.name} returned a non-object")
        result = updated
    return result


def enrich_source_explain(
    db: Any,
    resident_id: str,
    event_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    current = result
    for entry in _source_explain.entries():
        updated = entry.callback(db, resident_id, event_id, current)
        if not isinstance(updated, dict):
            raise TypeError(f"source explain enricher {entry.name} returned a non-object")
        current = updated
    return current


def run_chat_middleware(
    runtime: Any,
    message: Any,
    model_route: str,
    core: Callback,
) -> Any:
    def invoke(current_message: Any, current_route: str) -> Any:
        return core(current_message, model_route=current_route)

    next_call = invoke
    for entry in reversed(_chat.entries()):
        following = next_call

        def wrapped(
            current_message: Any,
            current_route: str,
            *,
            _entry: CompositionEntry = entry,
            _next: Callback = following,
        ) -> Any:
            return _entry.callback(runtime, current_message, current_route, _next)

        next_call = wrapped
    return next_call(message, model_route)


def run_memory_extract(
    service: Any,
    text: str,
    turn_id: str,
    core: Callback,
) -> list[str]:
    for entry in _memory_veto.entries():
        if bool(entry.callback(service, text, turn_id)):
            return []
    return core(text, turn_id)


def run_curation(runtime: Any, core: Callback, **kwargs: Any) -> list[str]:
    for entry in _curation_veto.entries():
        if bool(entry.callback(runtime, kwargs)):
            return []
    return core(**kwargs)


def filter_receipts(receipts: list[str], *, compact: bool) -> list[str]:
    current = list(receipts)
    for entry in _receipt_filter.entries():
        updated = entry.callback(current, compact=compact)
        if not isinstance(updated, list) or any(not isinstance(item, str) for item in updated):
            raise TypeError(f"receipt filter {entry.name} returned an invalid list")
        current = updated
    return current


def dispatch_drawer_mode(
    house: Any,
    payload: dict[str, Any],
    context: dict[str, Any],
) -> tuple[bool, dict[str, Any] | None]:
    mode = str(payload.get("mode") or "browse").strip().lower()
    matches = [entry for entry in _drawer_modes.entries() if entry.name.startswith(mode + ":")]
    if not matches:
        return False, None
    if len(matches) != 1:
        raise RuntimeError(f"drawer mode collision: {mode}")
    result = matches[0].callback(house, payload, context)
    if not isinstance(result, dict):
        raise TypeError(f"drawer mode {mode} returned a non-object")
    return True, result


def apply_contract_contributions(
    action: str,
    fields: dict[str, Any],
    required: tuple[str, ...],
    examples: tuple[dict[str, Any], ...] | None,
    group: str,
    related: tuple[str, ...],
) -> tuple[
    dict[str, Any],
    tuple[str, ...],
    tuple[dict[str, Any], ...] | None,
    str,
    tuple[str, ...],
]:
    current = (dict(fields), tuple(required), examples, group, tuple(related))
    prefix = action + ":"
    for entry in _contracts.entries():
        if not entry.name.startswith(prefix):
            continue
        current = entry.callback(*current)
        if not isinstance(current, tuple) or len(current) != 5:
            raise TypeError(f"contract contribution {entry.name} returned an invalid contract")
    return current


def composition_plan() -> dict[str, Any]:
    return {
        "frozen": all(registry.frozen for registry in _REGISTRIES),
        "capability_installers": list(_capabilities.names()),
        "observatory_panels": list(_observatory.names()),
        "source_explain_enrichers": list(_source_explain.names()),
        "chat_middleware": list(_chat.names()),
        "memory_vetoes": list(_memory_veto.names()),
        "curation_vetoes": list(_curation_veto.names()),
        "receipt_filters": list(_receipt_filter.names()),
        "drawer_modes": list(_drawer_modes.names()),
        "contract_contributions": list(_contracts.names()),
    }
'''
write(SRC / "composition.py", COMPOSITION)

BOOTSTRAP = '''from __future__ import annotations

from importlib import import_module
from threading import RLock
from typing import Final

from .composition import freeze_composition


_INSTALLATION_PLAN: Final[tuple[tuple[str, str], ...]] = (
    ("sensory_apparatus", "register_composition"),
    ("attention_apparatus", "register_composition"),
    ("attention_keyring", "register_composition"),
    ("image_drawer_continuation", "register_composition"),
    ("workshop_sandbox", "register_composition"),
)

_lock = RLock()
_registered = False


def installation_plan() -> tuple[tuple[str, str], ...]:
    return _INSTALLATION_PLAN


def bootstrap_runtime() -> None:
    """Register and freeze built-in runtime composition exactly once."""

    global _registered
    with _lock:
        if _registered:
            return
        seen: set[tuple[str, str]] = set()
        for module_name, registrar_name in _INSTALLATION_PLAN:
            key = (module_name, registrar_name)
            if key in seen:
                raise RuntimeError(
                    f"duplicate runtime composition entry: {module_name}.{registrar_name}"
                )
            seen.add(key)
            module = import_module(f".{module_name}", package=__package__)
            registrar = getattr(module, registrar_name, None)
            if not callable(registrar):
                raise RuntimeError(
                    f"required runtime registrar is unavailable: {module_name}.{registrar_name}"
                )
            registrar()
        freeze_composition()
        _registered = True
'''
write(SRC / "bootstrap.py", BOOTSTRAP)
write(SRC / "__init__.py", '"""VESTIGIA portable continuity runtime."""\n\n__version__ = "0.8.0.dev0"\n')

# CapabilityRegistry gains a public, validated spec-replacement seam.
capabilities = SRC / "capabilities.py"
must_replace(
    capabilities,
    "from dataclasses import asdict, dataclass, field",
    "from dataclasses import asdict, dataclass, field, replace",
)
must_replace(
    capabilities,
    "    def spec(self, name: str) -> CapabilitySpec:\n",
    '''    def replace_spec(self, name: str, **changes: Any) -> CapabilitySpec:\n        normalized = name.strip().lower()\n        current = self.spec(normalized)\n        updated = replace(current, **changes)\n        if updated.name != normalized:\n            raise ValueError("replacement capability name must not change")\n        self._specs[normalized] = updated\n        return updated\n\n    def spec(self, name: str) -> CapabilitySpec:\n''',
)

# HousePort invokes explicit composition rather than receiving class mutation.
house = SRC / "house_tools.py"
must_replace(
    house,
    "        self.registry = CapabilityRegistry(config)\n",
    "        from .bootstrap import bootstrap_runtime\n\n        bootstrap_runtime()\n        self.registry = CapabilityRegistry(config)\n",
)
must_replace(
    house,
    "    def _install_capabilities(self) -> None:\n",
    '''    def _install_capabilities(self) -> None:\n        self._install_core_capabilities()\n        from .composition import install_capability_contributions\n\n        install_capability_contributions(self)\n\n    def _install_core_capabilities(self) -> None:\n''',
)
must_replace(
    house,
    "    def _image_drawer(",
    '''    def _image_drawer(\n        self, payload: dict[str, Any], context: dict[str, Any]\n    ) -> dict[str, Any]:\n        from .composition import dispatch_drawer_mode\n\n        handled, result = dispatch_drawer_mode(self, payload, context)\n        if handled:\n            assert result is not None\n            return result\n        return self._image_drawer_core(payload, context)\n\n    def _image_drawer_core(''',
)

# CoreRuntime uses registered middleware/veto/filter seams.
runtime = SRC / "runtime.py"
must_replace(
    runtime,
    "    def _chat_unlocked(self, message: NormalizedMessage, *, model_route: str = \"default\") -> RuntimeResult:\n",
    '''    def _chat_unlocked(self, message: NormalizedMessage, *, model_route: str = "default") -> RuntimeResult:\n        from .composition import run_chat_middleware\n\n        return run_chat_middleware(\n            self, message, model_route, self._chat_core_unlocked\n        )\n\n    def _chat_core_unlocked(self, message: NormalizedMessage, *, model_route: str = "default") -> RuntimeResult:\n''',
)
must_replace(
    runtime,
    "    @staticmethod\n    def _format_resident_receipts(receipts: list[str], *, compact: bool) -> str:\n",
    '''    @staticmethod\n    def _format_resident_receipts(receipts: list[str], *, compact: bool) -> str:\n        from .composition import filter_receipts\n\n        visible = filter_receipts(receipts, compact=compact)\n        if not visible:\n            return ""\n        return CoreRuntime._format_resident_receipts_core(visible, compact=compact)\n\n    @staticmethod\n    def _format_resident_receipts_core(receipts: list[str], *, compact: bool) -> str:\n''',
)
must_replace(
    runtime,
    "    def _run_curation_if_due(\n",
    '''    def _run_curation_if_due(\n        self,\n        *,\n        input_turn_id: str,\n        assistant_turn_id: str,\n        interface: str,\n        model_route: str,\n    ) -> list[str]:\n        from .composition import run_curation\n\n        return run_curation(\n            self,\n            self._run_curation_core_if_due,\n            input_turn_id=input_turn_id,\n            assistant_turn_id=assistant_turn_id,\n            interface=interface,\n            model_route=model_route,\n        )\n\n    def _run_curation_core_if_due(\n''',
)

# Memory extraction gets an explicit veto seam.
memory = SRC / "memory.py"
must_replace(
    memory,
    "    def extract_from_participant_turn(self, text: str, turn_id: str) -> list[str]:\n",
    '''    def extract_from_participant_turn(self, text: str, turn_id: str) -> list[str]:\n        from .composition import run_memory_extract\n\n        return run_memory_extract(\n            self, text, turn_id, self._extract_from_participant_turn_core\n        )\n\n    def _extract_from_participant_turn_core(self, text: str, turn_id: str) -> list[str]:\n''',
)

# Sensory apparatus owns the base Observatory and registers its other hooks explicitly.
sensory = SRC / "sensory_apparatus.py"
must_replace(sensory, "_CORE_INSTALLED = False\n", "")
must_replace(sensory, "def _observatory(house: Any, payload: dict[str, Any]) -> dict[str, Any]:\n", "def _observatory_core(house: Any, payload: dict[str, Any]) -> dict[str, Any]:\n")
must_replace(
    sensory,
    "\n\ndef _sensory_control(\n",
    '''\n\ndef _observatory(house: Any, payload: dict[str, Any]) -> dict[str, Any]:\n    from .composition import render_observatory\n\n    return render_observatory(house, payload, _observatory_core)\n\n\ndef _sensory_control(\n''',
)
must_replace(
    sensory,
    '''        lambda payload, _context: explain(\n            house.db,\n            house.resident_id,\n            str(payload.get("event_id") or ""),\n            resident_controls.ensure_listening_schema,\n        ),\n''',
    '''        lambda payload, _context: _explain_source(house, payload),\n''',
)
must_replace(
    sensory,
    "\n\ndef _register_capabilities(house: Any) -> None:\n",
    '''\n\ndef _explain_source(house: Any, payload: dict[str, Any]) -> dict[str, Any]:\n    from . import resident_controls\n    from .composition import enrich_source_explain\n\n    event_id = str(payload.get("event_id") or "")\n    result = explain(\n        house.db,\n        house.resident_id,\n        event_id,\n        resident_controls.ensure_listening_schema,\n    )\n    return enrich_source_explain(house.db, house.resident_id, event_id, result)\n\n\ndef _register_capabilities(house: Any) -> None:\n''',
)
replace_from(
    sensory,
    "\n\ndef install_core() -> None:\n",
    '''\n\ndef _memory_extract_veto(_service: Any, _text: str, turn_id: str) -> bool:\n    return _NOTHING_TURN.get() == str(turn_id)\n\n\ndef _curation_veto(_runtime: Any, values: dict[str, Any]) -> bool:\n    return _NOTHING_TURN.get() == str(values.get("input_turn_id") or "")\n\n\ndef _receipt_filter(receipts: list[str], *, compact: bool) -> list[str]:\n    del compact\n    return [\n        item\n        for item in receipts\n        if not item.startswith("tool_action:ok:make.nothing.happen:")\n    ]\n\n\ndef register_composition() -> None:\n    from .composition import (\n        register_capability_installer,\n        register_curation_veto,\n        register_memory_extract_veto,\n        register_receipt_filter,\n    )\n\n    register_capability_installer("sensory", _register_capabilities, order=10)\n    register_memory_extract_veto(\n        "sensory.make_nothing_happen", _memory_extract_veto, order=10\n    )\n    register_curation_veto(\n        "sensory.make_nothing_happen", _curation_veto, order=10\n    )\n    register_receipt_filter(\n        "sensory.make_nothing_happen", _receipt_filter, order=10\n    )\n''',
)

# Attention router registers capabilities and read-only enrichers.
attention = SRC / "attention_apparatus.py"
must_replace(attention, "_INSTALLED = False\n\n", "")
replace_from(
    attention,
    "\n\ndef install_core() -> None:\n",
    '''\n\ndef _observatory_panel(\n    house: Any, payload: dict[str, Any], result: dict[str, Any]\n) -> dict[str, Any]:\n    panels = result.get("observatory")\n    if not isinstance(panels, dict):\n        return result\n    state = report(\n        house.config,\n        house.db,\n        house.resident_id,\n        listening_controls=_listening_controls(house),\n    )\n    summary = {\n        "controls": state,\n        "metrics": metrics(house.db, house.resident_id, hours=24),\n        "recent_decisions": list_events(house.db, house.resident_id, limit=10),\n        "shadow_mode": True,\n        "live_routing_changed": False,\n        "semantic_gate_is_authority": False,\n    }\n    section = str(payload.get("section") or "all").strip().lower()\n    if section == "all":\n        panels["attention_router"] = summary\n    elif "doors" in panels and isinstance(panels["doors"], dict):\n        panels["doors"]["attention_router"] = state\n    return result\n\n\ndef _source_explain_enricher(\n    db: Any, resident_id: str, event_id: str, result: dict[str, Any]\n) -> dict[str, Any]:\n    router_event = by_listening_event(db, resident_id, event_id)\n    if router_event is not None:\n        result["attention_router"] = router_event\n        result["attention_router_boundary"] = (\n            "This was a shadow assessment. It did not widen authority or alter "\n            "the live sensory consequence."\n        )\n    return result\n\n\ndef register_composition() -> None:\n    from .composition import (\n        register_capability_installer,\n        register_observatory_panel,\n        register_source_explain_enricher,\n    )\n\n    register_capability_installer("attention.router", _register, order=20)\n    register_observatory_panel(\n        "attention.router", _observatory_panel, order=20\n    )\n    register_source_explain_enricher(\n        "attention.router", _source_explain_enricher, order=20\n    )\n''',
)

# Attention keyring uses chat middleware and a named Observatory panel.
keyring = SRC / "attention_keyring.py"
must_replace(keyring, "_INSTALLED = False\n\n", "")
replace_from(
    keyring,
    "\n\ndef install_core() -> None:\n",
    '''\n\ndef _chat_middleware(\n    runtime: Any, message: Any, model_route: str, next_call: Any\n) -> Any:\n    wake = consume_wake_context()\n    if message.interface != "discord" or not wake:\n        return next_call(message, model_route)\n    listening_event_id = str(message.metadata.get("listening_event_id") or "").strip()\n    router_event = (\n        by_listening_event(runtime.db, runtime.resident_id, listening_event_id)\n        if listening_event_id\n        else None\n    )\n    context_ids = [\n        str(item)\n        for item in [\n            message.external_id,\n            *list(message.metadata.get("ambient_message_ids") or []),\n        ]\n        if item\n    ]\n    opened = open_wake_receipt(\n        runtime.db,\n        resident_id=runtime.resident_id,\n        room_id=runtime.room_id,\n        interface="discord",\n        channel_id=str(\n            message.metadata.get("channel_id") or wake.get("channel_id") or ""\n        ),\n        message_id=str(\n            message.external_id\n            or message.metadata.get("triggering_message_id")\n            or ""\n        ),\n        listening_event_id=listening_event_id or None,\n        signal_kind=str(wake.get("signal_kind") or "unknown"),\n        reason_code=str(wake.get("reason_code") or "inherited_live_route"),\n        live_route=str(wake.get("live_route") or "invite"),\n        router_event=router_event,\n        included_context_ids=context_ids,\n    )\n    try:\n        result = next_call(message, model_route)\n    except Exception:\n        complete_wake_receipt(\n            runtime.db,\n            runtime.resident_id,\n            str(opened["id"]),\n            turn_id=None,\n            status="runtime_error",\n            response_prepared=None,\n        )\n        raise\n    prepared = bool(\n        result.text or result.outbound_attachments or result.outbound_reactions\n    )\n    complete_wake_receipt(\n        runtime.db,\n        runtime.resident_id,\n        str(opened["id"]),\n        turn_id=str(result.turn_id),\n        status="runtime_suppressed" if result.suppressed else "completed",\n        response_prepared=prepared,\n    )\n    return result\n\n\ndef _observatory_panel(\n    house: Any, payload: dict[str, Any], result: dict[str, Any]\n) -> dict[str, Any]:\n    panels = result.get("observatory")\n    if isinstance(panels, dict) and str(payload.get("section") or "all") == "all":\n        panels["attention_dashboard"] = _dashboard(house, {"limit": 10})\n    return result\n\n\ndef register_composition() -> None:\n    from .composition import (\n        register_capability_installer,\n        register_chat_middleware,\n        register_observatory_panel,\n    )\n\n    register_capability_installer("attention.keyring", _register, order=30)\n    register_chat_middleware(\n        "attention.keyring.wake_receipt", _chat_middleware, order=30\n    )\n    register_observatory_panel(\n        "attention.keyring", _observatory_panel, order=30\n    )\n''',
)

# Image drawer registers mode handlers and a pure contract contribution.
drawer = SRC / "image_drawer_continuation.py"
must_replace(drawer, "_INSTALLED = False\n", "")
replace_from(
    drawer,
    "\n\ndef _extend_contract() -> None:\n",
    '''\n\ndef _contract_contribution(\n    fields: dict[str, Any],\n    required: tuple[str, ...],\n    examples: tuple[dict[str, Any], ...] | None,\n    group: str,\n    related: tuple[str, ...],\n) -> tuple[\n    dict[str, Any],\n    tuple[str, ...],\n    tuple[dict[str, Any], ...] | None,\n    str,\n    tuple[str, ...],\n]:\n    from . import capability_contracts as contracts\n\n    updated = dict(fields)\n    updated["mode"] = contracts.S(enum=list(_DRAWER_MODES))\n    updated.update(\n        {\n            "cursor": contracts.ID,\n            "bookmark_id": contracts.ID,\n            "label": contracts.S(maxLength=240),\n            "note": contracts.S(maxLength=2000),\n        }\n    )\n    existing = tuple(examples or ())\n    continuation_examples = (\n        {\n            "action": "image.drawer",\n            "mode": "continue",\n            "cursor": "drawer_cursor_...",\n            "after": "continue",\n        },\n        {\n            "action": "image.drawer",\n            "mode": "bookmark",\n            "cursor": "drawer_cursor_...",\n            "label": "Mall reactions, page 4",\n            "after": "continue",\n        },\n        {\n            "action": "image.drawer",\n            "mode": "open_bookmark",\n            "bookmark_id": "drawer_bookmark_...",\n            "after": "continue",\n        },\n    )\n    return updated, required, existing + continuation_examples, group, related\n\n\ndef _drawer_mode_handler(\n    house: Any, payload: dict[str, Any], _context: dict[str, Any]\n) -> dict[str, Any]:\n    images = house._require_images()\n    mode = str(payload.get("mode") or "browse").strip().lower()\n    if mode in {"browse", "search"}:\n        return start_page(images, payload)\n    if mode == "continue":\n        return continue_page(images, payload)\n    if mode == "bookmark":\n        return bookmark_position(images, payload)\n    if mode == "open_bookmark":\n        return open_bookmark(images, payload)\n    if mode == "list_bookmarks":\n        return list_bookmarks(images, payload)\n    if mode == "remove_bookmark":\n        return remove_bookmark(images, payload)\n    raise ValueError(f"unsupported registered image drawer mode: {mode}")\n\n\ndef _refresh_spec(house: Any) -> None:\n    try:\n        house.registry.replace_spec(\n            "image.drawer",\n            description=(\n                "Browse, search, resume, bookmark, name, annotate, summarize, "\n                "pocket, or inspect resident-owned image memory cards through "\n                "stable private collection snapshots."\n            ),\n            next_step=(\n                "Use pagination.next_cursor with mode:continue, or preserve the "\n                "current cursor with mode:bookmark."\n            ),\n        )\n    except ValueError:\n        return\n\n\ndef register_composition() -> None:\n    from .composition import (\n        register_capability_installer,\n        register_contract_contribution,\n        register_drawer_modes,\n    )\n\n    register_drawer_modes(\n        "image.drawer.continuation", _DRAWER_MODES, _drawer_mode_handler, order=40\n    )\n    register_contract_contribution(\n        "image.drawer.continuation",\n        "image.drawer",\n        _contract_contribution,\n        order=40,\n    )\n    register_capability_installer(\n        "image.drawer.continuation", _refresh_spec, order=40\n    )\n''',
)

# Workshop sandbox registers capability and Observatory panel without class mutation.
workshop = SRC / "workshop_sandbox.py"
must_replace(workshop, "_INSTALLED = False\n", "")
replace_from(
    workshop,
    "\n\ndef install_core() -> None:\n",
    '''\n\ndef _observatory_panel(\n    house: Any, payload: dict[str, Any], result: dict[str, Any]\n) -> dict[str, Any]:\n    panels = result.get("observatory")\n    if isinstance(panels, dict) and str(payload.get("section") or "all") == "all":\n        panels["workshop_sandbox"] = _observatory_summary(house)\n    return result\n\n\ndef register_composition() -> None:\n    from .composition import register_capability_installer, register_observatory_panel\n\n    register_capability_installer("workshop.sandbox", _register, order=50)\n    register_observatory_panel(\n        "workshop.sandbox", _observatory_panel, order=50\n    )\n''',
)

# Capability contracts consume pure contributions rather than mutable globals.
contracts = SRC / "capability_contracts.py"
text = read(contracts)
start = text.index("def contract_for(name: str) -> dict[str, Any]:\n")
end = text.index("\n\ndef bell_contracts()", start)
contract_for = '''def contract_for(name: str) -> dict[str, Any]:\n    from .bootstrap import bootstrap_runtime\n    from .composition import apply_contract_contributions\n\n    bootstrap_runtime()\n    fields, required = FIELDS[name]\n    examples = EXAMPLES.get(name)\n    fields, required, examples, group, related = apply_contract_contributions(\n        name,\n        fields,\n        required,\n        examples,\n        GROUPS.get(name, "other"),\n        RELATED.get(name, ()),\n    )\n    properties = {\n        "action": {"type": "string", "const": name},\n        "after": AFTER,\n        **fields,\n    }\n    schema = object_schema(\n        properties,\n        required=("action", *required),\n        additional=False,\n        description=f"Executable input contract for {name}.",\n    )\n    if name == "image.share":\n        schema["confirm"] = "required only for private send or legacy claim"\n    if examples is None:\n        payload: dict[str, Any] = {"action": name, "after": "continue"}\n        for field in required:\n            payload[field] = _sample(properties[field], field)\n        examples = (payload,)\n    return {\n        "input_schema": schema,\n        "example_envelopes": examples,\n        "group": group,\n        "related_actions": related,\n    }\n'''
write(contracts, text[:start] + contract_for + text[end:])

# Doctor reports the exact frozen composition plan.
diagnostics = SRC / "diagnostics.py"
must_replace(
    diagnostics,
    "        \"database\": database,\n",
    '''        "composition": __import__(\n            "vestigia.composition", fromlist=["composition_plan"]\n        ).composition_plan(),\n        "database": database,\n''',
)

# Replace transitional tests with registry, import-side-effect, and mutation regressions.
TEST = '''from __future__ import annotations

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
    )
    forbidden = (
        "HousePort._install_capabilities =",
        "HousePort._image_drawer =",
        "CoreRuntime.chat =",
        "CoreRuntime._run_curation_if_due =",
        "CoreRuntime._format_resident_receipts =",
        "MemoryService.extract_from_participant_turn =",
        "sensory_apparatus._observatory =",
        "sensory_apparatus.explain =",
        "registry._specs",
        "contracts.FIELDS[",
        "contracts.EXAMPLES[",
    )
    for name in names:
        text = (root / name).read_text(encoding="utf-8")
        assert "def install_core" not in text
        for marker in forbidden:
            assert marker not in text, f"{name} still contains {marker}"
'''
write(TESTS / "test_runtime_bootstrap.py", TEST)

# Remove the one-shot migration payload from the final tree.
(Path(__file__).resolve()).unlink()
workflow = ROOT / ".github" / "workflows" / "apply-composition-migration.yml"
workflow.unlink(missing_ok=True)
