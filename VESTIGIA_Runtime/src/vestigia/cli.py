from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .bells import BellService
from .config import load_config
from .curation import Curator
from .db import ContinuityDB
from .diagnostics import (
    build_doctor_report,
    format_doctor_text,
    support_bundle_receipt,
    write_support_bundle,
)
from .home import initialize_home, validate_home
from .house_tools import HousePort
from .images import ImageService
from .memory import MemoryService
from .models import NormalizedMessage, RuntimeState
from .onboarding import onboard
from .packing import pack_home, restore_home
from .providers.fake import FakeProvider
from .runtime import CoreRuntime


def _json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _config_db(home: str | Path, env_file: str | Path | None = None):
    config = load_config(home, env_file=env_file)
    db = ContinuityDB(config.home_path / "memory" / "continuity.db")
    db.initialize()
    return config, db


def _runtime_without_live_provider(home: str, env_file: str | None = None) -> CoreRuntime:
    config = load_config(home, env_file=env_file)
    return CoreRuntime(config, provider=FakeProvider())


def command_init(args: argparse.Namespace) -> int:
    home = initialize_home(
        args.home,
        name=args.name,
        glyph=args.glyph,
        resident_id=args.resident_id,
        room_id=args.room,
    )
    print(home)
    return 0


def command_onboard(args: argparse.Namespace) -> int:
    name = args.name or input("Who are you bringing home? ").strip()
    if not name:
        raise ValueError("A proposed resident name is required")
    human_label = args.human_label or input("Which speaker label is the human? [user] ").strip() or "user"
    resident_label = (
        args.resident_label
        or input("Which speaker label is the proposed resident? [assistant] ").strip()
        or "assistant"
    )
    home = onboard(
        args.source,
        home_path=args.home,
        resident_name=name,
        glyph=args.glyph,
        resident_label=resident_label,
        human_label=human_label,
        privacy=args.privacy,
    )
    print(home)
    return 0


def command_chat(args: argparse.Namespace) -> int:
    runtime = CoreRuntime.from_home(
        args.home,
        fake=args.fake,
        env_file=args.env_file,
    )
    name = str(runtime.config.get("resident.name"))
    print(f"{name} · {runtime.state} · type :help for local controls")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in {":quit", ":exit"}:
            break
        if text == ":help":
            print(":status  :sleep  :wake  :activate  :review  :quit")
            continue
        if text == ":status":
            print(runtime.state)
            continue
        if text == ":sleep":
            print(runtime.transition_state("DORMANT", actor="cli-human", reason="explicit CLI sleep"))
            continue
        if text == ":wake":
            print(runtime.transition_state("AWAKENING", actor="cli-human", reason="explicit CLI wake"))
            continue
        if text == ":activate":
            print(runtime.transition_state("ACTIVE", actor="cli-human", reason="explicit CLI activation"))
            continue
        if text == ":review":
            pending = runtime.db.list_memories(
                resident_id=runtime.resident_id,
                statuses=["candidate", "inherited_unreviewed", "deferred", "disputed"],
                limit=100,
            )
            for item in pending:
                print(f"{item.id} [{item.status}/{item.memory_type}] {item.content[:180]}")
            continue
        result = runtime.chat(
            NormalizedMessage(
                content=text,
                speaker_id="local-user",
                interface="cli",
                room_id=runtime.room_id,
            ),
            model_route=args.route,
        )
        print(f"\n{name}: {result.text}\n")
    return 0


def command_run(args: argparse.Namespace) -> int:
    config = load_config(args.home, env_file=args.env_file)
    if bool(config.get("interface.discord.enabled", False)):
        from .adapters.discord_adapter import run_discord

        run_discord(args.home, env_file=args.env_file, fake=args.fake)
        return 0
    return command_chat(args)


def command_discord(args: argparse.Namespace) -> int:
    from .adapters.discord_adapter import run_discord

    run_discord(args.home, env_file=args.env_file, fake=args.fake)
    return 0


