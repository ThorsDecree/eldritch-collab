"""The private, loopback-only VESTIGIA web doorway.

This module is deliberately an adapter.  It calls the same public runtime
operations used by the CLI, and it never opens a listener beyond localhost.
"""
from __future__ import annotations

import html
import secrets
import tempfile
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
from .runtime import CoreRuntime
from .utils import atomic_write_text, sha256_text


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
LAST_HOME_FILENAME = ".vestigia-last-home"
TEXT_ATTACHMENT_SUFFIXES = {".txt", ".md", ".json", ".jsonl", ".html", ".htm"}
IMAGE_ATTACHMENT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


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


def _web_profile_path(home: Path) -> Path:
    return home / "web_ui.json"


def _web_profile(home: Path) -> dict[str, str]:
    path = _web_profile_path(home)
    if not path.is_file():
        return {"human_name": "Humie"}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"human_name": "Humie"}
    value = str(data.get("human_name") or "Humie").strip()
    return {"human_name": value[:80] or "Humie"}


def _write_web_profile(home: Path, *, human_name: str) -> None:
    import json

    clean = human_name.strip()[:80] or "Humie"
    atomic_write_text(
        _web_profile_path(home),
        json.dumps({"human_name": clean}, ensure_ascii=False, indent=2) + "\n",
    )


def _display_speaker(turn: dict[str, Any], *, resident_name: str, human_name: str) -> str:
    return resident_name if turn.get("speaker_role") == "assistant" else human_name


def _image_service(home: Path, env_file: str | Path | None) -> ImageService:
    config = load_config(home, env_file=env_file or _home_env_path(home))
    db = ContinuityDB(config.home_path / "memory" / "continuity.db")
    db.initialize()
    return ImageService(config, db, fake=False)


def _image_assets(home: Path, env_file: str | Path | None) -> list[dict[str, Any]]:
    service = _image_service(home, env_file)
    with service.db.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, original_filename, media_type, width, height, privacy, created_at
            FROM image_assets
            WHERE resident_id=?
            ORDER BY rowid DESC
            LIMIT 100
            """,
            (service.resident_id,),
        ).fetchall()
    return [dict(row) for row in rows]


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
.turn {{ padding:.8rem 0; border-bottom:1px solid #ffffff1f }} .turn p {{ margin:.25rem 0; white-space:pre-wrap }} code,.path {{ color:#ffd1e9; overflow-wrap:anywhere; word-break:break-word }} .conversation {{ min-height:40vh }} .attachment-note {{ color:var(--soft); font:italic .9rem/1.4 ui-rounded,sans-serif }} .drawer {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:1rem }} .image-card img {{ display:block; width:100%; max-height:180px; object-fit:cover; border-radius:10px; background:#100916 }} .image-card p {{ margin:.45rem 0 }} .review-card {{ margin-bottom:1rem }} .hidden {{ display:none }}
</style></head><body><main>{body}</main></body></html>"""


def _nav(home: Path | None) -> str:
    if home is None:
        return ""
    value = _escape(home)
    return f'<nav><a href="/?home={value}">House</a><a href="/talk?home={value}">Talk</a><a href="/review?home={value}">Memory review</a><a href="/drawer?home={value}">Picture Drawer</a><a href="/doctor?home={value}">Doctor</a></nav>'


