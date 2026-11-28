"""A narrow host-written witness before the Runtime chat middleware starts."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .utils import utc_now_iso


def write_pre_inference_record(home: Path, *, turn_id: str, route: str, statement: str,
                               approved: dict[str, Any] | None) -> Path:
    """Write once and flush before continuing; failures abort the chat turn.

    This is not a claim about reads during initialization, other entry points,
    or survival of a host/storage power loss. An interruption can leave a
    temporary file, which is not a published record and must not be treated as one.
    """
    directory = home / "traces" / "pre-inference"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{turn_id}.json"
    record = {
        "schema_version": 1,
        "turn_id": turn_id,
        "created_at": utc_now_iso(),
        "route": route.strip().lower() or "default",
        "configured_approved_identity": approved,
        "author": "runtime_configured",
        "statement": statement,
        "scope": "before_chat_middleware",
        "prior_reads_and_calls": "unknown",
        "provider_identity": "not_attested",
    }
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{turn_id}.", suffix=".tmp", dir=directory)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Publish without replacing an existing record. Hard-link support is
        # required; a filesystem without it fails closed before middleware.
        os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path
