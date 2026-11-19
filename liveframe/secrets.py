"""Where LiveFrame keeps secrets (Hub gallery keys, the camera's FTP password).

macOS: the login Keychain (`security`), service "LiveFrame ECS".
Elsewhere (tests, Linux): a 0600 JSON file in the LiveFrame home folder.
Never in the repo, an event file or the logs. Set LIVEFRAME_SECRETS=file to force the file store.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path

SERVICE = "LiveFrame ECS"


def home() -> Path:
    return Path(os.environ.get("LIVEFRAME_HOME", "~/LiveFrame")).expanduser()


def _keychain() -> bool:
    return platform.system() == "Darwin" and os.environ.get("LIVEFRAME_SECRETS") != "file"


def _file() -> Path:
    return home() / ".secrets.json"


def _read_file() -> dict:
    try:
        return json.loads(_file().read_text())
    except (FileNotFoundError, ValueError):
        return {}


def get(name: str) -> str | None:
    if _keychain():
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
                           capture_output=True, text=True)
        return r.stdout.strip() or None if r.returncode == 0 else None
    return _read_file().get(name)


def set(name: str, value: str) -> None:  # noqa: A001 - mirrors get()
    if _keychain():
        subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE, "-a", name, "-w", value],
                       check=True, capture_output=True)
        return
    data = _read_file()
    data[name] = value
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, f)


def has(name: str) -> bool:
    return bool(get(name))
