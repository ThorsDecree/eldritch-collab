from __future__ import annotations

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
