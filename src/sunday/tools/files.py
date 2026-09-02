"""File tools, and the sandbox that is the important part of them.

A path is accepted only if, after resolving symlinks and `..`, its real
location sits under one of the configured roots. Everything else is refused
before any disk access happens -- that is what stops `../../.ssh/id_rsa`.

Results are labelled `private`. Private is not secret: reading an ordinary file
does not shut the airlock door. That is the deny overlay's job, in guardrail.py.
"""

from __future__ import annotations

import os
from pathlib import Path

from sunday import config
from sunday.tools import Tool, register

REFUSED = (
    "refused: path is outside the configured roots. Tell the user this folder "
    "is not one Sunday is allowed to open, and that they can add it to "
    "config.toml under [files] roots. Do not guess at another reason."
)
TRUNCATED = "\n[truncated]"


def roots() -> list[Path]:
    out: list[Path] = []
    for raw in config.get().files.roots:
        try:
            out.append(Path(raw).resolve())
        except OSError:  # pragma: no cover - unreachable roots are skipped
            continue
    return out


def _under(path: Path, root: Path) -> bool:
    """Case-insensitive containment, because Windows paths are."""
    a = os.path.normcase(str(path))
    b = os.path.normcase(str(root))
    return a == b or a.startswith(b + os.sep)


def resolve(raw: str) -> Path | None:
    """The real location of `raw`, or None if it is outside every root.

    Resolution happens first and containment is checked on the result, so
    symlinks and `..` cannot walk out of the sandbox.
    """
    if not raw or not raw.strip():
        return None
    try:
        candidate = Path(raw.strip()).expanduser().resolve()
    except (OSError, ValueError):
        return None
    for root in roots():
        if _under(candidate, root):
            return candidate
    return None


def read_file(path: str) -> str:
    target = resolve(path)
    if target is None:
        return REFUSED
    if not target.exists():
        return f"error: no such file: {path}"
    if target.is_dir():
        return f"error: {path} is a directory, use list_dir"

    cap = config.get().files.max_read_bytes
    try:
        data = target.read_bytes()
    except OSError as exc:
        return f"error: could not read {path} ({exc.strerror or exc})"

    clipped = len(data) > cap
    text = data[:cap].decode("utf-8", errors="replace")
    return text + TRUNCATED if clipped else text


def write_file(path: str, text: str) -> str:
    target = resolve(path)
    if target is None:
        return REFUSED
    if is_credential_path(target):
        return "refused: will not write over a credential file"
    if target.is_dir():
        return f"error: {path} is a directory"

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except OSError as exc:
        return f"error: could not write {path} ({exc.strerror or exc})"
    return f"wrote {len(text)} characters to {target}"


def list_dir(path: str) -> str:
    target = resolve(path)
    if target is None:
        return REFUSED
    if not target.exists():
        return f"error: no such directory: {path}"
    if not target.is_dir():
        return f"error: {path} is a file, use read_file"

    lines: list[str] = []
    try:
        for entry in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if entry.is_dir():
                lines.append(f"{entry.name}/")
            else:
                try:
                    lines.append(f"{entry.name}  {entry.stat().st_size} bytes")
                except OSError:
                    lines.append(entry.name)
    except OSError as exc:
        return f"error: could not list {path} ({exc.strerror or exc})"

    if not lines:
        return f"{target} is empty"
    return f"{target}:\n" + "\n".join(lines)


def is_credential_path(path: Path) -> bool:
    """Filled in by the guardrail at milestone 3; here so write_file can ask
    the question from the start."""
    from sunday import guardrail

    return guardrail.is_secret_path(path)


register(
    Tool(
        name="read_file",
        description=(
            "Read a text file from the user's machine. Only paths inside the "
            "configured folders are allowed; anything else is refused."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path to the file"}
            },
            "required": ["path"],
        },
        fn=read_file,
        provenance="private",
    )
)

register(
    Tool(
        name="write_file",
        description=(
            "Write text to a file on the user's machine, replacing what is "
            "there. Only paths inside the configured folders are allowed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path to the file"},
                "text": {"type": "string", "description": "The full new contents"},
            },
            "required": ["path", "text"],
        },
        fn=write_file,
        provenance="private",
    )
)

register(
    Tool(
        name="list_dir",
        description=(
            "List the names and sizes in a folder on the user's machine. Use "
            "this to find a file before reading it. Never returns contents."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path to the folder"}
            },
            "required": ["path"],
        },
        fn=list_dir,
        provenance="private",
    )
)
