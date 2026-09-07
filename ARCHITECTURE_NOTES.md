# Architecture notes — deviations found while building

Running list of places where the build departed from `doc/sunday_architecture.html`,
or filled in something the document left open.

**Entries 1–21 have been applied to the HTML.** They are kept here as the record of
why each passage in that document reads the way it does — the HTML states the
decisions, this file states what they replaced. Add new entries below as they come up,
and apply them in a batch rather than editing the HTML mid-build.

Two of the applied entries are decisions, not fixes, and remain open in the document's
own Open decisions list: **#10**, the intent hint the agent writes, and **#21**, the
2b's prose. Applying them meant writing the limitation down, not removing it.

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

## 17. Abbreviations are a list, not a length heuristic

**Doc:** rule 2 of the splitter — "a short capitalised word before the dot" is a
non-break.
**Code:** an explicit abbreviation set (`Mr`, `Dr`, `Fig`, `No`, `e.g`, …) plus the
single-initial case (`J. Smith`).
**Why:** implemented as written first, and it ate "Sure." and "Yes." — four-letter
capitalised words that are exactly the short openers rule 1 exists to release early.
The heuristic and the goal were in direct conflict.

## 18. The forced comma split looks backwards from the limit

**Doc:** "Force a split at a comma once the buffer passes 180 characters."
**Code:** the last comma *at or before* 180, falling back to the first one after it.
**Why:** read literally, the search starts at 180, so a sentence whose only comma is
at 140 never splits at all — the case the rule exists for.

## 19. A recalled turn hands over the question, not the answer

**This is the largest deviation so far, and it fixes a correctness bug.**

**Doc:** Stage 08 stores a turn document; Stage 02 retrieves it and puts it in the
window.
**Code:** storage is unchanged — the full turn still goes to Chroma. What changed is
rendering: a recalled *turn* is shown to the model as the user's line plus the tools
that ran (`(answered using: get_asset_price)`), not as Sunday's past reply. A recalled
*session summary* still renders whole, because the fold prompt already wrote it as
third-person notes rather than as a reply to copy. Turns in the current session's
recent block are untouched — conversational continuity needs them.
**Why:** measured, not guessed. Asked the silver price with an empty store, the reply
was one correct sentence. Asked again with that turn in memory, the reply repeated the
first one's invented trend ("it has been climbing steadily on recent trading days")
with a fresh price pasted in. A 2b treats its own retrieved prose as the template for
the new answer, so a hallucination gets stored and re-served as a fact — and compounds
every time the question is asked. Three separate prompt phrasings failed to stop it;
one that was strong enough made it distrust the record entirely and refuse a fact the
user had actually stated. Rendering is the only layer where this is fixable.
**Cost:** a fact that exists only in Sunday's past reply, and nowhere in the user's
words or a re-runnable tool, is no longer recalled. That is the right trade: facts the
user stated are in *their* lines, and facts from tools can be fetched again.

## 20. Re-asking a question does not recall the old asking of it

**Code:** a recalled turn whose rendered question matches the one just asked is
dropped.
**Why:** since #19, a turn renders as its question — so asking "how much is silver"
twice retrieves a line reading "how much is silver" and nothing else. Noise with no
fact in it, spending the retrieved slice.

## 21. Open: 2b prose quality, with a measurement

**Doc:** listed as an open decision — "the thing most likely to disappoint. If replies
read flat, the fix is a larger single model."
**Observed:** two habits survive every prompt fix tried.
1. **Person slips.** Recalling a fact the user stated, it answers "My landlord is Pak
   Yusuf" rather than "your landlord", perhaps one time in two.
2. **Decoration.** It adds plausible context nothing gave it — a wind direction the
   weather tool did not return, "silver often spikes during geopolitical tension".
   Tightened prompt wording ("report what the tool returned and stop there") reduced
   this but did not end it.
