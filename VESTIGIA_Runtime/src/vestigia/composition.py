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
            return tuple(
                sorted(self._entries.values(), key=lambda item: (item.order, item.name))
            )

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
_workbench_providers = OrderedRegistry("workbench provider")
_context_sources = OrderedRegistry("context source factory")

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
    _workbench_providers,
    _context_sources,
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
    del name
    for mode in modes:
        normalized = str(mode).strip().lower()
        if not normalized:
            raise ValueError("drawer mode names must not be empty")
        _drawer_modes.register(normalized, callback, order=order)


def register_contract_contribution(
    name: str,
    action: str,
    callback: Callback,
    *,
    order: int,
) -> None:
    _contracts.register(f"{action}:{name}", callback, order=order)


def register_workbench_provider(name: str, callback: Callback, *, order: int) -> None:
    """Register one bounded provider of resident-facing Workbench affordances.

    Provider callbacks receive ``(house, request)``. ``request['mode']`` is either
    ``view`` or ``act``. View responses declare ``implemented_lanes`` and ``cards``;
    act responses are provider-owned semantic results. The composition layer owns
    deterministic ordering, collision rejection, freezing, card-shape validation,
    de-duplication, and the global result ceiling.
    """

    _workbench_providers.register(name, callback, order=order)


def register_context_source_factory(name: str, callback: Callback, *, order: int) -> None:
    """Register a deployment-time constructor for an optional context source.

    Factory callbacks receive ``(config, db)`` and return either one ContextSource or
    ``None`` when that source is not configured for the deployment. Factories are
    composition-time declarations only; they do not receive a turn or perform retrieval
    until ContextAssembler asks the returned source for bounded evidence.
    """

    _context_sources.register(name, callback, order=order)


def freeze_composition() -> None:
    for registry in _REGISTRIES:
        registry.freeze()


def install_capability_contributions(house: Any) -> None:
    for entry in _capabilities.entries():
        entry.callback(house)


def build_context_sources(config: Any, db: Any) -> list[Any]:
    """Construct configured optional context sources in deterministic order."""
    sources: list[Any] = []
    names: set[str] = set()
    for entry in _context_sources.entries():
        source = entry.callback(config, db)
        if source is None:
            continue
        source_name = str(getattr(source, "name", "")).strip().lower()
        if not source_name or source_name != getattr(source, "name", None):
            raise ValueError(
                f"context source factory {entry.name} returned an invalid normalized name"
            )
        if source_name in names:
            raise ValueError(f"duplicate constructed context source: {source_name}")
        if not callable(getattr(source, "retrieve", None)):
            raise TypeError(
                f"context source factory {entry.name} returned an object without retrieve()"
            )
        names.add(source_name)
        sources.append(source)
    return sources


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
        if not isinstance(updated, list) or any(
            not isinstance(item, str) for item in updated
        ):
            raise TypeError(f"receipt filter {entry.name} returned an invalid list")
        current = updated
    return current


def dispatch_drawer_mode(
    house: Any,
    payload: dict[str, Any],
    context: dict[str, Any],
) -> tuple[bool, dict[str, Any] | None]:
    mode = str(payload.get("mode") or "browse").strip().lower()
    entry = next(
        (candidate for candidate in _drawer_modes.entries() if candidate.name == mode),
        None,
    )
    if entry is None:
        return False, None
    result = entry.callback(house, payload, context)
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
            raise TypeError(
                f"contract contribution {entry.name} returned an invalid contract"
            )
    return current


def render_workbench_cards(
    house: Any,
    *,
    lane: str,
    limit: int,
) -> dict[str, Any]:
    """Render bounded cards from every registered Workbench provider."""

    requested = max(1, int(limit))
    cards: list[dict[str, Any]] = []
    card_ids: set[str] = set()
    implemented_lanes: set[str] = set()
    providers: list[str] = []

    for entry in _workbench_providers.entries():
        remaining = max(0, requested - len(cards))
        result = entry.callback(
            house,
            {"mode": "view", "lane": lane, "limit": remaining},
        )
        if not isinstance(result, dict):
            raise TypeError(f"workbench provider {entry.name} returned a non-object")
        raw_lanes = result.get("implemented_lanes", [])
        if not isinstance(raw_lanes, list) or any(
            not isinstance(item, str) for item in raw_lanes
        ):
            raise TypeError(
                f"workbench provider {entry.name} returned invalid implemented_lanes"
            )
        provider_lanes = {item.strip().lower() for item in raw_lanes if item.strip()}
        implemented_lanes.update(provider_lanes)

        raw_cards = result.get("cards", [])
        if not isinstance(raw_cards, list) or any(
            not isinstance(item, dict) for item in raw_cards
        ):
            raise TypeError(f"workbench provider {entry.name} returned invalid cards")
        providers.append(entry.name)
        for card in raw_cards:
            card_lane = str(card.get("lane") or "").strip().lower()
            if card_lane not in provider_lanes:
                raise ValueError(
                    f"workbench provider {entry.name} emitted undeclared lane {card_lane!r}"
                )
            if lane != "all" and card_lane != lane:
                raise ValueError(
                    f"workbench provider {entry.name} emitted lane {card_lane!r} for request {lane!r}"
                )
            provider_name = str(card.get("provider") or "").strip().lower()
            if provider_name != entry.name:
                raise ValueError(
                    f"workbench provider {entry.name} emitted mismatched provider {provider_name!r}"
                )
            card_id = str(card.get("card_id") or "").strip()
            if not card_id:
                raise ValueError(
                    f"workbench provider {entry.name} emitted a card without card_id"
                )
            if card_id in card_ids:
                raise ValueError(f"duplicate workbench card_id from providers: {card_id}")
            card_ids.add(card_id)
            cards.append(card)
            if len(cards) >= requested:
                break

    return {
        "cards": cards,
        "implemented_lanes": sorted(implemented_lanes),
        "providers": providers,
    }


def dispatch_workbench_action(
    house: Any,
    *,
    card: dict[str, Any],
    action_id: str,
    max_tokens: int,
    context: dict[str, Any],
) -> dict[str, Any]:
    provider_name = str(card.get("provider") or "").strip().lower()
    entry = next(
        (
            candidate
            for candidate in _workbench_providers.entries()
            if candidate.name == provider_name
        ),
        None,
    )
    if entry is None:
        raise KeyError(
            "workbench card provider is unavailable; refresh workbench.view"
        )
    result = entry.callback(
        house,
        {
            "mode": "act",
            "card": card,
            "action_id": action_id,
            "max_tokens": max_tokens,
            "context": dict(context),
        },
    )
    if not isinstance(result, dict):
        raise TypeError(
            f"workbench provider {entry.name} returned a non-object action result"
        )
    return result


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
        "workbench_providers": list(_workbench_providers.names()),
        "context_source_factories": list(_context_sources.names()),
    }
