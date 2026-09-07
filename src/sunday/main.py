"""Terminal client.

The fastest way to test the core without the app in the way. It stays alive
for the whole build: the desktop shell is another client of the same runtime,
not a replacement for this one.
"""

from __future__ import annotations

import sys

from sunday import config
from sunday.agent.llm import OllamaDown
from sunday.runtime import ConfirmRequest, Runtime

GREY = "\033[90m"
TEAL = "\033[36m"
AMBER = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"
RESET = "\033[0m"

#: The backstage trace, one colour per act, so a wall of grey resolves into
#: five phases you can skim. Memory is blue because that is the half people
#: cannot otherwise see happening.
TRACE_COLOURS = {
    "wake": GREY,
    "memory_read": BLUE,
    "agent": GREY,
    "tools": AMBER,
    "compose_reply": GREY,
    "memory_write": BLUE,
    "done": GREY,
}


def _confirm(request: ConfirmRequest) -> bool:
    """Overwriting a file you already have, or deleting one at all.

    Anything that is not clearly a yes is a no, including a closed stdin: the
    default has to be the one that leaves your file alone.
    """
    print(f"\n{AMBER}  ? {request.question} [y/N] {RESET}", end="", flush=True)
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in {"y", "yes"}


def _print_event(event: dict) -> None:
    kind = event.get("type")
    if kind == "trace":
        colour = TRACE_COLOURS.get(str(event.get("step")), GREY)
        print(
            f"{colour}  | {event['heading']} · {event['text']}{RESET}",
            flush=True,
        )
    elif kind == "tool":
        colour = TEAL if event.get("scope") == "external" else AMBER
        print(f"{colour}  · {event['name']}{RESET}", flush=True)
    elif kind == "notice":
        print(f"{GREY}  · {event['text']}{RESET}", flush=True)
    elif kind == "error":
        print(f"{RED}  · {event['text']}{RESET}", flush=True)


def main() -> int:
    # Weather results carry a degree sign and the console is not UTF-8 by
    # default on Windows.
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
        print(f"{RED}{exc}{RESET}")
        return 1

    where = cfg.source or "built-in defaults"
    print(f"{GREY}Sunday · {cfg.models.agent} · config: {where}{RESET}")
    if config.trace_enabled(cfg):
        print(
            f"{GREY}Backstage trace is on -- the '|' lines are what is "
            f"happening, not what you asked. SUNDAY_TRACE=0 to hush it.{RESET}"
        )
    print(f"{GREY}Type to talk. Ctrl-C or 'exit' to quit.{RESET}\n")

    first_token = [True]

    def on_token(piece: str) -> None:
        if first_token[0]:
            print("sunday> ", end="", flush=True)
            first_token[0] = False
        print(piece, end="", flush=True)

    while True:
        try:
            # PowerShell puts a UTF-8 BOM on the first line it pipes to a
            # native exe, so a scripted `"exit" | sunday` would otherwise be
            # answered as a question instead of quitting.
            text = input("you> ").lstrip("﻿").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in {"exit", "quit"}:
            break

        first_token[0] = True
        # Ctrl-C during a turn is handled inside run_turn, which cancels, logs
        # and tears down. It comes back as an uncommitted state, not a raise.
        state = runtime.run_turn(
            text,
            on_token=on_token,
            on_event=_print_event,
            on_confirm=_confirm,
        )
        if not state.get("committed") and not state.get("final_response"):
            print(f"\n{GREY}  · cancelled{RESET}\n")
            continue

        if first_token[0] and state.get("final_response"):
            print(f"sunday> {state['final_response']}", end="")
        print("\n")

    # Quitting is a session boundary like going idle: flush the summary.
    runtime.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
