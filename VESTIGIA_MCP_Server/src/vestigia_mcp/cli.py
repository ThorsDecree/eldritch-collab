from __future__ import annotations

import argparse
import logging

from .server import create_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the VESTIGIA MCP Server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO)
    server = create_server()
    if args.transport == "stdio":
        server.run()
        return
    server.run(
        transport="streamable-http",
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
