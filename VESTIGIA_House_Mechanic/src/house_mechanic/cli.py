from __future__ import annotations

import argparse
import json
from pathlib import Path

from .api import HouseMechanicServer, PROTOCOL
from .model import load_manifest
from .runner import run_recipe
from .service_model import load_service_manifest
from .tasking import load_repository_manifest


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
    serve.add_argument("--receipt-file", type=Path, required=True)
    serve.add_argument("--repositories", type=Path)
    serve.add_argument("--worktree-root", type=Path)
    serve.add_argument("--task-state-dir", type=Path)
    serve.add_argument("--deployment-state-dir", type=Path)
    serve.add_argument("--port", type=int, default=8770)
    serve.add_argument("--health-timeout", type=float, default=3.0)
    serve.add_argument("--health-max-response-bytes", type=int, default=65536)
    serve.add_argument("--lifecycle-health-wait", type=float, default=10.0)
    serve.add_argument("--lifecycle-stop-timeout", type=float, default=5.0)
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
        repositories = None
        if args.repositories is not None:
            if args.worktree_root is None:
                p.error("--worktree-root is required when --repositories is supplied")
            repositories = load_repository_manifest(args.repositories, args.repo_root)
        elif (
            args.worktree_root is not None
            or args.task_state_dir is not None
            or args.deployment_state_dir is not None
        ):
            p.error("--repositories is required when tasking/deployment paths are supplied")
        server = HouseMechanicServer(
            repo_root=args.repo_root,
            recipes=manifest,
            services=services,
            token_file=args.token_file,
            receipt_file=args.receipt_file,
            port=args.port,
            max_parallel=args.max_parallel,
            health_timeout_seconds=args.health_timeout,
            health_max_response_bytes=args.health_max_response_bytes,
            lifecycle_health_wait_seconds=args.lifecycle_health_wait,
            lifecycle_stop_timeout_seconds=args.lifecycle_stop_timeout,
            repositories=repositories,
            worktree_root=args.worktree_root,
            task_state_dir=args.task_state_dir,
            deployment_state_dir=args.deployment_state_dir,
        )
        host, port = server.server_address
        print(
            json.dumps(
                {
                    "protocol": PROTOCOL,
                    "host": host,
                    "port": port,
                    "recipe_count": len(manifest.recipes),
                    "service_count": len(services.services),
                    "receipt_persistence": True,
                    "process_authority": True,
                    "process_authority_scope": "mechanic_child_only",
                    "tasking_enabled": repositories is not None,
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