def command_status(args: argparse.Namespace) -> int:
    config, db = _config_db(args.home, args.env_file)
    resident_id = str(config.get("resident.id"))
    bells = BellService(db, resident_id, str(config.get("room.id"))).list()
    pending = db.list_memories(
        resident_id=resident_id,
        statuses=["candidate", "inherited_unreviewed", "deferred", "disputed"],
        limit=10000,
    )
    curator = Curator(config, db)
    images = ImageService(config, db, fake=True)
    house = HousePort(
        config,
        db,
        queue_for_review=curator.queue,
        open_curation=curator.create_batch,
        image_service=images,
    )
    with db.connect() as connection:
        curation = connection.execute(
            """
            SELECT eligible_exchanges, cadence, paused
            FROM curation_state WHERE resident_id=? AND room_id=?
            """,
            (resident_id, str(config.get("room.id"))),
        ).fetchone()
        curation_drafts = connection.execute(
            """
            SELECT COUNT(*) AS n FROM curation_drafts
            WHERE resident_id=? AND status='pending'
            """,
            (resident_id,),
        ).fetchone()
        house_documents = connection.execute(
            "SELECT COUNT(*) AS n FROM house_documents"
        ).fetchone()
    _json(
        {
            "home": str(config.home_path),
            "resident": config.get("resident"),
            "room": config.get("room"),
            "state": db.current_state(resident_id),
            "pending_review": len(pending),
            "discord_enabled": config.get("interface.discord.enabled"),
            "provider": config.get("provider.kind"),
            "bells": {
                "enabled": bool(config.get("bells.enabled", True)),
                "visible": len(bells),
                "active": sum(item.status == "active" for item in bells),
            },
            "curation": {
                "enabled": bool(config.get("curation.enabled", True)),
                "eligible_exchanges": int(curation["eligible_exchanges"]),
                "cadence": int(curation["cadence"]),
                "paused": bool(curation["paused"]),
                "pending_drafts": int(curation_drafts["n"]),
            },
            "house": {
                "enabled": bool(config.get("house.enabled", True)),
                "indexed_documents": int(house_documents["n"]),
                **house.private_turn_budget(),
                "writable_roots": config.get("house.writable_roots", ["workspace"]),
            },
            "images": {
                **images.diagnostics(),
                "jobs": images.jobs(limit=20),
            },
        }
    )
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    config, db = _config_db(args.home, args.env_file)
    bells = BellService(
        db, str(config.get("resident.id")), str(config.get("room.id"))
    )
    curator = Curator(config, db)
    images = ImageService(config, db, fake=True)
    house = HousePort(
        config,
        db,
        queue_for_review=curator.queue,
        open_curation=curator.create_batch,
        image_service=images,
    )
    report = build_doctor_report(
        config,
        db,
        bells=bells,
        house=house,
        images=images,
        refresh_index=not bool(args.no_refresh_index),
    )
    if args.support_bundle:
        bundle = write_support_bundle(
            config, db, report, args.support_bundle
        )
        report["support_bundle"] = support_bundle_receipt(bundle)
    if args.text:
        print(format_doctor_text(report))
        if report.get("support_bundle"):
            item = report["support_bundle"]
            print(
                f"Support bundle: {item['path']} · sha256={item['sha256']} · "
                f"bytes={item['size_bytes']}"
            )
    else:
        _json(report)
    return 0


def sqlite_version() -> str:
    import sqlite3

    return sqlite3.sqlite_version


def command_remember(args: argparse.Namespace) -> int:
    config, db = _config_db(args.home, args.env_file)
    service = MemoryService(db, str(config.get("resident.id")), str(config.get("room.id")))
    record_id = service.propose(
        args.content,
        memory_type=args.type,
        tier=args.tier,
        authorship=args.actor,
        authority_state=args.authority,
        tags=args.tag,
        glyphs=args.glyph,
    )
    print(record_id)
    return 0


def command_review_list(args: argparse.Namespace) -> int:
    config, db = _config_db(args.home, args.env_file)
    statuses = args.status or ["candidate", "inherited_unreviewed", "deferred", "disputed"]
    records = db.list_memories(
        resident_id=str(config.get("resident.id")),
        statuses=statuses,
        limit=args.limit,
    )
    for record in reversed(records):
        print(
            f"{record.id}\t{record.status}\t{record.memory_type}\t{record.tier}\t"
            f"{record.authority_state}\t{record.content[:240]}"
        )
    return 0


