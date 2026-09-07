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

## 36. Deleting is a tool, and unlike writing it always asks

**Doc:** Stage 04 — `read_file`, `write_file`, `list_dir`; "overwriting asks, creating does
not".
**Code:** `delete_file` joins them, and `runtime._confirm_delete` asks on every call. The
two confirmations share `runtime._ask`.
**Why:** the document's file-tool set had no way to remove a file, so asked to delete one
the model said it could not and told the user to open File Explorer. The confirmation rule
does not carry over unchanged, either. Overwriting asks only when there is something to
lose, because a confirmation on every new note is one you learn to click past. Deleting has
no such case: there is no arrangement of the arguments where it does not destroy something,
so the only calls that skip the question are the ones the sandbox or the deny overlay
refuses anyway. The refusal strings say what to tell the user, and it fails closed with
nobody attached, exactly as the write does.

## 37. The agent could not list its own tools, and did not know that

**Doc:** Stage 04 — the tool table.
**Code:** `sunday/tools/capabilities.py` registers `list_capabilities`, and a fast path in
`sunday/fastpaths.py` fires it before the model is asked anything.
**Why:** asked "what can you do", the 2b did not read its bound schemas back. It wrote a
confident paragraph about a marketplace database, an email drafter and a grammar checker,
none of which exist, and did not mention reading files, which does. That is not a prompt
problem — "what are your tools" is a question about the machine's own state, like "what is
in this folder", and the answer has to be read off the machine. The output is deliberately
plain sentences rather than a bulleted list: the 2b copies the shape of whatever it read
last, and the first version came back as a reply full of dashes, in a reply that may be
spoken aloud.

## 38. The folders it may open are stated every turn — but not as an instruction

**Doc:** Stage 04 — the sandbox and its roots.
**Code:** `runtime._roots_line()` appends `prompts.ROOTS_SYSTEM` to every turn's message
list.
**Why:** the model had no way to know which folders were configured, so it guessed paths,
and a guess it cannot check reads to the user as "Sunday cannot see my Documents folder"
when the truth is that it was never told the folder was there.

The first wording of this line cost a working call, which is the part worth keeping. It
ended with "Always pass a full path starting from one of those" — and the 2b read that as a
precondition rather than a description. Handed
`C:/Users/User/Documents/Sunday`, a path already inside a root, it refused to call
`list_dir` and asked the user for "your full path starting from either Sunday or Work". The
same turn had worked before the line was added. State the folders; do not instruct.

## 39. A relative path resolves against the roots, not the working directory

**Doc:** Stage 04 — "resolve, then check containment".
**Code:** `files.resolve` builds one candidate per root for a non-absolute path, keeps the
first that exists, and falls back to the cwd reading last. Every candidate is still checked
for containment, so the sandbox is not widened by a byte.
**Why:** `Documents/Sunday` used to resolve against whatever folder Sunday happened to be
launched from. When that folder is itself a root the result is a real path inside the
sandbox that simply does not exist, so the refusal read `no such directory` — a message
about the user's folder being missing, for a path they never asked for.

## 40. The backstage trace

**Doc:** new. Stage 10 had the event protocol, but every event on it reports an outcome.
**Code:** `sunday/trace.py` holds the phrasing, `runtime._trace` emits it as
`{"type":"trace", step, heading, text, detail}`, `main.py` prints it and `web/debug.html`
renders it in a Backstage panel. `[ui] trace` in config, `SUNDAY_TRACE=0` to override.
**Why:** the question a person actually has mid-turn is not answerable from any existing
channel. The one that forced this: *did it read long-term memory, or did that quietly fail?*
Retrieval swallows every exception on purpose, so a store that cannot be opened, a store
that is empty, and a store that was searched and had nothing close enough are three
different facts that look identical from outside — and only one of them is a fault.
`LongTermMemory.last_probe` records which, and the trace says so in those words.

The phrasing lives in its own module rather than in the runtime because the runtime should
read as the machine it is, and because these lines are meant to be read by someone tired.
Numbers before jokes; a joke that costs you a number is a bug in that file.

## 41. The overhead reservation was a measurement, and it had moved

**Doc:** Stage 02 — `overhead_tokens`, the part of the window no memory slice pays for.
**Code:** 1200 → 1750, and `test_the_real_overhead_fits_the_reservation` now measures the
standing system lines too, not just the prompt and the schemas.
**Why:** two new tools and the roots line put the real figure at ~1630. Leaving the
reservation at 1200 sizes the memory slices against room that is not there, `num_ctx`
overruns, and Ollama answers by dropping the oldest messages without saying so — the exact
silent truncation Stage 02 exists to prevent. The test was already the guard; it just had to
be told about the new lines.

