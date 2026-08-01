from __future__ import annotations

import json
import secrets
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .runtime import CoreRuntime


COMMANDER_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cottage Commander · Norton Commander for Daemons</title>
<style>
:root{--ink:#efe5c8;--dim:#aa9d7b;--gold:#e7bd58;--red:#a93c55;--panel:#15151b;--edge:#4d4738}
*{box-sizing:border-box}body{margin:0;background:#09090d;color:var(--ink);font:15px/1.4 ui-monospace,Consolas,monospace}
header{display:flex;gap:1rem;align-items:center;padding:.65rem 1rem;border-bottom:1px solid var(--edge);background:#111117}
h1{font-size:1rem;margin:0;color:var(--gold);letter-spacing:.08em}.status{color:var(--dim);margin-left:auto}
main{display:grid;grid-template-columns:1fr 1.5fr;grid-template-rows:42vh 45vh;gap:1px;background:var(--edge);height:calc(100vh - 48px)}
.pane{background:var(--panel);padding:.75rem;overflow:auto;position:relative}.pane h2{font-size:.78rem;color:var(--gold);margin:0 0 .6rem;letter-spacing:.12em}
button,input,textarea{font:inherit;color:var(--ink);background:#0e0e13;border:1px solid var(--edge)}
button{padding:.35rem .55rem;cursor:pointer}button:hover,button:focus{border-color:var(--gold);box-shadow:0 0 10px #e7bd5833;outline:none}
input{padding:.35rem;width:100%}.row{display:flex;gap:.45rem;margin:.35rem 0}.item{padding:.3rem .45rem;border-left:2px solid transparent;cursor:pointer}
.item:hover,.item.active{border-left-color:var(--gold);background:#202028}.muted{color:var(--dim)}pre{white-space:pre-wrap;word-break:break-word;margin:0}
textarea{width:100%;min-height:16rem;padding:.6rem;resize:vertical}.toolbar{display:flex;gap:.4rem;flex-wrap:wrap;margin-bottom:.55rem}
.image-preview{display:none;max-width:100%;max-height:18rem;margin:.5rem auto;border:1px solid var(--edge);object-fit:contain}
.bow{color:#f06485}.evidence{color:#8fd3a6}.warn{color:#e58a7b}@media(max-width:800px){main{display:block;height:auto}.pane{min-height:45vh}}
</style>
</head>
<body>
<header><h1>🎀 COTTAGE COMMANDER</h1><span>Norton Commander for Daemons · bounded navigator + workspace editor</span><span class="status" id="status">idle</span></header>
<main>
  <section class="pane"><h2>SHELVES + BOOKMARKS</h2><div id="shelves"></div><h2 style="margin-top:1rem">BOOKMARKS</h2><div id="bookmarks"></div></section>
  <section class="pane"><h2>OBJECTS</h2><div class="row"><input id="query" placeholder="Search filenames, text, notes, OCR, provenance"><button onclick="search()">Search</button></div><div id="objects"></div></section>
  <section class="pane"><h2>PREVIEW + WORKSPACE EDITOR</h2><div class="toolbar"><button onclick="bookmark()">🎀 Bookmark</button><button onclick="inspectPixels()">Inspect pixels</button><button onclick="prepareShare()">Prepare attachment</button><button onclick="showReceipts()">Receipts</button><button onclick="showActivity()">Activity</button><button onclick="save()">Save workspace file</button></div><img id="imagePreview" class="image-preview" alt="Verified local artifact preview"><input id="path" placeholder="house://workspace/notes.md"><textarea id="editor" spellcheck="false"></textarea></section>
  <section class="pane"><h2>PROVENANCE + HISTORY</h2><pre id="provenance">Select an object, then “show me what this is actually from.”</pre></section>
</main>
<script>
const TOKEN=__TOKEN__; let selected=null, selectedHash=null, selectedType=null, previewUrl=null;
const shelves=["index.md","identity","imports","sessions","scrapbook","artifacts","images/originals","images/generated","images/shelf","images/edits","workspace","receipts"];
function esc(s){return String(s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]))}
async function api(path, options={}){document.querySelector("#status").textContent="working";options.headers={...(options.headers||{}),"X-Cottage-Token":TOKEN};let r=await fetch(path,options);let j=await r.json();document.querySelector("#status").textContent=r.ok?"idle":"error";if(!r.ok)throw Error(j.error||r.statusText);return j}
function renderShelves(){document.querySelector("#shelves").innerHTML=shelves.map(s=>`<div class="item" onclick="list('${s}')">house://${s}/</div>`).join("")}
async function list(scope=""){let j=await api(`/api/action`,{method:"POST",body:JSON.stringify({action:"object.list",scope,limit:200,after:"finish"})});renderObjects(j.objects||[])}
function renderObjects(items){document.querySelector("#objects").innerHTML=items.map(x=>{let o=x.object||x;return `<div class="item" onclick="inspect('${esc(o.id)}')"><span class="evidence">${esc(o.object_type)}</span> ${esc(o.locator)}<br><span class="muted">${esc(o.id)} · ${esc(o.evidence_state)}</span></div>`}).join("")||'<span class="muted">No verified objects here.</span>'}
async function search(){let q=document.querySelector("#query").value;let j=await api(`/api/action`,{method:"POST",body:JSON.stringify({action:"object.search",query:q,limit:50,after:"finish"})});renderObjects(j.results||[])}
async function inspect(id){selected=id;let j=await api(`/api/action`,{method:"POST",body:JSON.stringify({action:"object.inspect",reference:id,max_tokens:5000,after:"finish"})});let o=j.object;selectedType=o.object_type;document.querySelector("#provenance").textContent=JSON.stringify(await provenance(id),null,2);document.querySelector("#path").value="house://"+o.locator;let text=j.inspection?.text||j.inspection?.inspection?.text||j.inspection?.memory?.content||j.inspection?.note?.content||JSON.stringify(j.inspection,null,2);document.querySelector("#editor").value=text||"";selectedHash=o.content_hash;await previewImage()}
async function previewImage(){let img=document.querySelector("#imagePreview");if(previewUrl){URL.revokeObjectURL(previewUrl);previewUrl=null}if(selectedType!=="image"){img.style.display="none";img.removeAttribute("src");return}let r=await fetch(`/api/image?reference=${encodeURIComponent(selected)}`,{headers:{"X-Cottage-Token":TOKEN}});if(!r.ok)throw Error((await r.json()).error||r.statusText);previewUrl=URL.createObjectURL(await r.blob());img.src=previewUrl;img.style.display="block"}
async function inspectPixels(){if(selectedType!=="image")return alert("Select an image first.");let j=await api(`/api/action`,{method:"POST",body:JSON.stringify({action:"object.inspect",reference:selected,routes:["ocr","vision_low"],question:"Describe this image and report any visible text.",after:"finish"})});document.querySelector("#editor").value=JSON.stringify(j.inspection,null,2);document.querySelector("#provenance").textContent=JSON.stringify({evidence_action:j.evidence_action,receipt_id:j.receipt_id,object:j.object},null,2)}
async function prepareShare(){if(selectedType!=="image")return alert("Select an image first.");let j=await api(`/api/action`,{method:"POST",body:JSON.stringify({action:"image.share",image_id:selected,reason:"Prepared in Cottage Commander for later resident review",after:"finish"})});document.querySelector("#provenance").textContent=JSON.stringify(j,null,2);alert("Attachment draft prepared. Claim its exact hash later through the authenticated Discord doorway.")} 
async function provenance(id){return api(`/api/action`,{method:"POST",body:JSON.stringify({action:"object.provenance",reference:id,after:"finish"})})}
async function bookmark(){if(!selected)return alert("Select an object first.");await api(`/api/action`,{method:"POST",body:JSON.stringify({action:"bookmark.add",reference:selected,label:"Cottage Commander",after:"finish"})});loadBookmarks()}
async function loadBookmarks(){let j=await api(`/api/action`,{method:"POST",body:JSON.stringify({action:"bookmark.list",limit:100,after:"finish"})});document.querySelector("#bookmarks").innerHTML=(j.bookmarks||[]).map(b=>`<div class="item bow" onclick="inspect('${esc(b.object_id)}')">🎀 ${esc(b.label||b.locator)}<br><span class="muted">${esc(b.id)}</span></div>`).join("")||'<span class="muted">No bookmarks yet.</span>'}
async function save(){let path=document.querySelector("#path").value;let content=document.querySelector("#editor").value;let p={action:"file.write",path,content,after:"finish"};if(selectedHash)p.expected_hash=selectedHash;let j=await api(`/api/action`,{method:"POST",body:JSON.stringify(p)});selected=j.object_id;selectedHash=j.content_hash;document.querySelector("#provenance").textContent=JSON.stringify(j,null,2)}
async function showReceipts(){let j=await api(`/api/action`,{method:"POST",body:JSON.stringify({action:"receipt.list",limit:50,after:"finish"})});document.querySelector("#provenance").textContent=JSON.stringify(j.receipts,null,2)}
async function showActivity(){let j=await api(`/api/action`,{method:"POST",body:JSON.stringify({action:"activity.status",after:"finish"})});document.querySelector("#provenance").textContent=JSON.stringify(j.activity,null,2)}
renderShelves();list("workspace");loadBookmarks();
</script>
</body></html>"""


class CommanderServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], runtime: CoreRuntime, token: str):
        super().__init__(address, CommanderHandler)
        self.runtime = runtime
        self.token = token


class CommanderHandler(BaseHTTPRequestHandler):
    server: CommanderServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, value: Any, status: int = 200) -> None:
        data = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        return secrets.compare_digest(
            self.headers.get("X-Cottage-Token", ""), self.server.token
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            page = COMMANDER_HTML.replace("__TOKEN__", json.dumps(self.server.token))
            data = page.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if not self._authorized():
            self._json({"error": "invalid Cottage Commander session token"}, 403)
            return
        query = parse_qs(parsed.query)
        if parsed.path == "/api/health":
            self._json({"ok": True, "resident_id": self.server.runtime.resident_id})
        elif parsed.path == "/api/activity":
            self._json(
                {"activity": self.server.runtime.house.legible.latest_activity()}
            )
        elif parsed.path == "/api/image":
            try:
                reference = str((query.get("reference") or [""])[0])
                asset = self.server.runtime.images.get_asset(reference)
                if not asset:
                    raise KeyError("unknown image")
                path = self.server.runtime.images.resolve_path(reference)
                data = path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header(
                    "Content-Type",
                    str(asset.get("media_type") or "application/octet-stream"),
                )
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'none'")
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                self._json({"error": str(exc)}, 404)
        else:
            self._json({"error": "not found", "query": query}, 404)

    def do_POST(self) -> None:
        if not self._authorized():
            self._json({"error": "invalid Cottage Commander session token"}, 403)
            return
        if urlparse(self.path).path != "/api/action":
            self._json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 2_000_000:
                raise ValueError("invalid bounded request size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("action payload must be an object")
            result = self.server.runtime.house.dispatch(
                payload,
                context={
                    "interface": "cottage_commander",
                    "invocation": "operator_local_ui",
                    "source_envelope": "TOOL_ACTION",
                },
            )
            self._json(result)
        except Exception as exc:
            self._json(
                {
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "receipt_id": getattr(exc, "house_receipt_id", None),
                },
                400,
            )


def run_commander(
    home: str,
    *,
    env_file: str | None = None,
    bind: str = "127.0.0.1",
    port: int = 4319,
    open_browser: bool = True,
) -> None:
    if bind not in {"127.0.0.1", "localhost", "::1"}:
        raise PermissionError("Cottage Commander is intentionally loopback-only")
    runtime = CoreRuntime.from_home(home, fake=True, env_file=env_file)
    token = secrets.token_urlsafe(24)
    server = CommanderServer((bind, port), runtime, token)
    url = f"http://{bind}:{server.server_address[1]}/"
    print(f"Cottage Commander: {url}")
    print("Loopback-only. Press Ctrl+C to close.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