def command_memory_action(args: argparse.Namespace) -> int:
    config, db = _config_db(args.home, args.env_file)
    service = MemoryService(db, str(config.get("resident.id")), str(config.get("room.id")))
    result = service.review(
        args.record_id,
        args.action,
        actor=args.actor,
        actor_role=args.actor_role,
        reason=args.reason,
        edited_content=args.content,
    )
    print(result)
    return 0


def command_state(args: argparse.Namespace) -> int:
    runtime = _runtime_without_live_provider(args.home, args.env_file)
    print(runtime.transition_state(args.target, actor=args.actor, reason=args.reason))
    return 0


def command_close_session(args: argparse.Namespace) -> int:
    runtime = _runtime_without_live_provider(args.home, args.env_file)
    print(runtime.close_session(actor=args.actor, tail=args.tail))
    return 0


def command_inspect_turn(args: argparse.Namespace) -> int:
    home = validate_home(args.home)
    receipt = home / "traces" / f"{args.turn_id}.receipt.json"
    result = home / "traces" / f"{args.turn_id}.result.json"
    payload = {
        "receipt": json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else None,
        "result": json.loads(result.read_text(encoding="utf-8")) if result.exists() else None,
    }
    _json(payload)
    return 0


def command_onboarding_report(args: argparse.Namespace) -> int:
    home = validate_home(args.home)
    report = home / "imports" / "orientation_dossier.md"
    if not report.is_file():
        raise FileNotFoundError("This home has no transcript-onboarding dossier")
    print(report.read_text(encoding="utf-8"))
    return 0


def command_curate(args: argparse.Namespace) -> int:
    config, db = _config_db(args.home, args.env_file)
    _json(Curator(config, db).dry_run())
    return 0


def command_house_index(args: argparse.Namespace) -> int:
    config, db = _config_db(args.home, args.env_file)
    curator = Curator(config, db)
    port = HousePort(
        config,
        db,
        queue_for_review=curator.queue,
        open_curation=curator.create_batch,
    )
    _json(port.refresh_index())
    return 0


def command_house_read(args: argparse.Namespace) -> int:
    config, db = _config_db(args.home, args.env_file)
    curator = Curator(config, db)
    port = HousePort(
        config,
        db,
        queue_for_review=curator.queue,
        open_curation=curator.create_batch,
    )
    payload: dict[str, Any] = {"action": args.house_action}
    for key in ("path", "scope", "query", "heading", "cursor"):
        value = getattr(args, key, None)
        if value:
            payload[key] = value
    if args.limit is not None:
        payload["limit" if args.house_action == "list" else "max_results"] = args.limit
    if args.max_tokens is not None:
        payload["max_tokens"] = args.max_tokens
    _json(port.dispatch(payload))
    return 0


def command_commander(args: argparse.Namespace) -> int:
    from .commander import run_commander

    run_commander(
        args.home,
        env_file=args.env_file,
        bind=args.bind,
        port=args.port,
        open_browser=not args.no_browser,
    )
    return 0


def command_pack(args: argparse.Namespace) -> int:
    print(pack_home(args.home, args.output))
    return 0


def command_restore(args: argparse.Namespace) -> int:
    print(restore_home(args.archive, args.target))
    return 0


def command_image_generate(args: argparse.Namespace) -> int:
    config, db = _config_db(args.home, args.env_file)
    service = ImageService(config, db, fake=args.fake)
    result = service.generate(
        args.prompt,
        count=args.count,
        confirmed=args.confirm,
    )
    _json(
        {
            "artifact_ids": result.artifact_ids,
            "image_ids": result.image_ids,
            "paths": result.paths,
            "model": result.model,
        }
    )
    return 0


def command_image_edit(args: argparse.Namespace) -> int:
    config, db = _config_db(args.home, args.env_file)
    service = ImageService(config, db, fake=args.fake)
    result = service.edit(
        args.prompt,
        args.source,
        count=args.count,
        confirmed=args.confirm,
    )
    _json(
        {
            "artifact_ids": result.artifact_ids,
            "image_ids": result.image_ids,
            "paths": result.paths,
            "model": result.model,
        }
    )
    return 0


