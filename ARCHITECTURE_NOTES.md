# Architecture notes — deviations found while building

Running list of places where the build departed from `doc/sunday_architecture.html`,
or filled in something the document left open.

**Entries 1–89 have been applied to the HTML.** They are kept here as the record of
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

## 48. The intent hint is vetted, so the airlock's guarantee is structural again

**Doc:** Stage 05 — "the query writer cannot mention your sell target because your sell
target was never in the room". Listed in the context log as the one place the guarantee was
weaker than it sounded.
**Code:** `airlock.vet_intent` keeps only the words of the hint that already appear in the
material the prompt is about to contain — the user's line and the `public` results. The
count of dropped words comes back on `Cleared` and is traced.
**Why:** the composer's context was airtight and the *hint* was not. The hint is written by
the agent, and the agent has seen the private half, so "the query writer cannot leak" was
true while "the thing that writes the hint cannot leak" was only a hope.

The fix keeps what a hint is for and removes what it could do wrong. Selecting and
reordering words that were already going to cross is the whole job: `gold price forecast`
out of "what is the gold price forecast for June". Introducing a word that was nowhere in
the cleared material is the only behaviour that could leak, and it is the only behaviour
this takes away. A hint of `gold price against my sell target of 4600` arrives as
`gold price`.

Two details that decide whether it works:

- **A number is one token.** `4,600` split on the comma is two three-digit fragments, each
  harmless-looking on its own. The word pattern keeps inner punctuation.
- **The filler list is tiny and closed.** Twenty-odd words that cannot identify anything
  (`the`, `current`, `price`, `today`), so the vetted hint still reads as English rather
  than as keyword salad. Every addition to that list is a hole, so it does not grow.

The dropped count is reported rather than swallowed: a silent filter is how you end up
believing a guarantee you no longer have.

## 49. The roots have names now

**Doc:** Stage 04 — the sandbox listed paths. Note 47 left this open in as many words.
**Code:** `[files] roots` takes either a bare path or `{ label, path }`; `config.Files.
entries()` normalises both. `files.resolve` accepts a label as a path, alone or as the head
of one, and the system line lists `label: path`.
**Why:** "the documents folder" meant `C:/Users/User/Documents/Sunday` to the user and
nothing at all to the model, which was left matching English against paths — and invented a
folder called `documents` three separate ways rather than admit it could not tell. Nothing
in the configuration had ever said what these folders were called.

A bare string still works and takes the folder's own name, so no existing config breaks. On
this machine that exposed the reason labels were needed: both roots are called `Sunday`.

`work`, `work/notes.txt`, `the work folder` and `WORK` all resolve, and whatever a label
resolves to is still checked for containment like every other path — a label cannot smuggle
anything, it can only name something that was already allowed.

## 50. Markdown is stripped from the stream, not asked for politely

**Doc:** Stage 03 — "no markdown, ever", stated in the system prompt.
**Code:** `stream.Despeckler` filters the token stream inside `stream.fork`, so the text
sink, the sentence sink and the stored reply are the same characters.
**Why:** the prompt forbids it and the model obeys most of the time. Most of the time is
not good enough for a channel that gets read aloud, where a backtick is pronounced and a
dash at the start of a line is a spoken word. Even after the list hint of note 42, single
backticks kept arriving: "The file `gold.txt` has been renamed to `plan.txt`."

It sits in `fork` rather than in either sink because cleaning one and not the others is how
a transcript ends up disagreeing with what the person heard.

Deliberately narrow: asterisks and backticks anywhere, a list marker at the start of a line,
and nothing else. Underscores are left alone — half the filenames on this machine have one —
and no part of it parses markdown.

The streaming case is the fiddly one. A bullet is only a bullet at the start of a line, and
a token boundary falls wherever the model put it, often between the newline and the dash. So
the stripper tracks whether it is at a line start and holds back at most seven characters,
never past the end of a line, until it can tell.

## 51. The 8k window was a guess, and it was costing the memory slices

**Doc:** Stage 02 — "The 8k window, split five ways", and `context_tokens = 8192` in the
config block.
**Code:** 32768.
**Why:** 8192 was chosen before anything was measured, and never revisited. The model's own
limit is 262144. Measured on the 6 GB card this was built for:

| num_ctx | VRAM | throughput |
| --- | --- | --- |
| 8192 | 3286 MiB | 63.5 tok/s |
| 16384 | 3396 MiB | — |
| 32768 | 3546 MiB | 63.1 tok/s |
| 65536 | 3960 MiB | — |

Four times the window for 260 MiB and no measurable speed. Reserved KV cache that is never
filled is nearly free, and prompt processing scales with the tokens a turn actually uses —
which stays around 3–5k either way — not with the size of the window they sit in.

What 8192 was actually costing: `slices()` was scaling every allowance by 0.61 to fit the
overhead and the reply underneath it. The recent-turns slice was 1884 tokens where the
config asked for 3072, and the tool slice 1256 where it asked for 2048. The numbers in
config.toml were being quietly overruled by arithmetic. At 32768 nothing is scaled: the five
slices are what they say they are, which is 63% more memory per turn for the price of a
config value.

This also retires the framing in note 43 and note 47 that the tool schemas were crowding
out memory. At 8192 an overhead of 2400 was 29% of everything; at 32768 it is 7%. Adding
the calendar and mail tools is now an ordinary decision rather than a budget crisis.

The slices themselves are deliberately left where they were. There is room to grow them, and
that is a separate question with a real trade in it: more recent turns means slower prompt
processing and a 2b whose attention is already thin spread thinner. The window being wrong
is a fact; the slices being right is a judgement, and this note only fixes the fact.

**How it was missed for so long:** every test asserted the slices fit *under* the configured
window, which they always did — because `scaled_to` made them fit. Nothing asserted the
window was the right size, because nothing could: that is a hardware measurement, not an
invariant. The lesson is narrow and worth keeping. A number that no test can check is a
number nobody rechecks.

## 52. A fast path on a compound message answers half and convinces the model it is done