Neither is a privacy failure and neither survives into the airlock. Both are exactly
the trade the document names. `qwen3:8b` is already pulled on this machine, so the
comparison is cheap to run — but it needs the iGPU move first, and that is a decision
to take deliberately rather than mid-build.

---

## 22. The v2 write-confirmation decision was never carried into v3

**Status: RESOLVED — option 2 chosen and built. Applied to the HTML.**

**Doc:** `doc/sunday_context.md` section 7, marked LOCKED — "Read = auto-execute.
Write/create/modify/delete = always confirm with user first, no exceptions", implemented as
a LangGraph interrupt. The architecture HTML does not mention confirmation at all; every
occurrence of "interrupt" in it refers to voice barge-in.
**Code:** `tools/files.py:write_file` writes immediately. It is sandboxed to the configured
roots and refuses to overwrite a credential path, but inside those roots the model can
overwrite one of the user's files on its own judgement with no prompt.
**Why it is here:** this was not argued down during the v2→v3 redesign — it fell out
silently. Found while updating the context log, not by testing. A locked decision that
disappears without a counter-argument is exactly what a design log exists to catch.

Options, with the recommendation:

1. Reinstate in full — confirm before every write. A round trip, and awkward in voice.
2. **(recommended)** Confirm only when overwriting an existing file; creating a new one
   passes through. Protects the irreversible case, leaves the common one fluid.
3. Drop it deliberately — decide the sandbox plus credential refusal is enough, and strike
   section 7 rather than leaving a locked decision unmet.

**Chosen: option 2.** Overwriting an existing file asks; creating one does not. See note 31
for how it is built.

---

*Entries 23–29 come from the review in `CODE_REVIEW.md`. Not yet applied to the HTML.*

## 23. The window reserves what no slice pays for

**Doc:** Stage 02 — five slices summing to the 8192-token window.
**Code:** `[models] overhead_tokens = 1200` and `reply_tokens = 768` come off the top, and
the five slices are scaled into what remains, keeping the configured ratios. The default
now resolves to 778/2334/778/1556/778 = 6224, plus 1968 reserved, exactly 8192.
**Why:** the slices were never the only claimants. The system prompt (270), the bound tool
schemas (695) and the memory framing block (116) cost about 1080 tokens that no slice paid
for, and the reply had no reservation at all — so a full window asked Ollama for roughly
9300 against `num_ctx = 8192`, and Ollama drops the oldest messages without saying so.
Stage 02's claim is that overflow is "arithmetic you can check"; it now balances.

## 24. The hop cap refuses instead of tallying

**Doc:** Stage 06 — `hops ≤ 2`.
**Code:** `_ask_external` checks `flags.hops` before composing and refuses once the cap is
spent, the way `tool_calls` does.
**Why:** `hops` accumulated and nothing read it. Three `ask_external` calls in one model
response produced `hops: 6` against `max_hops: 2` — with `tool_calls = 5` the real bound
on outbound requests was five searches and five fetches.

## 25. `secret` is assigned, and only on a read that happened

**Doc:** Stage 03 — `secret`: anything read from a credential path.
**Code:** `_dispatch` relabels the result `secret` when `is_secret_path` matches *and* the
call succeeded.
**Why:** nothing in `src/` ever produced `"secret"` — it was read in three places and
written in none, so a `.env` read was filed in Chroma and logged as `private`. No leak
(the airlock excludes both alike), but the audit record was wrong. The `result.ok`
condition is new too: a refused path returned nothing, so tainting on it let the model
shut its own door for the turn by naming `~/.ssh/id_rsa`.

## 26. One fetch, and the attempt is the hop

**Doc:** Stage 05 — "one search, at most one page fetch".
**Code:** `web.gather` tries the first hit only, counts the attempt whether or not it
succeeded, and falls through to the snippets on failure.
**Why:** the loop ran over two hits, so a failing first URL bought a second fetch. With
`net.request` retrying once behind a 1 s sleep, that is up to 42 s before the turn can
answer — Stage 01 calls that gap the thing streaming cannot fix, and 40 s of it reads as a
hang. The returned `hops` also under-reported, since it added 1 regardless of attempts.

