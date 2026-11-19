"""Publishing backends for the live gallery (V1 = a shared Google Drive folder).

- folder: copy into a local folder synced by Google Drive for desktop.
          Drive for desktop keeps its own offline queue.
- rclone: `rclone copyto` to a Drive remote configured once on the Mac
          (`rclone config`); credentials stay in rclone's config, never here.
Any failure raises PublishError; the file stays in ready/ and is retried.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .config import Event


class PublishError(RuntimeError):
    pass


def publish(file: Path, event: Event) -> None:
    if event.publish_backend == "folder":
        _to_folder(file, Path(event.publish_target).expanduser())
    elif event.publish_backend == "rclone":
        _to_rclone(file, event.publish_target)
    else:  # pragma: no cover - validated in config
        raise PublishError(f"unknown backend {event.publish_backend}")


def _to_folder(file: Path, target: Path) -> None:
    if not target.is_dir():
        raise PublishError(f"gallery folder not available: {target}")
    dst = target / file.name
    tmp = target / ("." + file.name + ".part")
    try:
        shutil.copy2(file, tmp)
        os.replace(tmp, dst)  # atomic: the gallery never shows half a file
    except OSError as exc:
        raise PublishError(str(exc)) from exc


def _to_rclone(file: Path, remote: str) -> None:
    dst = remote.rstrip("/") + "/" + file.name
    try:
        r = subprocess.run(["rclone", "copyto", "--retries", "1", str(file), dst],
                           capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublishError(str(exc)) from exc
    if r.returncode != 0:
        raise PublishError(r.stderr.strip()[-400:] or f"rclone exit {r.returncode}")