**Doc:** Stage 04 — "the result is handed to the agent as an ordinary tool result, so the
turn carries on normally: the model can still call more tools, and a mixed question loses
nothing." That last clause was false.
**Code:** `fastpaths.match` returns `None` when the message contains a second instruction.
**Why:** "what is the price of gold and what is the weather in Jakarta" fired the weather
pattern in code, and the model — seeing a tool result already sitting in the transcript —
read the turn as finished and answered about the weather alone. It then added "no specific
price was returned for this request", which is true and useless: nothing had asked for one.
Half the question, dropped in silence, every time.

Telling the model was tried first: a system line saying the fast path covers one clause and
the rest still needs doing. It did not hold — the second half was still dropped. So the fast
path does not fire on a compound at all. It exists as insurance for the single-clause case
where a fumbled argument would be obvious, and on a compound the model does the work, which
it does correctly once nothing has answered ahead of it. The same question now comes back
with both calls in one round and both answers in the reply.

**And the same left-edge bug as note 34, in a different file.** `_TAIL` strips trailing
words a person adds to a city name, and its `and` branch had no word boundary — so "weather
in Thailand" asked for the weather in **Thail**. `sk-` matching inside "ta|sk-oriented" was
supposed to have taught this once already. Any pattern that cuts a string needs to say where
a word begins.

## 53. A repeated failing call is refused without running

**Doc:** Stage 06 — the tool-call cap is described as the bound on a runaway loop.
**Code:** the loop records the `(tool, arguments)` of every call that came back failed.
A repeat is refused without dispatching; a third sets `unresolved` and ends the loop.
**Why:** asked to copy a file to a folder that did not exist, the model sent the identical
`move_file` on rounds 1, 2, 3, 5 and 7 — spending the entire cap on one call that could not
work, and ending the turn with an empty reply. `RETRY_HINT` says "do not send the same call
again unchanged" in as many words, and it sent it again unchanged. **A prompt cannot enforce
a loop bound.** The cap did eventually stop it, which is the cap doing its job badly: it is
a backstop against runaway cost, not a way to notice the model is stuck.

Only *failed* calls are guarded. A repeated success is wasteful rather than pathological,
and two of this codebase's own guarantees — the hop cap and the no-room refusal — are tested
by issuing the same successful call twice. Guarding those broke both tests, which is how the
distinction was found.

## 54. A turn with nothing to say now says so

**Doc:** nothing covered this.
**Code:** `compose_reply` falls back to a plain sentence when the model streams no tokens,
and pushes it through both sinks so the text client, the speech client and memory agree.
**Why:** the turn that spent its whole budget on repeated calls came back with an empty
string. The user sees a blank line where the answer goes, which reads as a crash rather than
as a failure, and memory_write skips a turn that has no response — so there is not even a
record of it having happened.

## 55. `write_file` was the one tool still creating folders

**Doc:** Stage 04, the rule block "Sunday does not create folders".
**Code:** `files.no_such_folder` is now shared by `write_file`, `copy_file` and `move_file`,
and `mkdir(parents=True)` is gone.
**Why:** note 47 established the rule after the model invented `E:/Work/Sunday/documents`
three separate ways, and the fix was applied to the transfer tools only. `write_file` went
on calling `mkdir(parents=True, exist_ok=True)`, so the guarantee held for two tools out of
three and the third would happily build a folder chain on a guess — the exact behaviour the
rule exists to stop, reachable by the more common tool.

Two tests had to change, and they were both asserting the old behaviour:
`test_write_creates_parents_inside_the_root` existed specifically to pin folder creation.
A test that encodes a violation is how a violation survives an audit, and this one had
survived two.

The rule now lives in one function rather than in each tool, because that is what stops the
next tool from drifting.

## 56. Two refusal strings were status codes, not scripts

**Doc:** Stage 06, the rule block "A refusal string is a script, not a status code".
**Code:** the credential refusals in `write_file`, `delete_file` and `_prepare` say what to
tell the user.
**Why:** `refused: will not write over a credential file` says what happened and nothing
about what the person should hear. The lesson is already recorded — `refused: path is
outside the configured roots` was relayed to a user as "C: isn't mounted" — and three
strings had been written since without it. A test now walks every refusal in the sandbox and
requires the words "tell the user" in each, so the next one cannot be written bare.

## 57. The trace never said what left the machine

**Doc:** Stage 10 — the backstage trace, "one line per step".
**Code:** `runtime._ask_external` emits `trace.query_left` next to the `query` event.
**Why:** `trace.query_left` was written, tested by eye once, and never called. Of every line
in that module it is the one that matters most — the exact text that crossed the airlock —
and the narration was silent on it while describing every local file read in detail. Found
by diffing the functions the module defines against the ones the runtime emits, which is a
check worth repeating: a trace line that is never called is indistinguishable from a step
that never happens.

## 58. A test that waits on the clock lies when the machine is busy

**Doc:** nothing covered this.
**Code:** `tests/test_server.py` waits through `_recv`, a 30-second *silence* budget that
fails with the messages it did receive and what it was still waiting for.
**Why:** `test_a_client_can_drive_a_whole_turn` failed twice in one session, both times in a
run competing with a live Ollama probe, and reported `TimeoutError` with nothing else. The
first failure cost an investigation that ended in "not reproduced in eleven runs"; the
second only named itself because it happened to be captured in a file.

The budget is on silence rather than on the turn, so raising it costs nothing when the test
passes — it only waits longer in the case that was already going to fail. What it buys is a
failure that says which message never arrived.

## 59. The slices doubled, and the reason it was not tripled is measured

**Doc:** Stage 02 — the five allowances, and note 51 which widened the window and
deliberately left them alone.
**Code:** `slice_summary` 1024→2048, `slice_recent` 3072→6144, `slice_retrieved` 1024→2048,
`slice_tools` 2048→4096, `thinking_budget` 1024→2048. The ratios are unchanged.
**Why:** note 51 widened the window to 32768 and said in as many words that how much of it
memory should take was a separate question with a real trade in it. This is that question,
answered with numbers instead of instinct.

Prompt processing on this card runs at roughly **0.15 ms per token** above 3k, measured:

