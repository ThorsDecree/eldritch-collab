from __future__ import annotations

import argparse
import json
from pathlib import Path

from .api import HouseMechanicServer
from .model import load_manifest
from .runner import run_recipe
from .service_model import ServiceManifest, load_service_manifest


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--repo-root", type=Path, required=True)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("recipe_id")
    serve = sub.add_parser("serve")
    serve.add_argument("--services", type=Path, required=True)
    serve.add_argument("--token-file", type=Path, required=True)
    serve.add_argument("--port", type=int, default=8770)
    serve.add_argument("--max-parallel", type=int, default=1)
    args = p.parse_args()

    manifest = load_manifest(args.manifest, args.repo_root)
    if args.command == "list":
        print(json.dumps([
            {"id": r.id, "description": r.description, "sha256": r.digest()}
            for r in manifest.recipes.values()
        ], indent=2))
        return 0

    if args.command == "serve":
        services = load_service_manifest(args.services, manifest)
        server = HouseMechanicServer(
            repo_root=args.repo_root,
            recipes=manifest,
            services=services,
            token_file=args.token_file,
            port=args.port,
            max_parallel=args.max_parallel,
        )
        host, port = server.server_address
        print(
            json.dumps(
                {
                    "protocol": "vestigia.house-mechanic-api.v0.1",
                    "host": host,
                    "port": port,
                    "recipe_count": len(manifest.recipes),
                    "service_count": len(services.services),
                    "process_authority": False,
                }
            ),
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0

    recipe = manifest.recipes.get(args.recipe_id)
    if recipe is None:
        p.error(f"unknown recipe: {args.recipe_id}")
    receipt = run_recipe(recipe, args.repo_root)
    print(json.dumps(receipt.to_dict(), indent=2))
    return 0 if receipt.expected_exit and not receipt.timed_out and not receipt.output_limit_exceeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