## 42. A multi-line tool result needs the no-markdown rule repeated next to it

**Doc:** Stage 03 — "no markdown, ever", in the system prompt.
**Code:** `agent_loop.list_instruction` appends `prompts.LIST_HINT` before the final pass
whenever a successful tool result has more than one line.
**Why:** the system prompt is the furthest thing from a 2b's attention by the time it
answers, and what is nearest is a `list_dir` result with one filename per line. It copied
the shape: asked to list a folder it replied with "- gold.txt" and "- scratch.txt", dashes
and all. Same failure as the retrieved-memory framing in note 19, and the same fix — put the
rule next to the thing that triggers it, which is the only place a 2b reliably reads.

## 43. Moving and copying, and why the source rule matters more than the destination

**Doc:** Stage 04 — the file tools were read, write, delete and list. Nothing moved a file.
**Code:** `files.copy_file` and `files.move_file`, both taking `source` and `destination`,
both confirmed by `runtime._confirm_landing` when something is already at the destination.
`PATH_TOOLS` grew, and the taint check now walks `PATH_ARGS` rather than a single `path`.
**Why:** "put this in that folder" is one of the few things a person asks an assistant to do
with files, and it could not be done at all.

The security argument is the part worth writing down, because it is the opposite of the
obvious one. The obvious worry is the destination: a copy is how a file leaves the sandbox,
so the destination is resolved and checked for containment exactly like every other path.
That much is the sandbox doing its normal job.

The *source* rule is the one that is easy to miss. The deny overlay works on paths: reading
`.env` marks the turn `secret` and bolts the web door for the rest of it. Copying `.env` to
`notes.txt` leaves the same bytes at a path the overlay says nothing about — so the next
read is an ordinary private read, the turn stays clean, and the door stays open. Taint does
not help, because by then the credential is at a path that was never on the list. A copy is
a laundering operation on the one thing the overlay protects, so a credential source is
**refused outright** rather than allowed-and-tainted.

Three smaller decisions:

- **A destination that is an existing folder means "into it, keeping the name."** That is
  what a person means by copy-paste, and what the model passes when it repeats the folder
  back. Anything else is the new full path, so a rename is the same call.
- **The confirmation is about the destination, not the source.** A copy leaves the source
  alone and a move leaves it one call away from coming back; what cannot be undone is
  whatever was already sitting at the destination. So the write rule applies unchanged:
  replacing asks, creating does not.
- **Copy-then-unlink, not rename.** The roots can sit on different drives — `C:` and `E:` in
  the configuration this was built against — and a rename across volumes fails. It also
  gives one behaviour when the destination exists, where `os.rename` overwrites on POSIX and
  raises on Windows. If the copy lands and the unlink fails, the result says so and names
  both files rather than reporting a clean failure the user would read as "nothing
  happened".

Copying a file onto itself is refused. `shutil.copy2` would open the destination for writing
first and truncate it to nothing — a data-loss bug wearing the clothes of a no-op.

**Cost:** `overhead_tokens` 1750 → 2000, and the trend is now the thing to watch. Ten tools
put the bound schemas at 1239 tokens of an 8192 window, paid every turn whether or not a
tool is called, out of the memory slices. Eight tools was 1630 all-in; ten is ~1870. There
is not room for many more at this window size, and the next tool should either replace one
or come with a larger model behind it.

## 44. A move inside a root renames; it does not copy

**Doc:** note 43 said copy-then-unlink, always, because the roots can sit on different
drives.
**Code:** `files.move_file` calls `os.replace` first and falls back to
`_move_across_devices` only on a cross-device error.
**Why:** note 43 chose the fallback as the *only* path, which made the rare case pay for
the common one. Nearly every move is inside one root — "put this in that folder" means a
folder that is already configured — and a rename there touches directory entries and
nothing else. Measured on this machine with a 64 MB file: 18–28 ms copying, 0.2 ms
renaming, and the rename figure does not move when the file gets bigger.

Speed is the smaller half. A rename is atomic: there is no instant where the file exists
twice, or where a crash leaves a half-written destination next to an intact source. The
copy path cannot offer that, which is why its failure message has to name both files.

Three details that are easy to get wrong:

- **`os.replace`, not `os.rename`.** When the destination exists, rename overwrites on
  POSIX and raises on Windows. This needs one behaviour, and by the time it runs the
  runtime has already asked the user about that destination.
- **Only a cross-device error may fall back.** A permissions failure that fell through to
  the copy path would silently succeed at copying and leave the original behind — a "move"
  that duplicated the file. `_is_cross_device` checks `errno.EXDEV` and Windows error 17,
  because CPython does not consistently map the latter to the former.
