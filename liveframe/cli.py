"""Command line: python3 -m liveframe <command> ...

  app                              the LiveFrame app (V2): local screen + camera FTP + live engine

  run      events/x.toml           live loop (Ctrl+C stops; restart any time)
  once     events/x.toml           a single cycle (useful for checks)
  status   events/x.toml           counters for the event
  approve  events/x.toml [names]   review mode: release photos in review/ (all if no names)
  notify   events/x.toml [--open]  hourly notice (run by launchd every hour)
  recap    events/x.toml --music track.m4a [--duration 45 --bpm 120]
  samples  folder/                 sample LUT / PNG / track for testing
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import notify, pipeline, recap, samples
from .config import load_event
from .state import Ledger


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="liveframe", description="LiveFrame ECS · V1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "once", "status"):
        p = sub.add_parser(name)
        p.add_argument("event")
        p.add_argument("--interval", type=float, default=2.0)
        p.add_argument("--settle", type=float, default=2.0,
                       help="seconds a file must be untouched before it is processed")
    p = sub.add_parser("approve")
    p.add_argument("event")
    p.add_argument("names", nargs="*", help="file names in review/; none = all of them")
    p = sub.add_parser("notify")
    p.add_argument("event")
    p.add_argument("--open", action="store_true", help="open WhatsApp with the text filled in")
    p = sub.add_parser("recap")
    p.add_argument("event")
    p.add_argument("--music")
    p.add_argument("--music-start", type=float, default=0.0)
    p.add_argument("--duration", type=float, default=45.0)
    p.add_argument("--bpm", type=float, default=120.0)
    p.add_argument("--beats-per-cut", type=int, default=4)
    p.add_argument("--out")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("samples")
    p.add_argument("folder")
    p = sub.add_parser("app", help="local screen at http://127.0.0.1:8711")
    p.add_argument("--port", type=int, default=8711)
    p.add_argument("--ftp-port", type=int)
    p.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")

    if a.cmd == "samples":
        for k, v in samples.make_all(Path(a.folder)).items():
            print(f"{k}: {v}")
        return 0

    if a.cmd == "app":
        from . import app
        app.serve(a.port, a.ftp_port, open_browser=not a.no_browser)
        return 0

    event = load_event(a.event)
    if a.cmd == "run":
        pipeline.run_forever(event, a.interval, a.settle)
    elif a.cmd == "once":
        s = pipeline.run_once(event, a.settle)
        print(json.dumps(s.__dict__, indent=2))
    elif a.cmd == "status":
        event.ensure_dirs()
        led = Ledger(event.state_db)
        out = {k: led.count(k) for k in ("review", "ready", "sent", "rejected",
                                         "skipped", "failed", "video")}
        out["incoming_waiting"] = sum(1 for p in event.incoming.rglob("*") if p.is_file())
        led.close()
        print(json.dumps({"event": event.name, **out}, indent=2))
    elif a.cmd == "approve":
        n = pipeline.approve(event, a.names)
        print(f"{n} photo(s) moved to approved/; the running loop publishes them")
    elif a.cmd == "notify":
        n = notify.run(event, open_link=a.open)
        print("no new photos since last notice" if n is None
              else f"+{n.count}\n{n.message}\n{n.wa_link}")
    elif a.cmd == "recap":
        out = Path(a.out) if a.out else event.root / "recap" / f"{event.slug}_recap.mp4"
        rep = recap.build(event.video, out, Path(a.music) if a.music else None, event.lut,
                          event.signature_portrait or event.signature, a.duration, a.bpm,
                          a.beats_per_cut, a.music_start, dry_run=a.dry_run)
        print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
