# Code review — Sunday against the spec

Reviewed: `src/sunday/**` and `tests/**` at `04e7d9f`, against
`doc/sunday_architecture.html` (the spec), `doc/sunday_context.md` (the design log)
and `CLAUDE.md`. Scope is milestones 1–6 and 10, which are the ones claimed built.

All 113 existing tests pass. Every finding below was reproduced rather than
inferred; the three probe tests that produced findings 1, 3 and 4 are noted where
they apply and are not committed.

Findings are ordered by consequence, not by how hard they are to fix.

---

## 1. The outgoing-query redaction notice can never fire

`airlock.py:66` and `runtime.py:331` — **reproduced**

`airlock.compose` already scrubs, and throws the count away:

```python
query, _ = guardrail.scrub_query(query)   # airlock.py:66
```

`_ask_external` then scrubs the *already-scrubbed* string and counts that:

```python
query, hits = guardrail.scrub_query(query)   # runtime.py:331
if hits:
    flags.redactions += hits
    self.ctx.events.notice(guardrail.NOTICE_REDACTED)
```

`[redacted]` contains no key shape, so `hits` is always 0. Measured with a key
surviving into the composed query:

```
composed query: gold price [redacted] news
redactions:     0
notices:        []
```

The key *is* stripped — the data boundary holds. What is lost is the telling.
`guardrail.NOTICE_REDACTED` is unreachable code, and `state["redactions"]` is
always 0 for an outgoing query, so the JSONL `redactions` field cannot be used to
tune anything on the airlock side.

Stage 05 spends a paragraph on why there must be two different sentences, and the
build's own note records that one sentence covering both taught the user to
discount the next notice. A notice that never appears is the same lesson again.

Fix: have `airlock.compose` return the count — the unused `airlock.Cleared`
dataclass at `airlock.py:24` was clearly meant for exactly this — or drop the scrub
from `compose` and leave it to the caller. Do not leave both.

## 2. A truncated private key is not redacted

`guardrail.py:29`

```python
_PEM = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
```

The pattern needs the `END` line. `read_file` truncates at `files.max_read_bytes`
*before* the guardrail runs (`files.py:78` → `runtime.py:287`), so a key whose
`END` falls past the cap is not matched and the base64 body reaches the model
intact:

```
whole file:      hits= 1
truncated file:  hits= 0 | leaks: '-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA7f8ndhs…'
```

The same holds for the spec's mail path (bodies truncated to 4000 characters) once
milestone 13 lands, and for any key pasted near the end of a large log.

Fix: add an unterminated fallback — match `-----BEGIN … KEY-----` plus the run of
base64 that follows it, whether or not `END` arrives. The prefix is unambiguous, so
this costs no false positives, which is the criterion Stage 05 sets.

## 3. `[external] enabled = false` is a silent door

`runtime.py:128` and `runtime.py:320` — **reproduced**

With web lookups switched off, `tools.available()` withholds `ask_external` and
`_ask_external` returns a refusal string. Neither path does anything else:

```
blocked:    False
unresolved: None
notices:    []
```

No notice, no `blocked` event, no `unresolved`, and no system line telling the model
its web half is gone. That is precisely the failure the build already found and
fixed for taint — *"withdrawing a tool tells the model nothing; an absent tool
leaves no trace to explain"* — left unfixed for the configuration path. A user who
turns the web off gets confidently answered out of the 2b's own head.

Fix: give it the same two additions taint gets. A system line whenever
`ask_external` is withheld for any reason, and a notice; and set
`blocked`/`unresolved` when the refusal branch at `runtime.py:320` is reached.

## 4. The hop cap is not enforced across calls

`runtime.py:336` — **reproduced**

`flags.hops` accumulates and nothing ever reads it. Three `ask_external` calls in
one model response:

```
hops: 6 | max_hops: 2
```

Stage 06's table says `hops ≤ 2`, and the argument for one counter is that *"it
lives in graph state, only ever increases, and no node can reset it"*. `tool_calls`
does that. `hops` does not — it is a tally, not a cap. With `tool_calls = 5` the
real bound on outbound requests is five searches and five fetches.

Fix: check `flags.hops >= cfg.external.max_hops` in `_ask_external` before
composing, and refuse with a readable string when it is reached.

## 5. `web.gather` can fetch two pages, and can take about 40 seconds

`web.py:127`

