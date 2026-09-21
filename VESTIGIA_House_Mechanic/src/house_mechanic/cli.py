from __future__ import annotations

import argparse
import json
from pathlib import Path

from .model import load_manifest
from .runner import run_recipe


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--repo-root", type=Path, required=True)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("recipe_id")
    args = p.parse_args()

    manifest = load_manifest(args.manifest, args.repo_root)
    if args.command == "list":
        print(json.dumps([
            {"id": r.id, "description": r.description, "sha256": r.digest()}
            for r in manifest.recipes.values()
        ], indent=2))
        return 0

    recipe = manifest.recipes.get(args.recipe_id)
    if recipe is None:
        p.error(f"unknown recipe: {args.recipe_id}")
    receipt = run_recipe(recipe, args.repo_root)
    print(json.dumps(receipt.to_dict(), indent=2))
    return 0 if receipt.expected_exit and not receipt.timed_out and not receipt.output_limit_exceeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
