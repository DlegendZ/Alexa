"""Voice in, voice out -- everything that turns air into a string, and back.

Speech never enters the graph. This package ends at a `str`, which is handed to
`Runtime.run_turn(..., modality="voice")` exactly as if it had been typed. That
is the whole seam: the text client and the voice client share one memory, one
agent and one set of bugs, because only one of them exists past this line.

Four pieces, in the order a turn meets them:

| piece | model | job |
| --- | --- | --- |
| capture | -- | 16 kHz mono, 20 ms frames, into a ring buffer |
| wake word | openWakeWord, ~5 MB | listens forever on the CPU for one phrase |
| VAD | Silero, ~2 MB | where speech starts and stops; later, barge-in |
| STT | Moonshine, 0.5 GB | one closed clip becomes one line of text |

Nothing here imports its model at module level. The wrappers hold a lazy handle
so the whole package can be imported -- and most of it tested -- on a machine
with no sound card, no ONNX runtime and no models on disk. `Listener` is the
part with the logic in it, and it is a pure state machine over frames.
"""

from __future__ import annotations

from sunday.audio.listener import Clip, Listener, VoiceEvent
from sunday.audio.models import ModelMissing, model_path

__all__ = ["Clip", "Listener", "ModelMissing", "VoiceEvent", "model_path"]
