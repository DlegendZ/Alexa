# Architecture notes — deviations found while building

Running list of places where the build departed from `doc/sunday_architecture.html`,
or filled in something the document left open. Nothing here is applied to the HTML
yet — this is the queue for that update.

Each entry: what the doc says, what the code does, why.

---

## 1. `config.toml` is searched at the repo root first

**Doc:** config lives at `%LOCALAPPDATA%\Sunday\config.toml`.
**Code:** repo root `config.toml` wins if present, then `%LOCALAPPDATA%\Sunday\config.toml`.
`SUNDAY_HOME` overrides the data directory entirely.
**Why:** development needs a config next to the source that is not the installed one.
`config.toml` is gitignored; `config.example.toml` is committed.

## 2. New key `[models] summariser`

**Doc:** Stage 05 names `deepseek-v4-flash` as the airlock summariser in prose, but
`config.toml` has no key for it.
**Code:** `[models] summariser = "deepseek-v4-flash"`.
**Why:** the one model that is not local should be nameable without editing source.

## 3. New key `[limits] tool_calls`

**Doc:** Stage 06 states a hard cap of 5, but the cap is not in `config.toml`.
**Code:** `[limits] tool_calls = 5`.
**Why:** it is the single counter the whole bounded-loop argument rests on; it belongs
where the other thresholds are.

## 4. `thinking_budget` is a reservation, not an API cap

**Doc:** `[models] thinking_budget = 1024`, described as headroom.
**Code:** Ollama's API takes `think: true | false` and has no token budget parameter,
so the number is enforced only as the Stage 02 context reservation, never passed to
the model.
**Why:** the provider does not expose the knob. The slice accounting still holds.

## 5. Tool-selection rounds cap their own output

**Doc:** silent on this.
**Code:** rounds inside the tool loop run with `num_predict = 384`. Their prose is
discarded — only the tool calls matter — and `compose_reply` writes the reply you see.
**Why:** without it, chitchat generates a full reply twice: once thrown away in the
loop, once streamed. This costs a fraction of a second on a 2b instead of a full
generation.

## 6. Adapters sit outside the graph

**Doc:** the node-contract table lists `input adapter` and `output adapter` alongside
the graph nodes.
**Code:** the LangGraph graph is `memory_read → agent → compose_reply → memory_write`.
The adapters are the client's job (terminal client, later the sidecar), matching
Stage 01's own line that speech is converted before the graph and back after it.
**Why:** keeps one graph shared by the text and voice clients, with no modality branch
inside it.

## 7. Per-turn scratch lives in the runtime, not in state

**Doc:** `SundayState` deliberately carries no `messages` list.
**Code:** honoured — the turn's message list, the notice list and the turn log live in
a `TurnContext` owned by the runtime for the duration of one turn.
**Why:** stated here only because it is the thing a reader would expect to find in
state and will not.

## 8. `get_asset_price` accepts spoken names, not only symbols

**Doc:** the symbol is validated against a closed set; the word→symbol mapping is the
fast path's job.
**Code:** the tool also resolves `gold`, `silver`, `bitcoin`, … to symbols before
validating.
**Why:** the closed set is still enforced, and a 2b that answers `"gold"` instead of
`"XAU"` gets a price rather than an error. The fast path still exists for the same
phrasings.

## 9. The path refusal string tells the model what to say

**Doc:** `refused: path is outside the configured roots`.
**Code:** that sentence plus an instruction to relay it and to offer the config fix,
and not to guess at another reason.
**Why:** tested live, the 2b took the bare string and invented an explanation
("`C:` isn't currently mounted"). Tool results are the only place to correct that —
the model reads the last thing it was handed.

## 10. The intent hint is written by a context that has seen the private half

**Doc:** Stage 05 — the airlock's fresh context contains "the user's own words for
this turn, any `public` results already gathered, and the intent hint", and calls the
result a structural guarantee.
**Code:** implemented exactly as written, and the guarantee holds for everything
except the hint itself. `ask_external("...")` is authored by the agent, which by then
may have a private file in scope. Nothing stops a model from writing the sell target
into its own intent string.
**Mitigations in place:** the composer is told to use only what it is given and to
write a keyword query; the composed query is scrubbed for key shapes and capped at
200 characters; when the composer returns nothing, the fallback is the *user's* words,
never the intent.
**Still open, and worth a decision:** whether to scrub the intent before it enters the
airlock's context, or to drop the hint entirely and compose from the user's words plus
public results alone. Dropping it costs steering quality on multi-part questions.
This is the one place where the document's "cannot happen" is really "is very
unlikely to happen".

## 11. Two redaction notices, not one

**Doc:** one notice — "I removed something that looked like a credential before
searching."
**Code:** that text when a *query* was scrubbed, and a second one when a *tool result*
was scrubbed ("Something in what I read looked like a credential…").
**Why:** live, a `.env` read redacted a key and the user was told it had been removed
"before searching" — nothing had been searched. A privacy notice that misdescribes
what happened is worse than none.

## 12. Withdrawing the door is announced, not silent

**Doc:** once a turn is tainted, `ask_external` is unbound for the next round.
**Code:** unbinding still happens, and the model is additionally handed one system
line saying the tool was withdrawn and to say so if the user wanted a lookup; the user
gets the `NOTICE_BLOCKED` line whenever a turn is tainted, not only when a call was
refused.
**Why:** live, asking it to read `.env` *and* search produced a confident answer with
no search and no mention that the search never happened. Unbinding is invisible from
the outside unless something says it out loud.

## 13. Token counting is an estimate, deliberately

**Doc:** the five slices are stated in tokens.
**Code:** `budget.count` estimates at 3.6 characters per token rather than running a
real tokeniser.
**Why:** every accurate option (tiktoken, a HF tokeniser) downloads a vocabulary on
first use. An assistant whose whole premise is that it works offline should not have a
network dependency in its context accounting. The estimate errs high, so the budget is
conservative. If it turns out to matter, Ollama could be asked to count.

## 14. Chroma is opened in cosine space

**Doc:** `distance_cutoff = 0.45`.
**Code:** the collection is created with `hnsw:space = "cosine"`.
**Why:** Chroma's default is squared L2, on which 0.45 is a much stricter and less
interpretable threshold. In cosine space the number means what a reader assumes:
0 identical, 1 unrelated.

## 15. Retrieval de-duplicates against the recent block

**Doc:** silent on it.
**Code:** a retrieved document already present in the session's recent turns is
dropped rather than shown twice.
**Why:** the same turn is in both stores by design (write-through), so without this the
current session's last few turns appear twice in the window.

## 16. The memory context tells the model what it is reading

**Doc:** silent on the framing.
**Code:** the retrieved block is introduced as a record, saying which lines are the
user and which are Sunday's own past replies, and instructing it not to reuse the
wording.
**Why:** live, the 2b answered "who is my landlord" by copying its own stored reply
back, first person and all: "My landlord's name is Pak Yusuf". With the framing it
answers in the second person, correctly.