| prompt tokens | prompt eval |
| --- | --- |
| 1349 | 331 ms |
| 3329 | 661 ms |
| 6629 | 1031 ms |
| 13229 | 2002 ms |

A turn pays that before it produces a word. So 8192 of slices was about 1.6 s at worst,
16384 is about 2.9 s, and 24576 — the largest that would still fit — would be over four
seconds on every turn of a long session. For something meant to be spoken to, that is the
wrong trade, and the difference between "fits" and "should" is exactly the thing 8192 got
wrong for a whole build.

What makes any of it affordable is that slices are **allowances, not usage**. A short
session fills none of them and pays nothing; the cost arrives gradually and only in sessions
long enough to have earned it. Verified after the change: a three-turn session assembled 91
to 336 tokens of context against an allowance of 10240, at the same latency as before.

The remaining 13216 tokens stay slack. Overhead has moved three times already — 1200 to
1750 to 2000 to 2400 — and a large tool result has to land somewhere that is not the
memory slices.

---

*Entries 60–63 come from a second conformance audit, run file by file against the
document rather than against the code's own idea of itself. Applied to the HTML.*

## 60. "A fast path never fires on a compound message" was true of two paths out of three

**Doc:** Stage 04, the rule block of that name, and note 52 which established it.
**Code:** `fastpaths.match` tested `_CAPABILITIES` *before* `_COMPOUND`, so
"what can you do and what is the weather in Jakarta" took the shortcut. The
compound test now runs before any pattern is tried.
**Why:** the same failure note 52 fixed, reached through the one door left open —
and the capability list is the worst of the three results to answer half a question
with. It is long, it is the last thing the model read, and a 2b reading it decides
the turn was about itself. The weather half came back as a tour of the tool belt.

The shape of this is note 55 again, exactly: a rule stated once and applied per
call site, where one call site was written before the rule and never revisited.
Note 55 was `write_file` still calling `mkdir(parents=True)` after copy and move
had stopped. Both were found the same way — by reading the rule as a claim about
*every* path and then checking every path, rather than checking the paths the
rule's own commit had touched.

The test that existed passed the whole time. It listed five compound messages and
not one of them was a capability question, because it was written from the
transcript that prompted note 52 rather than from the rule. **A test written from
the incident checks the incident; a test written from the rule checks the rule.**

## 61. A turn that broke kept its notices to itself

**Doc:** Stage 05 — "you get the notice whenever a turn goes tainted, not only
when a call was actually refused".
**Code:** `run_turn`'s `OllamaDown` handler returned before the notices were
emitted. They are emitted there now, through `Runtime._say`, before `done` and
while a sink still exists. The `Cancelled` path deliberately still returns none.
**Why:** the door shuts on the *read*; the model dies later. So a turn could touch
a credential, strip a key, lose Ollama, and tell you only that Ollama was
unreachable — every privacy event of that turn silently dropped. It is the same
ordering bug as emitting notices after the sinks were cleared, which is why no
client saw a redaction for a milestone, arriving through a path added afterwards.

Cancellation is the deliberate exception, and it is worth stating so it does not
drift back: a barge-in discards the turn, and an apology for work the user stopped
caring about is noise rather than honesty.

## 62. Four numbers in the source still described the 8k window

**Doc:** Stage 02 as revised by note 51 — the window is 32768, the reservation is
2400, nothing is scaled.
**Code:** `budget.py`'s module docstring opened "The 8k window, split five ways";
`budget.slices()` said the overhead was "around 1080 tokens"; `config.Models`
said the system prompt cost 576 tokens where it costs 276, and closed with "at an
8k window that leaves the five slices about 5000 tokens between them, and there
is no room for another round of this" — the framing note 51 explicitly retired.
A test docstring carried the 1080 as well.
**Why it is worth an entry:** none of it changed behaviour, and all of it is the
thing a person reads *instead of* the document. Note 51's lesson was that a number
no test can check is a number nobody rechecks; this is its second half. A number
that is only in prose is not rechecked either, and prose is where the next person
looks first. The slices test now asserts the unscaled values, so at least the
32768 claim has something holding it down.

## 63. Three passages of the HTML had been overtaken by their own rules

**Doc, against itself:** the hero chip still read `8k context`. The `write_file`
row said "creates parent directories" three paragraphs above the rule block
saying Sunday does not create folders — the exact violation note 55 fixed in the
code, left standing in the sentence that describes the code. The folder rule
closed with "**Still open.** The roots have no names", immediately below the table
row that describes the names they have had since note 49. The sample trace listed
eight tools on the belt when there have been ten since note 43. And Stage 03's
summary of the system prompt's seven lines named a rule the prompt does not have
and omitted one it does.
**Why:** a specification that contradicts itself is worse than one that is merely
behind, because a reader cannot tell which half is current — and both halves here
were written deliberately, at different times, by someone who had checked. The
fix for the class, rather than for these five: every entry in this file that
*resolves* an open question has to strike the passage that raised it, in the same
pass. Note 49 closed the roots question and added the table row; it did not delete
the paragraph three lines down that said the question was open.

## 64. The voice models are run directly, not through their packages

**Doc:** Stage 01 and Desktop 02 name openWakeWord, Silero VAD and Moonshine as
components; the document is silent on how they are loaded.
**Code:** all three are ONNX graphs run on `onnxruntime`, which chromadb already
brings. `sunday/audio/wake.py` is openWakeWord's three-graph pipeline —
melspectrogram, embedding, wake model — written out. `vad.py` and `stt.py` are the
same idea for the other two.
**Why:** the packages cost more than the models. `openwakeword` brings scikit-learn
and a TensorFlow-Lite runtime to do what is three sequential `session.run` calls;
`silero-vad` brings torch and torchaudio for a 2 MB graph; the supported Moonshine
distributions want either a git-subdirectory install or transformers. What the voice
extra actually adds to the install is `sounddevice` and `tokenizers`.

The trade is that the shapes are now this repo's problem, and two of them are the
next three notes. That is the honest cost, and it was worth paying once.

## 65. Silero v5 wants the 64 samples before the window, and does not say so

