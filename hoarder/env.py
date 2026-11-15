"""Minimal `.env` loader (stdlib only, no python-dotenv dependency)."""
from __future__ import annotations

import os
from pathlib import Path


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse `KEY=value` lines. Supports comments, blank lines, `export` prefixes and quoted values."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:  # inline comment on an unquoted value
            value = value.split(" #", 1)[0].rstrip()
        if key:
            out[key] = value
    return out


def load_dotenv(path: Path) -> list[str]:
    """Load `path` into os.environ without overriding variables already set (shell / CI wins).

    Empty values are ignored so a blank template line never masks a real variable. Returns the keys loaded.
    """
    if not path.is_file():
        return []
    loaded = []
    for key, value in parse_dotenv(path.read_text()).items():
        if value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
