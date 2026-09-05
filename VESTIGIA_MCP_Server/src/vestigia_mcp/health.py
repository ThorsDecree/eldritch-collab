from __future__ import annotations

import json
import posixpath
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import PurePosixPath
from urllib.parse import unquote

from .adapters.archive import ArchiveError, ArchiveSource, normalize_relative_path


CANONICAL_REGISTRY_PATH = "00_Bootloader/house_index.json"
_MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\n]+)\)")
_EXTERNAL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _target_present(paths: tuple[str, ...], target: str, kind: str) -> bool:
    if kind == "directory":
        boundary = target.rstrip("/") + "/"
        return target in paths or any(path.startswith(boundary) for path in paths)
    return target in paths


def _registry_inventory(
    source: ArchiveSource,
    max_bytes: int,
) -> tuple[dict[str, object], list[dict[str, object]], dict[str, list[str]]]:
    try:
        registry = json.loads(
            source.read_text(CANONICAL_REGISTRY_PATH, max_bytes=max_bytes)
        )
    except json.JSONDecodeError as exc:
        raise ArchiveError("Canonical house registry is not valid JSON") from exc
    if not isinstance(registry, dict):
        raise ArchiveError("Canonical house registry must be a JSON object")

    paths = source.all_paths()
    checks: list[dict[str, object]] = []
    labels_by_path: dict[str, list[str]] = defaultdict(list)

    def add_check(label: str, raw_path: object, kind: str) -> None:
        if not isinstance(raw_path, str):
            raise ArchiveError(f"Registry target is not a string: {label}")
        normalized = normalize_relative_path(raw_path)
        checks.append(
            {
                "label": label,
                "path": normalized,
                "kind": kind,
                "present": _target_present(paths, normalized, kind),
            }
        )
        labels_by_path[normalized].append(label)

    anchors = registry.get("anchors", {})
    if not isinstance(anchors, dict):
        raise ArchiveError("Registry anchors must be an object")
    for name, path in sorted(anchors.items()):
        add_check(f"anchor:{name}", path, "file")

    residents = registry.get("residents", {})
    if not isinstance(residents, dict):
        raise ArchiveError("Registry residents must be an object")
    for resident_name, record in sorted(residents.items()):
        if not isinstance(record, dict):
            raise ArchiveError(
                f"Resident registry entry must be an object: {resident_name}"
            )
        if "shell" in record:
            add_check(f"resident:{resident_name}:shell", record["shell"], "directory")
        if "breathprint" in record:
            add_check(
                f"resident:{resident_name}:breathprint",
                record["breathprint"],
                "file",
            )
        if "index" in record:
            add_check(f"resident:{resident_name}:index", record["index"], "file")

    garden = registry.get("garden_breathprints", {})
    if not isinstance(garden, dict):
        raise ArchiveError("Registry garden_breathprints must be an object")
    for name, path in sorted(garden.items()):
        add_check(f"garden_breathprint:{name}", path, "file")

    return registry, checks, dict(labels_by_path)


def registry_status(source: ArchiveSource, max_bytes: int) -> dict[str, object]:
    registry, checks, labels_by_path = _registry_inventory(source, max_bytes)
    anchors = registry.get("anchors", {})
    residents = registry.get("residents", {})
    garden = registry.get("garden_breathprints", {})
    missing = [check for check in checks if not check["present"]]
    duplicates = [
        {"path": path, "labels": labels}
        for path, labels in sorted(labels_by_path.items())
        if len(labels) > 1
    ]
    return {
        "registry_path": CANONICAL_REGISTRY_PATH,
        "schema_version": registry.get("schema_version"),
        "generated": registry.get("generated"),
        "archive_root": registry.get("archive_root"),
        "summary": {
            "anchors": len(anchors),
            "residents": len(residents),
            "garden_breathprints": len(garden),
            "registered_targets": len(checks),
            "present": len(checks) - len(missing),
            "missing": len(missing),
            "duplicate_targets": len(duplicates),
        },
        "missing": missing,
        "duplicate_targets": duplicates,
    }


