#!/usr/bin/env python3
import sys
import os

if hasattr(sys, '_MEIPASS'):
    print(f"_MEIPASS: {sys._MEIPASS}")
    td = os.path.join(sys._MEIPASS, "testdisk-7.3-WIP")
    if os.path.isdir(td):
        print(f"Found: {td}")
        print("Files:", os.listdir(td))
    else:
        print(f"Not found: {td}")
        # List what's there
        print("Contents:", os.listdir(sys._MEIPASS)[:20])
else:
    print("Not frozen")
