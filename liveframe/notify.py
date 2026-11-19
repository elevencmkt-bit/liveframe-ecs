"""Hourly notice: "+N new photos" + gallery link.

V1 never sends anything by itself. It builds the client message and a wa.me
link already filled in; Levi opens it and presses send in WhatsApp
(official WhatsApp Business API is V3).
"""
from __future__ import annotations

import platform
import subprocess
import time
import urllib.parse
from dataclasses import dataclass

from .config import Event
from .state import Ledger


@dataclass
class Notice:
    count: int
    message: str
    wa_link: str


def build(event: Event, ledger: Ledger, now: float | None = None) -> Notice | None:
    since = ledger.last_notice_at()
    n = ledger.sent_since(since)
    if n == 0:
        return None
    msg = event.notice_template.format(n=n, event=event.name, link=event.gallery_link)
    q = urllib.parse.quote(msg)
    wa = f"https://wa.me/{event.client_whatsapp}?text={q}" if event.client_whatsapp \
        else f"https://wa.me/?text={q}"
    return Notice(n, msg, wa)


def _mac_notify(title: str, text: str) -> None:
    script = f'display notification {_q(text)} with title {_q(title)} sound name "Glass"'
    subprocess.run(["osascript", "-e", script], check=False)


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def run(event: Event, open_link: bool = False, now: float | None = None) -> Notice | None:
    ledger = Ledger(event.state_db)
    try:
        notice = build(event, ledger, now)
        if notice is None:
            return None
        ledger.record_notice(notice.count, notice.message, now)
    finally:
        ledger.close()
    log = event.root / "notices.log"
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M')}\t+{notice.count}\t{notice.wa_link}\n")
    if platform.system() == "Darwin":
        _mac_notify("LiveFrame ECS", f"+{notice.count} new photos — open WhatsApp to send")
        if open_link:
            # Opens WhatsApp with the message filled in; Levi still presses send.
            subprocess.run(["open", notice.wa_link], check=False)
    return notice
