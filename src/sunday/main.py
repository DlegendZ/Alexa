"""Terminal client.

The fastest way to test the core without the app in the way. It stays alive
for the whole build: the desktop shell is another client of the same runtime,
not a replacement for this one.
"""

from __future__ import annotations

import queue
import sys
import threading

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


#: How long a confirmation waits for a typed answer. The same two minutes the
#: socket allows, and the same answer when it runs out: silence is not consent.
CONFIRM_TIMEOUT_S = 120


class Keyboard:
    """One thread on stdin, and two things that want the lines it produces.

    There must only ever be one reader. Typed questions arrive whenever you
    like, and a confirmation has to be answered in the middle of a turn -- so
    the obvious shape, `input()` in the turn loop and `input()` again inside the
    confirmation, is two threads racing for the same file descriptor. Whichever
    wins takes the line: answer `y` to an overwrite and the turn loop is as
    likely to receive it, treat it as your next question, and leave the
    confirmation waiting for something that already happened.

    So the thread reads, and *routes*. While a confirmation is outstanding the
    next line belongs to it; otherwise the line is a question.
    """

    def __init__(self, inbox: "queue.Queue[Heard | None]") -> None:
        self.inbox = inbox
        self._answers: queue.Queue[str] = queue.Queue()
        self._awaiting = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._read, name="sunday-stdin", daemon=True)
        self._thread.start()

    def _read(self) -> None:
        while True:
            try:
                # PowerShell puts a UTF-8 BOM on the first line it pipes to a
                # native exe, so a scripted `"exit" | sunday` would otherwise
                # be answered as a question instead of quitting.
                text = input().lstrip("\ufeff").strip()
            except (EOFError, KeyboardInterrupt):
                # Release anything waiting on an answer before leaving, or the
                # turn hangs until the timeout on a stdin that will never speak
                # again. An empty answer is a no, which is the safe one.
                self._answers.put("")
                self.inbox.put(None)
                return
            if self._awaiting.is_set():
                self._answers.put(text)
                continue
            if not text:
                continue
            if text.lower() in {"exit", "quit"}:
                self.inbox.put(None)
                return
            self.inbox.put(("text", text))

    def confirm(self, request: ConfirmRequest) -> bool:
        """Overwriting a file you already have, or deleting one at all.

        Anything that is not clearly a yes is a no, including a closed stdin
        and a question nobody answered: the default has to be the one that
        leaves your file alone.
        """
        print(f"\n{AMBER}  ? {request.question} [y/N] {RESET}", end="", flush=True)
        self._awaiting.set()
        try:
            answer = self._answers.get(timeout=CONFIRM_TIMEOUT_S)
        except queue.Empty:
            print()
            return False
        finally:
            self._awaiting.clear()
        return answer.strip().lower() in {"y", "yes"}


#: Where typed lines and spoken ones meet. The turn loop pulls from one place
#: and never learns which of the two it came from -- which is the point of the
#: adapter seam, made visible in twenty lines.
Heard = tuple[str, str]  # (modality, text)

PROMPT = "you> "


def _prompt() -> None:
    """Put the prompt back.

    `input()` prints its prompt once, on a thread that is then blocked for the
    rest of the turn, so everything the turn prints lands after it and the
    screen ends on a trace line. It reads as hung when it is in fact waiting.
    The prompt is written by whoever knows the app is idle instead, which is
    this loop.
    """
    print(PROMPT, end="", flush=True)


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


def _listen(
    cfg: config.Config,
    inbox: "queue.Queue[Heard | None]",
    *,
    on_barge_in=None,
) -> object | None:
    """Open the microphone, if this run asked for it and can have it.

    Returns the ear so it can be stopped, or None with the reason printed --
    a terminal that cannot hear is still a terminal that works.
    """
    from sunday.audio import models
    from sunday.audio.voice import Ear

    outstanding = models.missing(models.required(cfg))
    if outstanding:
        print(
            f"{AMBER}  · voice needs {len(outstanding)} model(s) downloaded first: "
            rf".venv\Scripts\python.exe -m sunday.audio.models{RESET}"
        )
        return None

    def on_event(event: dict) -> None:
        kind = event.get("type")
        if kind == "state" and event.get("value") == "listening":
            # A newline first: the cursor is sitting after the prompt, and a
            # spoken turn is the one case where nobody pressed Enter.
            print(f"\n{TEAL}  · listening{RESET}", flush=True)
        elif kind == "partial":
            print(f"{TEAL}you (spoken)> {event['text']}{RESET}", flush=True)
        elif kind == "notice":
            print(f"{GREY}  · {event['text']}{RESET}", flush=True)
        elif kind == "error":
            print(f"{RED}  · {event['text']}{RESET}", flush=True)

    ear = Ear(
        cfg,
        on_event=on_event,
        on_transcript=lambda t: inbox.put(("voice", t)),
        on_barge_in=on_barge_in,
    )
    ear.start()
    phrase = cfg.wake.model.replace("_", " ") if cfg.wake.enabled else "the mic"
    talks = "It talks back" if cfg.tts.enabled else "Voice out is off in config"
    print(f'{GREY}Voice is on. Say "{phrase}" and then ask. {talks}.{RESET}')
    return ear


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
    print(f"{GREY}{cfg.assistant.name} · {cfg.models.agent} · config: {where}{RESET}")
    if config.trace_enabled(cfg):
        print(
            f"{GREY}Backstage trace is on -- the '|' lines are what is "
            f"happening, not what you asked. SUNDAY_TRACE=0 to hush it.{RESET}"
        )
    print(f"{GREY}Type to talk. Ctrl-C or 'exit' to quit.{RESET}\n")

    inbox: "queue.Queue[Heard | None]" = queue.Queue()
    ear = (
        _listen(cfg, inbox, on_barge_in=runtime.cancel)
        if "--voice" in sys.argv[1:]
        else None
    )

    keyboard = Keyboard(inbox)
    keyboard.start()
    _prompt()

    first_token = [True]
    speaker = f"{cfg.assistant.name.lower()}> "

    def on_token(piece: str) -> None:
        if first_token[0]:
            print(speaker, end="", flush=True)
            first_token[0] = False
        print(piece, end="", flush=True)

    while True:
        try:
            heard = inbox.get()
        except KeyboardInterrupt:
            print()
            break
        if heard is None:
            break
        modality, text = heard

        first_token[0] = True
        # Ctrl-C during a turn is handled inside run_turn, which cancels, logs
        # and tears down. It comes back as an uncommitted state, not a raise.
        state = runtime.run_turn(
            text,
            modality=modality,
            on_token=on_token,
            on_sentence=ear.say if ear is not None else None,
            on_event=_print_event,
            on_confirm=keyboard.confirm,
        )
        if not state.get("committed") and not state.get("final_response"):
            print(f"\n{GREY}  · cancelled{RESET}\n")
            _prompt()
            continue

        if first_token[0] and state.get("final_response"):
            print(f"{speaker}{state['final_response']}", end="")
        print("\n")
        # The door stays open for a moment, so the next question needs no wake
        # word. The ear waits for the reply to finish being spoken first.
        if ear is not None:
            ear.follow_up()
        _prompt()

    # Quitting is a session boundary like going idle: flush the summary.
    if ear is not None:
        ear.stop()
    runtime.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
