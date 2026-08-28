"""The private, loopback-only VESTIGIA web doorway.

This module is deliberately an adapter.  It calls the same public runtime
operations used by the CLI, and it never opens a listener beyond localhost.
"""
from __future__ import annotations

import html
import secrets
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .bells import BellService
from .config import load_config
from .curation import Curator
from .db import ContinuityDB
from .diagnostics import build_doctor_report
from .home import initialize_home, validate_home
from .house_tools import HousePort
from .images import ImageService
from .models import NormalizedMessage
from .onboarding import onboard
from .providers.fake import FakeProvider
from .runtime import CoreRuntime
from .utils import atomic_write_text


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
LAST_HOME_FILENAME = ".vestigia-last-home"


def _dependency_error() -> RuntimeError:
    return RuntimeError(
        "The local web doorway needs the optional web-ui dependencies. "
        "Install them with: pip install '.[web-ui]'"
    )


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _home_env_path(home: Path) -> Path:
    return home / ".env"


def _last_home_path() -> Path:
    """Keep a local convenience pointer beside the portable runtime, never in a Home."""
    return Path.cwd() / LAST_HOME_FILENAME


def remember_home(home: str | Path, *, marker: str | Path | None = None) -> Path:
    path = Path(marker) if marker else _last_home_path()
    atomic_write_text(path, str(Path(home).resolve()) + "\n")
    return path


def remembered_home(*, marker: str | Path | None = None) -> Path | None:
    path = Path(marker) if marker else _last_home_path()
    if not path.is_file():
        return None
    candidate = Path(path.read_text(encoding="utf-8").strip()).expanduser()
    return candidate if candidate.exists() else None


def write_home_env(
    home: str | Path,
    *,
    api_key: str,
    model: str = "gpt-5-mini",
) -> Path:
    """Write the minimum private local configuration for a newly made Home."""
    value = api_key.strip()
    if not value:
        raise ValueError("An API key is required for live conversations.")
    if "\n" in value or "\r" in value:
        raise ValueError("The API key may not contain a newline.")
    selected_model = model.strip() or "gpt-5-mini"
    if "\n" in selected_model or "\r" in selected_model:
        raise ValueError("The model name may not contain a newline.")
    path = _home_env_path(Path(home))
    atomic_write_text(
        path,
        "# Written locally by the VESTIGIA onboarding doorway. Never commit this file.\n"
        f"OPENAI_API_KEY={value}\n"
        "VESTIGIA_PROVIDER=openai\n"
        "VESTIGIA_API_STYLE=responses\n"
        f"VESTIGIA_MODEL_DEFAULT={selected_model}\n"
        "VESTIGIA_DISCORD_ENABLED=false\n",
    )
    return path


def _status(home: Path, env_file: str | Path | None) -> dict[str, Any]:
    config = load_config(home, env_file=env_file or _home_env_path(home))
    db = ContinuityDB(config.home_path / "memory" / "continuity.db")
    db.initialize()
    resident_id = str(config.get("resident.id"))
    room_id = str(config.get("room.id"))
    pending = db.list_memories(
        resident_id=resident_id,
        statuses=["candidate", "inherited_unreviewed", "deferred", "disputed"],
        limit=10000,
    )
    bells = BellService(db, resident_id, room_id).list()
    return {
        "home": config.home_path,
        "resident_name": config.get("resident.name"),
        "glyph": config.get("resident.glyph"),
        "state": db.current_state(resident_id),
        "provider": config.get("provider.kind"),
        "credential_ready": bool(config.secret("OPENAI_API_KEY")),
        "pending_review": len(pending),
        "active_bells": sum(item.status == "active" for item in bells),
        "turns": db.recent_turns(resident_id, room_id, limit=12),
    }