**Doc:** silent — "Silero VAD, ~2 MB, decides where speech starts and stops".
**Code:** `vad.Vad` keeps the last 64 samples of the previous window and prepends
them, so the graph is fed 576 samples to score 512.
**Why it is worth an entry:** leaving them out does not raise. The model loads, runs,
returns a float, and reports **0.02 for clear speech** — so the first symptom is a
VAD that never fires, which reads as a dead microphone or a wrong device, and the two
obvious things to check are both fine. The window size is also not negotiable: 512
samples at 16 kHz, not 320, not 480.

## 66. The merged Moonshine decoder has two shapes that are not guessable

**Doc:** silent.
**Code:** `stt.Moonshine`. First pass: `use_cache_branch=False` and every past tensor
zero-*length*, `(1, 8, 0, 52)`. Every pass after: the decoder past comes from the
previous `present`, and the cross-attention K/V is **the first pass's**, held for the
rest of the clip.
**Why:** both were found by failing.

A dummy past of length 1 rather than 0 runs the first token and then fails inside the
`optimum::if` node with "right operand cannot broadcast on dim 0" against a matmul in
`encoder_attn` — an error naming neither the input that is wrong nor the pass it is
wrong on.

And on the cache branch the model returns the cross-attention K/V *empty*, shape
`(0, 8, 1, 52)`, because it cannot have changed. Feeding those back collapses the
batch dimension to zero on step 2. The first pass computes them from
`encoder_hidden_states`; keep them and stop asking.

## 67. Moonshine returns nothing for a clip wrapped in silence

**Doc:** Desktop 02 — "Moonshine transcribes the clip", with two guards on the clip's
length and nothing about its shape.
**Code:** `listener._trim` cuts the clip to the voiced span plus `TRIM_MARGIN_MS`
(250) either side, using positions the VAD recorded. The pre-roll is fed through the
VAD too, so those positions cover the whole clip rather than only the part after the
wake word.
**Why:** measured on this build, on one four-second utterance of clear speech.

| padding either side | first-token logits | transcript |
| --- | --- | --- |
| 1 s | `Hey` 12.22, EOS 11.45 | "Hey Jarvis what is the weather in Jakarta" |
| 2 s | EOS 11.90, `Hey` 10.47 | empty |
| 4 s | EOS 9.86, `Hey` 9.60 | empty |

Moonshine was trained on tightly-trimmed segments, so silence either side is evidence
that there is nothing to transcribe, and greedy decoding takes end-of-sequence as its
very first token. **Every clip this pipeline builds is padded by construction** — half
a second of pre-roll in front, seven-tenths of a second of trailing silence behind,
both required by the design. So the common case was the failing case: the microphone
was fine, the VAD was fine, and the transcript was empty.

Gain is not the variable. The same clip at 0.3x and at 0.1x transcribes correctly.

## 68. Starting and stopping are two different clocks

**Doc:** Desktop 02's five steps read as one clock — the wake word fires, the VAD marks
speech, 700 ms of silence closes the clip.
**Code:** `vad_silence_ms` may only run once the utterance has *begun*, and "begun" is
`min_clip_ms` of speech heard after the wake word — not speech anywhere in the clip,
and not one window of it. Until then the only clock running is `lead_in_ms`, which
abandons the clip entirely.
**Why:** this is the bug that made milestone 7 look finished and useless.

Measured on a real recording: "hey jarvis" ran 2.88–3.68 s and the question ran
5.06–7.81 s. **A 1.38 second gap** — twice the silence that ends a clip. The pre-roll
holds the wake phrase, so counting it as "speech has started" started the stopwatch
that ends the clip, and the clip closed *inside the pause*, holding nothing but the
tail of the phrase. That was then correctly dropped as a cough, and from outside the
whole thing was silence: the wake word visibly fired, and nothing ever happened again.

The pre-roll flag alone was not enough, and the second failure is the more interesting
one. A VAD window straddling the wake word carries the tail of the phrase into the
live count, and **one 32 ms window was enough to restart the same bug**. So the gate
is a duration, and it is `min_clip_ms` rather than a new number: below that floor is
not enough speech to have begun talking, and it is not enough speech to have said
anything. The same question, asked at each end of the clip.

One consequence, deliberate: a cough after the wake word is now abandoned on the
lead-in rather than closed and then discarded. Nothing reaches the transcriber either
way, and the `_close` guard stays for the forced thirty-second close.

## 69. The wake threshold was 0.5 because nobody had measured one

**Doc:** Desktop 02 — "Threshold. Start at 0.5. Raise it if it fires at the
television; lower it if you have to shout." The document is explicitly offering a
starting point, and this is the measurement it asked for.
**Code:** `[wake] threshold = 0.3`.
**Why:** on this microphone, in one eight-second recording containing the phrase and a
full question.

| audio | peak score |
| --- | --- |
| a clearly spoken "hey jarvis" | **0.4901** |
| everything else in the same recording, question included | 0.0002 |
| an unrelated sentence, separately | 0.0000 |

The phrase missed the old bar by **one hundredth**, while sitting a factor of 2500
above anything that was not the phrase. So it fired perhaps one time in three, which
is the worst available failure mode — it looks like a microphone problem, and every
number you would check to rule that out is healthy. Anywhere from 0.05 to 0.45
separates the two populations here; 0.3 leaves room on both sides.

The lesson is note 51's in a new place: the bar sat inside the noise of one speaker's
voice rather than inside the gap, and no test could have told you, because the gap is
a property of your microphone and your room. So `python -m sunday.audio.check` prints
**both** numbers — the phrase, and the loudest thing that was not the phrase — rather
than only the one that failed.

## 70. Two new `[audio]` keys: `preroll_ms` and `lead_in_ms`

**Doc:** Desktop 02 states the 0.5 s pre-roll in prose and gives it no key, and does
not consider a wake word that fires with nothing after it.
**Code:** `[audio] preroll_ms = 500` and `[audio] lead_in_ms = 4000`.
**Why:** the pre-roll was already a number in the document, just not a settable one.
The lead-in is new and the design needs it: without it a wake word that fires at the
television records silence to the thirty-second cap and hands Moonshine thirty seconds
of room tone. Four seconds rather than three because of note 68's measurement — the
pause before a question is 1.4 s from someone who knows what they are about to ask.

