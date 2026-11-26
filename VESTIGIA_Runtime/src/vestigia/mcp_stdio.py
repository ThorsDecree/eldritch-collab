from __future__ import annotations

import asyncio
import sys
import threading
from typing import Any, Awaitable, Callable, TypeVar

from .context_sources import ContextSourceError


T = TypeVar("T")


class McpToolRejected(ContextSourceError):
    """A tool returned an explicit MCP error response."""


def run_async_blocking(factory: Callable[[], Awaitable[T]], timeout: float) -> T:
    """Run one bounded async MCP exchange from synchronous Runtime code.

    CLI/FastAPI sync paths normally have no running loop. If an interface invokes Runtime
    synchronously from inside an event-loop thread, use one short-lived helper thread rather
    than trying to nest asyncio.run(). The coroutine itself is wrapped in wait_for so client
    cancellation can tear down the stdio child.
    """

    async def bounded() -> T:
        return await asyncio.wait_for(factory(), timeout=timeout)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(bounded())

    value: dict[str, T] = {}
    error: list[BaseException] = []

    def runner() -> None:
        try:
            value["result"] = asyncio.run(bounded())
        except BaseException as exc:  # preserve the original exception across the thread
            error.append(exc)

    thread = threading.Thread(
        target=runner,
        name="vestigia-mcp-context",
        daemon=True,
    )
    thread.start()
    thread.join(timeout + 5.0)
    if thread.is_alive():
        raise TimeoutError("MCP context helper thread did not stop after timeout")
    if error:
        raise error[0]
    return value["result"]


class McpStdioClient:
    def __init__(self, timeout_seconds: float, *, args: tuple[str, ...] = ()) -> None:
        self.timeout_seconds = timeout_seconds
        self.args = ("-m", "vestigia_mcp.cli", *args)

    def _child_env(self) -> dict[str, str]:
        return {}

    def _client(self) -> Any:
        try:
            from mcp import Client, StdioServerParameters
        except ImportError as exc:
            raise ContextSourceError(
                "MCP tools are enabled but the optional MCP SDK is not installed; "
                "install VESTIGIA Runtime with the 'mcp-context' extra"
            ) from exc
        return Client(StdioServerParameters(
            command=sys.executable,
            args=list(self.args),
            env=self._child_env(),
        ))

    def list_tools(self) -> list[dict[str, Any]]:
        return run_async_blocking(self._list_tools_async, self.timeout_seconds)

    async def _list_tools_async(self) -> list[dict[str, Any]]:
        async with self._client() as client:
            tools = []
            cursor = None
            while True:
                page = await client.list_tools(cursor=cursor)
                tools.extend(tool.model_dump(by_alias=True, exclude_none=True) for tool in page.tools)
                cursor = page.next_cursor
                if cursor is None:
                    return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one MCP tool through the same credential-minimizing stdio bridge.

        Resident-facing MCP capabilities use this narrow seam rather than receiving a raw
        client. Image bytes remain separated from text/structured results so callers can put
        them into Runtime's private content-addressed image shelf without copying base64 into
        resident-visible receipts.
        """

        tool_name = str(name).strip()
        if not tool_name:
            raise ValueError("MCP tool name must not be empty")
        try:
            return run_async_blocking(
                lambda: self._call_tool_async(tool_name, dict(arguments)),
                self.timeout_seconds,
            )
        except ContextSourceError:
            raise
        except Exception as exc:
            raise ContextSourceError(
                f"VESTIGIA MCP tool call failed: {type(exc).__name__}: {exc}"
            ) from exc

    async def _call_tool_async(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        async with self._client() as client:
            result = await client.call_tool(name, arguments)
            server_metadata = {
                "protocol_version": str(client.protocol_version or "") or None,
                "server_name": (
                    str(client.server_info.name or "") or None
                    if client.server_info is not None
                    else None
                ),
                "server_version": (
                    str(client.server_info.version or "") or None
                    if client.server_info is not None
                    else None
                ),
            }

        text_blocks: list[str] = []
        image_blocks: list[dict[str, str]] = []
        for block in result.content:
            block_type = str(getattr(block, "type", ""))
            if block_type == "text":
                text_blocks.append(str(getattr(block, "text", "")))
            elif block_type == "image":
                image_blocks.append(
                    {
                        "data": str(getattr(block, "data", "")),
                        "mime_type": str(getattr(block, "mime_type", "")),
                    }
                )
        if result.is_error:
            detail = "; ".join(item for item in text_blocks if item).strip()
            raise McpToolRejected(
                f"MCP tool {name} returned an error"
                + (f": {detail}" if detail else "")
            )
        structured = result.structured_content
        return {
            "tool": name,
            "structured_content": (
                dict(structured) if isinstance(structured, dict) else None
            ),
            "text_blocks": text_blocks,
            "image_blocks": image_blocks,
            **server_metadata,
        }
