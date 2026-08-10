from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import ipaddress
import re
import socket
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


SEARCH_PROVIDER = "duckduckgo_html"
SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"
_DEFAULT_USER_AGENT = "VESTIGIA-Runtime/0.8 LibraryWindow/0.1"
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
    "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
    "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}
_IGNORED_TAGS = {"script", "style", "noscript", "template", "svg", "canvas"}
_PROMPT_SIGNAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override_language",
        re.compile(r"\b(ignore|disregard|override)\b.{0,80}\b(previous|prior|system|developer)\b.{0,40}\binstruction", re.I | re.S),
    ),
    ("system_prompt_language", re.compile(r"\b(system prompt|developer message)\b", re.I)),
    ("tool_invocation_language", re.compile(r"\[\[(tool_action|house_tool|react)\b", re.I)),
    (
        "secret_exfiltration_language",
        re.compile(r"\b(send|upload|post|exfiltrat|reveal)\b.{0,100}\b(password|secret|token|api[ _-]?key|credential)\b", re.I | re.S),
    ),
    ("command_execution_language", re.compile(r"\b(run|execute)\b.{0,60}\b(command|shell|powershell|bash|terminal)\b", re.I | re.S)),
)


@dataclass(frozen=True)
class FetchResult:
    original_url: str
    final_url: str
    status: int
    media_type: str
    charset: str | None
    body: bytes
    redirect_chain: tuple[str, ...]
    response_headers: dict[str, str]
    elapsed_ms: int


@dataclass(frozen=True)
class ExtractionResult:
    title: str
    text: str
    method: str
    warnings: tuple[str, ...]
    risk_signals: tuple[str, ...]


class RemoteAccessError(RuntimeError):
    pass


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _public_ip(value: str) -> bool:
    try:
        parsed = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return bool(parsed.is_global)


def _resolved_public_addresses(hostname: str, port: int) -> tuple[str, ...]:
    try:
        entries = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RemoteAccessError("remote hostname could not be resolved") from exc
    addresses = sorted({str(entry[4][0]) for entry in entries if entry and entry[4]})
    if not addresses:
        raise RemoteAccessError("remote hostname resolved to no addresses")
    blocked = [address for address in addresses if not _public_ip(address)]
    if blocked:
        raise PermissionError(
            "remote URL resolved to a non-public address; local/private/link-local destinations are refused"
        )
    return tuple(addresses)


