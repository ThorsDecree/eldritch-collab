from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


class SenseRegistryError(ValueError):
    """Raised when a sense-organ manifest or request is not safe to use."""


_REQUIRED_FIELDS = {
    "organ_id",
    "schema_version",
    "version",
    "display_name",
    "modality",
    "activation_topology",
    "invocation_surface",
    "consent_basis",
    "perception_scope",
    "allowed_payloads",
    "prohibited_payloads",
    "retention",
    "destinations",
    "limits",
    "receipt_schema",
    "semantic_policy",
    "status",
}


def _canonical_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


@dataclass(frozen=True)
class SenseOrganManifest:
    """Declarative, fail-closed contract for one bounded sense organ."""

    data: dict[str, Any]

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "SenseOrganManifest":
        missing = sorted(_REQUIRED_FIELDS - set(value))
        if missing:
            raise SenseRegistryError(
                f"Sense-organ manifest is missing required fields: {', '.join(missing)}"
            )
        organ_id = str(value.get("organ_id") or "").strip()
        if not organ_id or value.get("status") not in {"active", "disabled"}:
            raise SenseRegistryError("Sense-organ manifest has an invalid identity or status")
        if value.get("activation_topology") not in {
            "explicit_invocation",
            "event_triggered",
            "scheduled",
            "ambient_bounded",
        }:
            raise SenseRegistryError("Sense-organ activation topology is invalid")
        if not isinstance(value.get("allowed_payloads"), list) or not isinstance(
            value.get("prohibited_payloads"), list
        ):
            raise SenseRegistryError("Sense-organ payload declarations must be lists")
        return cls(deepcopy(value))

    @property
    def organ_id(self) -> str:
        return str(self.data["organ_id"])

    @property
    def digest(self) -> str:
        return _canonical_digest(self.data)

    def public(self) -> dict[str, Any]:
        result = deepcopy(self.data)
        result["digest"] = self.digest
        return result


def _porchlight_manifest() -> SenseOrganManifest:
    return SenseOrganManifest.from_mapping(
        {
            "organ_id": "porchlight",
            "schema_version": "vestigia.sense-organ.v0.1",
            "version": "0.1.0",
            "display_name": "Porchlight",
            "modality": "browser_context",
            "activation_topology": "explicit_invocation",
            "invocation_surface": ["chrome_extension_button", "loopback_bridge"],
            "consent_basis": "explicit_user_invocation",
            "perception_scope": {
                "capture_modes": ["selection", "page"],
                "metadata": ["url", "title", "tab_id", "captured_at"],
                "text": "readable_text_only",
            },
            "allowed_payloads": [
                "readable_text",
                "page_metadata",
                "screenshot",
            ],
            "prohibited_payloads": [
                "raw_html",
                "cookies",
                "credentials",
                "hidden_page_state",
                "browser_history",
            ],
            "retention": {
                "default": "warm_archive_receipt",
                "source_of_truth": "mcp_receipt_garden",
            },
            "destinations": ["mcp_receipt_garden", "runtime_selected_context"],
            "limits": {
                "max_text_bytes": 1_000_000,
                "max_screenshot_bytes": 2_000_000,
                "screenshot_default": False,
            },
            "receipt_schema": "vestigia.sense-receipt.v0.1",
            "semantic_policy": {
                "default_query_source": "explicitly_captured_text",
                "metadata_is_control_plane": True,
                "boilerplate_excluded": True,
            },
            "status": "active",
        }
    )


