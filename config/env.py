"""Tiny typed reader for environment variables.

Kept deliberately small (KISS): the project needs string/bool/list coercion and
a loud failure for missing secrets, nothing more.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env once, at import time, without clobbering real environment variables.
load_dotenv(_PROJECT_ROOT / ".env", override=False)

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        if required and default is None:
            raise ImproperlyConfigured(
                f"Environment variable {name!r} is required. "
                f"Copy .env.example to .env and fill it in."
            )
        return default if default is not None else ""
    return value


def env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSY:
        return False
    raise ImproperlyConfigured(f"Environment variable {name!r} must be a boolean, got {raw!r}.")


def env_list(name: str, *, default: list[str] | None = None, separator: str = ",") -> list[str]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return list(default or [])
    return [part.strip() for part in raw.split(separator) if part.strip()]