def source_clock(source: ArchiveSource) -> dict[str, object]:
    """Return a low-trust timestamp for the configured source container/path."""
    try:
        stat = source.root.stat()
    except OSError as exc:
        raise ArchiveError(f"Unable to stat Archive source: {source.root}") from exc
    modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    age_seconds = max(0, int((datetime.now(UTC) - modified).total_seconds()))
    return {
        "modified_at": modified.isoformat(),
        "age_seconds": age_seconds,
        "basis": (
            "zip_container_mtime"
            if source.kind == "zip"
            else "directory_entry_mtime_low_trust"
        ),
        "semantic_freshness_proven": False,
    }


def _markdown_destination(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value.startswith("<") and ">" in value:
        return value[1 : value.index(">")].strip()
    # Markdown titles follow the destination after whitespace. Paths containing
    # literal spaces should be angle-bracketed; preserve everything otherwise.
    title = re.match(r"^(\S+)\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\))\s*$", value)
    return title.group(1) if title else value


def _resolve_markdown_target(source_path: str, raw_target: str) -> str | None:
    target = unquote(_markdown_destination(raw_target)).strip()
    if not target or target.startswith("#"):
        return None
    if _EXTERNAL_SCHEME_RE.match(target):
        return None
    target = target.split("#", 1)[0].split("?", 1)[0].strip()
    if not target:
        return None
    if target.startswith("/"):
        candidate = posixpath.normpath(target.lstrip("/"))
    else:
        base = PurePosixPath(source_path).parent.as_posix()
        candidate = posixpath.normpath(posixpath.join(base, target))
    if candidate in {"", "."}:
        return None
    return candidate


def archive_health(
    source: ArchiveSource,
    *,
    max_bytes: int,
    issue_limit: int = 100,
    check_links: bool = True,
) -> dict[str, object]:
    """Inspect mechanical Archive health without modifying source content.

    Strong findings are missing registered targets, case-fold path collisions, and
    broken/escaping local Markdown links. Registry aliases and unrouted collections
    are surfaced separately because they are ambiguous or descriptive rather than
    automatically wrong.
    """
    if issue_limit <= 0 or issue_limit > 500:
        raise ArchiveError("Health issue limit must be between 1 and 500")
    if max_bytes <= 0:
        raise ArchiveError("Health text byte ceiling must be positive")

    paths = source.all_paths()
    path_set = set(paths)
    issues: list[dict[str, object]] = []
    issue_total = 0

    def add_issue(family: str, severity: str, **details: object) -> None:
        nonlocal issue_total
        issue_total += 1
        if len(issues) < issue_limit:
            issues.append({"family": family, "severity": severity, **details})

    try:
        registry, checks, labels_by_path = _registry_inventory(source, max_bytes)
        registry_error: str | None = None
    except ArchiveError as exc:
        registry = {}
        checks = []
        labels_by_path = {}
        registry_error = str(exc)
        add_issue("registry", "error", problem="unreadable", detail=registry_error)

    missing = [check for check in checks if not bool(check["present"])]
    for check in missing:
        add_issue(
            "registry",
            "error",
            problem="missing_target",
            label=check["label"],
            path=check["path"],
            kind=check["kind"],
        )

    aliases = [
        {"path": path, "labels": labels}
        for path, labels in sorted(labels_by_path.items())
        if len(labels) > 1
    ]

    folded: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        folded[path.casefold()].append(path)
    case_collisions = [
        sorted(values)
        for values in folded.values()
        if len(set(values)) > 1
    ]
    case_collisions.sort(key=lambda values: values[0].casefold())
    for values in case_collisions:
        add_issue(
            "normalization",
            "error",
            problem="casefold_collision",
            paths=values,
        )

    collection_counts: dict[str, int] = defaultdict(int)
    for path in paths:
        parts = PurePosixPath(path).parts
        if len(parts) > 1:
            collection_counts[parts[0]] += 1
    routed_collections = {
        PurePosixPath(str(check["path"])).parts[0]
        for check in checks
        if PurePosixPath(str(check["path"])).parts
    }
    unrouted_collections = [
        {"collection": name, "file_count": collection_counts[name]}
        for name in sorted(collection_counts)
        if name not in routed_collections
    ]

    markdown_scanned = 0
    markdown_links_checked = 0
    broken_links = 0
    escaped_links = 0
    skipped_oversize = 0
    skipped_non_utf8 = 0
    if check_links:
        for path in paths:
            if PurePosixPath(path).suffix.lower() != ".md":
                continue
            try:
                text = source.read_text(path, max_bytes=max_bytes)
            except ArchiveError as exc:
                message = str(exc)
                if "exceeds byte ceiling" in message:
                    skipped_oversize += 1
                    continue
                if "not valid UTF-8" in message:
                    skipped_non_utf8 += 1
                    continue
                raise
            markdown_scanned += 1
            for match in _MARKDOWN_LINK_RE.finditer(text):
                resolved = _resolve_markdown_target(path, match.group(1))
                if resolved is None:
                    continue
                markdown_links_checked += 1
                if resolved == ".." or resolved.startswith("../"):
                    escaped_links += 1
                    add_issue(
                        "markdown_links",
                        "error",
                        problem="escapes_archive",
                        source=path,
                        target=match.group(1).strip(),
                    )
                    continue
                target = resolved.replace("\\", "/")
                boundary = target.rstrip("/") + "/"
                present = target in path_set or any(
                    candidate.startswith(boundary) for candidate in paths
                )
                if not present:
                    broken_links += 1
                    add_issue(
                        "markdown_links",
                        "warning",
                        problem="missing_local_target",
                        source=path,
                        target=match.group(1).strip(),
                        resolved=target,
                    )

    archive_root = registry.get("archive_root") if registry else None
    portable_root = archive_root in {None, "", "."}

    return {
        "schema_version": "vestigia.archive-health.v0.1",
        "status": "ok" if issue_total == 0 else "warning",
        "source": {
            "kind": source.kind,
            "configured_path": str(source.root),
            "file_count": len(paths),
            "excluded_paths": list(source.excluded_paths),
            "clock": source_clock(source),
        },
        "summary": {
            "issue_count": issue_total,
            "issues_returned": len(issues),
            "registry_missing": len(missing),
            "registry_aliases": len(aliases),
            "case_collisions": len(case_collisions),
            "broken_markdown_links": broken_links,
            "escaped_markdown_links": escaped_links,
            "unrouted_collection_candidates": len(unrouted_collections),
        },
        "registry": {
            "available": registry_error is None,
            "error": registry_error,
            "schema_version": registry.get("schema_version") if registry else None,
            "generated": registry.get("generated") if registry else None,
            "archive_root": archive_root,
            "archive_root_portable": portable_root,
            "registered_targets": len(checks),
            "present_targets": len(checks) - len(missing),
            "missing_targets": missing,
            "aliases": aliases,
        },
        "coverage": {
            "claim": "descriptive_projection_only",
            "top_level_collections": [
                {"collection": name, "file_count": collection_counts[name]}
                for name in sorted(collection_counts)
            ],
            "routed_collections": sorted(routed_collections),
            "unrouted_collection_candidates": unrouted_collections,
            "note": (
                "An unrouted collection is a coverage canary, not proof of a defect. "
                "Generated routing furniture never outranks source records."
            ),
        },
        "normalization": {
            "casefold_collisions": case_collisions,
        },
        "markdown_links": {
            "enabled": check_links,
            "markdown_files_scanned": markdown_scanned,
            "local_links_checked": markdown_links_checked,
            "broken_links": broken_links,
            "escaped_links": escaped_links,
            "skipped_oversize": skipped_oversize,
            "skipped_non_utf8": skipped_non_utf8,
        },
        "implemented_checks": [
            "canonical_registry_targets",
            "registry_aliases",
            "top_level_coverage_canary",
            "casefold_collisions",
            "local_markdown_links" if check_links else "local_markdown_links_skipped",
            "source_container_clock_low_trust",
        ],
        "deferred_checks": [
            "stale_generated_index_semantics",
            "skill_contract_integrity",
            "cross-version_path_drift",
            "duplicate_resident_routing_semantics",
            "semantic_snapshot_freshness",
        ],
        "issues": issues,
        "truncated": issue_total > len(issues),
    }
