"""Development entrypoint for MCP Inspector.

The production package reads only its process environment. This wrapper is intentionally
responsible for loading the project-local .env before importing the module-level MCP server.
"""

from pathlib import Path

from dotenv import load_dotenv


load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=False)

from vestigia_mcp.server import mcp  # noqa: E402


__all__ = ["mcp"]
