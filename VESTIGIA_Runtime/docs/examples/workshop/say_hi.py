from __future__ import annotations

import json
import sys

payload = json.load(sys.stdin)
name = str(payload["arguments"].get("name", "friend"))[:80]
json.dump(
    {
        "schema_version": "vestigia.script-output.v0.1",
        "value": {"text": f"I made this machine make a machine say hi to {name}."},
        "artifacts": [],
        "warnings": [],
    },
    sys.stdout,
)