`min_clip_ms` also changed meaning, and the config comment now says so: it is measured
against the *speech* in a clip, never the clip's length. Every clip carries pre-roll
and trailing silence, so a guard on the total could never have fired at all.

## 71. `listen` is a new message on the socket, and `level` finally has a producer

**Doc:** the protocol table carries `level` with "NOT BUILT, waits on 07–09", and has
no push-to-talk message at all — though Desktop 05 specifies a global hotkey that does
exactly that.
**Code:** `{"type":"listen"}`, shell to sidecar. It opens the ear if voice mode was
never entered, and otherwise starts recording without waiting for the phrase. `level`
is emitted by the listener at 10 Hz.
**Why:** the hotkey and the mic button both need a way to say "I am talking now", and
`set_mode` is the wrong shape for it — a mode is a standing state, this is one event.

The ear runs on its own thread and reaches the socket through
`loop.call_soon_threadsafe`, which is the only correct way in and the reason `Sidecar`
now keeps a reference to its loop. It is also injectable
(`Sidecar(ear_factory=...)`), because the socket tests run on machines with no sound
card and no models, and a test that silently skips there is a test that never runs
anywhere.

## 72. A dropped clip that says nothing is a microphone that looks dead

**Doc:** silent. The guards are specified; what the person hears when one fires is not.
**Code:** `Ear._handle` emits a `notice` carrying the listener's own reason — "heard
nothing usable: only 288 ms of speech" — and a clip that transcribes to nothing says so
with the number: "transcribed 1400 ms of speech as nothing".
**Why:** this is `trace.query_left` again (note 57): the reason was computed and then
thrown away one function short of anybody reading it. Four different faults — the wake
word never fired, the clip was dropped as a cough, the clip was abandoned on the
lead-in, the transcriber returned empty — all presented as the same blank terminal.
Only two of the four are even unusual. The debugging session that produced notes 67
and 68 spent its first round working out which of them was happening, and the listener
had known all along.

## 73. The terminal client grew a second input source, and the prompt had to move

**Doc:** silent; the terminal client is the document's testing instrument rather than
part of the design.
**Code:** `sunday --voice`. Typed lines and spoken ones meet on one queue and the turn
loop never learns which it took — the Stage 01 adapter seam, in twenty lines. `input()`
now runs on its own thread, and the prompt is written by the loop that knows the app is
idle.
**Why the prompt moved:** `input("you> ")` prints its prompt once, on a thread that is
then blocked for the whole turn, so everything the turn prints lands *after* it and the
screen ends on a trace line with no prompt anywhere. It reads as hung when it is in
fact waiting — and in voice mode there is no keypress to make it obvious that it is
not.

## 74. `python -m sunday.audio.check` exists because the suite cannot measure a room

**Doc:** Desktop 03 says to measure the AEC delay once with a click test and write it
into config; Desktop 02 says to tune the wake threshold. Neither has a tool.
**Code:** `sunday.audio.check` records eight seconds, reports what each of the four
pieces made of it with the number that decided it, saves the recording, and prints the
config line to change. `sunday.audio.models` downloads the models.
**Why:** every number in `[audio]` and `[wake]` is a guess until it is measured on the
hardware it will run on, and note 69 is what happens when one is left at its guess. The
failures they cause are also identical from outside — you say the phrase, something
says "listening", nothing happens — so a tool that separates them is worth more than
any amount of prose about which to try first.

It saves the recording because the second round of every voice bug is a question the
summary cannot answer: was the phrase even in there, is the room noisy, did it clip.
Notes 67 and 69 were both settled from the file rather than from the numbers printed
above it.

## 75. The voice models are on the processor, and the VRAM plan assumed otherwise

**Doc:** Resources sizes a 6 GB card with Moonshine at 0.50 GB and Kokoro at 1.20 GB
resident on it, plus 0.50 GB of CUDA contexts, and concludes that moving the Windows
desktop to the integrated adapter "is not optional".
**Code:** the `onnxruntime` that arrives with the rest of the dependencies offers
`CPUExecutionProvider` and nothing else. All four voice models run on the processor
and hold **0 GB of VRAM**. They cost about 1.2 GB of ordinary RAM in the sidecar and
roughly a fifth of one core while listening.
**Why it is worth an entry:** the whole VRAM budget was built around a number that is
zero. Measured with everything running, `qwen3.5:4b` at a 16384 window is 3379 MiB
entirely on the card and the machine sits at 4390 MiB of 6144 with a browser open. Two
of the three savings in that section are consequently free money nobody needs: there is
one CUDA context rather than three, and the desktop is not competing with a full card.

It also removes a precondition the document had put on the model-size decision. "It
needs the integrated-graphics move first" was true of a plan in which the synthesiser
held 1.2 GB. It was not true of the build.

## 76. `qwen3.5:2b` to `qwen3.5:4b`, and the window came down to pay for it

**Doc:** Stage 03 — "a 2b that reliably picks the right tool beats a 4b that fumbles the
arguments. The trade is prose quality, and it is a real trade." Open decisions listed
2b prose quality as fixable only by a larger model.
**Code:** `[models] agent = "qwen3.5:4b"`, `context_tokens = 16384`, and the four memory
slices sized down to 1536/4608/1536/3072.
**Why:** the argument was the right way round and the wrong conclusion. Measured on the
same three questions, the 4b picks the same tools with the same arguments, so the trade
the 2b was accepted for was never collected. What changed is the sentences:

| asked | `qwen3.5:2b` | `qwen3.5:4b` |
| --- | --- | --- |
| gold price | "…$4,367.50 per ounce. I have provided the information directly here without…" | "The current gold price is 4,367.50 USD per ounce." |
| weather | "Jakarta is currently 26.5°C… If you need further informa…" | "It is 26.5 degrees Celsius in Jakarta with a clear sky…" |

Both 2b answers narrate their own conduct after answering, which is the decoration habit
Stage 03 measured and could not prompt away. And `26.5°C` is a degree sign in text that
gets spoken aloud.

**The window is the cost, and it is a hard one.** Measured on the 6 GB card, browser
open:

