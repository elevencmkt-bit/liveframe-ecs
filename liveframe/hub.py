"""Client gallery in the ECS Hub (V2): approved photos go straight to the gallery Levi sends the client.

Each Hub gallery has a LiveFrame key (Hub → gallery → LiveFrame → Create key). The app keeps
it in the Keychain under `hub:<event key ref>`; LIVEFRAME_HUB_KEY overrides it for the CLI.
The Hub skips a photo whose name is already in the gallery, so offline retries never duplicate.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import secrets
from .config import Event

TIMEOUT = 25


class HubError(RuntimeError):
    pass


def key_name(event: Event) -> str:
    return f"hub:{event.hub_key_ref or event.slug}"


def _key(event: Event) -> str:
    key = os.environ.get("LIVEFRAME_HUB_KEY") or secrets.get(key_name(event))
    if not key:
        raise HubError("no Hub gallery key for this event (New event → Hub gallery key)")
    return key


def _call(event: Event, method: str, path: str, body: bytes | None = None,
          headers: dict | None = None, key: str | None = None) -> dict:
    req = urllib.request.Request(event.hub_url.rstrip("/") + path, data=body, method=method,
                                 headers={"authorization": f"Bearer {key or _key(event)}",
                                          "user-agent": "LiveFrame-ECS", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read()).get("error", "")
        except Exception:
            msg = ""
        raise HubError(f"Hub {exc.code}: {msg or exc.reason}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise HubError(f"Hub unreachable: {exc}") from exc


def gallery_info(event: Event, key: str | None = None) -> dict:
    """{title, client, status, published, link} for the gallery this key opens."""
    return _call(event, "GET", "/api/liveframe/gallery", key=key)


def upload_photo(event: Event, file: Path) -> dict:
    return _call(event, "POST", "/api/liveframe/photo", body=file.read_bytes(),
                 headers={"content-type": "image/jpeg",
                          "x-file-name": urllib.parse.quote(file.name)})