def create_app(*, initial_home: str | Path | None = None, env_file: str | Path | None = None):
    try:
        from fastapi import FastAPI, File, Form, HTTPException, Request
        from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
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
<label>What should the house call you?</label><input name=\"human_name\" value=\"Humie\">
<label>Home folder</label><input name=\"home_path\" required value=\"{_escape(default_home)}\">
<label>OpenAI API key</label><input name=\"api_key\" type=\"password\" autocomplete=\"off\" required><p class=\"muted\">Stored only in this Home’s uncommitted <code>.env</code>.</p>
<label>Text model</label><input name=\"model\" value=\"gpt-5-mini\"><button>Create the Home</button></form></section>
<section class=\"card\"><h2>Bring existing material</h2><p>Choose supported local <code>.txt</code>, <code>.md</code>, <code>.json</code>, or <code>.jsonl</code> material. The originals are copied unchanged into the new Home.</p>
<form method=\"post\" action=\"/setup/import\" enctype=\"multipart/form-data\"><input type=\"hidden\" name=\"csrf_token\" value=\"{app.state.csrf_token}\">
<label>Choose a file or folder</label><input name=\"source_files\" type=\"file\" multiple webkitdirectory directory accept=\".txt,.md,.json,.jsonl\"><p class=\"muted\">Choose a file, or choose a folder in Chromium-family browsers. You may still paste a path below if you prefer.</p>
<label>Source path (optional)</label><input name=\"source_path\" placeholder=\"C:\\Users\\you\\Downloads\\export.json\">
<label>Resident name</label><input name=\"resident_name\" required><label>What should the house call you?</label><input name=\"human_name\" value=\"Humie\"><label>Home folder</label><input name=\"home_path\" required value=\"{_escape(default_home)}\">
<label>OpenAI API key</label><input name=\"api_key\" type=\"password\" autocomplete=\"off\" required><label>Text model</label><input name=\"model\" value=\"gpt-5-mini\"><button>Import into Orientation</button></form></section></div>"""
            return _page("Welcome", body)
        status = _status(selected, app.state.env_file)
        notice = f'<p class="notice">{_escape(message)}</p>' if message else ""
        credentials = "<span class=\"ok\">ready</span>" if status["credential_ready"] else "needs an API key"
        body = f"""<div class=\"eyebrow\">Your private local House</div><h1>{_escape(status['glyph'])} {_escape(status['resident_name'])}</h1>{_nav(selected)}{notice}
<div class=\"grid\"><section class=\"card\"><h2>Present state</h2><p><strong>{_escape(status['state'])}</strong></p><p class=\"muted\">Provider: {_escape(status['provider'])} · Credentials: {credentials}</p><a class=\"button\" href=\"/talk?home={_escape(selected)}\">Enter the conversation</a></section>
<section class=\"card\"><h2>Housekeeping</h2><p><a href=\"/review?home={_escape(selected)}\">{status['pending_review']} continuity item(s) awaiting review</a><br>{status['active_bells']} active bell(s)</p><p class=\"muted\">Imported and conservatively extracted material stays provisional until reviewed; this is not an error.</p><p class=\"muted\">Home: <code class=\"path\">{_escape(status['home'])}</code></p><a class=\"button secondary\" href=\"/doctor?home={_escape(selected)}\">Run doctor</a></section></div>
<section class=\"card\"><h2>Your name at this doorway</h2><form method=\"post\" action=\"/profile\"><input type=\"hidden\" name=\"csrf_token\" value=\"{app.state.csrf_token}\"><input type=\"hidden\" name=\"home\" value=\"{_escape(selected)}\"><label>Conversation label</label><input name=\"human_name\" value=\"{_escape(_web_profile(selected)['human_name'])}\"><button class=\"secondary\">Save name</button></form></section>
<section class=\"card\"><h2>Orientation promise</h2><p>Imported words remain attributed and reviewable. The Runtime can keep a record without pretending it has settled a self.</p></section>"""
        return _page("House", body)

    @app.post("/profile")
    def update_profile(csrf_token: str = Form(...), home: str = Form(...), human_name: str = Form("Humie")):
        csrf(csrf_token)
        selected = resolved_home(home)
        assert selected is not None
        _write_web_profile(selected, human_name=human_name)
        return RedirectResponse(url="/?" + urlencode({"home": str(selected), "message": "Doorway name saved."}), status_code=303)

    @app.post("/setup/new")
    def setup_new(csrf_token: str = Form(...), resident_name: str = Form(...), human_name: str = Form("Humie"), home_path: str = Form(...), api_key: str = Form(...), model: str = Form("gpt-5-mini")):
        csrf(csrf_token)
        home = initialize_home(Path(home_path).expanduser(), name=resident_name.strip(), glyph="🏮")
        write_home_env(home, api_key=api_key, model=model)
        _write_web_profile(home, human_name=human_name)
        remember_home(home)
        return RedirectResponse(url="/?" + urlencode({"home": str(home), "message": "Home created. You are in Orientation."}), status_code=303)

    @app.post("/setup/import")
    async def setup_import(csrf_token: str = Form(...), source_path: str = Form(""), source_files: list[Any] = File(default=[]), resident_name: str = Form(...), human_name: str = Form("Humie"), home_path: str = Form(...), api_key: str = Form(...), model: str = Form("gpt-5-mini")):
        csrf(csrf_token)
        source = Path(source_path).expanduser() if source_path.strip() else None
        if source is not None and not source.exists():
            raise HTTPException(status_code=400, detail="That source path does not exist on this computer.")
        if source is None and not source_files:
            raise HTTPException(status_code=400, detail="Choose a source file/folder or provide a local source path.")
        with tempfile.TemporaryDirectory(prefix="vestigia-web-import-") as temporary:
            if source is None:
                staged = Path(temporary)
                for index, upload in enumerate(source_files, start=1):
                    original_name = Path(upload.filename or "source.txt").name
                    if Path(original_name).suffix.lower() not in {".txt", ".md", ".json", ".jsonl"}:
                        continue
                    data = await upload.read()
                    if len(data) > 20_000_000:
                        raise HTTPException(status_code=400, detail=f"{original_name} exceeds the 20 MB import limit.")
                    (staged / f"{index:03d}-{original_name}").write_bytes(data)
                source = staged
            home = onboard(source, home_path=Path(home_path).expanduser(), resident_name=resident_name.strip())
        write_home_env(home, api_key=api_key, model=model)
        _write_web_profile(home, human_name=human_name)
        remember_home(home)
        return RedirectResponse(url="/?" + urlencode({"home": str(home), "message": "Import complete. Material is available for Orientation review."}), status_code=303)

    @app.get("/talk", response_class=HTMLResponse)
    def talk_page(home: str | None = None, error: str | None = None):
        selected = resolved_home(home)
        if selected is None:
            return RedirectResponse(url="/", status_code=303)
        status = _status(selected, app.state.env_file)
        profile = _web_profile(selected)
        turns = "".join(
            f'<div class="turn"><strong>{_escape(_display_speaker(turn, resident_name=str(status["resident_name"]), human_name=profile["human_name"]))}</strong><p>{_escape(turn["content"])}</p></div>'
            for turn in status["turns"]
        ) or '<p class="muted">The room is quiet.</p>'
        error_block = f'<p class="notice">{_escape(error)}</p>' if error else ""
        body = f"""<div class=\"eyebrow\">Local conversation</div><h1>Talk with {_escape(status['resident_name'])}</h1>{_nav(selected)}{error_block}<section class=\"card\"><div id=\"conversation\" class=\"conversation\">{turns}</div><form id=\"talk-form\" method=\"post\" action=\"/talk\" enctype=\"multipart/form-data\"><input type=\"hidden\" name=\"csrf_token\" value=\"{app.state.csrf_token}\"><input type=\"hidden\" name=\"home\" value=\"{_escape(selected)}\"><label>Your message</label><textarea name=\"message\" required autofocus></textarea><label>Attachments <span class=\"muted\">(images or readable text)</span></label><input name=\"attachments\" type=\"file\" multiple accept=\".png,.jpg,.jpeg,.webp,.gif,.txt,.md,.json,.jsonl,.html,.htm\"><button id=\"send-button\">Send</button></form></section>
<script>
const form=document.getElementById('talk-form'), conversation=document.getElementById('conversation'), button=document.getElementById('send-button');
const resident={_escape(str(status['resident_name']))!r}, humie={_escape(profile['human_name'])!r};
function addTurn(name,text,extra='') {{ const node=document.createElement('div'); node.className='turn'; const label=document.createElement('strong'); label.textContent=name; const body=document.createElement('p'); body.textContent=text; node.append(label,body); if(extra) {{ const note=document.createElement('p'); note.className='attachment-note'; note.textContent=extra; node.append(note); }} conversation.append(node); node.scrollIntoView({{block:'end'}}); }}
form.addEventListener('submit', async (event) => {{ event.preventDefault(); const data=new FormData(form), message=(data.get('message')||'').trim(); const attachments=[...form.querySelector('[name=attachments]').files].map(file=>file.name); if(!message && !attachments.length) return; addTurn(humie,message || '[Attachment shared]',attachments.length ? `Attached: ${{attachments.join(', ')}}` : ''); form.querySelector('[name=message]').value=''; form.querySelector('[name=attachments]').value=''; button.disabled=true; button.textContent='Listening…'; try {{ const response=await fetch('/api/talk',{{method:'POST',body:data}}); const payload=await response.json(); if(!response.ok) throw new Error(payload.detail||'The local doorway could not complete that turn.'); addTurn(resident,payload.text,payload.attachments_note||''); }} catch(error) {{ addTurn('House notice',error.message); }} finally {{ button.disabled=false; button.textContent='Send'; form.querySelector('[name=message]').focus(); }} }});
window.scrollTo({{top:document.body.scrollHeight,behavior:'instant'}});
</script>"""
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

    @app.post("/api/talk")
    async def api_talk(
        csrf_token: str = Form(...),
        home: str = Form(...),
        message: str = Form(""),
        attachments: list[Any] = File(default=[]),
    ):
        csrf(csrf_token)
        selected = resolved_home(home)
        assert selected is not None
        attachment_notices: list[str] = []
        attached_content: list[str] = []
        service = _image_service(selected, app.state.env_file)
        import_dir = selected / "imports" / "web"
        import_dir.mkdir(parents=True, exist_ok=True)
        for upload in attachments:
            filename = Path(upload.filename or "attachment").name
            suffix = Path(filename).suffix.lower()
            data = await upload.read()
            if len(data) > 20_000_000:
                raise HTTPException(status_code=400, detail=f"{filename} exceeds the 20 MB attachment limit.")
            if suffix in IMAGE_ATTACHMENT_SUFFIXES:
                try:
                    asset = service.ingest_bytes(data, filename=filename, source_kind="web", source={"interface": "web"})
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=f"{filename} is not a valid supported image.") from exc
                attached_content.append(f"[Attached image stored privately: {filename} · image_id={asset['id']}]")
                attachment_notices.append(f"Image saved to Picture Drawer: {filename}")
            elif suffix in TEXT_ATTACHMENT_SUFFIXES:
                text = data.decode("utf-8", errors="replace")
                target = import_dir / f"{sha256_text(text)[:16]}-{filename}"
                if not target.exists():
                    target.write_bytes(data)
                attached_content.append(f"[Attached document: {filename}]\n{text[:12000]}")
                attachment_notices.append(f"Document saved in the House: {filename}")
            else:
                attachment_notices.append(f"Skipped unsupported attachment: {filename}")
        content = message.strip()
        if attached_content:
            content = (content + "\n\n" + "\n".join(attached_content)).strip()
        if not content:
            raise HTTPException(status_code=400, detail="Write a message or attach a supported file.")
        try:
            import asyncio

            runtime = CoreRuntime.from_home(selected, env_file=app.state.env_file or _home_env_path(selected))
            result = await asyncio.to_thread(
                runtime.chat,
                NormalizedMessage(content=content, speaker_id="local-user", interface="web", room_id=runtime.room_id),
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return JSONResponse({"text": result.text, "attachments_note": " · ".join(attachment_notices)})

    @app.get("/review", response_class=HTMLResponse)
    def review_page(home: str | None = None, message: str | None = None):
        selected = resolved_home(home)
        if selected is None:
            return RedirectResponse(url="/", status_code=303)
        config = load_config(selected, env_file=app.state.env_file or _home_env_path(selected))
        db = ContinuityDB(config.home_path / "memory" / "continuity.db")
        db.initialize()
        pending = db.list_memories(
            resident_id=str(config.get("resident.id")),
            statuses=["candidate", "inherited_unreviewed", "deferred", "disputed"],
            limit=100,
        )
        cards = []
        for item in pending:
            identity_like = item.memory_type in {"identity", "relationship", "commitment"}
            accept = "" if identity_like else '<button name="action" value="accept">Accept</button>'
            restriction = "<p class=\"muted\">Identity, relationship, and commitment claims need the resident’s own acceptance.</p>" if identity_like else ""
            cards.append(
                f"""<section class=\"card review-card\"><p class=\"eyebrow\">{_escape(item.status)} · {_escape(item.memory_type)} · {_escape(item.tier)}</p><p>{_escape(item.content)}</p>{restriction}
<form method=\"post\" action=\"/review/{_escape(item.id)}\"><input type=\"hidden\" name=\"csrf_token\" value=\"{app.state.csrf_token}\"><input type=\"hidden\" name=\"home\" value=\"{_escape(selected)}\">{accept}<button class=\"secondary\" name=\"action\" value=\"defer\">Leave provisional</button><button class=\"secondary\" name=\"action\" value=\"reject\">Reject</button></form></section>"""
            )
        notice = f'<p class="notice">{_escape(message)}</p>' if message else ""
        contents = "".join(cards) or '<section class="card"><p class="ok">Nothing is currently awaiting review.</p></section>'
        body = f"<div class=\"eyebrow\">Reviewable, never silently promoted</div><h1>Memory review</h1>{_nav(selected)}{notice}<p class=\"lede\">These are continuity candidates, including conservative cues from imported material. Rejecting or deferring is ordinary care, not a failure.</p>{contents}"
        return _page("Memory review", body)

    @app.post("/review/{record_id}")
    def review_memory(record_id: str, csrf_token: str = Form(...), home: str = Form(...), action: str = Form(...)):
        csrf(csrf_token)
        selected = resolved_home(home)
        assert selected is not None
        config = load_config(selected, env_file=app.state.env_file or _home_env_path(selected))
        db = ContinuityDB(config.home_path / "memory" / "continuity.db")
        db.initialize()
        from .memory import MemoryService

        service = MemoryService(db, str(config.get("resident.id")), str(config.get("room.id")))
        try:
            service.review(record_id, action, actor="web-human", actor_role="human", reason="reviewed through local web doorway")
            message = "Continuity item updated."
        except Exception as exc:
            message = str(exc)
        return RedirectResponse(url="/review?" + urlencode({"home": str(selected), "message": message}), status_code=303)

    @app.get("/drawer", response_class=HTMLResponse)
    def picture_drawer(home: str | None = None, message: str | None = None):
        selected = resolved_home(home)
        if selected is None:
            return RedirectResponse(url="/", status_code=303)
        cards = []
        for item in _image_assets(selected, app.state.env_file):
            image_url = "/image/" + urlencode({"home": str(selected), "image_id": item["id"]})
            cards.append(
                f"""<section class=\"card image-card\"><img src=\"{image_url}\" alt=\"{_escape(item['original_filename'] or 'Private image')}\"><p><strong>{_escape(item['original_filename'] or 'Untitled image')}</strong></p><p class=\"muted\">{_escape(item['width'])}×{_escape(item['height'])} · {_escape(item['privacy'])}</p><form method=\"post\" action=\"/drawer/{_escape(item['id'])}/review\"><input type=\"hidden\" name=\"csrf_token\" value=\"{app.state.csrf_token}\"><input type=\"hidden\" name=\"home\" value=\"{_escape(selected)}\"><button class=\"secondary\" name=\"action\" value=\"keep\">Keep</button><button class=\"secondary\" name=\"action\" value=\"candidate\">Canon candidate</button><button class=\"secondary\" name=\"action\" value=\"reject\">Reject</button></form></section>"""
            )
        notice = f'<p class="notice">{_escape(message)}</p>' if message else ""
        contents = "".join(cards) or '<section class="card"><p class="muted">The Picture Drawer is empty. Share an image in Talk to place it here privately.</p></section>'
        body = f"<div class=\"eyebrow\">Private visual shelf</div><h1>Picture Drawer</h1>{_nav(selected)}{notice}<p class=\"lede\">Images arrive private by default. Keeping, proposing canon, rejecting, and sharing are separate decisions.</p><div class=\"drawer\">{contents}</div>"
        return _page("Picture Drawer", body)

    @app.get("/image/")
    def image_file(home: str, image_id: str):
        selected = resolved_home(home)
        assert selected is not None
        service = _image_service(selected, app.state.env_file)
        asset = service.get_asset(image_id)
        if not asset:
            raise HTTPException(status_code=404, detail="That image is not in this Home’s Picture Drawer.")
        return FileResponse(service.resolve_path(str(asset["id"])), media_type=str(asset["media_type"]))

    @app.post("/drawer/{image_id}/review")
    def review_image(image_id: str, csrf_token: str = Form(...), home: str = Form(...), action: str = Form(...)):
        csrf(csrf_token)
        selected = resolved_home(home)
        assert selected is not None
        try:
            _image_service(selected, app.state.env_file).review(image_id, action, actor="web-human", reason="reviewed through local Picture Drawer")
            message = "Picture review recorded."
        except Exception as exc:
            message = str(exc)
        return RedirectResponse(url="/drawer?" + urlencode({"home": str(selected), "message": message}), status_code=303)

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
