"""The script PyInstaller freezes: the sidecar, and nothing else.

`build_sidecar.py` points PyInstaller here rather than at `sunday/sidecar.py`
so the frozen program has an ordinary `__main__` to start from, and so
`freeze_support` runs before anything that might spawn a child process --
without it a frozen Windows program that uses `multiprocessing` starts itself
again instead of the child.
"""

import multiprocessing
import sys

from sunday.sidecar import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
