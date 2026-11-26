from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .config import Settings, load_deployment_env
from .porchlight_bridge import PairingTokenStore, create_porchlight_bridge


def main() -> None:
    """Run the local stdio MCP server.

    Network transports are intentionally deferred until authentication, host/origin
    restrictions, and deployment-scoped grants are implemented and tested.
    """

    parser = argparse.ArgumentParser(description="Run the VESTIGIA MCP stdio server")
    parser.add_argument("--deployment-env", type=Path, help="Read MCP settings from this deployment file")
    arguments = parser.parse_args()
    if arguments.deployment_env is not None:
        environment = load_deployment_env(arguments.deployment_env)
        for key in list(os.environ):
            if key.startswith("VESTIGIA_MCP_"):
                del os.environ[key]
        os.environ.update(environment)
    logging.basicConfig(level=logging.INFO)
    # The stdio server is constructed at import time. Apply the selected settings first.
    from .server import mcp

    mcp.run()


def porchlight_bridge_main() -> None:
    """Run the authenticated local Porchlight loopback bridge."""
    logging.basicConfig(level=logging.INFO)
    server = create_porchlight_bridge(Settings.from_env())
    try:
        print(
            f"Porchlight bridge listening at http://{server.server_address[0]}:{server.server_address[1]}",
            flush=True,
        )
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def porchlight_pair_main() -> None:
    """Print the local token used to pair the Porchlight extension."""
    settings = Settings.from_env()
    token = PairingTokenStore(
        settings.porchlight_bridge_token_path or settings.state_dir / "porchlight-token"
    )
    print(token.read())


if __name__ == "__main__":
    main()
