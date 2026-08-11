from __future__ import annotations


HTML_TEXT_SUFFIXES = frozenset({".html", ".htm"})


def register_composition() -> None:
    """Expose local HTML as bounded house text without widening the code lane.

    HousePort owns the actual path, size, symlink, root, and read boundaries.  This
    contribution only teaches that existing bounded text lane that HTML/HTM files are
    readable text documents.  JavaScript remains excluded as source code and images
    continue through the image apparatus.

    The change is intentionally idempotent because bootstrap may be exercised by tests
    and restored homes in the same Python process.
    """

    from . import house_tools

    house_tools.TEXT_SUFFIXES.update(HTML_TEXT_SUFFIXES)
