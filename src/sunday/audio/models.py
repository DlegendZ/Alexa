r"""Where the voice models live, and how they get there.

They are not in the installer and never will be: three of them are small and
one is a quarter of a gigabyte, and all four change independently of this
code. So they are downloaded once, into `%LOCALAPPDATA%\Sunday\models\`, and
after that they are files on disk like any other.

Nothing here is imported by the runtime. `fetch()` is a setup step you run
before the first voice turn; `model_path()` is what the wrappers call, and it
raises `ModelMissing` with the command to fix it rather than a stack trace
about a path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sunday import config

#: openWakeWord ships its models as release assets. The two feature models are
#: shared by every wake phrase; only the last file changes when the phrase does.
_OWW = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"

#: Moonshine's own repo, the merged float ONNX export. `base` rather than
#: `tiny`: 247 MB against 80, and the difference is audible on a first
#: sentence spoken across a room.
_MOONSHINE = "https://huggingface.co/UsefulSensors/moonshine/resolve/main/onnx/merged/base/float/"

_SILERO = "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/"


@dataclass(frozen=True)
class Asset:
    """One file, and where it comes from."""

    key: str
    url: str
    approx_mb: float


#: Keyed by the name the code asks for. `wake/<phrase>.onnx` is the only entry
#: that varies with config, so it is looked up rather than listed.
ASSETS: dict[str, Asset] = {
    "wake/melspectrogram.onnx": Asset("wake/melspectrogram.onnx", _OWW + "melspectrogram.onnx", 1.1),
    "wake/embedding_model.onnx": Asset("wake/embedding_model.onnx", _OWW + "embedding_model.onnx", 1.3),
    "wake/hey_jarvis.onnx": Asset("wake/hey_jarvis.onnx", _OWW + "hey_jarvis_v0.1.onnx", 1.3),
    "wake/alexa.onnx": Asset("wake/alexa.onnx", _OWW + "alexa_v0.1.onnx", 1.3),
    "wake/hey_mycroft.onnx": Asset("wake/hey_mycroft.onnx", _OWW + "hey_mycroft_v0.1.onnx", 1.3),
    "vad/silero_vad.onnx": Asset("vad/silero_vad.onnx", _SILERO + "silero_vad.onnx", 2.3),
    "moonshine/encoder_model.onnx": Asset("moonshine/encoder_model.onnx", _MOONSHINE + "encoder_model.onnx", 80.8),
    "moonshine/decoder_model_merged.onnx": Asset("moonshine/decoder_model_merged.onnx", _MOONSHINE + "decoder_model_merged.onnx", 166.2),
    "moonshine/tokenizer.json": Asset("moonshine/tokenizer.json", _MOONSHINE + "tokenizer.json", 3.8),
}

#: What milestone 7 needs on disk before it can hear anything. The wake phrase
#: is added by `required()`, because it is the one that depends on config.
VOICE_IN: tuple[str, ...] = (
    "wake/melspectrogram.onnx",
    "wake/embedding_model.onnx",
    "vad/silero_vad.onnx",
    "moonshine/encoder_model.onnx",
    "moonshine/decoder_model_merged.onnx",
    "moonshine/tokenizer.json",
)


class ModelMissing(RuntimeError):
    """A model file is not on disk. Says which, and what to run."""

    def __init__(self, key: str) -> None:
        super().__init__(
            f"voice model {key} is not downloaded. Run "
            r".venv\Scripts\python.exe -m sunday.audio.models"
        )
        self.key = key


def wake_key(phrase: str) -> str:
    r"""The asset key for a configured wake phrase.

    A phrase that is not one of the three pretrained ones is taken as a file
    the user trained themselves and dropped in `models\wake\`.
    """
    return f"wake/{phrase.strip().lower().replace(' ', '_')}.onnx"


def required(cfg: config.Config | None = None) -> list[str]:
    cfg = cfg or config.get()
    return [*VOICE_IN, wake_key(cfg.wake.model)]


def path_for(key: str) -> Path:
    return config.MODEL_DIR / key


def model_path(key: str) -> Path:
    """The file, or a `ModelMissing` naming the command that fetches it."""
    p = path_for(key)
    if not p.is_file():
        raise ModelMissing(key)
    return p


def missing(keys: list[str] | None = None) -> list[str]:
    return [k for k in (keys or required()) if not path_for(k).is_file()]


def fetch(
    keys: list[str] | None = None,
    *,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> list[str]:
    """Download whatever is not already there. Returns what it fetched.

    `httpx` is already a dependency for the web pipeline, so this adds nothing
    to the install. A partial download is written to `.part` and renamed only
    when it completes, so an interrupted fetch cannot leave a truncated model
    that loads and then produces nonsense.
    """
    import httpx

    got: list[str] = []
    for key in missing(keys):
        asset = ASSETS.get(key)
        if asset is None:
            raise ModelMissing(key)
        target = path_for(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(target.suffix + ".part")
        with httpx.stream("GET", asset.url, follow_redirects=True, timeout=60) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or 0)
            done = 0
            with part.open("wb") as handle:
                for block in response.iter_bytes(1 << 16):
                    handle.write(block)
                    done += len(block)
                    if on_progress is not None:
                        on_progress(key, done, total)
        part.replace(target)
        got.append(key)
    return got


def main() -> int:
    """`python -m sunday.audio.models` -- the first-run download, in a terminal."""
    import sys

    keys = required()
    outstanding = missing(keys)
    if not outstanding:
        print(f"all {len(keys)} voice models are already in {config.MODEL_DIR}")
        return 0

    size = sum(ASSETS[k].approx_mb for k in outstanding if k in ASSETS)
    print(f"fetching {len(outstanding)} file(s), about {size:.0f} MB, into {config.MODEL_DIR}")

    state = {"key": "", "line": 0}

    def progress(key: str, done: int, total: int) -> None:
        if key != state["key"]:
            state["key"] = key
            print(f"  {key}", end="", flush=True)
        step = done * 20 // max(total, 1)
        if step != state["line"]:
            state["line"] = step
            print(".", end="", flush=True)
        if total and done >= total:
            print(" done", flush=True)

    try:
        fetch(keys, on_progress=progress)
    except Exception as exc:  # network, disk, a moved URL
        print(f"\nfailed: {exc}", file=sys.stderr)
        return 1
    print(f"ready. {len(keys)} model(s) in {config.MODEL_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