## 27. Truncated credentials still redact

**Doc:** Stage 05 lists `-----BEGIN … PRIVATE KEY-----` as a shape.
**Code:** a second pattern matches `BEGIN` plus everything after it, for the case where
`END` never arrives.
**Why:** `read_file` truncates at `max_read_bytes` *before* the guardrail runs, so a key
straddling the cap reached the model as plain base64. The same will hold for mail bodies
at milestone 13. The BEGIN line is unambiguous, so this costs no false positives — the bar
Stage 05 sets.

## 28. The credential and shape lists are longer

**Doc:** the two lists in `config.toml`.
**Code:** `secret_paths` gains `.env.*` (the one that actually happens), `*.pfx`, `*.p12`,
`id_ed25519`, `.gnupg/`, `token.json`, `service-account*.json`, `secrets.*`, `.npmrc`,
`.pypirc`, `.netrc`. `key_shapes` gains `sk_live_`, `gho_`, `glpat-`, `ASIA`, `xoxp-`,
`xapp-`, `AIza`, `ya29.`, `hf_`, `dop_v1_`.
**Why:** every addition is a fixed prefix or a filename, so none of them costs the
false-positive tax Stage 05 rejects entropy scoring over. Tested against invoice numbers,
git hashes and UUIDs.

## 29. Refusals where there used to be silence

**Doc:** silent on all three.
**Code:** three new refusal scripts, each written for the model to relay.
- `[external] enabled = false` now sets `blocked`, notices, and puts a system line in the
  turn — the same treatment taint gets, which it previously did not have. A user who
  switched the web off was answered confidently out of the 2b's own head.
- Calls skipped by the `tool_calls` cap get a synthetic refusal each, so every tool call
  the assistant message declares has an answer. Breaking outright left the chat template
  holding unanswered calls.
- A result clipped to no remaining room returns a refusal instead of a bare `[truncated]`
  marker with `ok=True`, which read as a successful call that returned nothing.

## 30. `saw_private` is audit, not a mechanism

**Doc:** state schema — `saw_private  # drives the airlock, not the door`.
**Code:** comment corrected and the field written to the JSONL line. The airlock filters
by `Result.provenance` through `AIRLOCK_VISIBLE`, which is the stronger mechanism.
**Why:** nothing read the field. A state field nothing reads is a claim the code is not
making — either the comment was wrong or a consumer was missing, and it was the comment.

## 31. Confirmation is a sink, not a graph interrupt

**Doc (v2):** a LangGraph interrupt — the graph pauses, state is saved, the next user
message resumes it.
**Code:** a `ConfirmSink` passed into `run_turn`, called synchronously from `_dispatch`
immediately before `write_file`, alongside the door checks.
**Why:** an interrupt suspends the whole graph and resumes it as a new invocation, which
means the turn's message list, tool results and streaming sinks all have to survive a
round trip through checkpoint storage. A blocking callback gets the same guarantee — the
write does not happen until a person says so — without any of that. It also works
identically for both clients, which an interrupt would not: the terminal prompts on stdin,
the sidecar sends `confirm` and waits for `confirm_response`.

**The rules, as built:**
- Creating a file passes through. Nothing is lost, and a prompt on every new note is one
  you learn to click past — which would cost the prompt its meaning when it matters.
- Overwriting an existing file inside the roots asks, and names the file and its size.
- No confirm sink means no consent: the write is refused with a script telling the user the
  file was left alone. Silence is not a yes, and a background session cannot approve.
- The sandbox still answers first. A path outside the roots, or a credential file, is
  refused before anything is asked -- confirmation widens what the user may allow, never
  what the sandbox allows.
