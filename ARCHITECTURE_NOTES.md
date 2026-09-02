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
