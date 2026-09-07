r"""Entry point for the sidecar process.

    .venv\Scripts\sunday-sidecar.exe

The Tauri shell spawns this and reads handshake.json to find it. Until that
shell exists, web/debug.html drives the same protocol from a browser.
"""

from __future__ import annotations

import asyncio
import sys

from sunday import config
from sunday.agent.llm import OllamaDown
from sunday.runtime import Runtime
from sunday.server import Sidecar


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass

    cfg = config.get()
    runtime = Runtime(cfg)
    try:
        runtime.preflight()
    except OllamaDown as exc:
        print(exc, file=sys.stderr)
        return 1

    sidecar = Sidecar(runtime)
    print(f"token: {sidecar.token}")
    try:
        asyncio.run(sidecar.serve())
    except KeyboardInterrupt:
        print("\nshutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
