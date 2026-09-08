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


def session(path: Path | str, *, threads: int | None = 1) -> Any:
    """One CPU session. `threads=None` means as many as the machine has."""
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    if threads is not None:
        options.intra_op_num_threads = threads
    options.add_session_config_entry(*_NO_SPIN)
    return ort.InferenceSession(
        str(path), options, providers=["CPUExecutionProvider"]
    )


__all__ = ["session"]