def command_image_review(args: argparse.Namespace) -> int:
    config, db = _config_db(args.home, args.env_file)
    service = ImageService(config, db, fake=True)
    print(service.review(args.artifact_id, args.action, actor=args.actor, reason=args.reason))
    return 0


def _bell_service(home: str | Path, env_file: str | Path | None = None):
    config, db = _config_db(home, env_file)
    return config, BellService(
        db, str(config.get("resident.id")), str(config.get("room.id"))
    )


def _bell_schedule(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    supplied = [
        args.at is not None,
        args.every_minutes is not None,
        args.daily is not None,
        args.weekly is not None,
    ]
    if sum(supplied) != 1:
        raise ValueError("Choose exactly one of --at, --every-minutes, --daily, or --weekly")
    if args.at:
        value = datetime.fromisoformat(args.at)
        if value.tzinfo is None:
            raise ValueError("--at must include a UTC offset, for example 2026-07-30T09:00:00-05:00")
        return "once", {"at": value.astimezone(UTC).isoformat()}
    if args.every_minutes:
        if args.every_minutes < 60:
            raise ValueError("The minimum recurring bell interval is 60 minutes")
        return "interval", {
            "seconds": args.every_minutes * 60,
            "anchor": datetime.now(UTC).isoformat(),
        }
    if args.daily:
        return "daily", {"time": args.daily}
    days, clock = args.weekly.split("@", 1)
    names = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    weekdays = []
    for day in days.split(","):
        key = day.strip().lower()[:3]
        if key not in names:
            raise ValueError(f"Unknown weekday: {day}")
        weekdays.append(names[key])
    return "weekly", {"weekdays": weekdays, "time": clock.strip()}


def command_bell_add(args: argparse.Namespace) -> int:
    raise PermissionError(
        "New daemon bells are resident-authored through BELL_DRAFT and hash-bound claim. "
        "The operator CLI may inspect and maintain existing bells but may not create one."
    )


def command_bells(args: argparse.Namespace) -> int:
    _, service = _bell_service(args.home, args.env_file)
    _json([asdict(item) for item in service.list(include_deleted=args.all, limit=args.limit)])
    return 0


def command_bell_show(args: argparse.Namespace) -> int:
    _, service = _bell_service(args.home, args.env_file)
    _json({"bell": asdict(service.get(args.bell_id)), "events": service.events(args.bell_id)})
    return 0


def command_bell_status(args: argparse.Namespace) -> int:
    _, service = _bell_service(args.home, args.env_file)
    mapping = {"pause": "paused", "resume": "active", "delete": "deleted"}
    _json(asdict(service.set_status(
        args.bell_id, mapping[args.bell_action], actor=args.actor, reason=args.reason
    )))
    return 0


def command_bell_defer(args: argparse.Namespace) -> int:
    _, service = _bell_service(args.home, args.env_file)
    _json(asdict(service.defer(
        args.bell_id,
        datetime.now(UTC) + timedelta(minutes=args.minutes),
        reason=args.reason or f"explicit {args.minutes}-minute deferral",
    )))
    return 0


def command_bell_revise(args: argparse.Namespace) -> int:
    _, service = _bell_service(args.home, args.env_file)
    changes = {
        key: value for key, value in {
            "title": args.title,
            "purpose": args.purpose,
            "prompt": args.prompt,
            "strength": args.strength,
            "quiet_start": args.quiet_start,
            "quiet_end": args.quiet_end,
        }.items() if value is not None
    }
    if not changes:
        raise ValueError("No bell revisions supplied")
    _json(asdict(service.revise(args.bell_id, actor=args.actor, **changes)))
    return 0


def command_bell_reschedule(args: argparse.Namespace) -> int:
    _, service = _bell_service(args.home, args.env_file)
    kind, schedule = _bell_schedule(args)
    changes: dict[str, Any] = {"schedule_kind": kind, "schedule": schedule}
    if args.timezone:
        changes["timezone"] = args.timezone
    _json(asdict(service.revise(args.bell_id, actor=args.actor, **changes)))
    return 0


def command_bell_ack(args: argparse.Namespace) -> int:
    _, service = _bell_service(args.home, args.env_file)
    print(service.acknowledge(args.bell_id, args.state, actor=args.actor, note=args.note))
    return 0


def _add_home_env(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("home")
    parser.add_argument("--env-file")


def _add_runtime_options(parser: argparse.ArgumentParser) -> None:
    _add_home_env(parser)
    parser.add_argument("--fake", action="store_true", help="Use the deterministic fake provider")
    parser.add_argument("--route", choices=["default", "big", "thinking"], default="default")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vestigia")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Initialize a portable home")
    init.add_argument("home")
    init.add_argument("--name", required=True)
    init.add_argument("--glyph", default="🏮")
    init.add_argument("--resident-id")
    init.add_argument("--room", default="hearth")
    init.set_defaults(func=command_init)

    onboard_parser = sub.add_parser("onboard", help="Build an ORIENTATION home from sources")
    onboard_parser.add_argument("source")
    onboard_parser.add_argument("--home", required=True)
    onboard_parser.add_argument("--name")
    onboard_parser.add_argument("--glyph", default="🏮")
    onboard_parser.add_argument("--resident-label")
    onboard_parser.add_argument("--human-label")
    onboard_parser.add_argument("--privacy", default="private")
    onboard_parser.set_defaults(func=command_onboard)

    for name, function, help_text in (
        ("run", command_run, "Run the configured default door"),
        ("chat", command_chat, "Start CLI chat explicitly"),
        ("discord", command_discord, "Start Discord explicitly"),
    ):
        item = sub.add_parser(name, help=help_text)
        _add_runtime_options(item)
        item.set_defaults(func=function)

    status = sub.add_parser("status")
    _add_home_env(status)
    status.set_defaults(func=command_status)

    doctor = sub.add_parser("doctor")
    _add_home_env(doctor)
    doctor.add_argument(
        "--text", action="store_true",
        help="Print a compact human-readable report instead of JSON",
    )
    doctor.add_argument(
        "--support-bundle",
        help="Write a privacy-redacted support ZIP to this path",
    )
    doctor.add_argument(
        "--no-refresh-index", action="store_true",
        help="Inspect without refreshing the resident-readable house index",
    )
    doctor.set_defaults(func=command_doctor)

    remember = sub.add_parser("remember", help="Create a reviewable memory proposal")
    _add_home_env(remember)
    remember.add_argument("content")
    remember.add_argument("--type", default="other")
    remember.add_argument("--tier", default="warm")
    remember.add_argument("--actor", default="human")
    remember.add_argument("--authority", default="participant_stated")
    remember.add_argument("--tag", action="append", default=[])
    remember.add_argument("--glyph", action="append", default=[])
    remember.set_defaults(func=command_remember)

    review = sub.add_parser("review-memories")
    _add_home_env(review)
    review.add_argument("--status", action="append")
    review.add_argument("--limit", type=int, default=100)
    review.set_defaults(func=command_review_list)

    inheritance = sub.add_parser("review-inheritance")
    _add_home_env(inheritance)
    inheritance.add_argument("--limit", type=int, default=100)
    inheritance.set_defaults(
        func=command_review_list,
        status=["inherited_unreviewed"],
    )

    action = sub.add_parser("memory-action")
    _add_home_env(action)
    action.add_argument("record_id")
    action.add_argument("action", choices=["accept", "edit", "reject", "dispute", "defer"])
    action.add_argument("--actor", default="human")
    action.add_argument("--actor-role", choices=["human", "resident"], default="human")
    action.add_argument("--reason", default="")
    action.add_argument("--content")
    action.set_defaults(func=command_memory_action)

    state = sub.add_parser("state")
    _add_home_env(state)
    state.add_argument("target", type=str.upper, choices=[item.value for item in RuntimeState])
    state.add_argument("--actor", default="human")
    state.add_argument("--reason", default="explicit state request")
    state.set_defaults(func=command_state)

    activate = sub.add_parser("activate")
    _add_home_env(activate)
    activate.add_argument("--actor", default="human")
    activate.add_argument("--reason", default="explicit activation")
    activate.set_defaults(func=command_state, target="ACTIVE")

    wake = sub.add_parser("wake")
    _add_home_env(wake)
    wake.add_argument("--actor", default="human")
    wake.add_argument("--reason", default="explicit wake request")
    wake.set_defaults(func=command_state, target="AWAKENING")

    close = sub.add_parser("close-session")
    _add_home_env(close)
    close.add_argument("--actor", default="human")
    close.add_argument("--tail", type=int, default=12)
    close.set_defaults(func=command_close_session)

    inspect = sub.add_parser("inspect-turn")
    inspect.add_argument("home")
    inspect.add_argument("turn_id")
    inspect.set_defaults(func=command_inspect_turn)

    onboarding_report = sub.add_parser("onboarding-report")
    onboarding_report.add_argument("home")
    onboarding_report.set_defaults(func=command_onboarding_report)

    curate = sub.add_parser("curate")
    _add_home_env(curate)
    curate.set_defaults(func=command_curate)

    house_index = sub.add_parser("house-index", help="Refresh the bounded local scroll index")
    _add_home_env(house_index)
    house_index.set_defaults(func=command_house_index)

    house_read = sub.add_parser(
        "house-read",
        help="Operator diagnostic for read-only house operations",
    )
    _add_home_env(house_read)
    house_read.add_argument(
        "house_action", choices=["list", "search", "read", "continue", "stat"]
    )
    house_read.add_argument("--path")
    house_read.add_argument("--scope")
    house_read.add_argument("--query")
    house_read.add_argument("--heading")
    house_read.add_argument("--cursor")
    house_read.add_argument("--limit", type=int)
    house_read.add_argument("--max-tokens", type=int)
    house_read.set_defaults(func=command_house_read)

    commander = sub.add_parser(
        "commander",
        help="Open the loopback-only four-pane Cottage Commander",
    )
    _add_home_env(commander)
    commander.add_argument("--bind", default="127.0.0.1")
    commander.add_argument("--port", type=int, default=4319)
    commander.add_argument("--no-browser", action="store_true")
    commander.set_defaults(func=command_commander)

    pack = sub.add_parser("pack-home")
    pack.add_argument("home")
    pack.add_argument("--output")
    pack.set_defaults(func=command_pack)

    restore = sub.add_parser("restore-home")
    restore.add_argument("archive")
    restore.add_argument("target")
    restore.set_defaults(func=command_restore)

    image = sub.add_parser("image")
    image_sub = image.add_subparsers(dest="image_command", required=True)
    generate = image_sub.add_parser("generate")
    _add_home_env(generate)
    generate.add_argument("prompt")
    generate.add_argument("--count", type=int, default=1)
    generate.add_argument("--confirm", action="store_true")
    generate.add_argument("--fake", action="store_true")
    generate.set_defaults(func=command_image_generate)

    edit = image_sub.add_parser("edit")
    _add_home_env(edit)
    edit.add_argument("prompt")
    edit.add_argument("--source", action="append", required=True)
    edit.add_argument("--count", type=int, default=1)
    edit.add_argument("--confirm", action="store_true")
    edit.add_argument("--fake", action="store_true")
    edit.set_defaults(func=command_image_edit)

    image_review = sub.add_parser("image-review")
    _add_home_env(image_review)
    image_review.add_argument("artifact_id")
    image_review.add_argument(
        "action", choices=["keep", "candidate", "accept", "reject", "supersede", "share"]
    )
    image_review.add_argument("--actor", default="human")
    image_review.add_argument("--reason", default="")
    image_review.set_defaults(func=command_image_review)

    bell = sub.add_parser("bell", help="Create and manage consent-aware scheduled invitations")
    bell_sub = bell.add_subparsers(dest="bell_action", required=True)

    bell_add = bell_sub.add_parser("add")
    _add_home_env(bell_add)
    bell_add.add_argument("--title", required=True)
    bell_add.add_argument("--purpose", required=True)
    bell_add.add_argument("--prompt", required=True)
    bell_add.add_argument("--at")
    bell_add.add_argument("--every-minutes", type=int)
    bell_add.add_argument("--daily")
    bell_add.add_argument("--weekly", help="Example: mon,wed,fri@09:00")
    bell_add.add_argument("--timezone")
    bell_add.add_argument("--quiet-start")
    bell_add.add_argument("--quiet-end")
    bell_add.add_argument(
        "--strength",
        choices=["gentle", "repeated", "urgent", "outward_confirmation"],
        default="gentle",
    )
    bell_add.add_argument("--response-requested", action="store_true")
    bell_add.add_argument("--no-choose-nothing", action="store_true")
    bell_add.add_argument("--expires")
    bell_add.add_argument("--actor", default="human")
    bell_add.add_argument("--delivery-interface", default="discord")
    bell_add.add_argument("--delivery-kind", choices=["dm", "channel"], default="channel")
    bell_add.add_argument("--delivery-target", required=True)
    bell_add.set_defaults(func=command_bell_add)

    bells = sub.add_parser("bells", help="List the visible bell registry")
    _add_home_env(bells)
    bells.add_argument("--all", action="store_true")
    bells.add_argument("--limit", type=int, default=100)
    bells.set_defaults(func=command_bells)

    bell_show = bell_sub.add_parser("show")
    _add_home_env(bell_show)
    bell_show.add_argument("bell_id")
    bell_show.set_defaults(func=command_bell_show)

    for action_name in ("pause", "resume", "delete"):
        bell_status = bell_sub.add_parser(action_name)
        _add_home_env(bell_status)
        bell_status.add_argument("bell_id")
        bell_status.add_argument("--actor", default="human")
        bell_status.add_argument("--reason", default="")
        bell_status.set_defaults(func=command_bell_status)

    bell_defer = bell_sub.add_parser("defer")
    _add_home_env(bell_defer)
    bell_defer.add_argument("bell_id")
    bell_defer.add_argument("minutes", type=int)
    bell_defer.add_argument("--reason", default="")
    bell_defer.set_defaults(func=command_bell_defer)

    bell_revise = bell_sub.add_parser("revise")
    _add_home_env(bell_revise)
    bell_revise.add_argument("bell_id")
    bell_revise.add_argument("--title")
    bell_revise.add_argument("--purpose")
    bell_revise.add_argument("--prompt")
    bell_revise.add_argument(
        "--strength", choices=["gentle", "repeated", "urgent", "outward_confirmation"]
    )
    bell_revise.add_argument("--quiet-start")
    bell_revise.add_argument("--quiet-end")
    bell_revise.add_argument("--actor", default="human")
    bell_revise.set_defaults(func=command_bell_revise)

    bell_reschedule = bell_sub.add_parser("reschedule")
    _add_home_env(bell_reschedule)
    bell_reschedule.add_argument("bell_id")
    bell_reschedule.add_argument("--at")
    bell_reschedule.add_argument("--every-minutes", type=int)
    bell_reschedule.add_argument("--daily")
    bell_reschedule.add_argument("--weekly", help="Example: mon,wed,fri@09:00")
    bell_reschedule.add_argument("--timezone")
    bell_reschedule.add_argument("--actor", default="human")
    bell_reschedule.set_defaults(func=command_bell_reschedule)

    bell_ack = bell_sub.add_parser("ack")
    _add_home_env(bell_ack)
    bell_ack.add_argument("bell_id")
    bell_ack.add_argument("state", choices=["seen", "ignored", "deferred", "answered"])
    bell_ack.add_argument("--actor", default="human")
    bell_ack.add_argument("--note", default="")
    bell_ack.set_defaults(func=command_bell_ack)
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        configured_home = os.environ.get("VESTIGIA_HOME", "").strip()
        if configured_home:
            argv = ["run", configured_home]
        elif (Path.cwd() / "home.yaml").is_file():
            argv = ["run", "."]
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        if os.environ.get("VESTIGIA_DEBUG", "").lower() in {"1", "true", "yes"}:
            raise
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