- A cancelled turn releases any question still waiting, refusing, so barge-in cannot leave
  a write pending on a 120-second timeout.

**Protocol addition:** `confirm` out (`{id, text, path}`), `confirm_response` in
(`{id, approved}`). `web/debug.html` renders it as two buttons, focused on "Keep it".

---

*Entries 32–35 come from round 2 of the review. Applied to the HTML.*

## 32. The sidecar runs the turn beside the read loop, not inside it

**Doc:** Desktop 01 lists `cancel` as the stop button and, since note 31, `confirm` as a
message expecting an answer.
**Code:** `text_input` now spawns the turn as a task; the connection keeps reading.
**Why — this is the one that mattered.** `_run_turn` was awaited inside the client's own
`async for`, so that connection processed nothing further until the turn ended. With one
client — the shipped case until the Tauri shell exists — every mid-turn message was
unreachable:

- **the confirmation could not be answered.** The reply sat unread in the socket buffer
  while the worker thread waited out `CONFIRM_TIMEOUT_S` and then refused. In production
  that is a two-minute hang on every overwrite, ending in a refusal, which reads as the app
  being broken. Reproduced: approval sent, `approval honoured? False`.
- **`cancel` was wired and unreachable.** It was read only after the turn it was cancelling
  had committed. Milestone 9's barge-in worked in the runtime and the terminal and had
  never worked over the socket.

The `_turn_lock` guard already handled a second input arriving mid-turn, so one-turn-at-a-
time still holds. Three socket-level tests now drive a confirm, a cancel and a ping through
a real WebSocket; against the old code they take 123 s and fail, against the new one 3 s
and pass.

## 33. One owner for the confirmation prompt

**Doc:** note 31 — `confirm` out, `confirm_response` in.
**Code:** the sidecar's `confirm()` is the only producer of a `confirm` message, and the
runtime hands the sink a `ConfirmRequest` (question, path, action) instead of also emitting
an event.
**Why:** both layers emitted `type: "confirm"` with different shapes, so a client rendered
two prompt cards. The runtime's came first and had no `id`; clicking it sent a response the
sidecar resolved to `None` and dropped without a word. The user clicked Overwrite, nothing
happened, and the second card was the real one. A prompt that cannot be answered is worse
than no prompt.

## 34. Key shapes need a left edge, and two needed their real shape

**Doc:** Stage 05 — prefix plus length, no entropy scoring, no false-positive tax.
**Code:** every shape is anchored with `(?<![A-Za-z0-9_])`, and `AKIA`, `ASIA`, `hf_` and
`AIza` carry their exact tails rather than a generic run.
**Why:** the claim in note 28 that the additions carry no false-positive tax was wrong, and
the original list was wrong too.

- `ASIA` is an English word where `AKIA` is not, so `ASIA-PACIFIC-2024` redacted.
- `hf_` collides with snake_case: `hf_dataset_loader.py` redacted.
- Worse, and not in the review: with no left edge a prefix matched *mid-word*. `sk-` turned
  "a ta|sk-oriented approach" into "a ta[redacted] approach", and "di|sk-image-backup" into
  "di[redacted]". Ordinary English, silently blanked, with the credential notice fired over
  it — the exact daily tax Stage 05 rejects entropy scoring to avoid.

The old test passed because its sample had no capitalised word and no snake_case identifier.

## 35. The hop cap counts the hop, not the entry

**Doc:** Stage 05 "one search, at most one page fetch"; Stage 06 `hops ≤ 2`.
**Code:** the remaining turn budget is passed into `web.run` and `gather` skips the fetch
when only one hop is left.
**Why:** note 24 made the cap refuse, which fixed six hops down to three but not to two.
The check gated *entry*: a lookup whose snippets sufficed spent one hop and left the counter
at 1, which passes `1 >= 2`, so the next lookup searched *and* fetched and the turn ended at
three. Gating entry cannot bound a step that costs more than one.
