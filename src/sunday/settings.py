r"""What the settings screen reads and writes.

Everything in `config.Config` is editable, which is the point: this is shipped
to somebody who has to make it work on their own machine, with their own
folders, their own microphone and their own credentials, and a setting they
cannot reach is a setting that is wrong for them forever.

The window renders a form it does not know. The schema below is the only
description of what a setting is called, what it means and what editing it
costs, and `describe()` sends it down the socket with the values attached. The
alternative -- a form written out in Svelte against a dataclass written out in
Python -- is the same two-ends problem `tests/test_shell.py` exists for, except
that here the two ends would drift silently rather than loudly: a renamed field
would render as an empty box, and a new one would simply never appear.

So `test_settings.py` walks `config.Config` and requires every field to be
either in this schema or in `HIDDEN`, with a reason. Adding a config field and
forgetting the screen is a failing test rather than a setting nobody can find.

Two things here are not `config.Config` fields at all and are handled beside
them because a user does not care about the distinction:

  * the credentials, which live in their own file and never come back up the
    socket as values -- only as "set" and the last four characters,
  * the long-term memory store, which is a thing to look at the size of and
    occasionally to empty.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any

from sunday import config

# -- what the window is told -----------------------------------------------

#: Section order, title and the sentence under it. Order is deliberate: the
#: things a first run has to touch come first, and the measured numbers a
#: stranger has no business changing come last.
SECTIONS: list[tuple[str, str, str]] = [
    (
        "assistant",
        "Name",
        "What it calls itself, in its own replies and in the transcript.",
    ),
    (
        "files",
        "Folders",
        "The only folders it may read, write, move or delete inside. Anything "
        "outside them is refused before the disk is touched. The name is what "
        "you will actually say -- “put that in the work folder”.",
    ),
    (
        "prompts",
        "Character",
        "The instructions it is given before every turn. Leave it empty to use "
        "the built-in one.",
    ),
    (
        "models",
        "The model",
        "Which local model answers, and how much of it fits on your graphics "
        "card. These take effect when the assistant restarts.",
    ),
    (
        "external",
        "The web",
        "The one door out of this machine. What crosses it is composed "
        "separately, from your question and public results only.",
    ),
    (
        "audio",
        "Microphone",
        "Voice in. The numbers marked as measured belong to your hardware "
        "rather than to this program, and the measuring tool runs from a "
        "source checkout: python -m sunday.audio.check. Without it, the "
        "defaults are somebody else's room -- they usually work, and when "
        "they do not, this is where.",
    ),
    ("wake", "Wake word", "The phrase that opens the microphone."),
    ("tts", "Voice out", "How replies are spoken."),
    (
        "echo",
        "Hearing itself",
        "The three layers that stop it answering its own voice, and let you "
        "talk over it.",
    ),
    (
        "memory",
        "Memory",
        "How much of the conversation is carried, and how close a past turn "
        "has to be before it is recalled.",
    ),
    (
        "guardrail",
        "Credentials",
        "Reading any of these paths shuts the web door for the rest of the "
        "turn. Anything shaped like these prefixes is removed before the model "
        "sees it.",
    ),
    ("limits", "Limits", "How much work one turn may do."),
    ("ui", "Window", "The window itself."),
]

#: Config fields the screen deliberately does not show, and why. A field is
#: either here or in `HELP`; the test refuses a third option.
HIDDEN: dict[str, str] = {
    "source": "which config file was loaded, not a setting",
}

#: Fields whose value came from a measurement on real hardware rather than
#: from taste. The screen says so, and says what was measured, because every
#: one of these has already cost this project real behaviour once.
MEASURED: dict[str, str] = {
    "models.context_tokens": (
        "Measured: 23552 is the largest window that keeps every layer on a "
        "6 GB card. One step up and 15% of the model moves to the processor "
        "and nothing but `ollama ps` says so. Your card is not this card -- "
        "raise it and check."
    ),
    "models.overhead_tokens": (
        "Measured: what no memory slice pays for -- the bound tool schemas, "
        "the system prompt and the framing. Set it too low and the window "
        "overruns, and Ollama answers by silently dropping the oldest "
        "messages."
    ),
    "memory.distance_cutoff": (
        "Measured against 830 real turns: relevant answers came back at 0.13 "
        "to 0.60, unrelated ones at 0.72 to 0.90, and nothing landed between. "
        "It was 0.45 once, which threw away answers the store had."
    ),
    "memory.slice_summary": "Sized to fit the window without being scaled down.",
    "memory.slice_recent": "Sized to fit the window without being scaled down.",
    "memory.slice_retrieved": "Sized to fit the window without being scaled down.",
    "memory.slice_tools": "Sized to fit the window without being scaled down.",
    "wake.threshold": (
        "Measured: the phrase peaks around 0.49 on the microphone this was "
        "built on and everything that is not the phrase peaks at 0.0002. It "
        "is a property of your voice and your microphone, so measure it: "
        "python -m sunday.audio.check"
    ),
    "audio.aec_delay_ms": (
        "How long your sound card takes to play what it was handed. Measure "
        "it once: python -m sunday.audio.check --echo"
    ),
    "echo.barge_in_ratio": (
        "How loud the residual has to be, against what was played, to be a "
        "person. Measure it: python -m sunday.audio.check --echo"
    ),
    "echo.tail_ms": (
        "Still counts as speaking for this long after the last block reaches "
        "the sound card -- there is a buffer to play and then a room still "
        "ringing."
    ),
}

#: Settings the running process cannot pick up, and why the screen offers a
#: restart instead of pretending.
#:
#: Kept short on purpose. Everything else is read fresh each turn from
#: `config.get()`, and the ear is closed and reopened when an audio setting
#: moves, so the honest list is this one: the model client is built once.
#:
#: `[ui]` is not on it, and was. `autostart` and `start_minimised` are read by
#: the Rust half, and "Restart now" restarts the *sidecar* -- so the button
#: offered for them changed neither. The shell re-reads `[ui]` after every
#: save instead: the Run key and the frame rates are applied on the spot, and
#: `start_minimised` means something only at a launch, which is when it is
#: read. A restart that cannot apply a setting is a button that lies.
RESTART_REQUIRED: set[str] = {
    "models.agent",
    "models.ollama_url",
    "models.context_tokens",
    "models.thinking_budget",
    "models.summariser",
    "models.stt",
    "models.overhead_tokens",
    "models.reply_tokens",
}

#: One line per field, in the second person. This is the whole of the screen's
#: copy; there is nowhere else it is written down.
HELP: dict[str, tuple[str, str]] = {
    "assistant.name": (
        "Name",
        "What it calls itself. The wake word should plausibly be the same "
        "word -- change both together.",
    ),
    "prompts.system": (
        "System prompt",
        "Replaces the built-in one entirely. Write {name} where its name "
        "should go. Empty means the built-in.",
    ),
    "models.agent": ("Model", "An Ollama model tag. It is pulled from your own Ollama."),
    "models.ollama_url": ("Ollama address", "Where Ollama is listening."),
    "models.context_tokens": ("Window", "How many tokens the model is given."),
    "models.thinking_budget": ("Thinking", "Reserved for the model's own reasoning."),
    "models.summariser": (
        "Web summariser",
        "The one model that is not local. It only ever sees a cleared query "
        "and text that was already public, and only if a DeepSeek key is set.",
    ),
    "models.stt": ("Transcriber", "Which speech model turns your voice into text."),
    "models.overhead_tokens": ("Overhead", "Taken off the top before memory is sized."),
    "models.reply_tokens": ("Reply", "Room kept for the answer itself."),
    "audio.sample_rate": ("Sample rate", "16000 unless you know otherwise."),
    "audio.input_device": ("Input device", "Empty means the system default."),
    "audio.output_device": ("Output device", "Empty means the system default."),
    "audio.aec_delay_ms": ("Echo delay", "Your sound card's playback delay."),
    "audio.vad_threshold": ("Speech threshold", "How sure it must be that a sound is speech."),
    "audio.vad_silence_ms": ("End of speech", "Silence this long ends a clip."),
    "audio.min_clip_ms": ("Shortest clip", "Of speech, not of clip. Below this is a cough."),
    "audio.max_clip_ms": ("Longest clip", "A hard stop, so a stuck microphone ends."),
    "audio.preroll_ms": ("Pre-roll", "Kept from before the wake word finished."),
    "audio.listen_on_start": (
        "Open the microphone at startup",
        "Off by default, and worth understanding before you turn it on: with "
        "it on, speech anywhere in the room can start a turn without anybody "
        "clicking anything.",
    ),
    "audio.lead_in_ms": ("Wait for the question", "Woke, heard nothing, go back to sleep."),
    "wake.enabled": ("Wake word", "Off means every question needs a click."),
    "wake.model": ("Phrase", "alexa, hey_jarvis and hey_mycroft ship ready to use."),
    "wake.threshold": ("Sensitivity", "Lower catches more, and more of what is not you."),
    "wake.cooldown_ms": ("Cooldown", "How long before the phrase can fire again."),
    "wake.follow_up_ms": (
        "Follow-up window",
        "After a reply, speech alone opens the next question for this long. "
        "0 turns it off and every question needs the phrase.",
    ),
    "tts.enabled": ("Speak replies", "Off means replies are only written."),
    "tts.voice": ("Voice", "54 ship in one file: python -m sunday.audio.check --voices"),
    "tts.speed": ("Speed", "1.0 is the natural rate."),
    "tts.language": ("Language", "The voice's own language tag."),
    "echo.speech_threshold_while_speaking": (
        "Threshold while speaking",
        "The bar a sound has to clear while a reply is playing.",
    ),
    "echo.transcript_similarity_cutoff": (
        "Heard itself",
        "How much a transcript may resemble what was just said before it is "
        "thrown away as its own voice.",
    ),
    "echo.barge_in_ms": ("Interrupt after", "Sustained speech this long stops the reply."),
    "echo.tail_ms": ("Speaking tail", "Still counts as speaking after the last block."),
    "echo.barge_in_ratio": ("Interrupt loudness", "How loud you must be to talk over it."),
    "files.roots": (
        "Folders",
        "Each one needs a path. The name is what you will say out loud; leave "
        "it empty to use the folder's own name.",
    ),
    "files.max_read_bytes": ("Largest file", "Bytes it will read from one file."),
    "memory.distance_cutoff": ("Recall cutoff", "0 is identical, 1 is unrelated."),
    "memory.top_k": ("Recall count", "How many past turns to consider."),
    "memory.idle_minutes": ("Session gap", "Quiet this long and the session is folded up."),
    "memory.slice_summary": ("Summary slice", "Tokens for the running summary."),
    "memory.slice_recent": ("Recent slice", "Tokens for this session's own turns."),
    "memory.slice_retrieved": ("Recall slice", "Tokens for what came back from the store."),
    "memory.slice_tools": ("Tool slice", "Tokens for what tools returned."),
    "external.enabled": (
        "Allow web lookups",
        "Off means nothing at all leaves this machine.",
    ),
    "external.search": ("Search engine", "Which search backend to use."),
    "external.max_hops": ("Lookups per turn", "How many searches one turn may make."),
    "external.fetch_timeout_s": ("Fetch timeout", "Seconds to wait for a page."),
    "external.fetch_max_bytes": ("Largest page", "Bytes to download from one page."),
    "external.extract_max_chars": ("Extract limit", "Characters kept from a page."),
    "external.query_max_chars": ("Query limit", "Characters an outgoing query may be."),
    "guardrail.secret_paths": (
        "Credential paths",
        "One per line. Reading a file that matches shuts the web door for the "
        "rest of the turn. Removing one removes that protection.",
    ),
    "guardrail.key_shapes": (
        "Credential prefixes",
        "One per line. Anything starting with one of these is removed before "
        "the model sees it.",
    ),
    "limits.tool_calls": ("Tool calls per turn", "The only bound on how long a turn runs."),
    "ui.fps_focused": ("Frame rate, focused", "How often the orb redraws."),
    "ui.fps_blurred": ("Frame rate, blurred", "When the window is not in front."),
    "ui.start_minimised": (
        "Start hidden",
        "Launch to the tray rather than to a window. Read at launch, so it "
        "shows the next time it starts.",
    ),
    "ui.autostart": ("Start with Windows", "Adds it to the Run key. Off by default."),
    "ui.trace": ("Backstage", "The panel that says what each step actually did."),
}

#: Which editor the window puts on a field, keyed off the dataclass default's
#: type with two overrides. Nothing here needs the window to know a field name.
_LONG = {"prompts.system"}
_LINES = {"guardrail.secret_paths", "guardrail.key_shapes"}


def _kind(key: str, value: Any) -> str:
    if key in _LONG:
        return "long"
    if key in _LINES:
        return "lines"
    if key == "files.roots":
        return "roots"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        return "lines"
    return "text"


@dataclass
class Field:
    key: str
    label: str
    help: str
    kind: str
    value: Any
    default: Any
    measured: str = ""
    restart: bool = False

    def as_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "help": self.help,
            "kind": self.kind,
            "value": self.value,
            "default": self.default,
            "measured": self.measured,
            "restart": self.restart,
        }


def schema(cfg: config.Config | None = None) -> list[dict[str, Any]]:
    """Every section, every field, with the value in use and the default.

    Walked off the dataclasses rather than listed, so a config field that
    exists is a field that appears. What is *not* walked off them is the copy
    -- a label and a sentence per field, which no amount of introspection will
    produce and which is the whole reason this file is long.
    """
    cfg = cfg or config.get()
    blank = config.Config()
    out: list[dict[str, Any]] = []

    for name, title, blurb in SECTIONS:
        section = getattr(cfg, name)
        fallback = getattr(blank, name)
        rendered: list[dict[str, Any]] = []
        for spec in fields(section):
            key = f"{name}.{spec.name}"
            label, note = HELP.get(key, (spec.name.replace("_", " "), ""))
            value = getattr(section, spec.name)
            default = getattr(fallback, spec.name)
            if key == "files.roots":
                value, default = _roots_for_window(value), _roots_for_window(default)
            rendered.append(
                Field(
                    key=key,
                    label=label,
                    help=note,
                    kind=_kind(key, default),
                    value=value,
                    default=default,
                    measured=MEASURED.get(key, ""),
                    restart=key in RESTART_REQUIRED,
                ).as_json()
            )
        out.append({"key": name, "title": title, "blurb": blurb, "fields": rendered})
    return out


def _roots_for_window(roots: list) -> list[dict[str, str]]:
    """Every root as `{label, path}`, whichever way it was written.

    A bare string is a valid root -- it takes the folder's own name -- and the
    editor reads `.label` and `.path` off each entry. Sent as written, a string
    has neither, so it rendered as an empty row, and the next save that touched
    the list dropped it as a row with no path: a folder removed from the config
    by editing a different one. The label stays empty rather than being filled
    in with the derived name, so the screen shows what the file says.
    """
    out = []
    for raw in roots or []:
        if isinstance(raw, dict):
            out.append({
                "label": str(raw.get("label") or ""),
                "path": str(raw.get("path") or ""),
            })
        else:
            out.append({"label": "", "path": str(raw)})
    return out


def credentials() -> list[dict[str, Any]]:
    """The credentials, as presence and a masked tail. Never as values.

    The window is a WebView. It does not read files, by design, and it does not
    receive secrets either -- the same rule one layer up. What it needs in
    order to render honestly is whether a key is set, roughly which one it is,
    and where the value in use came from, because a development machine has a
    `.env` and the screen must not appear to be editing that.
    """
    live = {
        "DEEPSEEK_API_KEY": config.DEEPSEEK_API_KEY,
        "DEEPSEEK_BASE_URL": config.DEEPSEEK_BASE_URL,
        "GOOGLE_CLIENT_ID": config.GOOGLE_CLIENT_ID,
        "GOOGLE_CLIENT_SECRET": config.GOOGLE_CLIENT_SECRET,
    }
    labels = {
        "DEEPSEEK_API_KEY": "DeepSeek API key",
        "DEEPSEEK_BASE_URL": "DeepSeek address",
        "GOOGLE_CLIENT_ID": "Google client id",
        "GOOGLE_CLIENT_SECRET": "Google client secret",
    }
    out = []
    for key in config.CREDENTIAL_KEYS:
        value = live[key]
        # Two of these are not secrets, and masking a thing that is not a
        # secret costs something real: `····.com` is what the last four
        # characters of a Google client id are, which tells the user nothing
        # about which id is in there -- the opposite of what a hint is for.
        #
        # The base URL is an address. The Google *client id* is public by
        # construction in an installed-application flow: it travels in the
        # authorisation URL, which is shown in the address bar of the browser
        # this app opens. The client secret is not, and is masked; so is the
        # DeepSeek key.
        secret = key not in {"DEEPSEEK_BASE_URL", "GOOGLE_CLIENT_ID"}
        out.append({
            "key": key,
            "label": labels[key],
            "secret": secret,
            "set": bool(value),
            "hint": config.mask(value) if secret else value,
            "source": config.credential_source(key),
            "cost": config.CREDENTIAL_COSTS.get(key, ""),
        })
    return out


def _store_bytes() -> int:
    try:
        return sum(f.stat().st_size for f in config.MEMORY_DIR.rglob("*") if f.is_file())
    except OSError:
        return 0


def describe(
    cfg: config.Config | None = None,
    *,
    remembered: int | None = None,
) -> dict[str, Any]:
    """The whole payload the settings screen renders from."""
    from sunday.agent import prompts
    from sunday.memory import budget

    cfg = cfg or config.get()
    written = (cfg.prompts.system or "").strip()
    return {
        "sections": schema(cfg),
        "credentials": credentials(),
        "prompt": {
            # What the built-in actually is, so "reset" can show what it is
            # resetting to rather than emptying a box and hoping.
            "builtin": prompts.SYSTEM,
            "builtin_tokens": budget.count(prompts.SYSTEM),
            "tokens": budget.count(written or prompts.SYSTEM),
            "overhead": cfg.models.overhead_tokens,
        },
        "memory": {
            "path": str(config.MEMORY_DIR),
            "bytes": _store_bytes(),
            "remembered": remembered,
        },
        "paths": {
            "config": str(target_path()),
            "credentials": str(config.CREDENTIALS_PATH),
            "home": str(config.SUNDAY_HOME),
        },
    }


# -- writing it back --------------------------------------------------------


def target_path() -> Path:
    """The file a save is written to.

    Whichever file `config.load()` would read, so that saving and loading
    cannot disagree. On a development checkout that is the repository root's
    `config.toml`; on an installed copy there is no repository, and it is the
    one beside the memory store.
    """
    return config.config_path() or (config.SUNDAY_HOME / "config.toml")


HEADER = """# Written by the settings screen.
#
# Hand edits survive -- this is read as ordinary TOML -- but comments do not:
# the next save rewrites the whole file from the values in it. The commented
# template is config.example.toml, which is never overwritten.
"""


def _dump(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        # A TOML basic string and a JSON string escape the same characters, so
        # this is a valid one -- including for the multi-line system prompt,
        # whose newlines become \\n rather than a triple-quoted block that
        # would then be sensitive to its own indentation.
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        inner = ", ".join(f"{k} = {_dump(v)}" for k, v in value.items())
        return "{ " + inner + " }"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        return "[\n" + "".join(f"  {_dump(v)},\n" for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def emit(cfg: config.Config) -> str:
    """The whole config as TOML.

    Everything, not only what differs from the default. A file that records
    only the changes is a file whose meaning moves when a default moves, and
    the defaults here are measured numbers that do move.
    """
    out = [HEADER]
    for spec in fields(cfg):
        section = getattr(cfg, spec.name)
        if not is_dataclass(section):
            continue  # `source` -- which file this came from, not a setting
        out.append(f"[{spec.name}]")
        for entry in fields(section):
            out.append(f"{entry.name} = {_dump(getattr(section, entry.name))}")
        out.append("")
    return "\n".join(out) + "\n"


class Invalid(Exception):
    """A value the screen sent that cannot be what it says it is."""


def _coerce(key: str, default: Any, value: Any) -> Any:
    """One incoming JSON value, as the type the dataclass field holds.

    Every failure names the field, because the window shows this string next
    to the box that caused it.
    """
    label = HELP.get(key, (key, ""))[0]
    if key == "files.roots":
        roots = []
        for raw in value or []:
            path = str((raw or {}).get("path", "")).strip()
            if not path:
                continue
            entry = {"label": str((raw or {}).get("label", "")).strip(), "path": path}
            if not entry["label"]:
                entry.pop("label")
            roots.append(entry)
        return roots
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            raise Invalid(f"{label} has to be a whole number") from None
    if isinstance(default, float):
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            raise Invalid(f"{label} has to be a number") from None
    if isinstance(default, list):
        if isinstance(value, str):
            return [line.strip() for line in value.splitlines() if line.strip()]
        return [str(v).strip() for v in (value or []) if str(v).strip()]
    return str(value if value is not None else "")


@dataclass
class Saved:
    """What happened, in the three parts the screen has to say separately."""

    #: Settings that were written but cannot take effect until a restart.
    restart: list[str]
    #: Things that are saved and worth knowing -- a folder that is not there.
    warnings: list[str]
    #: Whether an audio setting moved, so the ear has to be reopened.
    audio_changed: bool


def apply(
    values: dict[str, Any],
    credentials: dict[str, Any] | None = None,
) -> Saved:
    """Write the given `section.field` values, then reload the config.

    Every value is read as its type before anything is written, and it is read
    into a copy. The first draft set them on the live config one at a time, so
    a refusal half-way down the list left the running process holding every
    field before it: the screen said the save had failed, the file agreed, and
    the assistant was already answering to the new name. Credentials wait for
    the same check. They are a different file, and a save that is refused
    should write neither.

    A key that is not a known field is ignored rather than refused: the window
    and this file are two ends of one contract, and the failure mode worth
    choosing is the one where an old window loses a setting rather than the one
    where it cannot save at all.
    """
    live = config.get()
    before = {key: _current(live, key) for key in RESTART_REQUIRED}
    audio_before = _audio_snapshot(live)
    cfg = copy.deepcopy(live)
    warnings: list[str] = []

    for key, value in values.items():
        section_name, _, field_name = key.partition(".")
        section = getattr(cfg, section_name, None)
        if not is_dataclass(section):
            continue
        known = {f.name: f for f in fields(section)}
        if field_name not in known:
            continue
        default = getattr(config.Config(), section_name)
        setattr(
            section,
            field_name,
            _coerce(key, getattr(default, field_name), value),
        )

    for root in cfg.files.entries():
        if not Path(root.path).is_dir():
            warnings.append(
                f"{root.label}: {root.path} is not a folder that exists. It will "
                f"be refused until it does -- nothing here creates folders."
            )

    # Only now is anything written. Credentials first: they only ever came up
    # as what the user typed, and a key absent from them is left alone.
    if credentials:
        config.save_credentials(credentials)

    path = target_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(emit(cfg), encoding="utf-8")
    reloaded = config.reload(path)

    restart = sorted(
        HELP.get(key, (key, ""))[0]
        for key in RESTART_REQUIRED
        if _current(reloaded, key) != before[key]
    )
    return Saved(
        restart=restart,
        warnings=warnings,
        audio_changed=_audio_snapshot(reloaded) != audio_before,
    )


def _current(cfg: config.Config, key: str) -> Any:
    section_name, _, field_name = key.partition(".")
    return getattr(getattr(cfg, section_name), field_name, None)


def _audio_snapshot(cfg: config.Config) -> tuple:
    """Everything the ear reads when it is built.

    Compared rather than watched field by field: the ear takes the whole
    config at construction, so the honest question is whether any of the four
    sections it reads has moved, and the answer is a closed microphone
    reopened rather than a setting that appears to save and does nothing.
    """
    return (
        tuple(sorted(vars(cfg.audio).items())),
        tuple(sorted(vars(cfg.wake).items())),
        tuple(sorted(vars(cfg.tts).items())),
        tuple(sorted(vars(cfg.echo).items())),
    )
