"""Configuration: config.toml plus the two secrets that live in .env.

Search order for config.toml is repo root first (development), then
SUNDAY_HOME. Anything absent falls back to the defaults below, so the app
starts with no config file at all.
"""

from __future__ import annotations

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

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic")


@dataclass
class Models:
    agent: str = "qwen3.5:4b"
    ollama_url: str = "http://127.0.0.1:11434"
    #: 16384, and the number belongs to the model rather than to the window.
    #: On the 2b, reserved KV was nearly free and 32768 cost 260 MiB more than
    #: 8192 at the same 63 tokens a second. The 4b is larger, and the card runs
    #: out. Measured here, with a browser open:
    #:
    #:      model  ctx     on GPU   tok/s   card
    #:      2b     32768   100%      64.8   3612M
    #:      4b     16384   100%      46.6   4430M
    #:      4b     20480   100%      46.6   4560M
    #:      4b     24576    85%      40.3   4628M
    #:      4b     32768    79%      32.4   4666M
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
    context_tokens: int = 20480
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
    #: it happens. Measured at ~2260 worst case: 1239 of bound schemas, 276 of
    #: system prompt, and the rest in framing and the situational lines.
    #: Underestimating this overruns num_ctx, and Ollama answers by dropping
    #: the oldest messages without saying so, so the rest is headroom.
    #:
    #: The trend is still the thing to watch -- all of it is paid every turn,
    #: whether or not a tool is called -- but it is no longer a crisis. At the
    #: old 8192 window this was 29% of everything and the slices were being
    #: scaled to 0.61 to fit under it; at 16384 the same 2400 is 15% and the
    #: slices are sized to fit rather than scaled into it. Ten tools costing
    #: 1239 tokens of bound schema is a cost worth knowing rather than a
    #: reason not to add the eleventh.
    overhead_tokens: int = 2400
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
    #: than felt. On this microphone a clearly spoken "hey jarvis" peaks at
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
    #: Eight seconds is long enough to think of the next thing and short enough
    #: that a room does not spend its evening being listened to. Zero turns it
    #: off and every turn needs the phrase again.
    follow_up_ms: int = 8000


@dataclass
class Tts:
    """Voice out. The model is Kokoro, which is not a choice -- these are."""

    enabled: bool = True
    #: 54 voices ship in one 28 MB file; `python -m sunday.audio.check --voices`
    #: lists them. `af_heart` is the default because it is the clearest of the
    #: American female set at speed 1.0, and clarity is what survives a room.
    voice: str = "af_heart"
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
    distance_cutoff: float = 0.45
    top_k: int = 5
    idle_minutes: int = 10
    #: Sized to fit the window rather than scaled into it. With 20480 of
    #: window, 2400 of overhead and 768 for the reply, 17312 is left; these
    #: four plus the thinking reservation come to 16384, so nothing is
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
    slice_summary: int = 2048
    slice_recent: int = 6144
    slice_retrieved: int = 2048
    slice_tools: int = 4096


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
    #: Eight rather than five, so a turn can be wrong once and still finish.
    #: Five was sized for a turn that meant one lookup; a turn that picks the
    #: wrong tool, reads the refusal and tries again needs room for the
    #: recovery as well as the mistake.
    tool_calls: int = 8


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
class Config:
    assistant: Assistant = field(default_factory=Assistant)
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


def require_deepseek_key() -> None:
    if not DEEPSEEK_API_KEY:
        raise RuntimeError(
            f"DEEPSEEK_API_KEY not set. Fill it in at {ENV_PATH} "
            "(copy .env.example there first)."
        )
