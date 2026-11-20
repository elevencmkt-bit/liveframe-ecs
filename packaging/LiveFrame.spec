# PyInstaller spec for LiveFrame.app (run from the repo root):
#     pyinstaller --noconfirm packaging/LiveFrame.spec
# Needs build/art/LiveFrame.icns (python packaging/make_art.py build/art).
import os
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

ROOT = Path(SPECPATH).parent
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "liveframe" / "__init__.py").read_text()).group(1)
ARCH = os.environ.get("LIVEFRAME_ARCH") or None   # None = the runner's own (arm64 on macos-14)

# imageio-ffmpeg ships one static ffmpeg in imageio_ffmpeg/binaries; keep it executable (binaries, not data)
import imageio_ffmpeg
ff = Path(imageio_ffmpeg.get_ffmpeg_exe())
ffmpeg_bin = [(str(ff), "imageio_ffmpeg/binaries")]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=ffmpeg_bin,
    datas=[(str(ROOT / "liveframe" / "web"), "liveframe/web")],
    hiddenimports=["liveframe.app", "liveframe.desktop", "pyftpdlib.handlers", "pyftpdlib.servers",
                   "pyftpdlib.authorizers", "tkinter", "tkinter.filedialog", "tkinter.messagebox"],
    excludes=["pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LiveFrame", console=False,
          target_arch=ARCH, codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, name="LiveFrame")
app = BUNDLE(
    coll,
    name="LiveFrame.app",
    icon=str(ROOT / "build" / "art" / "LiveFrame.icns"),
    bundle_identifier="com.elevencreativestudio.liveframe",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "LiveFrame",
        "CFBundleName": "LiveFrame",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "© Eleven Creative Studio",
        "NSLocalNetworkUsageDescription": "LiveFrame receives photos from the camera over the local Wi-Fi (FTP).",
        "NSAppleEventsUsageDescription": "LiveFrame shows notifications and opens WhatsApp for the hourly notice.",
    },
)