- **`samefile`, not just a string compare.** Both paths are already resolved, so symlinks
  are gone, but hard links are two real names for one set of bytes. `copy2` opens the
  destination for writing before reading anything, so a copy onto an alias of the source
  truncates it to nothing and then copies the nothing. The string compare cannot see that;
  a device-and-inode comparison can.

## 45. The turn gets a second chance, and the loop's own instructions never reach the reply

**Doc:** Stage 03 — one agent, a tool loop that runs until the model stops asking for tools.
**Code:** `prompts.SECOND_CHANCE` and `prompts.RETRY_HINT`, appended through
`TurnContext.scaffold`, and removed again by `clear_scaffolding` before `compose_reply`.
`limits.tool_calls` 5 → 8.
**Why:** the loop could see a tool *fail*. It could not see the model decline to act, and
that is the failure that actually happens. Asked to move a file, the 2b did one of two
things, both of which look like a finished turn from inside the loop: it claimed the move
had happened with no tool call anywhere in it, or it refused with invented reasoning
("today's tools are restricted to that drive"). No call ran, so nothing errored, so nothing
prompted a retry.

So a turn that reaches the end of a round with **no tool calls at all** is asked once more,
with the request restated. Once, and only when nothing ran: a turn that already used a tool
has evidence to write from, and nudging there is how a 2b talks itself into calling the same
thing twice. `RETRY_HINT` is the same idea for the case the loop *can* see — a call came
back refused, and the model needed telling that a corrected retry was an option at all.

**The part that cost a working build.** Both nudges are instructions to the loop, and they
sat in the message list when the reply was written. A 2b copies whatever wording is nearest,
and these were nearest of all. It began answering *with them*: "I cannot use tools in this
session", "Do not call any tools to reply. Only respond naturally" — sentences nobody wrote
and nothing meant, delivered to the user as Sunday's own words. So the loop's instructions
are now tracked and stripped before the final pass. Facts about the turn stay — the web door,
a spent cap, an unresolved gap all have to be explained. Instructions about *how to think*
go. Note 19 was the same lesson about retrieved memory; this is the third time it has been
learned.

Eight tool calls rather than five, because a turn now has to be able to be wrong once and
still finish: the transcript that prompted this spent all five on reads and listings without
ever reaching the tool that does the job.

## 46. The system prompt got longer, and had to be cut back

**Doc:** Stage 03 — "kept short on purpose. A 2b spends its attention on the last thing it
read, so a long constitution costs more than it buys."
**Code:** `prompts.SYSTEM` went from 325 tokens to 673, then back to 276.
**Why:** the long version was written to fix real behaviour — the model narrating its own
confirmation rules at the user, calling `list_dir` twice to answer "i love you sunday", and
moving a file by reading it and writing it somewhere else. Each rule was earned by a
transcript.

Then it started reciting the prompt. "The user said they love you sunday. You are Sunday, a
local personal assistant on their Windows computer. This statement is an expression of
affection rather than a request. Do not call any tools to reply." That is the constitution
coming back out of its mouth, and it is exactly the failure the original note predicted.

The kept version is seven lines, each one paying for itself: no markdown, do not invent, do
not claim you did something a tool did not do, moving is `move_file`, a folder as the
destination, do not go looking to check whether you are allowed, and chat is not a job. The
rules that were cut were true and not worth their length.

## 47. Sunday does not create folders

**Doc:** note 43 — a destination folder means "into it, keeping the name".
**Code:** `_prepare` refuses when the destination's parent is not an existing directory, and
neither transfer calls `mkdir` any more. `_landing` also reads an extensionless destination
as a folder when the source has a suffix.
**Why:** "move gold.txt to the documents folder" has no correct answer here — the configured
root is `Documents/Sunday`, and there is no folder called `documents`. The model invented one
three separate ways across three runs: as a *file* named `documents` holding the gold price,
as a folder `E:/Work/Sunday/documents/`, and as `E:/Work/Sunday/documents/folder`. Every one
of them succeeded, so every one of them moved the user's file somewhere the next turn could
not find it, and the next turn then hunted through folders and gave up.

Creating a folder was never asked for in any of those turns. A missing folder is a question
for the user, and now that is what it becomes: the error names the folders that do exist. The
model's next move, having been told, was to ask which one was meant — which is the right
answer to an ambiguous instruction.

The extensionless rule is the cheap half: `gold.txt` → `documents` is somebody naming a
folder, not a file, and taken as a filename it silently swallows the file.

**Still open.** The roots have no names. The user says "the documents folder" and means
`C:/Users/User/Documents/Sunday`; nothing in the configuration says so, and the model is left
matching words against paths. A label per root in `config.toml` would end this properly.
