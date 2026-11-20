"""LiveFrame.app: the Mac app around the LiveFrame screen.

Opening LiveFrame.app starts the camera FTP, the live engine and the screen, opens the screen
in the browser and shows a small control window (version, data folder, Open, Quit).
Closing the window keeps LiveFrame running (click the Dock icon to bring it back); Quit stops it.

Data lives outside the app, so updating LiveFrame.app never touches it:
    ~/Library/Application Support/LiveFrame/location.json   → which data folder to use
    <data folder>/  settings.json, events/, work/, assets/, inbox/, logs/
The data folder is chosen on first open (default: ~/Library/Application Support/LiveFrame).

    LiveFrame.app/Contents/MacOS/LiveFrame --selftest   checks the packaged app without a window
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path

from . import __version__

APP_NAME = "LiveFrame ECS"
SUPPORT = Path("~/Library/Application Support/LiveFrame").expanduser()
POINTER = SUPPORT / "location.json"
LEGACY_HOME = Path("~/LiveFrame").expanduser()   # where the terminal version (V2) kept its data
BG, PANEL, INK, SOFT, YELLOW = "#050a0e", "#11161c", "#f4f4f2", "#9aa0a8", "#f9bf24"

log = logging.getLogger("liveframe.desktop")


# data folder ------------------------------------------------------------------
def saved_home() -> Path | None:
    try:
        p = Path(json.loads(POINTER.read_text())["home"]).expanduser()
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return None
    return p


def save_home(home: Path) -> Path:
    home = home.expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    SUPPORT.mkdir(parents=True, exist_ok=True)
    tmp = POINTER.with_suffix(".tmp")
    tmp.write_text(json.dumps({"home": str(home)}, indent=2))
    os.replace(tmp, POINTER)
    return home


def choose_home(root) -> Path | None:
    """First open: ask where LiveFrame keeps events and settings. None = user cancelled."""
    from tkinter import filedialog, messagebox

    legacy = LEGACY_HOME / "settings.json"
    msg = ("Where should LiveFrame keep events, settings and photos?\n\n"
           f"Yes: the standard folder\n{SUPPORT}\n\n"
           "No: choose another folder (for example an external drive).\n\n"
           "This stays outside the app, so updates never erase it.")
    if legacy.exists():
        msg += f"\n\nYour earlier events are in {LEGACY_HOME}. Choose that folder to keep using them."
    answer = messagebox.askyesnocancel(APP_NAME, msg, parent=root)
    if answer is None:
        return None
    if answer:
        return save_home(SUPPORT)
    picked = filedialog.askdirectory(parent=root, title="Choose the LiveFrame data folder",
                                     initialdir=str(LEGACY_HOME if LEGACY_HOME.exists() else Path.home()),
                                     mustexist=False)
    return save_home(Path(picked)) if picked else choose_home(root)


def setup_logging(home: Path) -> Path:
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    f = logs / "liveframe.log"
    h = logging.handlers.RotatingFileHandler(f, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(h)
    return f


def reveal(path: Path) -> None:
    subprocess.run(["open", str(path)], check=False)


# window -------------------------------------------------------------------------
def run_window() -> int:
    import tkinter as tk
    from tkinter import messagebox

    from .app import Server

    root = tk.Tk()
    root.withdraw()
    root.title(APP_NAME)

    home = saved_home() or choose_home(root)
    if home is None:
        root.destroy()
        return 0
    home.mkdir(parents=True, exist_ok=True)
    os.environ["LIVEFRAME_HOME"] = str(home)
    log_file = setup_logging(home)
    log.info("LiveFrame ECS %s starting (data: %s)", __version__, home)

    try:
        srv = Server()
    except OSError as exc:
        log.exception("could not start")
        messagebox.showerror(APP_NAME, "LiveFrame could not start: port 8711 is in use.\n\n"
                             "Is LiveFrame already open (or running in a Terminal)? Quit it and try again.\n\n"
                             f"Details: {exc}", parent=root)
        root.destroy()
        return 1
    srv.start_background()

    # -- the control window
    root.configure(bg=BG, padx=24, pady=20)
    root.resizable(False, False)

    def label(text, size=13, color=INK, bold=False, **kw):
        w = tk.Label(root, text=text, bg=BG, fg=color, font=("Helvetica Neue", size, "bold" if bold else "normal"),
                     anchor="w", justify="left", **kw)
        w.pack(fill="x")
        return w

    title = tk.Frame(root, bg=BG)
    title.pack(fill="x")
    tk.Label(title, text="LIVEFRAME", bg=BG, fg=YELLOW, font=("Helvetica Neue", 20, "bold")).pack(side="left")
    tk.Label(title, text=" ECS", bg=BG, fg=INK, font=("Helvetica Neue", 20, "bold")).pack(side="left")
    label(f"Version {__version__}", 12, SOFT)
    tk.Frame(root, bg=BG, height=12).pack()
    status = label("● Running", 13, "#4ade80")
    label(srv.url, 12, SOFT)
    camera = label("", 12, SOFT)
    tk.Frame(root, bg=BG, height=8).pack()
    label("Data folder", 11, SOFT)
    label(str(home), 12, INK, wraplength=420)
    tk.Frame(root, bg=BG, height=14).pack()

    def btn(parent, text, cmd, primary=False):
        # macOS ignores bg on tk.Button, so buttons are labels that act on click
        w = tk.Label(parent, text=f"  {text}  ", bg=YELLOW if primary else PANEL, fg="#050a0e" if primary else INK,
                     font=("Helvetica Neue", 13, "bold" if primary else "normal"), padx=10, pady=7, cursor="hand2")
        w.bind("<Button-1>", lambda _e: cmd())
        w.pack(side="left", padx=(0, 8))
        return w

    row = tk.Frame(root, bg=BG)
    row.pack(fill="x")
    btn(row, "Open LiveFrame", lambda: webbrowser.open(srv.url), primary=True)
    btn(row, "Data folder", lambda: reveal(home))
    btn(row, "Log", lambda: reveal(log_file))
    tk.Frame(root, bg=BG, height=12).pack()
    label("Closing this window keeps LiveFrame running. Quit (⌘Q) stops the camera FTP.", 11, SOFT,
          wraplength=440)

    stopping = {"done": False}

    def quit_app(*_):
        if stopping["done"]:
            return
        st = srv.studio.state()
        live = st.get("active")
        if live and not messagebox.askokcancel(
                APP_NAME, f"The event \"{live}\" is live. Quit anyway?\n"
                          "The camera stops sending until you open LiveFrame again.", parent=root):
            return
        stopping["done"] = True
        status.config(text="● Stopping…", fg=YELLOW)
        root.update_idletasks()
        try:
            srv.stop()
        finally:
            root.destroy()

    def show(*_):
        root.deiconify()
        root.lift()

    def refresh():
        if stopping["done"]:
            return
        try:
            cam = srv.studio.state().get("camera") or {}
            camera.config(text="Camera connected" + (f" · {cam.get('address')}" if cam.get("address") else "")
                          if cam.get("connected") else ("Camera FTP off · use the cable" if cam.get("disabled")
                                                        else "Camera not connected"))
        except Exception:
            log.exception("status refresh failed")
        root.after(2000, refresh)

    root.protocol("WM_DELETE_WINDOW", root.withdraw)       # red button: hide, keep running
    root.createcommand("tk::mac::Quit", quit_app)           # ⌘Q and Dock → Quit
    root.createcommand("tk::mac::ReopenApplication", show)  # click on the Dock icon
    root.bind_all("<Command-q>", quit_app)

    root.after(600, lambda: webbrowser.open(srv.url))
    refresh()
    show()
    root.mainloop()
    if not stopping["done"]:
        srv.stop()
    return 0


# self-test (CI) -----------------------------------------------------------------
def selftest() -> int:
    """Exercise the packaged app end to end without a window: screen, FTP, treatment, recap."""
    import urllib.request

    from . import recap, samples, tools, treat
    from .app import Server
    from .config import Event

    os.environ["LIVEFRAME_SECRETS"] = "file"
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        os.environ["LIVEFRAME_HOME"] = str(tmp / "home")
        print(f"version {__version__}")
        ff = tools.ffmpeg()
        print(f"ffmpeg {ff}")
        if getattr(sys, "frozen", False):
            assert str(Path(sys.executable).resolve().parents[1]) in str(Path(ff).resolve()), \
                "the app must use its own bundled ffmpeg"

        srv = Server(port=0, ftp_port=0)
        srv.start_background()
        try:
            st = json.loads(urllib.request.urlopen(srv.url + "api/state", timeout=10).read())
            assert st["version"] == __version__, st
            assert st["home"] == str(tmp / "home"), st
            assert srv.ftp is not None, "camera FTP did not start"
            html = urllib.request.urlopen(srv.url, timeout=10).read()
            assert b"LiveFrame" in html
            print(f"screen ok, FTP on port {srv.ftp.port}")
        finally:
            srv.stop()

        lut = samples.write_cube(tmp / "w.cube")
        sig = samples.write_frame_png(tmp / "f.png", 1080, 1920)
        photo = samples.write_photo(tmp / "DSC00001.JPG")
        ev = Event(slug="selftest", name="Self test", root=tmp / "work", lut=lut, signature=sig)
        out = tmp / "treated.jpg"
        treat.treat_photo(photo, out, ev)
        assert out.stat().st_size > 10_000
        print("photo treatment ok")

        clips = tmp / "video"
        clips.mkdir()
        samples.write_clip(clips / "C0001.MP4", 6, "testsrc2")
        samples.write_clip(clips / "C0002.MP4", 6, "smptebars", portrait=True)
        music = samples.write_track(tmp / "t.m4a", 40)
        reel = tmp / "reel.mp4"
        rep = recap.build(clips, reel, music, lut, sig, duration=30, bpm=120)
        got = tools.media_duration(reel)
        assert got and 29 <= got <= 31, got
        print(f"recap ok ({got:.1f}s, {len(rep['segments'])} cuts)")

        import tkinter  # noqa: F401  (the window needs Tk inside the bundle)
        print("tk ok")
    print("SELFTEST OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--selftest" in argv:
        return selftest()
    if "--version" in argv:
        print(__version__)
        return 0
    return run_window()


if __name__ == "__main__":
    sys.exit(main())
