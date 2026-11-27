from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import ProviderReply

if TYPE_CHECKING:
    from .legible import LegibleLedger


class ModelApprovalRequired(PermissionError):
    def __init__(self, *, turn_id: str, receipt_id: str, earlier_tool_attempts: int,
                 pre_dispatch: bool = False, notice: str | None = None) -> None:
        self.receipt_id = receipt_id
        self.earlier_tool_attempts = earlier_tool_attempts
        message = (
            f"⚠️ Model approval required. The Runtime stopped before the provider request. Inspect turn {turn_id}."
            if pre_dispatch else
            "⚠️ Model approval required. The Runtime paused before acting on this reply. "
            f"Inspect turn {turn_id}."
        )
        if notice is not None:
            message = notice
        if earlier_tool_attempts:
            message += " Earlier approved rounds may already have acted."
        super().__init__(message)


class ModelApprovalGate:
    """Check observable response identity before handing output to effectful consumers."""

    def __init__(self, policy: object, ledger: LegibleLedger) -> None:
        if not isinstance(policy, dict) or set(policy) - {"enabled", "routes", "pre_dispatch"}:
            raise ValueError("model_approval must contain only enabled, pre_dispatch and routes")
        enabled = policy.get("enabled", False)
        pre_dispatch = policy.get("pre_dispatch", False)
        routes = policy.get("routes", {})
        if not isinstance(enabled, bool) or not isinstance(pre_dispatch, bool) or not isinstance(routes, dict):
            raise ValueError("model_approval.enabled and pre_dispatch must be booleans and routes a mapping")
        if pre_dispatch and not enabled:
            raise ValueError("pre_dispatch requires model_approval.enabled")
        self.enabled = enabled
        self.pre_dispatch = pre_dispatch
        self.ledger = ledger
        self.routes: dict[str, dict[str, Any]] = {}
        for route, identity in routes.items():
            if not isinstance(route, str) or not route or route != route.strip().lower():
                raise ValueError("model_approval routes must be lowercase route names")
            if not isinstance(identity, dict) or set(identity) != {
                "provider", "model", "reported_model"
            }:
                raise ValueError("Each model approval requires provider, model, and reported_model")
            if any(not self._identifier(identity[field]) for field in ("provider", "model")):
                raise ValueError("Approved provider and model IDs must be nonempty strings")
            reported = identity["reported_model"]
            if reported is not None and not self._identifier(reported):
                raise ValueError("Approved reported_model must be a nonempty string or null")
            self.routes[route] = dict(identity)

    @staticmethod
    def _identifier(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip()) and value == value.strip()

    def admit(self, provider: Any, *, route: str, turn_id: str, phase: str) -> None:
        """Check requested identity before private context or provider dispatch.

        This is an adapter's declared request identity, not a provider attestation.
        An adapter without the contract is denied when the boundary is enabled.
        """
        if not self.pre_dispatch:
            return
        normalized = route.strip().lower() or "default"
        approved = self.routes.get(normalized)
        identity_fn = getattr(provider, "admission_identity", None)
        try:
            observed = identity_fn(normalized) if callable(identity_fn) else None
        except Exception:
            observed = None
        valid = isinstance(observed, dict) and set(observed) == {"provider", "model"} and all(
            self._identifier(observed[field]) for field in ("provider", "model")
        )
        allowed = bool(valid and approved is not None and all(
            observed[field] == approved[field] for field in ("provider", "model")
        ))
        reason = "approved_request" if allowed else (
            "route_not_approved" if approved is None else
            "request_identity_unknown" if not valid else "request_identity_mismatch"
        )
        receipt_id = self.ledger.record_receipt(
            action="model.admission", status="succeeded" if allowed else "refused",
            turn_id=turn_id, source_envelope="RUNTIME_MODEL_ADMISSION",
            result={"route": normalized, "phase": phase, "reason": reason,
                    "approved": approved, "requested": observed if valid else None,
                    "reported_model": None},
        )
        if not allowed:
            raise ModelApprovalRequired(turn_id=turn_id, receipt_id=receipt_id,
                                        earlier_tool_attempts=0, pre_dispatch=True)

    def check(
        self,
        reply: ProviderReply,
        *,
        route: str,
        turn_id: str,
        invocation: str,
        call_index: int,
        earlier_tool_attempts: int = 0,
    ) -> None:
        if not self.enabled:
            return
        route = route.strip().lower() or "default"
        observed = {
            "provider": reply.provider,
            "model": reply.model,
            "reported_model": reply.reported_model,
        }
        approved = self.routes.get(route)
        allowed = approved == observed
        reason = "approved_identity" if allowed else (
            "route_not_approved" if approved is None else "identity_mismatch"
        )
        receipt_id = self.ledger.record_receipt(
            action="model.approval_gate",
            status="succeeded" if allowed else "refused",
            turn_id=turn_id,
            source_envelope="RUNTIME_MODEL_APPROVAL",
            result={
                "route": route,
                "invocation": invocation,
                "call_index": call_index,
                "reason": reason,
                "approved": approved,
                "observed": observed,
                "response_id": reply.response_id,
                "earlier_tool_attempts": earlier_tool_attempts,
            },
        )
        if not allowed:
            raise ModelApprovalRequired(
                turn_id=turn_id,
                receipt_id=receipt_id,
                earlier_tool_attempts=earlier_tool_attempts,
            )
