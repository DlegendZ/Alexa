# Code review — round 2

Reviewed `1afa016` and `8d672d2` against `doc/sunday_architecture.html`,
`doc/sunday_context.md` and `CLAUDE.md`. 157 tests pass.

Nineteen of the twenty round-1 findings are closed, and the write-confirmation
ruling is implemented as option 2. The closed list is at the bottom.

Five things remain. Two of them are the same bug, and it is the one that matters:
**the confirmation you just built cannot be answered over the socket.** Both were
reproduced against a running sidecar with a real WebSocket client.

---

## 1. The sidecar reads nothing while a turn is running

`server.py:131` — **reproduced twice**

```python
async for raw in websocket:
    await self._on_message(websocket, raw)   # -> _run_turn, awaited to completion
```

`_run_turn` is awaited inside the client's own read loop, so that connection
processes no further messages until the turn ends. Every mid-turn message from the
only attached client is therefore unreachable — and the shipped case is one client,
until the Tauri shell exists.

**Confirmation times out and refuses.** One client, `text_input` that overwrites an
existing file, answered `approved: true` on the same socket:

```
confirm asked at 0.0s
answered YES on the same socket
done at 6.0s          (the patched CONFIRM_TIMEOUT_S; 120 s in production)
file on disk: 'original'
approval honoured? False
```

The turn sat in `pending.event.wait(CONFIRM_TIMEOUT_S)` on the worker thread while
the answer sat unread in the socket buffer. `server.py:33` says the timeout decides
*"no, because silence is not consent"* — correct policy, but this is not silence.
In production every overwrite hangs for two minutes and then refuses, which reads
as the app being broken.

**Cancel is ignored, and the turn commits.** Same shape, sent 0.7 s into a ~4 s
reply:

```
sent cancel at 0.7s
done at 4.0s committed=True
cancel honoured? False
```

That one is pre-existing, not from this round — I missed it in round 1 by reading
`server.py` instead of exercising it. But it means milestone 10's *"a plain browser
page can drive a whole turn"* is true only for turns that need nothing from the user
mid-flight, and the protocol's `cancel` (*"stop button / barge-in from UI"*) is
wired and unreachable. Milestone 9's cancellation works in the runtime and in the
terminal client; it does not work over the socket.

Fix: do not await the turn inside the read loop.

```python
elif kind == "text_input":
    ...
    asyncio.create_task(self._run_turn(text, modality="text"))
```

The `_turn_lock.locked()` guard at `server.py:203` already handles a second input
arriving mid-turn, so one-turn-at-a-time still holds. Worth a test that drives a
confirm and a cancel through an actual socket — the existing confirm tests all call
`Runtime.run_turn` directly, which is why this layer had no cover.

## 2. Two `confirm` events per question, and the first one does nothing

`runtime.py:394` and `server.py:216` — **reproduced**

Both layers emit `type: "confirm"`, with different shapes:

```
confirm event keys: ['path', 'text', 'type']    <- runtime._emit, no id
confirm event keys: ['id', 'text', 'type']      <- server.confirm()
```

The runtime's `_emit(type="confirm", …)` reaches the client through `on_event=push`
before `_on_confirm` is ever called, so `web/debug.html:219` renders two identical
prompt cards. Clicking the first sends `confirm_response` with no `id`;
`_on_message` resolves `self._pending.get("")` to `None` and drops it without a
word. The user clicks Overwrite, nothing happens, and the second card is the real
one.

`test_the_confirm_event_reaches_the_client` asserts exactly one confirm event and
passes, because at the runtime layer there is exactly one.

Fix: pick one owner. Either drop the `_emit` from `_confirm_overwrite` and let the
sidecar's `confirm()` be the only source, or have `_emit` carry the id and let the
sidecar reuse it. The `path` field is worth keeping wherever it lands.

## 3. `ASIA` and `hf_` redact ordinary text

`config.py:129` — **reproduced**

```
REDACTED Our [redacted] revenue report is in the shared drive.      <- ASIA-PACIFIC-2024
REDACTED The [redacted] spreadsheet has the numbers.                <- ASIA_REGION_SUMMARY
REDACTED See [redacted] for the loader.                             <- hf_dataset_loader.py
```

`_SHAPE_TAIL` is `[A-Za-z0-9_\-\./+=]{8,}`, so any capitalised `ASIA` followed by
eight token characters matches — and `ASIA` is an English word, which `AKIA` is
not. `hf_` collides with ordinary snake_case.

