"""File tools, and the sandbox that is the important part of them.

A path is accepted only if, after resolving symlinks and `..`, its real
location sits under one of the configured roots. Everything else is refused
before any disk access happens -- that is what stops `../../.ssh/id_rsa`.

Results are labelled `private`. Private is not secret: reading an ordinary file
does not shut the airlock door. That is the deny overlay's job, in guardrail.py.
"""

from __future__ import annotations

import errno
import os
import shutil
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

    A *relative* path is resolved against the roots rather than against the
    process's working directory. Two reasons, and neither is convenience.
    Whatever folder Sunday happens to be launched from is not a thing the model
    can know, so `Documents/Sunday` used to land at `<cwd>/Documents/Sunday` --
    which, when the cwd is itself a root, is a real path inside the sandbox
    that simply does not exist, so the refusal read "no such directory" and the
    user was told their folder was missing. Trying each root in order gives the
    answer they meant, and gives it without widening the sandbox by one byte:
    every candidate is still checked for containment below.
    """
    if not raw or not raw.strip():
        return None
    text = raw.strip()

    candidates: list[Path] = []
    try:
        expanded = Path(text).expanduser()
    except (OSError, ValueError, RuntimeError):
        return None
    if expanded.is_absolute():
        candidates.append(expanded)
    else:
        candidates.extend(root / expanded for root in roots())
        # Last, and only as a fallback: the old cwd-relative reading, so a
        # path that did work carries on working.
        candidates.append(expanded)

    resolved: list[Path] = []
    for candidate in candidates:
        try:
            real = candidate.resolve()
        except (OSError, ValueError):
            continue
        if not any(_under(real, root) for root in roots()):
            continue
        if real.exists():
            return real  # an existing match beats a hypothetical one
        resolved.append(real)
    # Nothing exists yet: hand back the first allowed reading, so writing a new
    # file to a relative path still works.
    return resolved[0] if resolved else None


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


def delete_file(path: str) -> str:
    """Delete one file. The runtime asks you first -- always, unlike a write.

    Overwriting leaves you a file with different contents; deleting leaves you
    nothing, so there is no version of this that is safe to auto-execute. The
    confirmation lives in the runtime for the same reason the write one does:
    a tool has no channel to ask on.

    Folders are refused outright. A recursive delete is not a thing a 2b gets
    to reach for on a misparsed sentence.
    """
    target = resolve(path)
    if target is None:
        return REFUSED
    if is_credential_path(target):
        return "refused: will not delete a credential file"
    if target.is_dir():
        return (
            f"error: {path} is a folder, and Sunday only deletes single files. "
            "Tell the user to remove folders themselves."
        )
    if not target.exists():
        return f"error: no such file: {path}"

    try:
        target.unlink()
    except OSError as exc:
        return f"error: could not delete {path} ({exc.strerror or exc})"
    return f"deleted {target}"


def _landing(source: Path, destination: str) -> Path | None:
    """Where the file actually ends up.

    A destination that is an existing folder means "into it, keeping the name",
    which is what a person means by copy-paste and what the model will pass when
    it repeats the folder back. Anything else is taken as the new full path,
    so renaming while moving works in one call.
    """
    target = resolve(destination)
    if target is None:
        return None
    return target / source.name if target.is_dir() else target


def _prepare(source: str, destination: str, verb: str) -> tuple[Path, Path] | str:
    """Both ends checked before either is touched, or the refusal to return.

    The credential rules here are the load-bearing part, and the *source* rule
    is the one that is easy to miss. The deny overlay works on paths: reading
    `.env` marks the turn secret and bolts the web door. Copying `.env` to
    `notes.txt` would leave the same bytes at a path the overlay says nothing
    about -- so the next read is ordinary, the turn stays clean, and the door
    stays open. A copy is a laundering operation on the one thing the overlay
    protects, so a credential source is refused outright rather than tainted.
    """
    src = resolve(source)
    if src is None:
        return REFUSED
    if not src.exists():
        return f"error: no such file: {source}"
    if src.is_dir():
        return (
            f"error: {source} is a folder, and Sunday only {verb}s one file at "
            f"a time. Tell the user to {verb} folders themselves."
        )
    if is_credential_path(src):
        return (
            f"refused: will not {verb} a credential file. Tell the user that "
            f"plainly -- copying one out from under its own name is exactly "
            f"what the rule exists to stop."
        )

    dst = _landing(src, destination)
    if dst is None:
        return REFUSED
    if is_credential_path(dst):
        return f"refused: will not {verb} a file onto a credential path"
    if dst.is_dir():  # pragma: no cover - _landing only returns a dir's child
        return f"error: {destination} is a folder"
    if _same_file(src, dst):
        return f"error: the source and the destination are the same file"
    return src, dst


def _same_file(src: Path, dst: Path) -> bool:
    """Are these two paths one file?

    The string compare catches the ordinary case, and both paths have already
    been through `resolve()`, so symlinks are gone. What is left is hard links:
    two real, different names for one set of bytes. `samefile` compares the
    device and inode, which is the only thing that sees them.

    This is not a tidiness check. `copy2` opens the destination for writing
    before it reads anything, so a copy onto an alias of the source truncates
    the file to nothing and then copies the nothing.
    """
    if os.path.normcase(str(src)) == os.path.normcase(str(dst)):
        return True
    try:
        return dst.exists() and os.path.samefile(src, dst)
    except OSError:  # pragma: no cover - unreadable metadata is not "same"
        return False


def copy_file(source: str, destination: str) -> str:
    """Copy one file to another place inside the roots."""
    prepared = _prepare(source, destination, "copy")
    if isinstance(prepared, str):
        return prepared
    src, dst = prepared

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    except OSError as exc:
        return f"error: could not copy {source} ({exc.strerror or exc})"
    return f"copied {src} to {dst}"


#: Cross-device, as each platform reports it. POSIX raises EXDEV; Windows
#: raises winerror 17, "The system cannot move the file to a different disk
#: drive", which CPython maps to EXDEV on some paths and not others -- so both
#: are checked rather than trusting one.
_CROSS_DEVICE = {errno.EXDEV}
_CROSS_DEVICE_WINERROR = 17


def _is_cross_device(exc: OSError) -> bool:
    return exc.errno in _CROSS_DEVICE or getattr(exc, "winerror", None) == (
        _CROSS_DEVICE_WINERROR
    )


def move_file(source: str, destination: str) -> str:
    """Move one file to another place inside the roots.

    A rename first, and a copy only when the filesystem refuses one. Within a
    root -- which is where nearly every move happens, because "put this in that
    folder" means a folder you already configured -- a rename touches only the
    directory entries. It does not read or write a single byte of the file, so
    it costs the same for a 2 KB note and a 2 GB recording, and it is atomic:
    there is no instant where the file exists twice or not at all.

    The copy path still exists because the roots can sit on different drives --
    C: and E: in the configuration this was built against -- and no filesystem
    can rename across that boundary. That case pays what it has to.

    `os.replace` rather than `os.rename`, because rename's behaviour when the
    destination exists differs by platform: POSIX overwrites, Windows raises.
    `replace` overwrites on both, which is the one behaviour this needs -- and
    by the time it runs, the runtime has already asked the user about that
    destination.
    """
    prepared = _prepare(source, destination, "move")
    if isinstance(prepared, str):
        return prepared
    src, dst = prepared

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"error: could not move {source} ({exc.strerror or exc})"

    try:
        os.replace(src, dst)
    except OSError as exc:
        if not _is_cross_device(exc):
            return f"error: could not move {source} ({exc.strerror or exc})"
        return _move_across_devices(src, dst, source)
    return f"moved {src} to {dst}"


def _move_across_devices(src: Path, dst: Path, source: str) -> str:
    """The slow path: different drives, so the bytes really do have to travel."""
    try:
        shutil.copy2(src, dst)
    except OSError as exc:
        return f"error: could not move {source} ({exc.strerror or exc})"
    try:
        src.unlink()
    except OSError as exc:
        # The copy landed, so say where it is. Reporting a clean failure here
        # would leave the user believing nothing happened, with two files.
        return (
            f"error: copied {src} to {dst} but could not remove the original "
            f"({exc.strerror or exc}). Both copies exist."
        )
    return f"moved {src} to {dst}"


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
            "there. Only paths inside the configured folders are allowed. "
            "Replacing an existing file asks the user first; creating a new "
            "one does not. If the answer says they declined, that is their "
            "decision -- do not try again and do not write it elsewhere."
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
        name="delete_file",
        description=(
            "Delete one file on the user's machine, permanently. Only paths "
            "inside the configured folders are allowed, folders themselves are "
            "refused, and the user is always asked before it happens."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path to the file"}
            },
            "required": ["path"],
        },
        fn=delete_file,
        provenance="private",
    )
)

register(
    Tool(
        name="copy_file",
        description=(
            "Copy a file. Both paths must be inside the configured folders. "
            "A destination folder means 'into it, same name'; otherwise it is "
            "the new full path. Replacing an existing file asks the user."
        ),
        parameters={
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Full path of the file"},
                "destination": {
                    "type": "string",
                    "description": "New full path, or a folder to put it in",
                },
            },
            "required": ["source", "destination"],
        },
        fn=copy_file,
        provenance="private",
    )
)

register(
    Tool(
        name="move_file",
        description=(
            "Move or rename a file. Both paths must be inside the configured "
            "folders. A destination folder means 'into it, same name'; "
            "otherwise it is the new full path. Replacing asks the user."
        ),
        parameters={
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Full path of the file"},
                "destination": {
                    "type": "string",
                    "description": "New full path, or a folder to put it in",
                },
            },
            "required": ["source", "destination"],
        },
        fn=move_file,
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