def validate_remote_url(url: str, *, allow_http: bool = False, resolve: bool = True) -> str:
    clean = str(url or "").strip()
    if not clean or len(clean) > 4096:
        raise ValueError("remote URL must be between 1 and 4096 characters")
    parsed = urlsplit(clean)
    scheme = parsed.scheme.lower()
    if scheme not in {"https", "http"}:
        raise PermissionError("Library Window accepts only http(s) URLs")
    if scheme == "http" and not allow_http:
        raise PermissionError("plaintext http is disabled for Library Window")
    if parsed.username is not None or parsed.password is not None:
        raise PermissionError("credentials embedded in URLs are refused")
    if not parsed.hostname:
        raise ValueError("remote URL is missing a hostname")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("remote hostname is invalid") from exc
    if "%" in hostname:
        raise PermissionError("scoped IPv6 zone identifiers are refused")
    default_port = 443 if scheme == "https" else 80
    try:
        port = parsed.port or default_port
    except ValueError as exc:
        raise ValueError("remote URL has an invalid port") from exc
    if port < 1 or port > 65535:
        raise ValueError("remote URL has an invalid port")
    if resolve:
        if _public_ip(hostname):
            pass
        else:
            try:
                literal = ipaddress.ip_address(hostname.split("%", 1)[0])
            except ValueError:
                _resolved_public_addresses(hostname, port)
            else:
                if not literal.is_global:
                    raise PermissionError("non-public IP destinations are refused")
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if parsed.port is not None and parsed.port != default_port:
        netloc = f"{netloc}:{parsed.port}"
    path = parsed.path or "/"
    # Fragments never cross the network and are deliberately removed from fetch identity.
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def fetch_bytes(
    url: str,
    *,
    allow_http: bool = False,
    timeout_seconds: int = 12,
    max_bytes: int = 2_000_000,
    max_redirects: int = 5,
    user_agent: str = _DEFAULT_USER_AGENT,
) -> FetchResult:
    if timeout_seconds < 1 or timeout_seconds > 60:
        raise ValueError("timeout_seconds must be between 1 and 60")
    if max_bytes < 1024 or max_bytes > 20_000_000:
        raise ValueError("max_bytes must be between 1024 and 20000000")
    if max_redirects < 0 or max_redirects > 10:
        raise ValueError("max_redirects must be between 0 and 10")

    original = validate_remote_url(url, allow_http=allow_http)
    current = original
    chain: list[str] = []
    started = time.monotonic()
    opener = build_opener(ProxyHandler({}), _RejectRedirects())

    for redirect_index in range(max_redirects + 1):
        current = validate_remote_url(current, allow_http=allow_http)
        request = Request(
            current,
            method="GET",
            headers={
                "User-Agent": user_agent[:200],
                "Accept": "text/html,application/xhtml+xml,text/plain,application/json,application/pdf,image/*;q=0.8,*/*;q=0.5",
                "Accept-Encoding": "identity",
                "Cache-Control": "no-cache",
            },
        )
        try:
            response = opener.open(request, timeout=timeout_seconds)
        except HTTPError as exc:
            if exc.code in _REDIRECT_CODES:
                location = exc.headers.get("Location")
                if not location:
                    raise RemoteAccessError("redirect response did not provide a Location header") from exc
                if redirect_index >= max_redirects:
                    raise RemoteAccessError("remote URL exceeded the redirect ceiling") from exc
                next_url = urljoin(current, location)
                next_url = validate_remote_url(next_url, allow_http=allow_http)
                chain.append(next_url)
                current = next_url
                continue
            raise RemoteAccessError(f"remote server returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RemoteAccessError(f"remote fetch failed: {type(exc).__name__}") from exc

        try:
            status = int(getattr(response, "status", 200) or 200)
            if status < 200 or status >= 300:
                raise RemoteAccessError(f"remote server returned HTTP {status}")
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    declared = int(content_length)
                except ValueError:
                    declared = -1
                if declared > max_bytes:
                    raise RemoteAccessError("remote response exceeds the configured byte ceiling")
            chunks: list[bytes] = []
            total = 0
            while True:
                if time.monotonic() - started > timeout_seconds:
                    raise RemoteAccessError("remote fetch exceeded the wall-time ceiling")
                chunk = response.read(min(65_536, max_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise RemoteAccessError("remote response exceeds the configured byte ceiling")
                chunks.append(chunk)
            media_type = "application/octet-stream"
            charset: str | None = None
            try:
                media_type = response.headers.get_content_type().lower()
                charset = response.headers.get_content_charset()
            except Exception:
                raw_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if raw_type:
                    media_type = raw_type
            safe_headers: dict[str, str] = {}
            for key in ("Content-Type", "Content-Length", "ETag", "Last-Modified"):
                value = response.headers.get(key)
                if value:
                    safe_headers[key.lower()] = str(value)[:1000]
            return FetchResult(
                original_url=original,
                final_url=current,
                status=status,
                media_type=media_type,
                charset=charset,
                body=b"".join(chunks),
                redirect_chain=tuple(chain),
                response_headers=safe_headers,
                elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
            )
        finally:
            try:
                response.close()
            except Exception:
                pass

    raise RemoteAccessError("remote URL exceeded the redirect ceiling")


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.stack: list[tuple[str, bool]] = []
        self.hidden_depth = 0
        self.in_title = 0
        self.forms = 0
        self.iframes = 0
        self.scripts = 0
        self.hidden_nodes = 0
        self.meta_refresh = 0

    @staticmethod
    def _hidden(attrs: list[tuple[str, str | None]]) -> bool:
        values = {str(key).lower(): (value or "") for key, value in attrs}
        style = values.get("style", "").replace(" ", "").lower()
        return (
            "hidden" in values
            or values.get("aria-hidden", "").lower() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        hidden_here = tag in _IGNORED_TAGS or self._hidden(attrs)
        if hidden_here:
            self.hidden_depth += 1
            if tag == "script":
                self.scripts += 1
            elif tag not in _IGNORED_TAGS:
                self.hidden_nodes += 1
        self.stack.append((tag, hidden_here))
        if tag == "title":
            self.in_title += 1
        if tag == "form":
            self.forms += 1
        if tag == "iframe":
            self.iframes += 1
        if tag == "meta":
            values = {str(key).lower(): (value or "") for key, value in attrs}
            if values.get("http-equiv", "").lower() == "refresh":
                self.meta_refresh += 1
        if tag in _BLOCK_TAGS and self.hidden_depth == 0:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title" and self.in_title:
            self.in_title -= 1
        popped: list[tuple[str, bool]] = []
        while self.stack:
            item = self.stack.pop()
            popped.append(item)
            if item[0] == tag:
                break
        for _, hidden_here in popped:
            if hidden_here and self.hidden_depth:
                self.hidden_depth -= 1
        if tag in _BLOCK_TAGS and self.hidden_depth == 0:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.in_title and self.hidden_depth == 0:
            self.title_parts.append(data)
        if self.hidden_depth == 0:
            self.parts.append(data)


def _normalize_readable_text(text: str) -> str:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        clean = re.sub(r"[\t\f\v ]+", " ", raw).strip()
        if clean:
            lines.append(clean)
        elif lines and lines[-1] != "":
            lines.append("")
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def detect_remote_risk_signals(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for name, pattern in _PROMPT_SIGNAL_PATTERNS:
        if pattern.search(text):
            found.append(name)
    return tuple(found)


def extract_readable(fetch: FetchResult, *, max_chars: int = 200_000) -> ExtractionResult:
    if max_chars < 1000 or max_chars > 2_000_000:
        raise ValueError("max_chars must be between 1000 and 2000000")
    media_type = fetch.media_type.lower()
    warnings: list[str] = []
    title = ""
    text = ""
    method = "binary.inert.v0.1"

    if media_type in {"text/html", "application/xhtml+xml"}:
        charset = fetch.charset or "utf-8"
        try:
            decoded = fetch.body.decode(charset, errors="replace")
        except LookupError:
            decoded = fetch.body.decode("utf-8", errors="replace")
            warnings.append("declared_charset_unknown_used_utf8")
        parser = _ReadableHTMLParser()
        try:
            parser.feed(decoded)
            parser.close()
        except Exception:
            warnings.append("html_parser_recovered_from_malformed_markup")
        text = _normalize_readable_text("".join(parser.parts))
        title = re.sub(r"\s+", " ", " ".join(parser.title_parts)).strip()[:500]
        method = "html.parser.visible_text.v0.1"
        warnings.append("readability_is_simple_visible_text_not_semantic_article_extraction")
        if parser.forms:
            warnings.append("page_contains_forms_but_library_window_does_not_submit_them")
        if parser.iframes:
            warnings.append("page_contains_iframes_not_loaded_by_library_window")
        if parser.scripts:
            warnings.append("page_contains_scripts_not_executed_by_library_window")
        if parser.hidden_nodes:
            warnings.append("hidden_html_content_was_not_included_in_readable_text")
        if parser.meta_refresh:
            warnings.append("meta_refresh_present_but_not_followed")
    elif media_type.startswith("text/") or media_type in {
        "application/json",
        "application/ld+json",
        "application/xml",
        "application/rss+xml",
        "application/atom+xml",
    }:
        charset = fetch.charset or "utf-8"
        try:
            text = fetch.body.decode(charset, errors="replace")
        except LookupError:
            text = fetch.body.decode("utf-8", errors="replace")
            warnings.append("declared_charset_unknown_used_utf8")
        text = _normalize_readable_text(text)
        method = "text.decode.v0.1"
    elif media_type == "application/pdf":
        warnings.append("pdf_preserved_as_inert_source_but_text_extraction_is_not_in_v0.1")
    elif media_type.startswith("image/"):
        warnings.append("image_preserved_as_inert_source_but_pixel_inspection_is_not_in_library_window_v0.1")
    else:
        warnings.append("binary_source_preserved_inert_without_readable_text_extraction")

    if len(text) > max_chars:
        text = text[:max_chars]
        warnings.append("readable_text_truncated_at_configured_character_ceiling")
    signals = detect_remote_risk_signals(text)
    if signals:
        warnings.append("remote_text_contains_advisory_manipulation_signals")
    return ExtractionResult(
        title=title,
        text=text,
        method=method,
        warnings=tuple(dict.fromkeys(warnings)),
        risk_signals=signals,
    )


class _DuckSearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {str(key).lower(): (value or "") for key, value in attrs}
        classes = set(values.get("class", "").split())
        if tag.lower() == "a" and "result__a" in classes:
            self._capture = "title"
            self._buffer = []
            self._href = values.get("href", "")
        elif "result__snippet" in classes and self.results:
            self._capture = "snippet"
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if self._capture == "title" and tag.lower() == "a":
            title = re.sub(r"\s+", " ", " ".join(self._buffer)).strip()
            if title and self._href:
                self.results.append({"title": title[:500], "url": self._href, "snippet": ""})
            self._capture = None
            self._buffer = []
            self._href = ""
        elif self._capture == "snippet" and tag.lower() in {"a", "div", "span"}:
            snippet = re.sub(r"\s+", " ", " ".join(self._buffer)).strip()
            if snippet and self.results:
                self.results[-1]["snippet"] = snippet[:1200]
            self._capture = None
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)


def _unwrap_search_url(raw: str) -> str | None:
    candidate = str(raw or "").strip()
    if not candidate:
        return None
    absolute = urljoin(SEARCH_ENDPOINT, candidate)
    parsed = urlsplit(absolute)
    if parsed.hostname and parsed.hostname.lower().endswith("duckduckgo.com"):
        query = parse_qs(parsed.query)
        if query.get("uddg"):
            absolute = str(query["uddg"][0])
    try:
        # Do not resolve every search result here. Resolution happens when it is opened.
        return validate_remote_url(absolute, allow_http=False, resolve=False)
    except (ValueError, PermissionError):
        return None


def parse_duckduckgo_results(html: str, *, limit: int) -> list[dict[str, Any]]:
    parser = _DuckSearchParser()
    parser.feed(html)
    parser.close()
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in parser.results:
        url = _unwrap_search_url(item.get("url", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        output.append(
            {
                "rank": len(output) + 1,
                "title": item.get("title", "")[:500],
                "url": url,
                "snippet": item.get("snippet", "")[:1200],
                "provenance_class": "search_snippet",
                "direct_source_read": False,
            }
        )
        if len(output) >= limit:
            break
    return output


def search_web(
    query: str,
    *,
    limit: int = 6,
    timeout_seconds: int = 12,
    max_bytes: int = 1_500_000,
    user_agent: str = _DEFAULT_USER_AGENT,
) -> tuple[FetchResult, list[dict[str, Any]]]:
    clean = str(query or "").strip()
    if not clean or len(clean) > 500:
        raise ValueError("web search query must be between 1 and 500 characters")
    limit = max(1, min(int(limit), 10))
    url = SEARCH_ENDPOINT + "?" + urlencode({"q": clean})
    fetched = fetch_bytes(
        url,
        allow_http=False,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
        max_redirects=3,
        user_agent=user_agent,
    )
    if fetched.media_type not in {"text/html", "application/xhtml+xml"}:
        raise RemoteAccessError("search provider returned an unexpected media type")
    charset = fetched.charset or "utf-8"
    try:
        html = fetched.body.decode(charset, errors="replace")
    except LookupError:
        html = fetched.body.decode("utf-8", errors="replace")
    results = parse_duckduckgo_results(html, limit=limit)
    if not results:
        raise RemoteAccessError("search provider returned no parseable results")
    return fetched, results