The consequence is not a hang, it is worse: reading a business document about
Asia-Pacific silently blanks the text *and* fires *"something in what I read looked
like a credential"*. The build's own note says a privacy notice that misdescribes
what happened teaches the user to discount the next one, and Stage 05's entire
argument for rejecting entropy scoring is avoiding this daily false-positive tax.
`config.example.toml:73` now claims these prefixes *"carry no false-positive tax"*,
and two of them do.

`test_the_additions_carry_no_false_positive_tax` passes because its sample —
invoice numbers, a commit hash, a UUID, `AI12345` — contains no capitalised word
and no snake_case identifier.

Fix: give those two their real shapes rather than a bare prefix. An AWS temporary
key is `ASIA` plus 16 base32 characters; a Hugging Face token is `hf_` plus 30-plus
alphanumerics. Both are then unambiguous, which is the bar the comment claims.

## 4. The hop cap is still reachable at three

`runtime.py:439` — **reproduced**

```
hops: 3 | max_hops: 2 | blocked: True
within cap? False
```

Down from 6, so the fix did most of the work. What is left is that the check gates
*entry* rather than the hop:

```python
if flags.hops >= self.cfg.external.max_hops:
```

A lookup whose snippets were enough spends one hop and leaves the counter at 1,
which passes `1 >= 2`; the next lookup searches and fetches, and the turn ends at 3.
Stage 05 says two, and Stage 06's table says `hops ≤ 2`.

Fix: reserve the worst case — refuse when `flags.hops + cfg.max_hops > cfg.max_hops`
would overrun, i.e. pass the remaining budget into `web.run` and let `gather` skip
the fetch when only one hop is left.

## 5. The sidecar's fixed docstring is now a syntax warning

`sidecar.py:3`

```
SyntaxError: invalid escape sequence '\S'
```

(under `-W error`; a `SyntaxWarning` on stderr otherwise, on first compile of the
module — which is the sidecar's entry point, so it prints at launch.)

`.venv\Scripts\sunday-sidecar.exe` in a plain docstring makes `\S` an unrecognised
escape. Right command, wrong quoting. Make the docstring raw (`r"""`), or use
forward slashes.

---

## Round 1, closed

| # | Finding | State |
| --- | --- | --- |
| 1 | Query-scrub notice unreachable | fixed — `compose` returns `Cleared`, scrub happens once, notice fires |
| 2 | Truncated PEM not redacted | fixed — `_PEM_UNTERMINATED`, ordered after the terminated form |
| 3 | `external.enabled = false` silent | fixed — `DOOR_OFF_SYSTEM` line every turn, `DOOR_OFF` + `blocked` + notice when reached |
| 4 | Hop cap not enforced | mostly — refuses now, but see 4 above |
| 5 | Two fetches, ~40 s worst case | fixed — one attempt, counted whether or not it succeeds |
| 6 | `provenance="secret"` never set | fixed — set on a successful credential read, and now in the log line |
| 7 | Slices consumed the whole window | fixed — `overhead_tokens` + `reply_tokens` off the top, `scaled_to` keeps the ratios |
| 8 | Zero-room result became bare `[truncated]` | fixed — `_fit` returns `NO_ROOM` with `ok=False` below `MIN_RESULT_TOKENS` |
| 9 | Cap orphaned the rest of the batch | fixed — every skipped call gets a `CAP_SPENT` answer |
| 10 | `saw_private` and `Cleared` dead | fixed — comment corrected to audit-only and logged; `Cleared` now used |
| 11 | Write confirmation unimplemented | implemented as option 2, and correct in the runtime and the terminal client. Delivery over the socket is findings 1 and 2 |
| 12 | `secret_paths` gaps | fixed |
| 13 | `key_shapes` gaps | fixed, but two additions overreach — see 3 above |
| 14 | Byte cap applied after download | fixed — `_capped` streams and stops at the cap. Verified against a mock transport: small bodies, oversized bodies, 4xx, 5xx and the `max_bytes=None` path all behave |
| 15 | Stale splitter docstring | fixed |
| 16 | Bash-ism in the sidecar docstring | fixed, with a new warning — see 5 above |
| 17 | `KeyboardInterrupt` escaped `run_turn` | fixed — caught with `Cancelled`, so the turn still logs and tears down |
| 18 | A refused path could taint | fixed — taint requires `result.ok` |
| 19 | No guard on a non-cosine collection | fixed — `_check_space` warns |
| 20 | README fences tagged `bash` | fixed |

The load-bearing claims re-verified after the changes: the airlock still assembles
from `task` plus `visible_results` only, with no path from `context`; the door is
still re-checked per call; the sandbox still resolves before it checks; the tool
loop still terminates; and retrieval still hands back the question rather than
Sunday's prose.
