"""Configuration: config.toml plus the two secrets that live in .env.

Search order for config.toml is repo root first (development), then
SUNDAY_HOME. Anything absent falls back to the defaults below, so the app
starts with no config file at all.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path

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
    agent: str = "qwen3.5:2b"
    ollama_url: str = "http://127.0.0.1:11434"
    context_tokens: int = 8192
    thinking_budget: int = 1024
    summariser: str = "deepseek-v4-flash"
    #: What no memory slice pays for: the system prompt, the bound tool
    #: schemas, the memory framing block, and the standing system lines the
    #: runtime adds every turn -- which folders are open, and what asks before
    #: it happens. Measured at ~1870 worst case with ten tools bound, of which
    #: the schemas are 1239 on their own. Underestimating this overruns
    #: num_ctx and Ollama truncates without saying so, so the rest is headroom.
    #:
    #: The trend is the thing to watch: every tool added is paid for out of
    #: the memory slices, every turn, whether or not it is called. At eight
    #: tools this was 1630. There is not room for many more at an 8k window.
    overhead_tokens: int = 2000
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
    min_clip_ms: int = 300
    max_clip_ms: int = 30000


@dataclass
class Wake:
    enabled: bool = True
    model: str = "hey_jarvis"
    threshold: float = 0.5
    cooldown_ms: int = 1500


@dataclass
class Echo:
    speech_threshold_while_speaking: float = 0.8
    transcript_similarity_cutoff: float = 0.6


@dataclass
class Files:
    roots: list[str] = field(default_factory=list)
    max_read_bytes: int = 200_000


@dataclass
class Memory:
    distance_cutoff: float = 0.45
    top_k: int = 5
    idle_minutes: int = 10
    slice_summary: int = 1024
    slice_recent: int = 3072
    slice_retrieved: int = 1024
    slice_tools: int = 2048


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
    tool_calls: int = 5


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
    models: Models = field(default_factory=Models)
    audio: Audio = field(default_factory=Audio)
    wake: Wake = field(default_factory=Wake)
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
