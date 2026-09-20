from __future__ import annotations

import base64
import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from vestigia_mcp.config import Settings
from vestigia_mcp.porchlight_bridge import create_porchlight_bridge


def post(bridge, token: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    request = Request(
        f"http://127.0.0.1:{bridge.server_address[1]}/v1/shares",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": "chrome-extension://porchlight",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_real_bridge_shares_text_image_and_preserves_latest_on_failure(tmp_path: Path) -> None:
    live = tmp_path / "archive"
    (live / "Modules" / "Porchlight" / "latest").mkdir(parents=True)
    (live / "Modules" / "Porchlight" / "history").mkdir()
    (live / "Modules" / "Porchlight" / "receipts").mkdir()
    (live / "Modules" / "Porchlight" / "images").mkdir()
    settings = Settings(
        live_archive_root=live,
        snapshot_archive_root=None,
        state_dir=tmp_path / "state",
        deployment_id="integration",
        archive_write_prefixes=("Modules/Porchlight",),
        porchlight_bridge_port=0,
    )
    bridge = create_porchlight_bridge(settings)
    thread = threading.Thread(target=bridge.serve_forever, daemon=True)
    thread.start()
    try:
        token = bridge.tokens.read()
        status, result = post(
            bridge,
            token,
            {
                "url": "https://example.test/thread",
                "title": "Example",
                "content": "first readable page",
                "mode": "page",
                "captured_at": "2026-09-20T12:00:00+00:00",
                "screenshot_base64": base64.b64encode(b"\x89PNG\r\n\x1a\nshot").decode(),
            },
        )
        assert status == 200
        assert result["shared_directly"] is True
        assert result["latest_path"].startswith("Modules/Porchlight/latest/")
        latest = live / Path(*str(result["latest_path"]).split("/"))
        history = live / Path(*str(result["history_path"]).split("/"))
        receipt = live / Path(*str(result["receipt_path"]).split("/"))
        image = live / Path(*str(result["screenshot_path"]).split("/"))
        assert latest.read_text(encoding="utf-8") == "first readable page"
        assert history.read_text(encoding="utf-8") == "first readable page"
        assert json.loads(receipt.read_text(encoding="utf-8"))["screenshot"]["mime_type"] == "image/png"
        assert image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

        unchanged_status, unchanged = post(
            bridge,
            token,
            {
                "url": "https://example.test/thread",
                "title": "Example",
                "content": "first readable page",
                "mode": "update",
                "captured_at": "2026-09-20T12:01:00+00:00",
            },
        )
        assert unchanged_status == 200
        assert unchanged["unchanged"] is True

        failed_status, _ = post(
            bridge,
            token,
            {
                "url": "https://example.test/thread",
                "title": "Example",
                "content": "second readable page",
                "mode": "page",
                "captured_at": "2026-09-20T12:02:00+00:00",
                "screenshot_base64": base64.b64encode(b"not-png").decode(),
            },
        )
        assert failed_status == 400
        assert latest.read_text(encoding="utf-8") == "first readable page"
    finally:
        bridge.shutdown()
        bridge.server_close()
        thread.join(timeout=2)