```python
for hit in hits[:2]:
    try:
        body = extract(fetch(hit.url))
    except (net.HttpError, Exception):
        continue          # <- second fetch
```

Spec: *"one search, at most one page fetch"*. The returned `hops` is `hops + 1`
regardless of how many fetches were attempted, so the log under-reports too.

Worse is the latency. `net.request` retries once after a 1 s sleep, so one `fetch`
is up to 2 × 10 s + 1 s, and two hits make that roughly 42 s before the turn can
answer — on top of the search. Stage 01 says the gap between the user stopping and
the first token is transcription plus tool time; 40 s of it reads as a hang.

Also: `except (net.HttpError, Exception)` is redundant — the second arm subsumes
the first — and the bare `Exception` swallows genuine bugs in `extract`.

Fix: attempt one fetch, count every attempt as a hop, and let a failed fetch fall
through to the snippets rather than trying the next URL.

## 6. `provenance="secret"` is never assigned

`runtime.py:281`

Reading a credential path sets `flags.tainted` but leaves the `Result` labelled
`private`. Nothing anywhere in `src/` produces `"secret"` — it is read in three
places and written in none. Consequences:

- the JSONL `provenance` array records `private` for a `.env` read, so the audit
  line cannot distinguish an ordinary file from a credential;
- `store.strongest()` (`memory/store.py:34`) can never return `secret`, so a turn
  that read `.env` is filed in Chroma as `private`;
- Stage 03's provenance table — *`secret`: anything read from a credential path* —
  is not implemented.

Not a leak: `AIRLOCK_VISIBLE` excludes `private` and `secret` alike, and the tests
prove the exclusion. But the label exists to make the record honest, and it does
not.

Fix: in `_dispatch`, when `is_secret_path` matches, `replace(result,
provenance="secret")` alongside setting the taint.

## 7. The five slices consume the whole window, leaving nothing for the prompt

`memory/budget.py:55`, `config.py:88`

```
slices:  summary=1024 recent=3072 retrieved=1024 tools=2048 thinking=1024
total:   8192
num_ctx: 8192
```

Measured overhead that no slice pays for:

| Claimant | Tokens |
| --- | --- |
| `prompts.SYSTEM` | 270 |
| tool schemas (5 tools) | 695 |
| memory framing block (`loop.py:28`) | 116 |
| **unbudgeted total** | **1081** |

Plus the reply itself, which has no reservation at all. A full window therefore asks
Ollama for roughly 9 300 tokens against `num_ctx = 8192`, and Ollama answers by
dropping the oldest messages without saying so. Stage 02's whole claim is that
*"overflow becomes arithmetic you can check instead of a silent truncation"* — the
arithmetic is checkable, and it does not balance.

Fix: reserve the fixed overhead plus a reply allowance, and size the slices against
`context_tokens` minus that, rather than to it.

## 8. A tool result clipped to zero room becomes only `[truncated]`

`runtime.py:220`

```python
room = max(0, tools_budget - spent)
if budget.count(result.content) > room:
    result = replace(result, content=budget.clip(result.content, room), truncated=True)
```

`budget.clip(text, 0)` returns `'\n[truncated]'`. So once the tools slice is spent,
every later result reaches the model as a marker with `ok=True` and no content —
a successful tool call that returned nothing. Given the 2b's documented habit of
decorating, that is an invitation to invent the answer.

Stage 04 says the marker exists *"so the model knows it is not seeing everything"*.
Seeing nothing is a different message.

Fix: floor the room at something usable, and when there is genuinely none, return a
readable refusal (`refused: no room left in the context for this result — tell the
user…`) with `ok=False`, in the style of the other refusal scripts.

## 9. The tool-call cap orphans the rest of the batch

`runtime.py:210`

`ctx.messages.append(reply.raw)` puts an assistant message declaring N tool calls
into the conversation; the `break` on the cap means only M ≤ N tool messages follow
it. `compose_reply` then streams from that message list.

`tests/test_guardrail.py:216` builds exactly this shape — four calls, cap two — and
passes, because `FakeAgent` never looks at the messages. Against real Ollama the
chat template is handed an assistant turn whose tool calls are partly unanswered.

Fix: append a synthetic `refused: the tool-call cap for this turn was reached`
result for each skipped call, so every declared call has an answer. It also gives
the model something to relay, which is what Stage 06 asks for.

## 10. `saw_private` is dead, and so is `Cleared`