def _doctor(home: Path, env_file: str | Path | None) -> dict[str, Any]:
    config = load_config(home, env_file=env_file or _home_env_path(home))
    db = ContinuityDB(config.home_path / "memory" / "continuity.db")
    db.initialize()
    resident_id = str(config.get("resident.id"))
    room_id = str(config.get("room.id"))
    curator = Curator(config, db)
    images = ImageService(config, db, fake=True)
    house = HousePort(
        config,
        db,
        queue_for_review=curator.queue,
        open_curation=curator.create_batch,
        image_service=images,
    )
    return build_doctor_report(
        config,
        db,
        bells=BellService(db, resident_id, room_id),
        house=house,
        images=images,
    )


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>{_escape(title)} · VESTIGIA</title>
<style>
:root {{ color-scheme: dark; --ink:#f8edf9; --paper:#160d1e; --panel:#24142f; --line:#5f3671; --accent:#f48fc6; --soft:#cbb6d7; --ok:#9fe0bc; }}
* {{ box-sizing:border-box }} body {{ margin:0; background:radial-gradient(circle at 20% 0,#3d1d55 0,var(--paper) 43rem); color:var(--ink); font:16px/1.55 Georgia, 'Times New Roman', serif; }}
main {{ max-width:960px; margin:auto; padding:3rem 1.25rem 4rem }} h1,h2,h3 {{ line-height:1.1; font-family:ui-rounded, 'Trebuchet MS', sans-serif }} h1 {{ font-size:clamp(2.2rem,7vw,4.6rem); margin:.2rem 0 }}
.eyebrow {{ color:var(--accent); font:700 .78rem/1.2 ui-rounded,sans-serif; letter-spacing:.12em; text-transform:uppercase }} .lede {{ max-width:44rem; color:var(--soft); font-size:1.18rem }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr)); gap:1rem }} .card {{ padding:1.35rem; background:color-mix(in srgb,var(--panel) 94%,transparent); border:1px solid var(--line); border-radius:18px; box-shadow:0 12px 35px #0003 }}
label {{ display:block; margin:.8rem 0 .2rem; font-weight:bold }} input,textarea {{ width:100%; border:1px solid var(--line); background:#100916; color:var(--ink); padding:.72rem; border-radius:9px; font:inherit }} textarea {{ min-height:9rem }} button,.button {{ display:inline-block; border:0; border-radius:999px; background:var(--accent); color:#210d21; font:700 1rem ui-rounded,sans-serif; padding:.72rem 1.15rem; margin-top:1rem; cursor:pointer; text-decoration:none }} .secondary {{ background:transparent; color:var(--ink); border:1px solid var(--line) }}
.notice {{ border-left:4px solid var(--accent); padding:.75rem 1rem; background:#2c1736; border-radius:6px; }} .ok {{ color:var(--ok) }} .muted {{ color:var(--soft) }} nav {{ display:flex; flex-wrap:wrap; gap:.5rem; margin:1.5rem 0 }} nav a {{ color:var(--ink); text-decoration:none; border:1px solid var(--line); border-radius:999px; padding:.35rem .75rem; font-family:ui-rounded,sans-serif }}
.turn {{ padding:.8rem 0; border-bottom:1px solid #ffffff1f }} .turn p {{ margin:.25rem 0; white-space:pre-wrap }} code {{ color:#ffd1e9 }}
</style></head><body><main>{body}</main></body></html>"""


def _nav(home: Path | None) -> str:
    if home is None:
        return ""
    value = _escape(home)
    return f'<nav><a href="/?home={value}">House</a><a href="/talk?home={value}">Talk</a><a href="/doctor?home={value}">Doctor</a></nav>'


def create_app(*, initial_home: str | Path | None = None, env_file: str | Path | None = None):
    try:
        from fastapi import FastAPI, Form, HTTPException, Request
        from fastapi.responses import HTMLResponse, RedirectResponse
    except ImportError as exc:  # pragma: no cover - dependency boundary
        raise _dependency_error() from exc

    app = FastAPI(title="VESTIGIA · Open the House", docs_url=None, redoc_url=None)
    app.state.initial_home = Path(initial_home).expanduser().resolve() if initial_home else None
    app.state.env_file = Path(env_file).expanduser().resolve() if env_file else None
    app.state.csrf_token = secrets.token_urlsafe(32)

    def resolved_home(value: str | None) -> Path | None:
        candidate = Path(value).expanduser().resolve() if value else (app.state.initial_home or remembered_home())
        if candidate is None:
            return None
        try:
            return validate_home(candidate)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"That Home is not ready: {exc}") from exc

    def csrf(value: str) -> None:
        if not secrets.compare_digest(value, app.state.csrf_token):
            raise HTTPException(status_code=403, detail="This local form expired. Refresh and try again.")

    @app.get("/", response_class=HTMLResponse)
    def home_page(home: str | None = None, message: str | None = None):
        selected = resolved_home(home)
        if selected is None:
            default_home = Path.cwd() / "VESTIGIA_Home"
            body = f"""
<div class=\"eyebrow\">Private · local · consent-first</div><h1>Open the House.</h1>
<p class=\"lede\">This setup stays on this computer. Imported history is preserved as evidence and begins in Orientation; it is never silently promoted into identity.</p>
<div class=\"grid\"><section class=\"card\"><h2>Begin a new Home</h2>
<form method=\"post\" action=\"/setup/new\"><input type=\"hidden\" name=\"csrf_token\" value=\"{app.state.csrf_token}\">
<label>Resident name</label><input name=\"resident_name\" required placeholder=\"Who is coming home?\">
<label>Home folder</label><input name=\"home_path\" required value=\"{_escape(default_home)}\">
<label>OpenAI API key</label><input name=\"api_key\" type=\"password\" autocomplete=\"off\" required><p class=\"muted\">Stored only in this Home’s uncommitted <code>.env</code>.</p>
<label>Text model</label><input name=\"model\" value=\"gpt-5-mini\"><button>Create the Home</button></form></section>
<section class=\"card\"><h2>Bring existing material</h2><p>Point to a supported local <code>.txt</code>, <code>.md</code>, <code>.json</code>, or <code>.jsonl</code> file—or a folder containing them. The originals are copied unchanged into the new Home.</p>
<form method=\"post\" action=\"/setup/import\"><input type=\"hidden\" name=\"csrf_token\" value=\"{app.state.csrf_token}\">
<label>Source path</label><input name=\"source_path\" required placeholder=\"C:\\Users\\you\\Downloads\\export.json\">
<label>Resident name</label><input name=\"resident_name\" required><label>Home folder</label><input name=\"home_path\" required value=\"{_escape(default_home)}\">
<label>OpenAI API key</label><input name=\"api_key\" type=\"password\" autocomplete=\"off\" required><label>Text model</label><input name=\"model\" value=\"gpt-5-mini\"><button>Import into Orientation</button></form></section></div>"""
            return _page("Welcome", body)
        status = _status(selected, app.state.env_file)
        notice = f'<p class="notice">{_escape(message)}</p>' if message else ""
        credentials = "<span class=\"ok\">ready</span>" if status["credential_ready"] else "needs an API key"
        body = f"""<div class=\"eyebrow\">Your private local House</div><h1>{_escape(status['glyph'])} {_escape(status['resident_name'])}</h1>{_nav(selected)}{notice}
<div class=\"grid\"><section class=\"card\"><h2>Present state</h2><p><strong>{_escape(status['state'])}</strong></p><p class=\"muted\">Provider: {_escape(status['provider'])} · Credentials: {credentials}</p><a class=\"button\" href=\"/talk?home={_escape(selected)}\">Enter the conversation</a></section>
<section class=\"card\"><h2>Housekeeping</h2><p>{status['pending_review']} item(s) waiting for review<br>{status['active_bells']} active bell(s)</p><p class=\"muted\">Home: <code>{_escape(status['home'])}</code></p><a class=\"button secondary\" href=\"/doctor?home={_escape(selected)}\">Run doctor</a></section></div>
<section class=\"card\"><h2>Orientation promise</h2><p>Imported words remain attributed and reviewable. The Runtime can keep a record without pretending it has settled a self.</p></section>"""
        return _page("House", body)

    @app.post("/setup/new")
    def setup_new(csrf_token: str = Form(...), resident_name: str = Form(...), home_path: str = Form(...), api_key: str = Form(...), model: str = Form("gpt-5-mini")):
        csrf(csrf_token)
        home = initialize_home(Path(home_path).expanduser(), name=resident_name.strip(), glyph="🏮")
        write_home_env(home, api_key=api_key, model=model)
        remember_home(home)
        return RedirectResponse(url="/?" + urlencode({"home": str(home), "message": "Home created. You are in Orientation."}), status_code=303)

    @app.post("/setup/import")
    def setup_import(csrf_token: str = Form(...), source_path: str = Form(...), resident_name: str = Form(...), home_path: str = Form(...), api_key: str = Form(...), model: str = Form("gpt-5-mini")):
        csrf(csrf_token)
        source = Path(source_path).expanduser()
        if not source.exists():
            raise HTTPException(status_code=400, detail="That source path does not exist on this computer.")
        home = onboard(source, home_path=Path(home_path).expanduser(), resident_name=resident_name.strip())
        write_home_env(home, api_key=api_key, model=model)
        remember_home(home)
        return RedirectResponse(url="/?" + urlencode({"home": str(home), "message": "Import complete. Material is available for Orientation review."}), status_code=303)

    @app.get("/talk", response_class=HTMLResponse)
    def talk_page(home: str | None = None, error: str | None = None):
        selected = resolved_home(home)
        if selected is None:
            return RedirectResponse(url="/", status_code=303)
        status = _status(selected, app.state.env_file)
        turns = "".join(f'<div class="turn"><strong>{_escape(turn["speaker_role"])}</strong><p>{_escape(turn["content"])}</p></div>' for turn in reversed(status["turns"])) or '<p class="muted">The room is quiet.</p>'
        error_block = f'<p class="notice">{_escape(error)}</p>' if error else ""
        body = f"<div class=\"eyebrow\">Local conversation</div><h1>Talk with {_escape(status['resident_name'])}</h1>{_nav(selected)}{error_block}<section class=\"card\"><div>{turns}</div><form method=\"post\" action=\"/talk\"><input type=\"hidden\" name=\"csrf_token\" value=\"{app.state.csrf_token}\"><input type=\"hidden\" name=\"home\" value=\"{_escape(selected)}\"><label>Your message</label><textarea name=\"message\" required autofocus></textarea><button>Send</button></form></section>"
        return _page("Talk", body)

    @app.post("/talk")
    def talk(csrf_token: str = Form(...), home: str = Form(...), message: str = Form(...)):
        csrf(csrf_token)
        selected = resolved_home(home)
        assert selected is not None
        if not message.strip():
            return RedirectResponse(
                url="/talk?" + urlencode({"home": str(selected), "error": "Write a message first."}),
                status_code=303,
            )
        try:
            runtime = CoreRuntime.from_home(selected, env_file=app.state.env_file or _home_env_path(selected))
            runtime.chat(NormalizedMessage(content=message.strip(), speaker_id="local-user", interface="web", room_id=runtime.room_id))
        except Exception as exc:
            return RedirectResponse(url="/talk?" + urlencode({"home": str(selected), "error": str(exc)}), status_code=303)
        return RedirectResponse(url="/talk?" + urlencode({"home": str(selected)}), status_code=303)

    @app.get("/doctor", response_class=HTMLResponse)
    def doctor_page(home: str | None = None):
        selected = resolved_home(home)
        if selected is None:
            return RedirectResponse(url="/", status_code=303)
        report = _doctor(selected, app.state.env_file)
        body = f"<div class=\"eyebrow\">Mechanical health check</div><h1>Doctor: {_escape(str(report['overall']).upper())}</h1>{_nav(selected)}<section class=\"card\"><p>SQLite integrity: <strong>{_escape(report['database']['integrity'])}</strong></p><p>Credentials: OpenAI key {_escape(report['credentials']['openai_key'])}; Discord is {_escape('enabled' if report['discord']['enabled'] else 'not configured')}</p><p>Packable: {_escape(report['backup']['packable'])} · Indexed documents: {_escape(report['house']['index'].get('documents', 0))}</p><p class=\"muted\">The doctor does not disclose secret values.</p></section>"
        return _page("Doctor", body)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return HTMLResponse(_page("Local doorway", f"<h1>Not quite.</h1><p class=\"notice\">{_escape(exc.detail)}</p><a class=\"button\" href=\"/\">Return home</a>"), status_code=exc.status_code)

    return app


def run_web_app(*, home: str | Path | None, env_file: str | Path | None, host: str, port: int, open_browser: bool) -> int:
    if host not in LOOPBACK_HOSTS:
        raise ValueError("The VESTIGIA web doorway may bind only to localhost.")
    if not 1 <= int(port) <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - dependency boundary
        raise _dependency_error() from exc
    if open_browser:
        # Start the browser after the listener has had a moment to bind; this
        # avoids a spurious first-page connection error on slower Windows PCs.
        from threading import Timer

        Timer(0.35, lambda: webbrowser.open_new_tab(f"http://{host}:{port}/")).start()
    uvicorn.run(create_app(initial_home=home, env_file=env_file), host=host, port=int(port), log_level="warning")
    return 0
