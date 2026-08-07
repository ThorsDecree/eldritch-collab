from __future__ import annotations

import ast
import re
import sys
from typing import Any

from .utils import sha256_text, stable_json


INSPECTOR_VERSION = "0.1.0"
_HARDENED_IMPORTS = {
    "ctypes",
    "ftplib",
    "http",
    "importlib",
    "multiprocessing",
    "resource",
    "socket",
    "ssl",
    "subprocess",
    "telnetlib",
    "urllib",
    "winreg",
}
_HARDENED_CALLS = {"eval", "exec", "compile", "__import__"}
_FILESYSTEM_IMPORTS = {"os", "pathlib", "shutil", "tempfile"}
_NETWORK_HINTS = {"requests", "aiohttp", "httpx", "openai"}
_URL_RE = re.compile(r"https?://[^\s'\"<>]{3,}", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{12,}|api[_-]?key\s*[:=]|token\s*[:=]|password\s*[:=])",
    re.IGNORECASE,
)
_RULES = {
    "hardened_imports": sorted(_HARDENED_IMPORTS),
    "hardened_calls": sorted(_HARDENED_CALLS),
    "filesystem_imports": sorted(_FILESYSTEM_IMPORTS),
    "network_hints": sorted(_NETWORK_HINTS),
    "third_party_imports_require_hardened": True,
    "non_resident_authorship_requires_hardened": True,
}
RULESET_HASH = sha256_text(stable_json(_RULES))


def _call_name(node: ast.Call) -> str:
    target = node.func
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        parts: list[str] = [target.attr]
        current: ast.AST = target.value
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    return ""


def inspect_source(source: str, *, authored_lane: str) -> dict[str, Any]:
    imports: set[str] = set()
    calls: set[str] = set()
    url_hashes: set[str] = set()
    secret_hashes: set[str] = set()
    warnings: list[str] = []
    violations: list[str] = []
    try:
        tree = ast.parse(source, mode="exec", type_comments=True)
    except (SyntaxError, ValueError) as exc:
        return {
            "schema_version": "vestigia.script-inspection.v0.1",
            "inspector_version": INSPECTOR_VERSION,
            "ruleset_hash": RULESET_HASH,
            "parse_ok": False,
            "node_count": 0,
            "imports": [],
            "calls": [],
            "url_hashes": [],
            "secret_literal_hashes": [],
            "warnings": [],
            "violations": ["parse_failed"],
            "classification": "quarantined",
            "safe_message": f"Python parse failed: {exc.msg if isinstance(exc, SyntaxError) else 'invalid source'}",
        }
    nodes = list(ast.walk(tree))
    if len(nodes) > 10000:
        violations.append("ast_node_ceiling_exceeded")
    for node in nodes:
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name:
                calls.add(name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            for match in _URL_RE.findall(text):
                url_hashes.add(sha256_text(match))
            if _SECRET_RE.search(text):
                secret_hashes.add(sha256_text(text))

    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    third_party = sorted(item for item in imports if item not in stdlib and item != "__future__")
    hardened_imports = sorted((imports & _HARDENED_IMPORTS) | (set(third_party) & _NETWORK_HINTS))
    dangerous_calls = sorted(
        name
        for name in calls
        if name in _HARDENED_CALLS
        or name.startswith("subprocess.")
        or name.startswith("socket.")
        or name.startswith("ctypes.")
        or name.startswith("multiprocessing.")
        or name in {"os.system", "os.popen", "os.startfile"}
        or name.startswith("os.exec")
        or name.startswith("os.spawn")
    )
    filesystem_imports = sorted(imports & _FILESYSTEM_IMPORTS)
    if filesystem_imports:
        warnings.append("filesystem APIs observed; host-filesystem denial is not enforced by local_process")
    if url_hashes:
        warnings.append("embedded URL-like literals observed")
    if secret_hashes:
        warnings.append("secret-shaped string literal observed; literal content omitted from inspection evidence")
    if third_party:
        violations.append("third_party_import_requires_hardened")
    if hardened_imports:
        violations.append("sensitive_import_requires_hardened")
    if dangerous_calls:
        violations.append("dangerous_dynamic_or_process_call_requires_hardened")

    if authored_lane != "resident":
        classification = "hardened_only"
        warnings.append("non-resident-authored source requires a hardened backend in v0.1")
    elif violations:
        classification = "hardened_only"
    else:
        classification = "local_process_eligible"
    return {
        "schema_version": "vestigia.script-inspection.v0.1",
        "inspector_version": INSPECTOR_VERSION,
        "ruleset_hash": RULESET_HASH,
        "parse_ok": True,
        "node_count": len(nodes),
        "imports": sorted(imports),
        "third_party_imports": third_party,
        "calls": sorted(calls),
        "dangerous_calls": dangerous_calls,
        "hardened_imports": hardened_imports,
        "filesystem_imports": filesystem_imports,
        "url_hashes": sorted(url_hashes),
        "secret_literal_hashes": sorted(secret_hashes),
        "warnings": warnings,
        "violations": sorted(set(violations)),
        "classification": classification,
        "safe_message": (
            "Eligible for resident-authored local-process testing."
            if classification == "local_process_eligible"
            else "Stored source remains inert; hardened isolation is required before execution."
        ),
    }
