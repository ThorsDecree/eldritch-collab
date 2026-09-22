from __future__ import annotations

from collections.abc import Mapping
import os


_BASE_ENV_KEYS = {
    "SYSTEMROOT",
    "WINDIR",
    "SYSTEMDRIVE",
    "COMSPEC",
    "PATH",
    "PATHEXT",
    "TEMP",
    "TMP",
}
_PYTHON_ENV_KEYS = {
    "PYTHONUTF8",
    "PYTHONIOENCODING",
}


def filtered_environment(
    profile: str,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the deliberately small child environment for a recipe.

    Environment names are matched case-insensitively. That matters on Windows:
    Python normalizes/returns environment names in ways that need not preserve
    the casing used by this allowlist, while Windows itself treats names such as
    SystemRoot case-insensitively. Dropping SYSTEMROOT can leave a child able to
    start Python but unable to initialize system providers such as Winsock.
    """
    allowed = set(_BASE_ENV_KEYS)
    if profile == "python":
        allowed |= _PYTHON_ENV_KEYS

    values = os.environ if source is None else source
    return {
        key: value
        for key, value in values.items()
        if key.upper() in allowed
    }
