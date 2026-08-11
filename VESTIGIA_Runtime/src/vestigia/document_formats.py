from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable


HTML_TEXT_SUFFIXES = frozenset({".html", ".htm"})
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})


class _VisibleHTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        lowered = tag.lower()
        if self.skip_depth:
            if lowered in _SKIP_TAGS:
                self.skip_depth += 1
            return
        if lowered in _SKIP_TAGS:
            self.skip_depth = 1
            return
        if re.fullmatch(r"h[1-6]", lowered):
            self.parts.append("\n" + ("#" * int(lowered[1])) + " ")
        elif lowered in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if self.skip_depth:
            if lowered in _SKIP_TAGS:
                self.skip_depth -= 1
            return
        if re.fullmatch(r"h[1-6]", lowered) or lowered in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)


def html_to_visible_text(source: str) -> str:
    """Render visible HTML text as lightweight Markdown-ish house text.

    The original HTML file remains the source of record and keeps its original hash.
    Only the searchable/readable representation is normalized. Script, style,
    template, SVG, and noscript bodies are intentionally excluded from the text lane.
    """

    parser = _VisibleHTMLText()
    parser.feed(source)
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _install_reader(house_tools: Any) -> None:
    current: Callable[[Any, Path, str], str] = house_tools.HousePort._read_index_text
    if bool(getattr(current, "_vestigia_html_reader", False)):
        return

    def read_index_text(self: Any, path: Path, relative: str) -> str:
        text = current(self, path, relative)
        if path.suffix.lower() in HTML_TEXT_SUFFIXES:
            return html_to_visible_text(text)
        return text

    setattr(read_index_text, "_vestigia_html_reader", True)
    house_tools.HousePort._read_index_text = read_index_text


def register_composition() -> None:
    """Expose local HTML as bounded house text without widening the code lane.

    HousePort continues to own path, size, symlink, root, and read boundaries. This
    contribution adds HTML/HTM to that existing bounded text lane and replaces markup
    with visible text before indexing. JavaScript files remain excluded as source code
    and images continue through the image apparatus.
    """

    from . import house_tools

    house_tools.TEXT_SUFFIXES.update(HTML_TEXT_SUFFIXES)
    _install_reader(house_tools)
