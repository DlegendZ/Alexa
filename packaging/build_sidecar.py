r"""Freeze the sidecar into a folder the installer can carry.

    .venv\Scripts\python.exe packaging\build_sidecar.py
    cd app; npm run tauri build

The first line produces `app\src-tauri\sidecar\sunday-sidecar.exe` and the
`_internal` folder beside it: a Python, every package the sidecar imports, and
the data files those packages need at runtime. The second bundles that folder
into the installer, next to the window. A machine that installs it needs no
Python, no repository and no `.venv` -- what it still needs is Ollama, which the
first-run screen says, and the models, which the first-run screen downloads.

What goes in is what `sunday.sidecar` imports, and nothing is swept from the
repository root. `.env`, `config.toml`, `data/` and the credential files are
never inputs -- and because "never an input" is a claim about a tool's
behaviour rather than a fact about the output, the output is searched for
them afterwards, and for anything shaped like a key. A hit fails the build.
The frozen sidecar would not read a `.env` even if one were there
(`config.FROZEN`), but a secret inside the installer is a leak whether or not
anything reads it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "src-tauri" / "sidecar"
WORK = ROOT / "build" / "pyinstaller"
NAME = "sunday-sidecar"

#: Packages that load files, native libraries or plugins PyInstaller cannot
#: see by following imports: Chroma's migrations and Rust bindings, the ONNX
#: runtime's DLLs, PortAudio, espeak-ng's voice data, trafilatura's settings.
COLLECT = [
    "chromadb",
    "onnxruntime",
    "tokenizers",
    "kokoro_onnx",
    "espeakng_loader",
    "phonemizer",
    "onnx_asr",
    "sounddevice",
    "_sounddevice_data",
    "trafilatura",
    "justext",
    "ddgs",
    "primp",
    "langgraph",
    "tiktoken",
    "tiktoken_ext",
]

#: Packages that look themselves up by distribution metadata at import time.
METADATA = [
    "chromadb",
    "opentelemetry-api",
    "opentelemetry-sdk",
    "langgraph",
    "langchain-core",
    "ollama",
    "anthropic",
    "httpx",
    "onnx-asr",
    "kokoro-onnx",
]

#: Nothing the sidecar uses, and each would add megabytes.
EXCLUDE = ["tkinter", "pytest", "IPython", "matplotlib", "PyInstaller"]

#: Files that must never be in the output, by name.
FORBIDDEN_NAMES = {
    ".env",
    "config.toml",
    "credentials.json",
    "google_token.json",
    "handshake.json",
}

#: Byte patterns that mean a secret got in: a `.env` line, a DeepSeek-style
#: key, a Google client secret. The variable names alone are not enough --
#: `config.py` names them, compiled, on purpose -- so the patterns look for a
#: value attached.
FORBIDDEN_BYTES = [
    re.compile(rb"(?:DEEPSEEK_API_KEY|GOOGLE_CLIENT_SECRET)\s*=\s*\S{8,}"),
    re.compile(rb"(?<![A-Za-z0-9_])sk-[A-Za-z0-9]{24,}"),
    re.compile(rb"GOCSPX-[A-Za-z0-9_\-]{10,}"),
]


def freeze() -> None:
    args = [
        sys.executable, "-m", "PyInstaller",
        str(ROOT / "packaging" / "sidecar_entry.py"),
        "--name", NAME,
        "--onedir",
        "--console",
        "--noconfirm",
        "--clean",
        "--distpath", str(WORK / "dist"),
        "--workpath", str(WORK / "work"),
        "--specpath", str(WORK),
        "--paths", str(ROOT / "src"),
        # Several modules are imported inside functions -- the audio half, the
        # Google tools -- so following imports from the entry point misses
        # them. Every submodule of this package goes in.
        "--collect-submodules", "sunday",
    ]
    for package in COLLECT:
        args += ["--collect-all", package]
    for dist in METADATA:
        args += ["--copy-metadata", dist]
    for module in EXCLUDE:
        args += ["--exclude-module", module]
    subprocess.run(args, check=True, cwd=ROOT)


def audit(folder: Path) -> list[str]:
    """Everything in the output that should not be there."""
    found = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        if path.name in FORBIDDEN_NAMES:
            found.append(f"{path.relative_to(folder)}: a file that must never ship")
            continue
        data = path.read_bytes()
        for pattern in FORBIDDEN_BYTES:
            hit = pattern.search(data)
            if hit:
                found.append(
                    f"{path.relative_to(folder)}: matches {pattern.pattern[:40]!r}"
                )
    return found


def main() -> int:
    freeze()
    built = WORK / "dist" / NAME
    shutil.rmtree(OUT, ignore_errors=True)
    shutil.move(str(built), str(OUT))

    problems = audit(OUT)
    if problems:
        print("\nREFUSING: the frozen sidecar contains things that must not ship:")
        for line in problems:
            print("  " + line)
        shutil.rmtree(OUT, ignore_errors=True)
        return 1

    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"\nfrozen sidecar: {OUT} ({size / 1e6:.0f} MB), audit clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
