from __future__ import annotations

import logging

from .server import create_server


def main() -> None:
    """Run the local stdio MCP server.

    Network transports are intentionally deferred until authentication, host/origin
    restrictions, and deployment-scoped grants are implemented and tested.
    """

    logging.basicConfig(level=logging.INFO)
    create_server().run()


if __name__ == "__main__":
    main()