def _lanternslide_manifest() -> SenseOrganManifest:
    return SenseOrganManifest.from_mapping(
        {
            "organ_id": "lanternslide",
            "schema_version": "vestigia.sense-organ.v0.1",
            "version": "0.1.0",
            "display_name": "Lanternslide",
            "modality": "archive_image",
            "activation_topology": "explicit_invocation",
            "invocation_surface": ["mcp_tool"],
            "consent_basis": "explicit_user_invocation",
            "perception_scope": {
                "source": "configured_live_archive",
                "formats": ["png", "jpeg", "gif", "webp"],
                "path_prefix": "configured_at_runtime",
                "operations": ["scan_metadata", "find_literal", "deal", "contact_sheet"],
            },
            "allowed_payloads": [
                "image_metadata",
                "explicitly_selected_contact_sheet",
            ],
            "prohibited_payloads": [
                "raw_image_bytes",
                "source_pixels",
                "automatic_caption",
                "identity_inference",
                "preference_inference",
                "semantic_image_search",
            ],
            "retention": {
                "default": "mcp_owned_local_catalog",
                "source_of_truth": "configured_live_archive",
            },
            "destinations": ["mcp_owned_local_catalog", "mcp_caller_response"],
            "limits": {
                "scan_batch_configurable": True,
                "image_bytes_configurable": True,
                "contact_sheet_bytes_configurable": True,
                "contact_sheet_max_images": 16,
            },
            "receipt_schema": "vestigia.sense-receipt.v0.1",
            "semantic_policy": {
                "automatic_retrieval": "none",
                "path_query": "literal_case_insensitive",
                "inclusion_does_not_imply_causality": True,
            },
            "status": "active",
        }
    )


class SenseOrganRegistry:
    """Small deterministic registry for declarative, bounded perception contracts."""

    def __init__(self, manifests: tuple[SenseOrganManifest, ...] | None = None):
        entries = manifests or (_lanternslide_manifest(), _porchlight_manifest())
        self._organs = {manifest.organ_id: manifest for manifest in entries}
        if len(self._organs) != len(entries):
            raise SenseRegistryError("Duplicate sense-organ ID")

    def list(self) -> dict[str, object]:
        organs = [
            {
                "organ_id": manifest.organ_id,
                "display_name": manifest.data["display_name"],
                "modality": manifest.data["modality"],
                "activation_topology": manifest.data["activation_topology"],
                "status": manifest.data["status"],
                "digest": manifest.digest,
            }
            for manifest in sorted(self._organs.values(), key=lambda item: item.organ_id)
        ]
        return {
            "schema_version": "vestigia.sense-registry.v0.1",
            "organs": organs,
            "observation_route": "organ_specific_and_explicit_only",
        }

    def show(self, organ_id: str) -> dict[str, object]:
        manifest = self._organs.get(str(organ_id).strip())
        if manifest is None:
            raise SenseRegistryError(f"Unknown sense organ: {organ_id}")
        return {"schema_version": "vestigia.sense-registry.v0.1", "organ": manifest.public()}

    def can_perceive(self, organ_id: str, request: dict[str, Any]) -> dict[str, object]:
        manifest = self._organs.get(str(organ_id).strip())
        if manifest is None:
            return {"allowed": False, "reason": "unknown_organ", "organ_id": organ_id}

        requested_mode = str(request.get("capture_mode") or "").strip()
        payloads = request.get("payloads")
        payloads = payloads if isinstance(payloads, list) else []
        payloads = [str(payload).strip() for payload in payloads]
        consent = str(request.get("consent") or "").strip()
        scope = manifest.data["perception_scope"]
        rejected = sorted(
            {
                payload
                for payload in payloads
                if payload not in manifest.data["allowed_payloads"]
                or payload in manifest.data["prohibited_payloads"]
            }
        )
        allowed_modes = scope.get("capture_modes", []) if isinstance(scope, dict) else []
        if manifest.data["status"] != "active":
            reason = "organ_disabled"
        elif manifest.data["activation_topology"] != "explicit_invocation":
            reason = "activation_topology_not_explicit"
        elif consent != manifest.data["consent_basis"]:
            reason = "explicit_consent_required"
        elif requested_mode not in allowed_modes:
            reason = "capture_mode_out_of_scope"
        elif not payloads:
            reason = "payloads_required"
        elif rejected:
            reason = "payload_out_of_scope"
        elif request.get("include_screenshot") and "screenshot" not in payloads:
            reason = "screenshot_must_be_declared"
        else:
            return {
                "allowed": True,
                "reason": "declared_scope_and_consent_match",
                "organ_id": manifest.organ_id,
                "matched_scope": requested_mode,
                "accepted_payloads": payloads,
                "rejected_payloads": [],
                "manifest_digest": manifest.digest,
            }
        return {
            "allowed": False,
            "reason": reason,
            "organ_id": manifest.organ_id,
            "matched_scope": None,
            "accepted_payloads": [],
            "rejected_payloads": rejected,
            "manifest_digest": manifest.digest,
        }