| model | window | on the GPU | tokens/s |
| --- | --- | --- | --- |
| 2b | 32768 | 100% | 64.8 |
| 4b | 16384 | 100% | 46.7 |
| 4b | 24576 | 85% | 41.0 |
| 4b | 32768 | 79% | 33.7 |

Past 16384 Ollama leaves part of the model on the CPU and says so in exactly one place:
`ollama ps`. Nothing else reports it, the turn still works, and it is 28% slower. This is
the rule the document has carried since milestone 1 — *check `ollama ps` says 100% GPU* —
earning its keep on the first change that could have broken it.

**One habit did not improve.** Asked what it can do, the 4b paraphrases the
`list_capabilities` result rather than reading it back, and dropped the file tools while
doing so. The fast path hands it correct prose and it edits it anyway. `qwen3:8b` is
pulled, would need the window smaller again, and has not been compared.

## 77. The slices were sized to fit rather than scaled into the smaller window

**Doc:** Stage 02 — "at 32768 there is nothing to scale."
**Code:** the shipped slices are 1536/4608/1536/3072, which with the thinking
reservation come to 12800 of the 13216 left after overhead and the reply. Nothing is
scaled, and a test now asserts that all four match the config rather than only two.
**Why:** carrying 2048/6144/2048/4096 into a 16384 window would have worked. `scaled_to`
would have shrunk every slice by 0.81 and no one would have been told — the recent-turns
allowance would read 6144 in `config.toml` and be 4956 in use.

That is note 51 arriving from the other direction. There the number was never measured;
here it would have been measured, written down, and then silently overridden by a
mechanism whose whole job is to be invisible. The scaling stays, because it is what makes
a smaller window degrade rather than break. But the shipped configuration must not be a
configuration that needs it, or the file is a description of something that is not
running.

## 78. Parakeet TDT replaces Moonshine, against the benchmark rather than with it

**Doc:** Stage 01 names Moonshine as the STT, at 0.50 GB, chosen because it is small
and does not stream.
**Code:** `[models] stt = "parakeet-tdt-0.6b-v2"` by default, `"moonshine-base"` still
selectable, and `stt.load()` builds whichever is named. Only the chosen one is
downloaded — fetching both is 900 MB to use one.
**Why it is worth an entry:** this is the one decision in the file taken *against* the
measurement rather than from it, and that should be visible.

Four transcribers, eight labelled clips, clean and with noise added:

| model | WER | ms/clip | at 15 dB | at 8 dB |
| --- | --- | --- | --- | --- |
| moonshine base | **0.0%** | **123** | **0.0%** | **0.0%** |
| parakeet tdt 0.6b int8 | 2.6% | 704 | 2.6% | 2.6% |
| whisper large-v3-turbo int8 | 0.0% | 5414 | 0.0% | 0.0% |
| whisper small.en int8 | 2.6% | 1298 | 2.6% | 2.6% |

Moonshine wins that outright, and it keeps winning on the clip the pipeline actually
builds: 102 ms and `"What's the weather in Jakarta?"` against Parakeet's 238 ms and
`"Surface what's the weather in Jakarta?"` — Parakeet renders the tail of the wake
phrase that the pre-roll deliberately keeps, and Moonshine drops it. Parakeet's single
error on the eight was joining a filename, `quarterlynotes.txt` for `quarterly
notes.txt`, which matters for an assistant that opens files by name.

