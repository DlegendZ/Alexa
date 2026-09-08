"""How every model in this package is loaded, in one place.

Four graphs, four sessions, one set of options -- and the options matter more
than they look. onnxruntime's intra-op pool busy-waits between operators by
default, so a session that is merely *resident* holds cores hot for the length
of every call. On the machine this was built for that is twenty of them, and
the thing that loses the contest is the audio callback: it reports
`input overflow` and drops microphone frames, in the middle of a reply.

Turning spinning off costs nothing and is measurably faster on Kokoro -- 867 ms
against 968 for the same sentence -- because the pool stops fighting itself.
Every session here sets it. Synthesis is also the only one allowed the whole
machine; the three small graphs run on one thread each, because starting a pool
for a graph that takes a millisecond costs more in scheduling than it saves.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: The one option that is not a performance preference but a correctness one:
#: without it, listening and speaking cannot happen at the same time.
_NO_SPIN = ("session.intra_op.allow_spinning", "0")


def options(*, threads: int | None = 1) -> Any:
    """The session options every model here is loaded with.

    Separate from `session` because two of the four models are built by a
    library rather than by us, and both of those take a `SessionOptions`. The
    spinning setting has to reach them too, or the two largest graphs are
    exactly the ones still holding the cores hot.
    """
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.inter_op_num_threads = 1
    if threads is not None:
        opts.intra_op_num_threads = threads
    opts.add_session_config_entry(*_NO_SPIN)
    return opts


def session(path: Path | str, *, threads: int | None = 1) -> Any:
    """One CPU session. `threads=None` means as many as the machine has."""
    import onnxruntime as ort

    return ort.InferenceSession(
        str(path), options(threads=threads), providers=["CPUExecutionProvider"]
    )


__all__ = ["options", "session"]
