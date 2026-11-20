"""Entry point of LiveFrame.app (PyInstaller runs this file)."""
import multiprocessing
import sys

from liveframe.desktop import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
