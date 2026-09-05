from __future__ import annotations

import unittest

from vestigia.mcp_projection import dispatch_read_projection, read_projection


class FakeRegistry:
    def __init__(self) -> None:
        self.contracts = {
            "status": {
                "name": "status",
                "description": "Read status.",
                "effects": ["database:read"],
                "confirmation": "none",
                "outward_facing": False,
                "callable_now": True,
                "dispatchable_via_tool_action": True,
                "schema_version": "v1",
                "group": "house",
                "input_schema": {"type": "object"},
            },
            "file.write": {
                "name": "file.write",
                "description": "Write workspace text.",
                "effects": ["filesystem:write_workspace", "database:audit_write"],
                "confirmation": "none",
                "outward_facing": False,
                "callable_now": True,
                "dispatchable_via_tool_action": True,
                "schema_version": "v1",
                "group": "workspace",
                "input_schema": {"type": "object"},
            },
            "discord.react": {
                "name": "discord.react",
                "description": "React outwardly.",
                "effects": ["database:read"],
                "confirmation": "resident_authenticated_doorway",
                "outward_facing": True,
                "callable_now": True,
                "dispatchable_via_tool_action": True,
                "schema_version": "v1",
                "group": "discord",
                "input_schema": {"type": "object"},
            },
        }

    def describe(self, target: str | None = None):
        if target is None:
            return list(self.contracts.values())
        if target not in self.contracts:
            raise KeyError(target)
        return [self.contracts[target]]


class FakeHouse:
    def __init__(self) -> None:
        self.registry = FakeRegistry()
        self.calls = []

    def dispatch(self, payload, *, turn_id=None, context=None):
        self.calls.append((payload, turn_id, context))
        return {"ok": True, "action": payload["action"], "receipt_id": "receipt_runtime"}


class McpProjectionTests(unittest.TestCase):
    def test_projection_derives_reads_from_runtime_contracts(self) -> None:
        house = FakeHouse()
        projected = read_projection(house)
        self.assertEqual(projected["authority"], "runtime_capability_registry")
        self.assertEqual(projected["capability_count"], 1)
        self.assertEqual(projected["capabilities"][0]["name"], "status")
        self.assertEqual(len(projected["capability_digest_sha256"]), 64)

        with self.assertRaises(PermissionError):
            read_projection(house, "file.write")
        with self.assertRaises(PermissionError):
            read_projection(house, "discord.react")

    def test_dispatch_preserves_request_id_and_uses_house_port(self) -> None:
        house = FakeHouse()
        result = dispatch_read_projection(
            house,
            action="status",
            arguments={"limit": 3},
            request_id="req_test",
            deployment_id="desktop",
        )
        payload, turn_id, context = house.calls[0]
        self.assertEqual(payload, {"action": "status", "limit": 3, "after": "finish"})
        self.assertEqual(turn_id, "req_test")
        self.assertEqual(context["source_envelope"], "MCP")
        self.assertEqual(context["request_id"], "req_test")
        self.assertEqual(result["request_id"], "req_test")
        self.assertEqual(result["runtime"]["receipt_id"], "receipt_runtime")

        with self.assertRaises(ValueError):
            dispatch_read_projection(
                house,
                action="status",
                arguments={"after": "continue"},
                request_id="req_bad",
            )


if __name__ == "__main__":
    unittest.main()
