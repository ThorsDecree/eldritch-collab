from __future__ import annotations

from vestigia_mcp import cli


class InterruptingBridge:
    server_address = ("127.0.0.1", 8765)

    def __init__(self) -> None:
        self.closed = False
        self.shutdown_called = False

    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def shutdown(self) -> None:
        self.shutdown_called = True
        raise AssertionError("synchronous CLI must not call shutdown from its server thread")

    def server_close(self) -> None:
        self.closed = True


def test_porchlight_bridge_cli_closes_without_same_thread_shutdown(monkeypatch) -> None:
    bridge = InterruptingBridge()
    monkeypatch.setattr(cli, "create_porchlight_bridge", lambda settings: bridge)
    monkeypatch.setattr(cli.Settings, "from_env", classmethod(lambda cls: object()))

    cli.porchlight_bridge_main()

    assert bridge.closed is True
    assert bridge.shutdown_called is False
