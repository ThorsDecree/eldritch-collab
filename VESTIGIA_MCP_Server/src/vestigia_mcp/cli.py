from __future__ import annotations

import logging

from .config import Settings
from .porchlight_bridge import PairingTokenStore, create_porchlight_bridge
from .server import create_server


def main() -> None:
    """Run the local stdio MCP server.

    Network transports are intentionally deferred until authentication, host/origin
    restrictions, and deployment-scoped grants are implemented and tested.
    """

    logging.basicConfig(level=logging.INFO)
    create_server().run()


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
