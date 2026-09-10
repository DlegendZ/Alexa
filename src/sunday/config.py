"""Configuration: config.toml, plus the credentials the settings screen writes.

Search order for config.toml is repo root first (development), then
SUNDAY_HOME. Anything absent falls back to the defaults below, so the app
starts with no config file at all.

Credentials are a separate file for a separate reason. They are optional --
all of them, and the app is expected to run with none set -- and they belong
to whoever installed this copy rather than to whoever built it. They live at
CREDENTIALS_PATH, which `credentials*` on the guardrail's deny list already
covers, and `.env` remains a fallback so a development machine keeps working
without a second place to put the same key.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path, PurePath

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"

load_dotenv(ENV_PATH)


def _sunday_home() -> Path:
    override = os.getenv("SUNDAY_HOME")
    if override:
        return Path(override)
    local = os.getenv("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(local) / "Sunday"


SUNDAY_HOME = _sunday_home()
MEMORY_DIR = SUNDAY_HOME / "memory"
LOG_DIR = SUNDAY_HOME / "logs"
MODEL_DIR = SUNDAY_HOME / "models"
HANDSHAKE_PATH = SUNDAY_HOME / "handshake.json"
GOOGLE_TOKEN_PATH = SUNDAY_HOME / "google_token.json"

#: Where a credential typed into the settings screen is kept.
#:
#: Not `.env`, which is a developer's file at a repository root -- and a
#: shipped build has no repository root to put one in. This lands beside the
#: memory store and the Google token, in the one directory an installed copy
#: is certain to own.
#:
#: It needs no new deny rule: `credentials*` has been on `[guardrail]
#: secret_paths` since the list was written, so the agent reading this file
#: shuts the web door for the turn exactly as reading `.env` does.
CREDENTIALS_PATH = SUNDAY_HOME / "credentials.json"

#: The base URL to fall back to, named once so the settings screen can show
#: what "empty" is going to mean.
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"

#: Everything the settings screen may set, and nothing else. The file is read
#: through this list rather than merged wholesale, so a stray key in it cannot
#: become a module attribute.
CREDENTIAL_KEYS = (
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
)

#: Every one of these is optional, and the app is expected to run with all of
#: them empty. What each one costs when it is missing is stated here because
#: it is what the settings screen has to tell the user.
CREDENTIAL_COSTS = {
    "DEEPSEEK_API_KEY": (
        "Without it, web lookups still work -- the page text comes back "
        "unsummarised rather than not at all."
    ),
    "DEEPSEEK_BASE_URL": (
        "Where the summariser is called. Leave it empty for DeepSeek's own "
        "endpoint."
    ),
    "GOOGLE_CLIENT_ID": "Without it, calendar and mail are switched off.",
    "GOOGLE_CLIENT_SECRET": "Without it, calendar and mail are switched off.",
}

#: The DeepSeek key, used by `web.summarise` and by nothing else. Empty is a
#: supported state: the summariser degrades to the raw material.
DEEPSEEK_API_KEY = ""
DEEPSEEK_BASE_URL = DEEPSEEK_DEFAULT_BASE_URL
#: The Google desktop OAuth client, for calendar and mail. Read-only scopes,
#: and the refresh token it earns lands at GOOGLE_TOKEN_PATH -- which is on the
#: credential list, so the agent reading it shuts the web door for the turn.
GOOGLE_CLIENT_ID = ""
GOOGLE_CLIENT_SECRET = ""


def read_credentials() -> dict[str, str]:
    """What is in the credential file, as strings, with anything unreadable
    treated as nothing.

    A missing or corrupt file is an ordinary first run, not a fault: every
    value in it is optional, so there is nothing here worth failing over.
    """
    try:
        raw = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: str(raw.get(key) or "").strip()
        for key in CREDENTIAL_KEYS
        if str(raw.get(key) or "").strip()
    }


def credential_source(key: str) -> str:
    """Where the value in use came from: the settings file, the environment,
    or nowhere.

    Worth reporting rather than inferring. On a development machine `.env`
    holds a key and the settings file does not, and a settings screen that
    showed a value it is not the source of would be lying about which one it
    is about to overwrite.
    """
    if read_credentials().get(key):
        return "settings"
    if os.getenv(key, "").strip():
        return "environment"
    return ""


def refresh_credentials() -> None:
    """Re-read the credential file and the environment into the module names.

    The settings file wins. `.env` and a real environment variable are the
    fallback, which is the order that makes the settings screen honest: a key
    typed into it is the key that runs, on a machine that happens to have a
    `.env` as much as on one that does not. The other way round, saving a
    credential here would appear to work and change nothing.

    These stay module attributes rather than becoming functions because every
    call site and every test already reads them by name, and a second spelling
    of the same fact is a second thing to keep in step.
    """
    global DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL
    global GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET

    stored = read_credentials()

    def pick(key: str, fallback: str = "") -> str:
        return stored.get(key) or os.getenv(key, "").strip() or fallback

    DEEPSEEK_API_KEY = pick("DEEPSEEK_API_KEY")
    DEEPSEEK_BASE_URL = pick("DEEPSEEK_BASE_URL", DEEPSEEK_DEFAULT_BASE_URL)
    GOOGLE_CLIENT_ID = pick("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = pick("GOOGLE_CLIENT_SECRET")


def save_credentials(changes: dict[str, str | None]) -> None:
    """Write the credential file and refresh the names from it.

    A key absent from `changes` is left exactly as it was, and an empty string
    clears it. That distinction is the whole reason the window never receives
    a value: it can send back only what the user actually typed, so "I did not
    touch this field" and "I emptied this field" stay different questions.
    """
    stored = read_credentials()
    for key, value in changes.items():
        if key not in CREDENTIAL_KEYS:
            continue
        text = "" if value is None else str(value).strip()
        if text:
            stored[key] = text
        else:
            stored.pop(key, None)

    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_text(
        json.dumps(stored, indent=2, sort_keys=True), encoding="utf-8"
    )
    refresh_credentials()


def mask(value: str) -> str:
    """A credential as it is allowed to appear outside this process.

    Enough to recognise which key is in there and never enough to use. The
    window is a WebView: it does not read files, and it does not receive
    secrets either -- the same rule, one layer up.
    """
    if not value:
        return ""
    tail = value[-4:] if len(value) >= 8 else ""
    return f"····{tail}" if tail else "····"


refresh_credentials()


@dataclass
class Models:
    agent: str = "qwen3.5:4b"
    ollama_url: str = "http://127.0.0.1:11434"
    #: 23552, and the number belongs to the card rather than to the model.
    #: On the 2b, reserved KV was nearly free and 32768 cost 260 MiB more than
    #: 8192 at the same 63 tokens a second. The 4b is larger, and the card runs
    #: out. Measured here, with a browser open:
    #:
    #:      model  ctx     on GPU   tok/s   card
    #:      2b     32768   100%      64.8   3612M
    #:      4b     16384   100%      46.6   4430M
    #:      4b     20480   100%      46.6   4560M
    #:      4b     21504   100%      46.5   ~4600M
    #:      4b     22528   100%      46.6   ~4650M
    #:      4b     23552   100%      46.1   ~4700M
    #:      4b     24576    85%      40.1   4628M
    #:      4b     32768    79%      31.9   4666M
    #:      4b     40960    71%      25.4      --
    #:      4b     70000    60%      16.8      --
    #:
    #: The cliff is between 23552 and 24576 and it is a cliff, not a slope:
    #: one step over it and 15% of the model is on the processor. 70000 was
    #: asked for and measured rather than argued about -- it costs 63% of
    #: generation speed, because the constraint is 6 GB of VRAM and not the
    #: disk, which has plenty.
    #:
    #: Past 20480 Ollama leaves part of the model on the CPU and never says so
    #: -- `ollama ps` reports it and nothing else does. The cost is not the
    #: window, it is the 28% of generation speed that goes with the spill. So
    #: this is the largest window that stays entirely on the card, and the
    #: build order has said since milestone 1 that `ollama ps` must read 100%.
    #:
    #: 16384 was the ceiling until the card was measured again with less on it.
    #: The number is not a property of the model alone -- it is the model, the
    #: window and whatever else wants the card, so it is worth re-measuring
    #: rather than assuming. `OLLAMA_KV_CACHE_TYPE=q8_0` would halve the KV and
    #: is the documented next lever if 32768 is ever wanted.
    context_tokens: int = 23552
    thinking_budget: int = 2048
    summariser: str = "deepseek-v4-flash"
    #: Which transcriber. `parakeet-tdt-0.6b-v2` is a 600M-parameter
    #: FastConformer with a token-and-duration transducer; `moonshine-base` is
    #: 61M and measurably faster on short English commands -- 123 ms a clip
    #: against 704, and it was the more accurate of the two on the only
    #: labelled set available here. Parakeet is the stronger model on every
    #: published benchmark and on the one real recording, so this is a bet on
    #: speech the set does not contain, paid for in latency.
    stt: str = "parakeet-tdt-0.6b-v2"
    #: What no memory slice pays for: the system prompt, the bound tool
    #: schemas, the memory framing block, and the standing system lines the
    #: runtime adds every turn -- which folders are open, and what asks before
    #: it happens, and the two nudges the loop can add to it. Measured at
    #: ~2550 worst case: 1552 of bound schemas, 371 of system prompt, and the
    #: rest in framing, the situational lines and the loop's own scaffolding.
    #: Underestimating this overruns num_ctx, and Ollama answers by dropping
    #: the oldest messages without saying so, so the rest is headroom.
    #:
    #: The trend is still the thing to watch -- all of it is paid every turn,
    #: whether or not a tool is called -- but it is no longer a crisis. At the
    #: old 8192 window this was 29% of everything and the slices were being
    #: scaled to 0.61 to fit under it; at 23552 the 2816 is 12% and the
    #: slices are sized to fit rather than scaled into it. Twelve tools costing
    #: 1552 tokens of bound schema is a cost worth knowing rather than a
    #: reason not to add the thirteenth.
    #:
    #: Moved a fifth time when the assistant was given a character: the system
    #: prompt went from 276 tokens to 371, and the loop gained a second nudge.
    #: Personality is not free, and this is where it is paid for.
    overhead_tokens: int = 2816
    #: Room for the reply itself, which has no slice of its own.
    reply_tokens: int = 768


@dataclass
class Audio:
    sample_rate: int = 16000
    input_device: str = ""
    output_device: str = ""
    aec_delay_ms: int = 60
    vad_threshold: float = 0.5
    vad_silence_ms: int = 700
    #: Measured against the *speech* in a clip, not its length. A clip always
    #: carries half a second of pre-roll and seven-tenths of trailing silence,
    #: so a guard against the total could never fire.
    min_clip_ms: int = 300
    max_clip_ms: int = 30000
    #: How much of the ring buffer is kept when the wake word fires. People
    #: start the question before they finish the trigger.
    preroll_ms: int = 500
    #: Open the microphone as soon as the sidecar comes up, rather than
    #: waiting for a client to turn voice on.
    #:
    #: This is what makes the app answer its name from a cold start: the shell
    #: launches at login, the sidecar comes up with it, and the wake word is
    #: already listening before anybody has clicked anything. Off by default
    #: because a headless or text-only run has no use for a microphone -- and
    #: because opening one unasked is a thing to opt into, not out of.
    listen_on_start: bool = False
    #: How long to wait, after the wake word fires, for the question to
    #: start. Nothing said in that time and the clip is abandoned -- the
    #: television, usually. Without it the clip records silence until
    #: max_clip_ms and hands Moonshine thirty seconds of room tone.
    #:
    #: Four seconds because the gap between "hey jarvis" and the question is
    #: a real pause, not a hesitation: measured at 1.4 s here, and that is
    #: someone who knows what they are about to ask. This is the clock that
    #: runs during it -- `vad_silence_ms` is for stopping, not starting, and
    #: while it was doing both, the clip closed inside the pause and dropped
    #: the wake phrase as a cough.
    lead_in_ms: int = 4000


@dataclass
class Assistant:
    """What it calls itself.

    A name rather than a string literal because it appears in three places
    that must agree -- the system prompt, the rendering of a past exchange,
    and the terminal banner -- and because the wake phrase and the name should
    plausibly be the same word. `sunday` stays the package, the process and the
    data directory; those are the program, and this is the person it plays.
    """

    name: str = "Alexa"


@dataclass
class Wake:
    enabled: bool = True
    #: `alexa`, `hey_jarvis` and `hey_mycroft` ship pretrained. Anything else
    #: is a model you trained yourself and dropped in `models\wake\`.
    model: str = "alexa"
    #: 0.3, not the 0.5 this started at, and the difference is measured rather
    #: than felt. Measured against `hey_jarvis`, which was the phrase at the
    #: time; it is a property of the phrase and the voice saying it, so it is
    #: worth re-running `python -m sunday.audio.check` for `alexa`.
    #: On this microphone a clearly spoken "hey jarvis" peaks at
    #: 0.490 -- under the old bar by a hundredth, so it fired perhaps one time
    #: in three and looked like a broken microphone the rest of the time. In
    #: the same recording everything that was *not* the phrase, the whole
    #: question included, peaked at 0.0002.
    #:
    #: So the gap is a factor of 2500 and the old threshold sat inside the
    #: noise of one speaker's voice rather than inside that gap. Anywhere from
    #: 0.05 to 0.45 would separate them here; 0.3 leaves room on both sides.
    #: `python -m sunday.audio.check` prints both numbers for your own voice.
    threshold: float = 0.3
    cooldown_ms: int = 1500
    #: How long the door stays open after a reply, so a conversation is a
    #: conversation rather than a sequence of summonings. Say the phrase once
    #: and the follow-up questions need only be spoken.
    #:
    #: Thirty seconds. It was eight, which is long enough to ask the next
    #: thing you had already decided on and short enough that a room is not
    #: listened to all evening -- and eight turned out to be the wrong side of
    #: that trade in use. Asking a follow-up means reading the answer first,
    #: and the window was expiring during the reading.
    #:
    #: What it costs is stated rather than hidden: for thirty seconds after
    #: every reply, sustained speech in the room opens a clip without the wake
    #: word. The guards still apply -- `barge_in_ms` of speech to open one,
    #: `min_clip_ms` of speech to keep it, `lead_in_ms` to abandon it -- so
    #: what a passing conversation costs is a dropped clip and a notice, not a
    #: turn. Zero turns the window off and every question needs the phrase.
    follow_up_ms: int = 30000


@dataclass
class Tts:
    """Voice out. The model is Kokoro, which is not a choice -- these are."""

    enabled: bool = True
    #: 54 voices ship in one 28 MB file; `python -m sunday.audio.check --voices`
    #: lists them. `af_bella` is the default because it is the clearest of the
    #: American female set at speed 1.0, and clarity is what survives a room.
    voice: str = "af_bella"
    speed: float = 1.0
    language: str = "en-us"


@dataclass
class Echo:
    speech_threshold_while_speaking: float = 0.8
    transcript_similarity_cutoff: float = 0.6
    #: What is left after cancellation has to be this loud, relative to what
    #: was played, before it counts as a person. The VAD alone is not enough:
    #: Silero scores Sunday's own voice coming back through the room at 1.0,
    #: because it *is* speech -- just not yours. Raising the VAD threshold
    #: cannot separate them either, for the same reason.
    #:
    #: Measured by `python -m sunday.audio.check --echo`, which plays into your
    #: room, records the result, and prints the ratio echo alone produces.
    #:
    #: The margin is wider than it looks. Measured on this machine, speaking a
    #: sentence through the speakers with the microphone open: what came back
    #: was 0.0002 of what went out, because the capture device cancels its own
    #: echo -- most laptop microphone arrays do. A person over the top of it
    #: reaches about 0.7. So anywhere between those works, and 0.35 sits in the
    #: middle rather than near either edge.
    #:
    #: On hardware that does *not* cancel for itself, the residual after layer
    #: 1 is what this has to clear. Raise it if it interrupts itself; lower it
    #: if talking over it stops working.
    barge_in_ratio: float = 0.35
    #: How long Sunday counts as still speaking after the last block has gone
    #: to the sound card.
    #:
    #: `write` returns when the card has *accepted* the audio, not when it has
    #: played it, so there is a buffer still to come and then a room still
    #: ringing. Everything that protects against hearing itself keys off the
    #: speaking flag -- the raised VAD bar, the barge-in floor, and the
    #: transcript check, which only examines clips recorded while speaking. Drop
    #: the flag at the last `write` and all three switch off while the sound is
    #: still in the air, and the follow-up window opens into it.
    tail_ms: int = 400
    #: Layer 2 wants "a sustained burst rather than a single frame", and this
    #: is how long a burst is. Three Silero windows at 16 kHz, which is 96 ms
    #: -- chosen against the sub-100 ms target for first syllable to silence,
    #: not rounded to something prettier. Residual echo is quiet and choppy and
    #: rarely holds three windows; a person interrupting always does.
    barge_in_ms: int = 96


@dataclass
class Root:
    """One folder Sunday may open, and what the user calls it.

    The label is the whole point. Without one the user says "the documents
    folder" and the model is left matching English against `C:/Users/User/
    Documents/Sunday` -- which it did, by inventing a folder called
    `documents` three different ways. A name is the fact that was missing.
    """

    label: str
    path: str


@dataclass
class Files:
    #: Either a bare path string or a {label, path} table. A bare string still
    #: works and takes the folder's own name as its label, so nothing that was
    #: configured before has to change.
    roots: list = field(default_factory=list)
    max_read_bytes: int = 200_000

    def entries(self) -> list[Root]:
        """The roots as (label, path), whichever way they were written."""
        out: list[Root] = []
        for raw in self.roots:
            if isinstance(raw, dict):
                path = str(raw.get("path", "")).strip()
                if not path:
                    continue
                label = str(raw.get("label", "")).strip() or _default_label(path)
            else:
                path = str(raw).strip()
                if not path:
                    continue
                label = _default_label(path)
            out.append(Root(label=label.lower(), path=path))
        return out

    def paths(self) -> list[str]:
        return [root.path for root in self.entries()]


def _default_label(path: str) -> str:
    """The folder's own name, which is what a person would call it anyway."""
    return PurePath(path.replace("\\", "/")).name or path


@dataclass
class Memory:
    #: Cosine distance, so 0 is identical and 1 is unrelated. Measured against
    #: 830 real turns rather than picked: nine questions whose answer was in
    #: the store came back at 0.13-0.60, six about things never discussed at
    #: 0.72-0.90, and nothing landed between. Any cutoff in that gap keeps
    #: every relevant turn and drops every irrelevant one.
    #:
    #: It was 0.45, and that kept two of the nine. The store had the answer,
    #: the vector search found it every time, and this number threw it away --
    #: which from outside is indistinguishable from an assistant with no
    #: long-term memory at all. Third time a guessed threshold has cost this
    #: project real behaviour; see `[wake] threshold` for the other two.
    distance_cutoff: float = 0.65
    top_k: int = 5
    idle_minutes: int = 10
    #: Sized to fit the window rather than scaled into it. With 23552 of
    #: window, 2816 of overhead and 768 for the reply, 19968 is left; these
    #: four plus the thinking reservation come to 19456, so nothing is
    #: scaled and the numbers here are the numbers used.
    #:
    #: That last part is the point. `scaled_to` exists so a smaller window
    #: degrades rather than breaks, and it is very good at hiding the fact
    #: that it fired: at 32768 these read 2048/6144/2048/4096 and were used
    #: whole, and moving to 16384 without touching them would have quietly
    #: shrunk every one by 0.81. A configured number that is not the number
    #: in use is the failure note 51 was written about.
    #:
    #: They are allowances, not usage. A short session fills none of them, so
    #: the prompt-eval cost -- about 0.15 ms per token above 3k -- arrives
    #: gradually and only in sessions long enough to have earned it.
    slice_summary: int = 2560
    slice_recent: int = 7680
    slice_retrieved: int = 2560
    slice_tools: int = 4608


@dataclass
class External:
    enabled: bool = True
    search: str = "ddgs"
    max_hops: int = 2
    fetch_timeout_s: int = 10
    fetch_max_bytes: int = 2_000_000
    extract_max_chars: int = 12_000
    query_max_chars: int = 200


@dataclass
class Guardrail:
    secret_paths: list[str] = field(
        default_factory=lambda: [
            ".env",
            ".env.*",  # .env.local is the one that actually happens
            "*.key",
            "*.pem",
            "*.pfx",
            "*.p12",
            "id_rsa",
            "id_ed25519",
            "credentials*",
            ".ssh/",
            ".aws/",
            ".gnupg/",
            "*.kdbx",
            "google_token.json",
            "token.json",
            "service-account*.json",
            "secrets.*",
            ".npmrc",
            ".pypirc",
            ".netrc",
        ]
    )
    key_shapes: list[str] = field(
        default_factory=lambda: [
            "sk-",
            "sk_live_",
            "ghp_",
            "gho_",
            "glpat-",
            "AKIA",
            "ASIA",
            "xoxb-",
            "xoxp-",
            "xapp-",
            "AIza",
            "ya29.",
            "hf_",
            "dop_v1_",
            "eyJ",
            "-----BEGIN",
        ]
    )


@dataclass
class Limits:
    #: Twelve rather than eight, so a turn can be a *job* rather than a
    #: lookup. Five was sized for one call; eight left room to be wrong once
    #: and recover. Twelve is for the turn that reads a file, thinks about it,
    #: writes another, moves it and says so -- which is the shape of work this
    #: is now asked to narrate its way through rather than answer in one shot.
    #:
    #: It bounds the turn and nothing else does: the loop ends when the model
    #: stops asking for tools, so this is the only thing standing between a
    #: confused model and an afternoon.
    tool_calls: int = 12


@dataclass
class UI:
    fps_focused: int = 60
    fps_blurred: int = 10
    start_minimised: bool = False
    autostart: bool = False
    #: The backstage trace: one line per step, saying what actually ran. On by
    #: default, and meant to stay that way until the app ships -- the whole
    #: point is that you can see whether long-term memory was read, not merely
    #: whether it returned anything. SUNDAY_TRACE=0 turns it off for one run
    #: without editing config.toml.
    trace: bool = True


@dataclass
class Prompts:
    """The one prompt a user is allowed to rewrite.

    Empty means the built-in, which is what makes "reset" a deletion rather
    than a copy of the default written back into the file -- a copied default
    goes stale the day `prompts.SYSTEM` is edited and nothing says so.

    Only the system prompt. The airlock's two prompts are not a personality
    setting: `AIRLOCK_SYSTEM` is the instruction that keeps a private word out
    of an outgoing query, and a settings screen that let it be rewritten would
    be a settings screen that can switch off the one guarantee this program
    makes.

    It is paid for out of `[models] overhead_tokens`, and the settings screen
    counts it: the built-in is 371 tokens, and the reservation is 2816 for the
    prompt, the bound schemas, the framing and the loop's own scaffolding
    together. A long prompt does not fail, it quietly shrinks the memory
    slices -- so the number is shown rather than left to be discovered.
    """

    system: str = ""


@dataclass
class Config:
    assistant: Assistant = field(default_factory=Assistant)
    prompts: Prompts = field(default_factory=Prompts)
    models: Models = field(default_factory=Models)
    audio: Audio = field(default_factory=Audio)
    wake: Wake = field(default_factory=Wake)
    tts: Tts = field(default_factory=Tts)
    echo: Echo = field(default_factory=Echo)
    files: Files = field(default_factory=Files)
    memory: Memory = field(default_factory=Memory)
    external: External = field(default_factory=External)
    guardrail: Guardrail = field(default_factory=Guardrail)
    limits: Limits = field(default_factory=Limits)
    ui: UI = field(default_factory=UI)
    source: Path | None = None


def config_path() -> Path | None:
    for candidate in (REPO_ROOT / "config.toml", SUNDAY_HOME / "config.toml"):
        if candidate.is_file():
            return candidate
    return None


def _apply(section, values: dict) -> None:
    known = {f.name for f in fields(section)}
    for key, value in values.items():
        if key in known:
            setattr(section, key, value)


def load(path: Path | None = None) -> Config:
    cfg = Config()
    path = path or config_path()
    if path is None:
        return cfg

    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    for name, value in raw.items():
        section = getattr(cfg, name, None)
        if is_dataclass(section) and isinstance(value, dict):
            _apply(section, value)
    cfg.source = path
    return cfg


_cache: Config | None = None


def get() -> Config:
    """The process-wide config. Loaded once, reloadable via reload()."""
    global _cache
    if _cache is None:
        _cache = load()
    return _cache


def reload(path: Path | None = None) -> Config:
    global _cache
    _cache = load(path)
    return _cache


def trace_enabled(cfg: "Config | None" = None) -> bool:
    """Config says yes or no; the environment gets the last word, so a single
    noisy run can be quietened without a file edit."""
    override = os.getenv("SUNDAY_TRACE")
    if override is not None:
        return override.strip().lower() not in {"0", "false", "no", "off", ""}
    return (cfg or get()).ui.trace