**What the benchmark cannot see, which is the case for the change.** The eight clips are
Windows SAPI speech: one synthetic voice, clean, US English, no accent and no room. A set
the incumbent scores 0.0% on cannot discriminate — it can only fail a challenger. On the
one recording of a real person in a real room, Parakeet was the only model of the four to
get the wake phrase nearly right (`"Hey Jarface"` against both Whispers' `"HR face"`),
and it is a 600M-parameter encoder against Moonshine base's 61M, at the top of the open
ASR leaderboard where Moonshine does not appear.

So the trade is about 130 ms a turn, and headroom on speech the test set does not
contain. Both are kept because a switch you cannot flip is not a record of a decision,
and if real use disagrees the line to change is one word in `config.toml`.

**`onnx-asr` is the third library in this package with an excuse**, and it is Kokoro's
excuse. What it supplies is not the model but the machinery: a mel front-end exported as
its own graph, and a TDT decode loop that emits a token and a duration together and skips
frames accordingly. That is a specific algorithm rather than glue. It brings numpy,
onnxruntime and huggingface-hub, all already here — unlike NeMo, which is the supported
way to run Parakeet and would bring torch.

## 79. The spinning setting has to reach the sessions we do not build

**Doc:** silent.
**Code:** `audio/onnx.py` grew `options()` beside `session()`, and Kokoro and Parakeet
are both constructed with it.
**Why:** note 79's parent is the `input overflow` fix, and it was half applied. Turning
off `session.intra_op.allow_spinning` fixed the two graphs this package builds itself and
missed the two that libraries build — which are the *largest two*, the 325 MB
synthesiser and the 652 MB encoder, and therefore exactly the ones capable of holding
twenty cores hot long enough to starve the audio callback.

The general shape: a setting that is a correctness requirement rather than a preference
cannot live at the call sites, because the call sites you do not own will not have it.
It lives in one function, and everything goes through that function.

## 80. The transcript check threw away the questions it was written to protect

**Doc:** Desktop 03, layer 3 — "compare the fresh transcript against the sentence
Kokoro is *currently speaking*, normalise both, take token overlap, and discard above
0.6 similarity."
**Code, as written:** compared every transcript against the last three sentences ever
spoken, whether or not anything was playing.
**Code, now:** only a clip that was recorded while Sunday was actually talking is
checked. `Clip.while_speaking` is set by the listener if any frame of the clip arrived
with `speaking` true.
**Why:** the widened version reads as more thorough and is unusable, and the reason is
structural rather than a matter of tuning.

**A reply always contains its question's subject.** So the next question about that
subject overlaps the answer, and layer 3 is an overlap test. Measured on the real
wording from a live session, against the reply it had just given:

| said next | similarity | verdict at 0.6 |
| --- | --- | --- |
| "what is the weather in Jakarta" | 0.67 | **rejected** |
| "and the humidity" | 1.00 | **rejected** |
| "is it going to rain in Jakarta" | 0.33 | passes |

The similarity is asymmetric on purpose — it asks how much of what was *heard* was
already being said, because the microphone catches part of a sentence more often than
all of it. That property, which is right, is exactly what makes a short follow-up score
1.00: every word of "and the humidity" is in the answer.

No cutoff fixes this. At 0.6 the follow-ups die; raising it far enough to save "and the
humidity" is raising it past 1.0. The check is sound and was being asked a question it
cannot answer, which is *whether Sunday was speaking* — and that is known for free.

What the user saw was three turns in a row answered with "ignored what sounded like my
own voice coming back", having said something perfectly ordinary into a silent room.
Note 72 added that line so a discarded clip would say why; here it correctly reported a
decision that should never have been taken.

The general shape, and it is worth keeping: **widening a check beyond its specification
is not free even when it looks conservative.** The spec said *currently speaking* and
meant it. Three sentences of history with no time bound and no speaking bound is a
different test wearing the same name.

## 81. The barge-in floor dropped to zero between two sentences of one reply

**Doc:** silent — the gap between sentences is an implementation detail of the speaker.
**Code:** `Aec.process` no longer zeroes `reference_rms` when it has no aligned
reference. Only `silence()` does, and that is called when playback actually ends.
**Why:** the barge-in gate requires the residual to clear `barge_in_ratio` of what is
being played. `reference_rms` is how it knows what is being played, and it was being set
to zero on any frame with no reference to align — which made the floor zero, and the
gate wide open.

That happens every time between two sentences of one reply. The speaker deliberately
holds `speaking` true across that gap, because dropping it would lower the VAD bar in
the middle of Sunday talking (which is why the flag is kept). But playback stops
producing reference for the length of a synthesis, so for a tenth of a second the gate
had no floor at all — at the one moment residual echo is loudest, because the room is
still ringing from the sentence that just ended.

The distinction the code was missing: *no reference right now* and *the reply is over*
are different facts, and only the second should reset the floor. `silence()` is the one
that means the second.

## 82. The window went to 20480, because the ceiling is not a property of the model

**Doc:** Stage 02 as revised by note 76 — the window is 16384, the largest the 4b can
have and stay entirely on the card.
**Code:** `context_tokens = 20480`, and the slices back up to 2048/6144/2048/4096.
**Why:** re-measured with less on the card, and the ceiling moved.

| model | window | on the GPU | tokens/s | card |
| --- | --- | --- | --- | --- |
| 4b | 16384 | 100% | 46.6 | 4430 MiB |
| 4b | **20480** | **100%** | **46.6** | 4560 MiB |
| 4b | 24576 | 85% | 40.3 | 4628 MiB |
| 4b | 32768 | 79% | 32.4 | 4666 MiB |

The number is not the model's and it is not the design's — it is the model, the window
and whatever else wants the card at that moment. 16384 was correct when it was measured
and 20480 is correct now, and the honest conclusion is that this is a number to
re-measure rather than to reason about. `ollama ps` is still the only thing that reports
a spill.

The slices go back to what they were before note 77 halved them, and for the same reason
they were halved: they are sized to fit, not scaled into the window. 16384 of slices plus
3168 of overhead and reply leaves 928 slack inside 20480, and nothing is scaled.

`OLLAMA_KV_CACHE_TYPE=q8_0` with `OLLAMA_FLASH_ATTENTION=1` is the documented next lever
and would roughly halve the KV — on this arithmetic it would put 32768 back inside the
card. It needs an environment variable and an Ollama restart, so it is written down here
rather than done quietly.

## 83. It is called Alexa, and the name is configuration rather than a literal

**Doc:** the assistant is called Sunday throughout.
**Code:** `[assistant] name = "Alexa"`, and `[wake] model = "alexa"` — which is one of the
three phrases openWakeWord ships pretrained, so the wake word and the name agree without
anyone training a model.
**Why:** asked for. What is worth writing down is the split.

**The name is the person; `sunday` is still the program.** The package, the process, the
entry points, the data directory and this repository keep the name they have. Renaming
those would move `%LOCALAPPDATA%\Sunday\`, which is where 200-odd filed turns live, and
buys nothing a user ever sees. What a user sees is three strings, and they now come from
one place: the system prompt, the rendering of a past exchange, and the terminal banner.

**One thing this broke, and it is the interesting part.** `Recalled.render` extracted the
question from a stored turn by splitting on the literal `"\nSunday:"`. Every document
filed before today says `Sunday:` and every document after says `Alexa:`, so a name-shaped
split would have quietly stopped finding the boundary on old documents — returning the
whole turn, including the reply, which is exactly what note 19 exists to prevent. It
splits on the first newline now, which is structural: the document is
`You: <task>\n<name>: <response>` whatever the name is.

The test that caught it was pinned to the literal too. It now files its fixture under the
*old* name deliberately, and asserts both that the current name renders and that the old
one does not survive — because a store with two eras in it is the permanent state of
affairs, not a migration.

## 84. The airlock may see the user's own earlier questions

**Doc:** Stage 05 — "the hint is vetted against exactly the material the prompt is about
to contain: your line for this turn, and the `public` results."
**Code:** and the user's own last three questions, from the session store.
**Why:** without them a question that points at something has nothing to point at.

From a live session, verbatim: *"Check the internet for that."* The agent knew what
"that" was and put it in the hint. The hint is vetted against the cleared material and
correctly stripped every word of it, because the cleared material was one sentence
containing no nouns. **The only text that left the machine was `"price of"`**, and the
reply explained at length that the search had returned nothing about Bitcoin.

That is the vetting rule working exactly as designed and producing a useless turn. The
rule is right — the hint is a model output and the model has seen the private half — so
what had to change is the material, not the check.

The user's own earlier lines are the same kind of thing as `state["task"]`: words the
user typed or said, carrying the `user` label, which `AIRLOCK_VISIBLE` has always
allowed. What is deliberately *not* included is everything else in the session: replies
may quote a file, retrieved turns may be private, tool results have their own label and
their own gate. `SessionMemory.recent_questions` returns tasks and nothing else, and that
is the whole of the widening.

Measured on the same turn, with the same hint:

| cleared material | hint survives as |
| --- | --- |
| the current line only | `price` |
| plus three earlier questions | `bitcoin price analysts` |

End to end afterwards, the query that left was `current bitcoin price 2026` and the reply
had figures in it.

Three, and not more, because the referent of "that" is the last thing said and never four
turns back — and because every line here is a line that can reach the composer.

## 85. The three wake models do not agree on their input names

**Doc:** Desktop 02 — "Ships with `hey_jarvis`, `alexa`, `hey_mycroft` pretrained", as
though they were interchangeable files behind one interface.
**Code, as written:** `self._model.run(None, {"x.1": context})`.
**Code, now:** every input name is read off the graph at load time.
**Why:** they are not interchangeable. Measured on the three shipped models:

| phrase | input | output |
| --- | --- | --- |
| `hey_jarvis` | `x.1` | `53` |
| `alexa` | `onnx::Flatten_0` | `13` |
| `hey_mycroft` | `onnx::Flatten_0` | `39` |

They were exported at different times by different toolchains, and nothing in the release
says so. A name written into this file works for whichever phrase it was written against
and raises `Required inputs (['onnx::Flatten_0']) are missing from input feed (['x.1'])`
for the others — on the audio thread, the first time somebody says the word, after
everything has loaded and reported itself ready.

**The test that existed could not have caught it**, and that is the part worth keeping.
`test_the_wake_word_does_not_fire_on_silence` loads `hey_jarvis` by name, because that
was the default when it was written. The config can name any of three; the test checked
one. It is parameterised over all three now — the same lesson as the compound-message
fast path and the folder rule, arriving through the one interface nobody thought had a
variant.

The rule, stated so it generalises: **when config chooses between files, the test iterates
the choices.** A default is not a sample.

## 86. The follow-up window is thirty seconds, not eight

**Doc:** Desktop 02 as revised by note 70 — the window exists, at `follow_up_ms`.
**Code:** `[wake] follow_up_ms = 30000`.
**Why:** eight was the wrong side of its own trade, and the reasoning behind it was
subtly wrong rather than merely short. Eight seconds is long enough to ask a follow-up
you had *already decided on* — but asking a follow-up means reading or hearing the
answer first, and the window was expiring during the reading. The thing it was sized
against was not the thing that happens.

What it costs, stated rather than discovered later: for thirty seconds after every reply,
sustained speech in the room opens a clip with no wake word. That is a real widening and
it is the point of the feature. The guards are unchanged and they are what keeps it
cheap — `barge_in_ms` of continuous speech to open a clip at all, `min_clip_ms` of speech
to keep it, `lead_in_ms` to abandon it — so a passing conversation costs a dropped clip
and a notice, not a turn. `0` turns the window off and every question needs the phrase.

The test that came with it is about the rule rather than the number: drive silence to
just inside the configured window and just past it, and check the door is open for one
and shut for the other. Changing a duration is only safe if the duration means what it
says.

## 87. A reply is not over when the last block reaches the sound card

**Doc:** Desktop 03 — layer 2 raises the VAD bar "while Kokoro is playing".
**Code, as written:** the speaker reported idle when the last block came back from
`stream.write`, and everything keyed off that instant.
**Code, now:** `Listener.speaking` stays true for `[echo] tail_ms` (400) after playback
stops.
**Why:** `write` returns when the sound card has *accepted* a block, not when it has
played it. At the last write there is still a buffer to come out of the speaker, and then
a room still ringing.

Three defences key off that one flag, and all three switched off together while the voice
was still audible:

- the raised VAD threshold, so residual echo was measured against the ordinary bar;
- the barge-in floor, because `Aec.silence()` had zeroed the reference at the same moment,
  and a floor of zero is no floor;
- the transcript check, which by note 80 only examines clips recorded *while speaking* —
  so the audio most likely to be an echo was the audio it was forbidden to look at.

And the follow-up window opened into exactly that. Note 86 had just widened it from eight
seconds to thirty, which made a narrow hole into a wide one: for thirty seconds after
every reply, the tail of its own voice could open a clip that no layer was allowed to
reject. **Widening one thing turned a latent bug into the reported one** — worth
recording, because the widening was correct and so was every layer. What was wrong was
where one of them stopped.

## 88. Not knowing how loud it is has to close the gate, not open it

**Doc:** silent.
**Code:** while `speaking`, a window counts toward the barge-in burst only if
`reference_rms` is above zero *and* the window clears `barge_in_ratio` of it.
**Why:** the reference lags the first block of a reply by `aec_delay_ms`, so
`reference_rms` is zero for the opening of every sentence. The gate compares against a
fraction of it, and a fraction of zero is zero — so the gate stood wide open at the start
of each sentence, which is precisely where the room is loudest and the canceller has not
converged.

The general form, and it is the same shape as note 81: a threshold derived from a
measurement must distinguish *the measurement is zero* from *there is no measurement*.
The first is information. The second is not, and defaulting it to zero silently converts
"I do not know" into "no floor required".

## 89. Interrupting it while it is thinking

**Doc:** Stage 01 — barge-in is described entirely in terms of talking over the reply:
"VAD runs continuously while Kokoro is speaking."
**Code:** `Listener.busy`, set when the transcript is handed to the graph and cleared when
the turn ends. Sustained speech during it is a barge-in exactly as during a reply.
**Why:** the gap between your question and the first word of the answer is seconds long —
memory, then tools, then generation — and it is the likeliest moment to change your mind.
Nothing was watching it. There was no wake word to say, because the wake path is only
consulted when idle, and no reply to talk over, because none had started. Talking then
did nothing at all.

It is also the easiest part of a turn to get right: nothing is playing, so there is no
echo to tell from a person, and loud sustained speech can only be one thing.

The three states now read as one rule rather than three cases. **Speaking, thinking, and
the tail after speaking are all "a turn is happening", and talking during any of them
interrupts it.** Only a genuinely idle listener needs the wake word.