`state.py:52` says `saw_private  # drives the airlock, not the door`. It is
computed in `memory_read` and in `agent_node` and read by nothing.
`airlock.Cleared` (`airlock.py:24`) is never constructed.

The airlock filters by `Result.provenance` through `AIRLOCK_VISIBLE`, which is the
stronger mechanism and the right one. So either the field's comment is wrong, or
something meant to consult it does not. Worth settling one way or the other — a
state field nothing reads is a claim the code is not making.

## 11. Write confirmation is still unimplemented

Already logged as `ARCHITECTURE_NOTES.md` note 22 and as *Unfinished business* in
`doc/sunday_context.md`. Restated only because it is still open and still the one
place where a LOCKED decision is unmet: `files.py:82` writes immediately, so inside
the roots the model can overwrite one of your files on its own judgement. The
recommendation on record is option 2 — confirm on overwrite, pass through on
create. It needs a ruling, not more analysis.

---

## Minor

| # | Where | What |
| --- | --- | --- |
| 12 | `config.py:107` | `.env.local`, `.env.production`, `token.json`, `service-account.json`, `secrets.yaml` and `.npmrc` are not on `secret_paths`; verified not tainting. The code matches the spec's list, so the spec has the same gap — `.env.local` is the one that will actually happen. |
| 13 | `config.py:120` | `key_shapes` misses prefixes that carry no false-positive tax: `glpat-`, `AIza`, `hf_`, `sk_live_`, `xapp-`, `dop_v1_`, `ya29.`. Verified: all pass through unredacted. Stage 05's own rationale — prefix plus length, no entropy — argues for adding them. |
| 14 | `net.py:59` | `fetch_max_bytes` is checked on `len(response.content)`, after the whole body is already in memory. The 2 MB cap rejects but does not cap. Needs a streaming read. |
| 15 | `stream.py:12` | Module docstring still states rule 2 as *"a short capitalised word before the dot"* — the heuristic the HTML and note 12 record as wrong. The code implements the explicit abbreviation list correctly; only the docstring is stale. |
| 16 | `sidecar.py:3` | Docstring says `PYTHONPATH=src python -m sunday.sidecar`. That is the bash-ism `CLAUDE.md` singles out, and the editable install removed the need for it. |
| 17 | `main.py:80` | `KeyboardInterrupt` escapes `run_turn`, which catches only `Cancelled` and `OllamaDown`, so `cancel()` is called after the fact and `_finish` never runs: no log line for the turn, no `done` event, and `_ctx` / `_on_token` / `_on_event` left set. |
| 18 | `runtime.py:281` | A path outside the roots still taints if it merely *looks* secret — the read was refused and nothing was returned. Fail-safe direction, but it lets the model shut its own door by naming `~/.ssh/id_rsa`. |
| 19 | `memory/store.py:93` | The live store is cosine (verified, 152 documents), so the 0.45 cutoff means what Stage 02 says. But `get_or_create_collection` silently ignores a conflicting `hnsw:space` on an existing collection, so pointing `SUNDAY_HOME` at a pre-cosine store would run squared L2 under a cosine threshold with no warning. |
| 20 | `README.md` | Command fences are tagged `bash` but hold PowerShell backslash paths, and the tests fence uses forward slashes while every other fence uses backslashes. |

## What the review did not find

Worth stating, because these are the load-bearing claims:

- **The airlock holds.** `compose_prompt` assembles from `state["task"]` and
  `visible_results` only, `AIRLOCK_VISIBLE` is `{user, public}`, and failed results
  are dropped by the `r.ok` filter — so a refusal string cannot cross either. There
  is no path from `context` (retrieved memory) into the composer.
- **Per-call re-checking is real.** `_dispatch` checks the door immediately before
  each call rather than at bind time, and both the in-loop check and the unbinding
  are present and tested.
- **The sandbox resolves before it checks.** `files.resolve` calls `.resolve()`
  first and tests containment on the result, case-insensitively for Windows, so
  `..` and symlinks cannot walk out.
- **The bounded loop terminates.** `tool_calls` only increases, no node resets it,
  and every iteration of the `while True` consumes at least one.
- **Retrieval does not hand back Sunday's prose.** `Recalled.render` splits on
  `\nSunday:` and keeps the question plus `tools_used`, and re-asked questions are
  dropped in `memory_read`. Notes 19 and 20 are implemented as written.
