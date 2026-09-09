# Architecture notes — deviations found while building

Running list of places where the build departed from `doc/sunday_architecture.html`,
or filled in something the document left open.

**Entries 1–94 have been applied to the HTML.** They are kept here as the record of
why each passage in that document reads the way it does — the HTML states the
decisions, this file states what they replaced. Add new entries below as they come up,
and apply them in a batch rather than editing the HTML mid-build.

An entry can be overtaken by a later one, and where it has been the earlier entry says
so on its own line rather than leaving a superseded number to be read as current.

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

> **Superseded by note 45 on the number, not on the key.** The cap is **8**. Five was
> sized for a turn that meant one lookup; a turn that picks the wrong tool, reads the
> refusal and tries again needs room for the recovery as well as the mistake.

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

## 90. Two readers on one stdin, and the confirmation lost the race

**Doc:** Stage 04 — overwriting asks, and anything that is not clearly a yes is a no.
**Code, as written:** `Keyboard._read` called `input()` in a loop for questions, and
`_confirm` called `input()` again from the turn thread for the answer.
**Code, now:** one thread reads and *routes*. While a confirmation is outstanding the next
line belongs to it; otherwise the line is a question.
**Why:** this is note 73 coming back through the door it opened.

Note 73 moved `input()` onto its own thread so the prompt could be reprinted by whoever
knew the app was idle. That was right, and it quietly gave a resource with exactly one
reader a second one. Typing `y` at an overwrite prompt then went to whichever thread won
the race — usually the reader, which had been blocked in `input()` first — so it arrived
as a *question*, the confirmation waited out its two minutes, refused, and the file was
left alone while "y" was answered as though it were something the user had asked.

Reproduced against the old shape, which is the only way to be sure it was the shape and
not the wording:

    old shape -> confirmation got: []  |  inbox got: [('text', 'y')]

It affected typing as much as voice. `--voice` was where it was noticed, because that is
where a spoken question and a typed answer sit either side of the same prompt, but the
reader thread became unconditional in note 73 and the race came with it.

**The rule worth keeping:** a file descriptor has one reader. If two parts of the program
want what arrives on it, the reader routes — it does not fork. `main.py` had no tests at
all before this, on the grounds that it is a printing loop; the one piece of it that was
not printing is the piece that broke.

## 91. The name was written down in four more places than note 83 counted

**Doc:** note 83 — "what a user sees is three strings, and they now come from one place:
the system prompt, the rendering of a past exchange, and the terminal banner."
**Code, as written:** four more, all of them literals. `list_capabilities` opened with
"These are the tools Sunday has"; nine of the sandbox's refusal strings said Sunday
("that path is outside the folders Sunday may open", "Sunday will not delete one",
"Sunday only deletes single files"); and `runtime._normalise` held the set
`{"you", "sunday"}` to strip a speaker prefix.
**Code, now:** no name in any of them. The tool result and the refusals are written in
the second person, and `_normalise` reads `[assistant] name` from config.
**Why:** note 83 counted the places the name is *printed* and missed the places it is
*spoken*, which is a larger set the moment a 2b is doing the speaking.

Three different failures, and it is worth separating them because the fixes are not the
same shape.

**The capability list and the refusals are scripts.** They exist to be relayed —
`list_capabilities` literally ends "Relay this in plain sentences", and every refusal in
`files.py` ends by telling the model what to say. This codebase's oldest observation is
that the 2b copies whatever wording is nearest, so a name in a script is a name the user
hears. "Sunday is not allowed into that folder", in a voice that answers to Alexa.

The fix is not to interpolate the configured name into them. It is to take the name out:
these are sentences addressed *to* the assistant about itself, and the second person is
both shorter and incapable of going stale. `refused: that path is outside the folders you
may open` needs no configuration to stay true.

**`_normalise` is a comparison, and that one is note 83's exact failure.** It strips a
leading speaker word so a recalled question compares equal to the question just asked. The
literal `"sunday"` compiled, passed every test, and silently stopped matching on the day
of the rename — the same shape as the `"\nSunday:"` split note 83 was written about, in a
function nobody thought to look at twice. It reads the configured name now.

It matters more than it looks, because the wake phrase *is* the assistant's name and the
transcriber renders the tail of it: note 78 measured Parakeet turning the pre-roll into
`"Surface what's the weather in Jakarta?"`. "Alexa how much is silver" is the same
question as "how much is silver", and the deduplication has to see that.

**The rule:** the name appears in prose that reaches a model or a person exactly once, in
`[assistant] name`. Anywhere else, write the second person. Two tests enumerate the paths
rather than the incidents — every refusal `files.py` can produce, and the capability list —
and both assert the word does not appear at all, on roots chosen so that a hit is prose
and not an interpolated path. The first of those shares its helper with note 92.

## 92. The refusal-wording test listed four incidents and not the rule

**Doc:** the convention this repository states about itself — "a test written from the
incident checks the incident; write it from the rule".
**Code, as written:** `test_every_refusal_tells_the_model_what_to_say` produced four
refusals and checked those.
**Code, now:** it produces every refusal `files.py` can return, from a shared helper, and
two tests read it — the wording rule and the no-name rule from note 91.
**Why:** the four were the four from the bug reports, and the one the list left out is the
one that matters most.

`copy_file(".env", "notes.txt")` — the credential *source* — is the single refusal in this
file that exists because taint cannot fix a laundering after the fact. It was covered
behaviourally in `test_transfer.py` and not by the wording rule, which is exactly the gap
note 60 found in the fast paths: a rule guarding two call sites out of three, for a whole
milestone, with a green suite the entire time.

The helper now enumerates nine paths: outside a root, one level above a root, a traversal,
and a credential at each of the six ends that can name one. When a rule says *never*, the
test walks the paths.

## 93. Four numbers still described the 16384 window

**Doc:** Stage 02 as revised by note 82 — the window is 20480 and the slices are sized to
fit it.
**Code:** `budget.py` opened "The window is 16384"; `config.py` opened the
`context_tokens` comment with "16384, and the number belongs to the model rather than to
the window" and closed the overhead comment with "at 16384 the same 2400 is 15%"; and
`config.example.toml` said the slices "come to 12800 of the 13216 left after overhead and
the reply".
**Why:** this is note 62, verbatim, one window later. Note 62 was written because four
numbers in the source still described the 8k window after note 51 widened it. Note 82
moved the window again, `config.py`'s `Memory` block was updated, and the four numbers
around it were not.

The arithmetic that is now written down, because it is checkable: 20480 of window, less
2400 of overhead and 768 for the reply, leaves 17312. The five slices come to 16384. 928
is slack, and every configured number is the number in use.

What note 62 concluded and this repeats: a number that lives only in prose is a number
nobody rechecks, and it is what a person reads *instead of* the document. `scaled_to`
firing silently is the failure these comments exist to prevent, so a comment that
describes the wrong window is worse than no comment. **When a measured number moves, grep
for it** — and the grep is `16384`, not "the window".

Also stale in the same pass, for the same reason: `vad.py` and `listener.py` still
introduced Moonshine as the transcriber four notes after 78 made Parakeet the default.
Both now name neither where the point is general, and say "Moonshine, still selectable"
where the point is specifically Moonshine's — the empty transcript for a padded clip,
which is why `_trim` exists and why it stays for either model.

## 94. The wake threshold had a second home in a default argument

**Doc:** note 69 — the threshold is 0.3, measured, and it lives in `[wake] threshold`.
**Code, as written:** `WakeWord.__init__(self, phrase="hey_jarvis", *, threshold=0.5)`.
**Code, now:** both default to `[wake]`, read from config at construction.
**Why:** 0.5 is the number note 69 exists to have removed. It sat one hundredth above
where the phrase actually peaks on this microphone, fired about one time in three, and
looked exactly like a hardware fault — and it was still there, in the signature, as the
answer for any caller who omits the keyword.

Every call site passes both today, so nothing was broken. That is precisely the condition
under which a wrong default survives: it is unreachable until somebody writes the fourth
call site, and then it is a bug on the audio thread, at the first word anybody says.

The general form is the one note 62 keeps restating. A measured number has one home. A
copy of it in a default argument is a second home that no measurement will ever update,
and it does not have the decency to be wrong loudly.

## 95. Two orb states were specified and had no producer

**Doc:** Desktop 04's state table lists `wake` — "single sharp expansion, 180 ms, this is
the 'I heard you' signal" — and `speaking`, "concentric rings pulse with output amplitude".
**Code, as written:** the ear turned a wake event straight into `{"state":"listening"}`,
and `level` carried the microphone and nothing else.
**Code, now:** the ear emits `wake` and then `listening`; `level` carries `out` beside
`rms`.
**Why:** this is note 55 in a new place. A line that is written and never emitted looks
exactly like a step that never happens, and both of these were in the document from the
first draft with nothing on the other end.

The wake flash is the one that costs something real. The whole argument for a wake word is
that it answers *before* anything slow starts — and the first slow thing is loading a
quarter of a gigabyte of transcriber. Emitting only `listening` meant the window said
nothing until that was done, which is the interval a person is most likely to conclude the
microphone is broken.

The amplitude was already measured and thrown away. `Listener.reference_rms` is computed
every frame for the barge-in floor; it is the level of what is actually coming out of the
speaker. Rings driven by a timer would have looked identical and meant nothing, which is
the distinction the level meter was added for in the first place — a state that says
`listening` over a flat bar is a device that was never opened.

The test is written from the rule and not from either bug: it reads the states the orb can
draw off `orb.js`, reads the states the package can emit off the Python, and compares the
two sets in both directions. A state nothing produces and a state the window cannot draw
are the two ways this goes wrong; enumerating them is what will catch the third. Reading
one spelling of the emission was itself the first failure — `thinking` is written
`value="thinking"` in the runtime and `"value": "thinking"` on the socket, and a scanner
that knew one of them reported a state that runs on every turn as unproduced.

## 96. The window is one window, and the WebView never touches a file

**Doc:** Desktop 01 — the sidecar writes `{port, token}` to `handshake.json` and "the shell
reads it". Desktop 04 — compact mode is "a small frameless always-on-top window showing
just the orb".
**Code:** Rust reads the handshake and hands the port and token to the window through a
`connection` command. Compact mode resizes, undecorates and pins *the same* window.
**Why:** two readings of the document that would both have worked, and both are worse.

A WebView that can read `handshake.json` can read everything next to it, including
`google_token.json` and the Chroma store. So the file is read once, in Rust, and what
crosses into the page is two values. In a browser — which is how this window was
developed, because a reload is a tenth of a second and a Rust build is not — the same two
come off the query string, exactly as `web/debug.html` takes them.

Compact as a *second* window would be two orbs, two sockets and two things to keep in
step, for a feature whose whole content is "the same orb, smaller". One window resized has
no synchronisation problem to get wrong.

The stale handshake is the trap in this area and it is worth writing down. The file is
rewritten each launch, but the previous one is on disk until then — so a shell that reads
it immediately after spawning finds the *old* port, and connects to nothing or, worse, to
something else. The handshake only counts once its `started` is no older than the moment
the child was spawned.

## 97. Closing the window hides it; quit is the one that stops the sidecar

**Doc:** Desktop 01's lifecycle covers start, health, crash and quit. It does not say what
the close button does, and Desktop 05 puts *show* in the tray menu.
**Code:** the close button hides the window; `Quit`, in the tray menu or the window's own
control, sends `shutdown` and then waits.
**Why:** a tray menu with *show* in it only makes sense if something can hide. And the
ordering is the part that matters: `shutdown` is what flushes the session summary into
Chroma, so a quit that kills the process first loses the conversation every time. The
shell waits five seconds for the child to go and then stops being polite — the same number
Desktop 01 already gives for the force-kill.

That ordering has one consequence worth stating, because it is the same mistake as
emitting notices after the sinks were cleared: the tray's quit cannot simply kill. It asks
the window to say goodbye over the socket, waits, and only then stops waiting.

## 98. Calendar and mail are `local`, and that is not a technicality

**Doc:** Stage 04's tool table gives both a `private` provenance and does not name a scope.
**Code:** `scope="local"` for both, with `provenance="private"`.
**Why:** scope answers "is this the web door", not "does a packet leave this machine". The
door exists because a *query composed here* can carry your private things out with it. The
calendar is your own account, reached with your own token, and there is nothing to compose
— the request is "the events between these two times".

`private` is what does the actual work, and it is structural rather than careful:
`AIRLOCK_VISIBLE` is `user` and `public`, so nothing either tool returns can reach the
composer at all. Labelling them `external` would have put them behind the airlock, which
would have been a category error in the other direction — the airlock's job is to compose a
query out of cleared material, and there is no query here to clear.

The test enumerates every tool that reads the user's own things — files, folders, calendar,
mailbox — rather than naming the two new ones, so the next such tool is covered before it
is written. This is note 92's lesson applied in advance for once, rather than after.

## 99. New keys `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, and consent is not a tool

**Doc:** Stage 04 — "OAuth desktop flow, once. Refresh token cached at
`%LOCALAPPDATA%\Sunday\google_token.json`."
**Code:** the *client* credentials are two new `.env` keys beside `DEEPSEEK_API_KEY`; the
consent flow is a console script, `sunday-google`, and never a tool call.
**Why:** the document says where the token ends up and not where the client comes from,
and they are different secrets with different lifetimes — the token is per-user and
earned, the client is per-installation and issued.

Consent cannot happen inside a turn. It needs a browser window that may never open, on a
machine that may have no browser, while a model waits on a tool result and the two-minute
confirmation timeout runs. So a missing sign-in is a refusal that names the command to
run — which is the rule that a refusal with no way to act on it is the same failure as a
prompt with no way to answer it.

The flow is a loopback redirect rather than a pasted code, so the authorisation code
reaches a socket on this machine and never a clipboard or a shell history. `access_type`
and `prompt` are both set, because without them Google returns no refresh token on a second
consent and the whole point is that this happens once.

Two smaller things the document did not have to say and the code had to decide. Message
bodies go through the key-shape scrubber *before* the agent sees them, not after: mail is
the commonest way a real credential arrives in a context window, and a key that has been
read is a key that can be repeated. And event descriptions are dropped, as Stage 04 asks —
it is the field most likely to hold something you would not want summarised aloud in a room
with other people in it, and it is never the answer to "what is on today".

## 100. `overhead_tokens` has moved a fourth time — 2400 to 2600

**Doc:** Stage 02 says the reservation has moved three times, and gives 2400.
**Code:** 2600, because calendar and mail cost 313 tokens of bound schema between them —
1239 to 1552 — and the slack under 2400 had fallen to 102 tokens.
**Why:** this is exactly what the reservation test exists to force, and it worked: the
suite was still green at 102 tokens of headroom, and the number moved because the
measurement was taken rather than because anything broke.

The arithmetic, since it is checkable: 20480 of window, less 2600 of overhead and 768 for
the reply, leaves 17112. The five slices come to 16384. 728 is slack, and every configured
number is still the number in use — `scaled_to` does not fire, which is the property note
82 asked to keep true.

And note 93, once more, in the same pass and for the same reason: `2400` and `17312` and
`928` and `1239` and `2260` were written down in seven places between the config default,
two TOML files, two docstrings and a test's own prose. The grep is for the digits.

## 101. Return sends explicitly rather than by implicit form submission

**Doc:** Desktop 04 — "a text box that is always usable".
**Code:** a real form with a real submit button, *and* a keydown handler on the input.
**Why:** the markup alone is correct and a browser would submit on Return unaided. But
"unaided" is behaviour no harness in this project can drive — a synthetic key event does
not trigger implicit submission — and Return is the key a person actually presses.

Belt and braces would not be worth an entry. The reason it is here is the rule it belongs
to: the last thing in this repo that nobody could run was the command in the README, and
it was wrong. A path that cannot be exercised is a path nobody checks, so the path was
made exercisable rather than reasoned about.

Found the same way and worth the same sentence: compact mode hid its controls with "all
but the last two", which depended on how many buttons happened to be rendered — and
outside the shell, where the window's own two do not exist, it hid the wrong two. The
buttons that survive are marked now rather than counted.

## 102. The sidecar listens even when it cannot answer, and that is what makes a first run possible

**Doc:** Desktop 05 — "First launch downloads Parakeet, Kokoro, Silero and the wake word,
and pulls `qwen3.5:4b` via Ollama — with a progress screen, because it is a few gigabytes."
**Code, as written:** `sidecar.main` ran `runtime.preflight()` and returned 1 if it raised.
**Code, now:** the failure is printed and the socket comes up anyway; `ready` carries a
`setup` block, turns are refused with a message naming which half is missing, and two new
messages — `fetch_models` in, `fetch` out — drive the download.
**Why:** the two halves of that sentence contradicted each other. A first launch is
precisely the launch on which the model has *not* been pulled, so preflight fails, so the
process exits, so the shell reports "the sidecar exited before it was listening" — and the
thing that would pull the model is on the other end of the socket that never opened.

Three new protocol lines, and the shape of them is the point. `setup` rides on `ready`
rather than sitting behind a request, because the window has to know before it lets anybody
type: a socket that accepts a question it cannot answer is worse than one that says what is
short. And it reports three separate facts — Ollama running, model pulled, voice models
present — where the code had one `OllamaDown`. That collapse is right for a startup check
and wrong for a screen: "start Ollama", "pull four gigabytes" and "download a quarter of a
gigabyte of ONNX" are three different things to go and do, and only the first is one this
app cannot do for you. So `Agent.reachable` and `Agent.model_present` are separate and
public, and `preflight` is left alone.

The download lives in the sidecar and not in the shell. The URLs, the sizes and the rule
that only the *configured* transcriber is wanted — note 79, and 900 MB if you get it wrong —
are all on that side already. A downloader in Rust would be a second copy of all of it,
kept in step by hand.

Two smaller decisions that the document does not settle and the build had to. The screen
appears when the *voice* models are missing too, even though they never block a typed
question — a first-run screen that only appeared when the app was broken would never mention
them, and the wake word would simply not work, which is exactly the silent downward failure
milestone 7 spent itself learning to say out loud. It is skippable in that case and not in
the other, because there has to be something behind it to skip to. And the download is one
at a time, guarded like a turn: there is one network, one disk and one `.part` file per
model, and two clicks racing for it would defeat the rename-on-complete that exists so an
interrupted fetch cannot leave a truncated model which loads and then produces nonsense.

One thing the test doubles taught, which is the reason `reachable` is public rather than a
call to `agent._client`. Written as private access across the boundary it worked, and broke
six socket tests whose fake agent has no client — which is the right complaint from the
right place. A stand-in that does not track the interface it stands in for stops testing the
thing it replaced, so `Talker` grew both methods and `SlowTalker` now subclasses it instead
of copying it.

## 103. The window is 23552, and 70000 was measured rather than argued about

**Doc:** Stage 02 — the window is 20480, the largest that stays entirely on the card, and
`ollama ps` must read 100%.
**Asked for:** a 70000-token window and a Q6_K quantisation, on the grounds that the
machine has plenty of space.
**Code:** 23552, still Q4_K_M, with the slices resized to 2560 / 7680 / 2560 / 4608.
**Why:** the space in question was disk, and the constraint is VRAM. Six gigabytes of it,
on a laptop 3050, shared with the desktop.

Measured before anything was changed, because that is what this section of the design has
said since milestone 1:

    ctx      on GPU   tok/s
    20480     100%     45.5
    22528     100%     46.6
    23552     100%     46.1
    24576      85%     40.1
    32768      79%     31.9
    40960      71%     25.4
    70000      60%     16.8

70000 runs. Turns complete and nothing errors, which is exactly the problem — 40% of the
model is on the processor, every reply is 2.7× slower, and the only thing in the system
that would tell you is `ollama ps`. That is the failure mode note 82 exists to describe,
and it was reproduced here rather than predicted.

**The ceiling is a cliff, not a slope.** 23552 is 100% and 24576 is 85%: one step up the
1024 grid costs 15% of the model and 13% of the speed. So the shipped number sits one grid
step below a wall, with 1279 MiB of card spare when measured with a browser open. Note 82's
warning that the ceiling depends on whatever else wants the card is not a caveat here, it is
the operating condition.

Q6_K was not a decision, it was unavailable: Ollama publishes `q4_K_M`, `q8_0` and `bf16`
for `qwen3.5` and nothing between. Verified against the registry — the two working tags
return 200 and every Q6_K spelling returns 404 — rather than inferred from a failed pull.
It could be imported from a GGUF, but Q6_K is roughly 1.2 GB larger than Q4_K_M, which
spends the headroom this window move just used and puts the model off the card at any
context worth having. The interesting half of that finding is that **the two requests were
in tension with each other**: a bigger window and a bigger quantisation compete for the same
six gigabytes, and satisfying either fully means abandoning the other.

The slices moved with the window, which is note 82's other rule and the one that is easy to
skip: `scaled_to` is silent when it fires, so leaving them at 16384 inside 23552 would have
worked, wasted the window, and left `config.toml` describing allowances nobody was using.
They are **not** grown to fill the window either, and the reason is prompt eval rather than
VRAM — roughly 0.15 ms per token paid before a turn says a word, so 19456 of slices is about
3.3 s worst case and much beyond that is over four seconds on every turn of a long session.
That is the trade Stage 02 already described; this only moves along it.

## 104. The shell compiled, and the two things it got wrong were both about the boundary

**Doc:** Desktop 01 — a Rust shell that owns the window and reads the handshake, and a
WebView that owns none of it.
**Code, as written:** it did not compile, and then it compiled and did not connect.
**Why it is worth an entry:** both faults were at the same seam, and neither was visible
by reading.

The compile error was one line: `app.global_shortcut()` needs `GlobalShortcutExt` in
scope. One error in about six hundred lines of Rust written without a toolchain is a
better rate than it deserved, and it is the boring half of this note.

The interesting half is that it then built, launched, spawned the sidecar, wrote the
handshake — and the window never connected. Everything *looked* right: `sunday.exe`
running, `sunday-sidecar.exe` running, the socket LISTENING. What was missing was an
ESTABLISHED connection, and nothing in the app says so out loud. `globalThis.__TAURI__`
does not exist unless `withGlobalTauri` is set, so `discover()` fell through to its
browser fallback, found no `?port=` in the query string, and sat in `connecting` forever.

That fallback is the thing to notice. It exists so the window can be developed in an
ordinary browser tab, which is genuinely how it was built — but a fallback that silently
catches the case it was not written for turns a hard failure into a soft one. The
diagnosis was `netstat`, not the app: **LISTENING with no ESTABLISHED** is the shape of
this bug, and it is worth knowing because every component reports itself healthy.

## 105. The window is not what decides whether the model fits. The desktop is.

**Doc:** note 103 — 23552, measured, the largest window that stays 100% on the card.
**Then:** the first turn through the finished shell came back `27%/60% CPU/GPU`.
**Now:** 23552, still, and 100% on the card — with the shell running.
**Why:** note 103 was measured in a quiet moment, and the shipping condition is not quiet.
It has its own WebView2, which is a Chromium, and Chromium wants the GPU.

The measurement that settles it, taken with the app *and* a browser open:

    ctx      model on card
    16384      3.5 GB
    18432      3.6 GB
    20480      3.7 GB
    22528      3.8 GB
    23552      3.8 GB

**Three hundred megabytes across the whole range.** The weights are about 3.4 GB and the
KV cache for this model is small, so the window is very nearly free — while the rest of
the desktop moved between 240 MiB and 3232 MiB during this session, an order of magnitude
more than the thing being tuned.

Which reverses the conclusion note 103 reached by a different route. That turn did not
spill *because the window was 23552*. It spilled because WebView2 had started thirty
seconds earlier and, with Brave also up, only 2912 MiB was free — and 20480 needs
3.7 GB, so the supposedly safe number would have spilled in exactly the same breath. The
choice between 20480 and 23552 buys 3072 tokens of context for about 100 MB. It is not
the lever.

The rule note 82 states — re-measure after anything changes — survives intact and gains a
sharper edge: **measure with the app running, because the app is one of the things
competing.** A ceiling measured on an idle desktop is a ceiling that is wrong the moment
the product it belongs to is open.

The lever that would actually matter, if a much larger window is ever wanted, is
`OLLAMA_KV_CACHE_TYPE=q8_0`, which halves the KV rather than shaving a tenth of a
gigabyte off it. That has still not been tried.

## 106. A hotkey another program already owns is the quietest failure in the app

**Doc:** Desktop 05 — `Ctrl+Alt+Space` focuses the window and starts listening.
**Found:** on this machine something else already holds it, so registration fails.
**Code, as written:** `eprintln!` and carry on.
**Code, now:** carry on, and tell the window, which puts it in the transcript.

Carrying on is right — a window that refuses to start over a keyboard shortcut is worse
than a window with no shortcut. Printing it to stderr is not: a packaged Tauri app has no
console, so the entire failure is that you press three keys and nothing whatsoever
happens. There is no error, no log you can reach, and every component involved is
working.

This is the same failure this codebase has now catalogued in three other places — the
withdrawn tool nobody was told about, the redaction emitted after the sinks were cleared,
and the four voice faults that all presented as a blank terminal. The pattern is constant:
**a capability that silently does not exist is indistinguishable from one that is
broken.** So the reason travels to the one place the person who pressed the keys is
looking.

## 107. The window grew a second set of window controls

**Doc:** Desktop 04 — the window has a transcript, an input row and a mode toggle;
*compact* is "a small frameless always-on-top window showing just the orb".
**Code, as written:** the full window kept the title bar Windows gives it, and the header
*also* drew a minimise and a close of its own.
**Why it matters:** the two closes did different things. The native one hides to the tray,
which is what this app wants -- it is meant to go on listening. The one an inch to its left
sent `shutdown` and quit. Two identical-looking buttons, a centimetre apart, one of which
ends the session and one of which does not.

Found by the app exiting when it was not expected to, which is the mildest possible version
of this bug and the reason it is worth writing down: the next person to meet it would have
lost a conversation rather than a test run.

The fix is the reading the document already gives. *Compact* is the frameless one; the full
window keeps its title bar and does not duplicate it. Quit lives in the tray menu, which is
where the ordered shutdown belongs anyway -- ask over the socket, wait, then stop waiting --
so nothing was lost by removing it from the header.

The general shape, since this codebase collects them: **when the platform already draws a
control, drawing a second one is not an addition, it is an ambiguity.** The CSS drag region
that made the header look like a title bar was written for the compact window and quietly
implied a frameless full one, which is how the duplicate got there.

## 108. The assistant was given a character, and it cost 95 tokens

**Doc:** Stage 03 — keep the system prompt short; it grew to 673 tokens once and the model
began reciting it at the user.
**Asked for:** an assistant that can chat, is funny, and has an identity, rather than one
that "feels dead" and only calls tools.
**Code:** 276 tokens to 371, and the overhead reservation from 2600 to 2816.

The constraint is what shaped the answer. There is no room for a page describing a
personality, and a page would not work anyway: a model imitates the register it is given
far more reliably than it follows a description of one. So the character is carried by the
shape of the sentences it is asked to write — "warm, quick and a little funny", "you tease
lightly, you never grovel", "short sentences, say the interesting part first" — which is
two lines rather than twenty.

It works, and the evidence is one real reply. Asked to move a file, it said: *"Done.
gold.txt is in the work folder now. Was it ready to be moved, or was I just being
helpful?"* The old prompt produced "The file has been moved successfully."

Two of the new lines are not personality at all and are the reason this note is filed
under behaviour rather than taste. **"If you say you are about to do something, do it in
the same turn"** and **"talking is a real thing to do"** are the two failures the previous
prompt actually produced — a cheerful promise with no tool call behind it, and an assistant
that treated a greeting as a work order.

## 109. A model that cannot narrate alongside a tool call needs a second generation

**Asked for:** the assistant should talk *during* a job rather than going silent from the
question to the finished answer.
**The obvious implementation, which does not work:** use the message content that comes
back alongside the tool calls. The tool loop has always produced that and always thrown it
away, so passing it on looked like a two-line change.

Measured on this model, a response carrying tool calls carries `content=''` **every time**.
With the system prompt asking for a line. With an extra system message ordering one
explicitly, immediately before the call. The chat template puts the tool calls where the
message would be, and there is nothing else in the response.

That matters more than the feature does, because a sink fed by nothing is note 55 in a new
costume: `_interim` would have been a mechanism that never fired, indistinguishable from a
mechanism that was broken, sitting in the code looking finished. Two of those have already
been found in this repo.

So the line is generated on purpose — one call, no tools bound, forty tokens — before the
first tool of a turn runs. It costs about a third of a second against several seconds of a
silent orb, and it is still the model's own voice rather than a template the runtime fills
in. Measured, from a real turn: *"I'm reading gold.txt right now."*

Once per turn, and specifically before the *first* tool, because that is the longest
silence: the person has just stopped speaking and nothing whatsoever has happened yet. The
gaps between later rounds are shorter and already have a moving orb in them.

**It is `Agent.one_liner`, not another `Agent.chat`, and that distinction is load-bearing.**
Written as a second `chat` in the middle of the loop it silently consumed the next reply of
every scripted test double, and four loop tests failed for reasons that had nothing to do
with the loop. A double that does not implement `one_liner` is a double that does not
narrate, which leaves those tests testing what they were written to test.

## 110. Turning the microphone on at startup makes ambient speech expensive

**Asked for:** the app should start with the machine, with the microphone open, so it
answers its name without anybody clicking anything.
**Code:** `[audio] listen_on_start`, and the sidecar opens the ear as it binds the socket
rather than waiting for a client to ask for voice mode.
**Why it is here and not just in the changelog:** it changes what the *default* failure
looks like, and the change is not small.

Two things happened within minutes of switching it on, in an ordinary room:

Speech from somewhere else in the room was transcribed and run as a turn. "And like yeah,
yeah, that's the thing. On the image center." arrived twice as user input. The clip guards
did their job — the turn was discarded and the trace said so — but the turn *ran*.

And a two-step job was cancelled by barge-in while it was working. The tools both ran and
the file was written correctly; the turn was then discarded, so nothing about it was
remembered. **Side effects applied, no memory that they were.** That combination is not new
— it is what `committed: False` has always meant — but an open microphone in a room with
other people in it is what makes it likely rather than theoretical.

None of the guards are wrong. Barge-in during *thinking* is deliberate and note-worthy in
its own right: the gap between a question and the first word of an answer is the likeliest
moment to change your mind. The follow-up window is deliberate. The VAD threshold is
measured. They simply add up to an assistant that, with the microphone open by default,
treats a conversation happening near it as instructions.

So the default stays `false` in the shipped example config and is `true` in the local one,
which is the honest split: it is what was asked for on this machine, and it is not
something to switch on for somebody else without telling them. If it fires at the
television, the levers are `[audio] vad_threshold`, `[wake] follow_up_ms`, and mute — which
is a real toggle that stops the capture stream rather than ignoring it.

## 111. The window was rebuilt, and every ugly thing about it was structural

**Asked for:** it looked ugly; the font was small and bad, the orb was stiff and
traditional, the backstage panel was complicated and the whole thing had poor UX. Make it
look like Claude.

None of that turned out to be a matter of taste, which is why it is a note. Each complaint
had a specific cause:

**The palette was cold.** `#0e0f12` is a blue-black, and amber on blue-black looks like a
warning light on a server. The greys have red in them now — `#262624` — and the three
load-bearing colours were warmed to sit in it. Amber is still work on this machine, teal is
still something leaving, red is still a refusal, and nothing else is ever teal. The
meanings did not move; only the hues did.

**The orb looked like a status LED because it had an edge.** A one-pixel stroke around a
single radial gradient is a widget. Light that falls off into the background over forty
pixels is a light. There is no stroke anywhere in it now — three layered gradients, a
bloom, a body and an offset highlight — and the particles are drawn in two passes, in front
of the core and behind it, so they have depth instead of sliding around a flat ring.

**It looked stiff because every animation was one sine wave.** One sine is a metronome, and
a person reads it as a machine. The breathing is three sines with incommensurate periods
now, so it never quite repeats.

**The backstage panel shouted.** Every line carried an uppercase letter-spaced 10px heading,
so a turn produced twenty small headings and no shape at all — the eye had nowhere to rest
and it read as an error log. The step is a quiet prefix now, the text is the size of text,
and only memory, tools and the closing line carry a colour. It is also closed by default:
the trace is still emitted, because it is the only thing that separates "memory was read
and had nothing" from "memory could not be opened", but a running column of machine
narration beside a conversation is what made this feel like a debugger.

**The layout spent a fifth of the window on sixty pixels of information.** The orb had a
190px band to itself; it now sits on one line with the name and the controls. The transcript
is a 660px reading column rather than the full width, because long lines are the fastest way
to make prose unreadable and this is mostly prose. The four controls in a row read as a
form, so they are inside one rounded field that reads as somewhere to talk.

**The name is not written down anywhere in the window.** It arrives on `ready`, from
`[assistant] name`. Note 83's rule reaches the shell too, and the markup nearly grew a
literal "Alexa" before it was caught.

## 112. The corrupt memory store was a copy, and force-killing the sidecar is what broke it

**Reported:** long-term memory was corrupt — `PRAGMA integrity_check` returning B-tree
damage across four tables, `count()` returning 0 against a 2.3 MB file, and the trace
saying "database disk image is malformed. Nothing was recalled, and that is a fault, not an
empty cabinet" on every turn.
**Actually true:** the real store at `%LOCALAPPDATA%\Sunday\memory` is intact. 411 turns
filed, `integrity_check` clean, recall returning five hits at sensible distances for an
ordinary query. It was never broken.

Two separate mistakes, and the second is the one worth keeping.

**The store being examined was not the store the app uses.** This project is developed
through a tool whose filesystem writes are redirected into a packaged app's container —
`...\Packages\Claude_…\LocalCache\Local\Sunday`. Windows copies a file into that container
the first time something inside it writes, so a sidecar launched from there gets a private
copy-on-write duplicate of the Chroma store, and the real one stops changing. Everything
diagnosed for a whole session was that duplicate. The give-away was visible the moment
anything native looked: `Get-ChildItem` shows the redirected entries with a `Target`
pointing into the package, and the real directory has a `memory` the container view does
not.

**The duplicate was corrupted by killing the sidecar, repeatedly, mid-write.** `taskkill /F`
was used a dozen times during this session to restart things quickly. SQLite does not
survive that reliably, and Chroma is SQLite. Which means the corruption was not a mystery
to be dated from logs — it was manufactured, on purpose, by the debugging.

That is an argument *for* the shutdown path the shell already has, rather than against
anything: `shutdown` over the socket, wait for the process to go, force-kill only after five
seconds. It exists so the session summary reaches Chroma. It turns out to also be what keeps
the file readable, and the cost of skipping it is now measured rather than assumed.

Two rules come out of this, and the first one is general.

**Diagnose the artefact the product uses, not the one the tooling touched.** Every number in
that diagnosis was correct and every conclusion from it was wrong, because the file being
measured was not the file in question. Before reporting that a user's data is damaged,
check the path natively.

**And the telemetry log should have caught it either way.** `logs/*.jsonl` records
`retrieved` and `retrieval_scores` per turn and has no field for *why* retrieval returned
nothing — so a store that could not be opened and a store with nothing close enough produce
identical log lines. The backstage trace distinguishes them, in words, and is the only thing
that does. That asymmetry is exactly what the trace was built for, and it is also a gap in
the log: the one channel that persists is the one that cannot tell a fault from an empty
cabinet.

## 113. A spoken turn appeared twice, because two things announced it

**Reported:** speaking one sentence put two identical messages in the transcript.
**Cause:** two producers of `partial`. The ear announced the transcript in `_deliver`, then
started a turn, and `Sidecar._run_turn` announced it again. A typed question appeared once;
a spoken one appeared twice.
**Fix:** the turn is the producer, because it is the thing that happens in both modalities.

This is the duplicate confirmation card again, exactly — two emitters for one fact — and it
survived because **no test could have caught it**. `FakeEar.heard()` calls `on_transcript`
directly and never runs `_deliver`, which is where the second one lived. The existing test
asserted `partials and partials[0]["text"] == ...`: that *a* partial arrives, never that
only one does. A double that skips the code under test, and an assertion that counts to at
least one.

So the test is a source scan instead, and it is written as the general rule rather than
this instance: **a message that states one fact about a turn is emitted from one place.**
`partial`, `ready`, `confirm` and `pong` are enumerated; the count of emitters is the
assertion.

`done` is deliberately excluded and the reason is worth recording, because it looks like an
exception being waved through. The runtime emits it at the end of every turn it runs, and
the socket emits it for the two turns the runtime never sees -- Ollama unreachable, and
setup unfinished. Those are not a second copy of one fact; they are the only copy, for a
turn that never reached the thing that would otherwise say it. A client stops listening at
`done`, so it has to arrive either way.

## 114. Compact mode was a trap, and the drag region is what sprang it

**Reported:** no way back out of compact mode.
**Cause:** the orb was the whole window *and* carried `-webkit-app-region: drag`. A drag
region swallows mouse events before the page sees them, so the click handler on it could
never fire. The way out was a double-click on an element that could not receive clicks.

Three ways out now, because this is the state in which having no way out strands the entire
app -- a small circle, always on top, with no menu and no title bar:

- a strip at the top of the compact window, which is the drag handle and carries an
  explicit **Expand** button that is marked `no-drag`;
- the orb itself, which is no longer a drag region and takes a single click;
- **Leave compact mode** in the tray menu, and a single left click on the tray icon.

The last one is the important one and generalises past this bug. **A mode that can make the
window hard to click needs an escape that is not in the window.** The tray already existed;
it just had no reason to know about compact.

The same reasoning fixed quitting, which was reported in the same breath. Closing the window
hides it to the tray, which is right for something meant to keep listening -- but note 107
had removed the header's close button to stop it duplicating the native one, and that left
*no* quit anywhere except a tray menu that had to be discovered. There is a labelled **Quit
Alexa** button in the window now. It is not a duplicate of the native close: they do
different things, and it says which one it is.

## 115. The title bar is drawn by Windows, so CSS cannot reach it

**Reported:** the title bar should be the colour of the app.
**Why it needed code:** the bar is painted by the window manager, above the WebView and
outside the document. A dark window under a light grey caption strip reads as two programs
stacked in one frame, and no stylesheet can touch it.

`DwmSetWindowAttribute` with `DWMWA_CAPTION_COLOR`, `DWMWA_TEXT_COLOR` and
`DWMWA_BORDER_COLOR`, which arrived in Windows 11. On anything older the calls fail, the bar
stays the system colour and the app is otherwise fine -- so the results are ignored rather
than reported. The border is set a little lighter than the background on purpose, or the
window has no edge at all against a dark desktop.

Two traps, one of which would have shipped. **A `COLORREF` is `0x00BBGGRR`, not RGB** --
writing the hex the way it appears in the stylesheet swaps red and blue, which looks like a
deliberate colour choice rather than a bug. And **leaving compact mode builds a new frame**,
painted in the system colour, so the caption has to be repainted every time decorations come
back. Painting it once at startup looks correct until the first trip through compact mode.

## 116. The program is Alexa; the project is still Sunday

**Asked for:** the app should be called Alexa everywhere, not Sunday.
**Changed:** `productName`, the window title, the executable, the installer, and the tray
menu.
**Not changed, deliberately:** the repository, the Python package, the sidecar process, and
`%LOCALAPPDATA%\Sunday`.

The data directory is the one that matters and the reason is not tidiness. It holds the
memory store, which has several hundred filed turns in it. Renaming the folder orphans all
of them, silently -- a fresh empty store would be created next to the old one and everything
would look like it was working. The name a person sees and the name a path uses are
different facts, and note 83 already established that the first one is configuration:
`[assistant] name` is what the window shows, and it now travels over `ready` rather than
being written into the markup, where it nearly ended up as a literal "Alexa".

## 117. The orb is a web now, and white at rest

**Asked for:** a white logo and a white orb, both shaped like a web of particles, with the
web animating differently in each phase.

The orb was one glowing sphere. A sphere has exactly two channels -- how bright it is and
what colour it is -- and every state had to be spelled with those two plus a wobble. A mesh
has a third: **how connected it is**. `link` is now a per-state number saying how far apart
two nodes may be and still be joined, so `transcribing` knits the web shut, `thinking`
churns it, and `idle` drops most of the lines and leaves dust drifting. Nothing in the old
orb could say that.

Thirty-four points on a unit sphere by the golden angle, rotated on two axes, projected
orthographically, and every pair measured each frame -- 561 distance checks, which is less
work than one of the three radial gradients the old one drew. Still no WebGL, for the
original reason: the card is holding the model.

**The number that had to be measured rather than picked.** Thirty-four points spread over a
sphere sit about **0.68 apart**, so the first set of `link` values -- 0.4 to 0.9, chosen to
look like a range -- drew a cloud of dots with one line in it. It was not a web and did not
look like one. The thresholds are calibrated against 0.68 now, and the docstring says so,
because the next person to add a state will otherwise pick a number that means nothing.

**White is not only a preference; it is what makes the colours louder.** Amber and teal were
already the two most common looks -- amber covered thinking, speaking and local tools -- so
teal arriving was a hue shift between two warm states. Colour is spent on three facts only:
amber while a tool runs on this machine, teal while one reaches off it, red for a refusal.
Everything else -- connecting, listening, transcribing, thinking, speaking, idle -- is white.
*(Two facts, from note 126: amber is gone as well, and a local tool call is white.)* `AIRLOCK_VISIBLE` did not change; what changed is that the one signal that matters
is now the only coloured thing on the screen.

The app icon is the same object, drawn once and held still, by the same generator that has
always drawn the orb. It needs its own numbers in two places: `LINK` is wider than the orb's
(0.82 against a moving 0.7-1.05), because the orb turns and a chord hidden this frame
arrives in the next one while a still picture has one frame; and below 96 pixels it drops to
fourteen points drawn heavier, because a web whose links are thinner than a pixel is a
smudge. A 16-pixel copy of the 512 is what an icon set exists to avoid.

## 118. Four cosmetic asks with one shape: the window was hoarding space

**Asked for:** no rule beside the orb; the backstage always open; both side panels wider;
bigger send and stop glyphs.

Four separate complaints, one cause. Every one of them was the window being conservative
about space or ceremony in a place where it had nothing to gain.

- **The rule beside the orb.** `border-right` on the left panel. The orb spent a whole
  milestone getting rid of its own one-pixel edge, because an edge makes a thing made of
  light read as a widget; putting a hairline down the side of it undoes that at one remove.
- **The backstage toggle.** A panel that answers "is it stuck, or is it reading?" is no use
  to somebody who has to decide to open it before the question occurs to them. It is always
  on. The toggle button went with it, and so did the `withpanel` class.
- **The widths.** 250/340 became 300/390. The transcript gives up the space and loses
  nothing, because it is capped at a 720px reading measure and was only centring itself in
  the slack. The minimum window width went to 900, since three columns cannot fit in 560.
- **The glyphs.** The arrow and the square were text characters at whatever weight the serif
  drew them, which at 19px inside a 44px circle is a hairline. They are inline SVG at
  stroke-width 2.4 now. The title bar's three are SVG for the same reason.

## 119. The title bar is the page's, because Windows will not sell half of one

**Asked for:** no name and no icon in the title bar, only minimise, maximise and close, and
those drawn bigger.

Windows draws the caption as one thing. You cannot keep its buttons and drop the icon and
the title beside them, and it sizes all three for a file manager. So `decorations` is off
and the bar is markup: a drag region with nothing in it and three controls at 48px with
19px glyphs.

That deletes two things note 115 needed. `set_compact` no longer toggles decorations, so
there is no new frame to repaint on the way back out of compact mode -- which was half of
115. And `DWMWA_CAPTION_COLOR` and `DWMWA_TEXT_COLOR` now paint something that is not drawn.
`titlebar.rs` keeps `DWMWA_BORDER_COLOR` alone, which is the one attribute that still lands
on a frameless window and the only thing standing between a dark window and a dark desktop.
The `0x00BBGGRR` trap survives with it, so the warning stays.

Close still hides to the tray. It is the same behaviour the native button had, and the app
is meant to keep listening.

## 120. One switch, not three

**Asked for:** "Mic live means mic is on and Alexa always listens to me, and that means voice
on -- make them into one." And, separately: delete push to talk.

There were three controls for one fact, and no combination of them was useful. Voice mode
with the microphone muted was an ear with its capture stream stopped. A live microphone in
text mode heard you and answered in silence. Push to talk was a fourth way to say the thing
the wake word already says.

The protocol lost `set_mode`, `set_mute` and `listen`, and gained `set_voice {on}` with a
`voice` reply; `ready` carries `voice` instead of `mode` and `muted`. On means the ear is
open, the wake word is listening and replies are spoken. Off means the window is a text box.
**Typing works either way**, which is exactly why this is a switch and not a mode.

Deleting the switch deleted its state. `Ear.set_muted`, `Ear.trigger`, `Listener.muted` and
the global shortcut all went, and so did `tauri-plugin-global-shortcut` and everything in
`main.rs` that reported which of four candidate hotkeys had bound -- note 106's whole
apparatus, removed by a request rather than by a bug. `Listener.muted` is the one worth
naming: it was the belt to the capture stream's braces, and with nothing left to set it, it
was a flag that could never be true.

## 121. A reply is over when the room is quiet, not when the model stops writing

**Asked for:** the orb should go back to listening when it finishes speaking, and when it is
interrupted.

Both were already nearly true and one line was undoing them. `Sidecar._run_turn` broadcast
`{"state": "idle"}` the moment the token stream ended -- which is while Kokoro still has
sentences queued -- so the orb dropped out of `speaking` before the reply had been *heard*,
and then each remaining sentence flicked it back. `Ear.follow_up` already waits for playback
to finish and already emits `listening`; the socket was racing it with a worse answer.

The socket says nothing at the end of a turn now, unless there is no ear to say it. Which
gave `idle` a meaning it did not have before: **`idle` is the closed microphone**, and it is
the only thing that means that. With the ear open the resting state is `listening`. That is
also why `muted` could be deleted from the orb's state table without losing the affordance --
the dim, barely-linked idle web *is* the affordance, and `Sidecar._resting()` is the one
place the choice is made, so a client attaching to a sidecar that has been listening since
boot is not told the microphone is shut.

Barge-in needed nothing: `Ear._barge_in` already emitted `listening`.

## 122. A turn is five acts, not twenty lines

**Asked for:** a gap between each task in the backstage, and a description of the one it is
handling now.

The panel had been through this once in the other direction. The first version put an
uppercase heading over every line, so a turn produced twenty headings and no shape; note 110
flattened it to one list with the step as a quiet prefix. That is also shapeless, and for
the same reason -- a turn is not twenty things, it is five acts of three or four things each.

Consecutive lines of the same step are one act now, with the sidecar's own heading over it
once and 22px between acts against 9px inside one. Consecutive rather than gathered by name:
the agent talks, calls a tool, talks again, and that really is three acts in order.

**Colour follows the tool, and it is read off `detail` rather than guessed.** Every trace
line already carries the raw numbers -- `scope`, `ok`, `why`, `approved`, `query` -- and they
were being thrown away by a panel that coloured on `step` alone, so a local call, a call to
the web and a refusal were the same amber. They are told apart now, the same way the orb
tells them apart, from the same facts the runtime already sends. *(White, teal and red, from
note 126.)*

## 123. Waiting with nothing to read

**Asked for:** show the loading status while the agent is working, with a small loading
particle, instead of leaving the user bored. And a button back to the newest message after
scrolling up.

The backstage already said what was happening; it was in the far right column, which is not
where somebody watching for their answer is looking. The newest trace line now also sits
directly above the composer while a turn is in flight, with one mote orbiting beside it. It
is deliberately the *same text* -- a second wording for one fact is a second thing to keep
true -- and it is a mote rather than a bar, because a bar promises a proportion that nothing
here can measure. The backstage grew the same block at the top of its own column, where it
holds still while the list under it grows.

The transcript has followed the bottom only while the reader was already there since it was
written, which is right and was half a rule. The other half was missing: not dragging you
down is not a reason to leave you to drag yourself back. A **Newest** button appears
floating over the transcript exactly while there is somewhere to go.

## 124. Quitting is a word, not a button

**Asked for:** remove the Quit Alexa button; typing "exit" or the title bar is enough.

Note 114 added that button because removing the header's close had left no quit anywhere
except an undiscovered tray menu. The title bar solves that properly, so the button became a
third way to do the same thing.

`exit`, `quit`, `bye`, `goodbye` and `keluar`, alone on a line, close the app. It is handled
in the window rather than as a tool or a fast path, and that is the point: quitting is
something the window does, not something the model may decide to do. The order is unchanged
and still matters -- `shutdown` over the socket first, because that is what flushes the
session summary into Chroma and what leaves the database readable.

## 125. It was already 100% on the card; the RAM is the voice models

**Asked:** is this really 100% GPU? RAM climbs while it runs. Change the context length to
whatever stays entirely on the card.

Measured rather than argued about, which is the rule for this number:

    NAME          ID              SIZE      PROCESSOR    CONTEXT    UNTIL
    qwen3.5:4b    2a654d98e6fb    3.8 GB    100% GPU     23552      4 minutes from now

`100% GPU` at the shipped 23552. Nothing is on the processor and there is nothing to change;
lowering the window would cost memory for no gain, and note 51's cliff at 24576 is where the
spill starts.

The climbing RAM is real and is not the language model. The four voice models -- Kokoro,
Parakeet, Silero and openWakeWord -- run on the CPU, because this `onnxruntime` has only
`CPUExecutionProvider`, and they cost about 1.2 GB of ordinary RAM. Chroma and the WebView
add their own. That is system memory, not VRAM, and the two are not the same question: the
binding constraint on the window is 6 GB of card, and it is not being touched.

## 126. Amber is gone, and two colours carry more than three did

**Asked for:** the orb should be white while a tool is running, not orange; and the tool dot
in the transcript and the backstage with it.

Note 117 had already cut colour down to three facts. This cuts it to two: **teal is something
leaving this machine, red is a refusal, and everything else is white** -- thinking, listening,
speaking, and a tool running here.

It reads as a smaller signal and is a larger one. The orb has to answer one question without
being asked, and that question is *is anything leaving?* With amber on the belt, a local tool
call and a web call were two coloured states and the eye had to tell warm-orange from
warm-teal; now the orb is white until it is not, and the moment it is not is the moment that
matters. Local work is told apart from thinking by the two channels the web has and the
sphere did not -- it is denser (0.98 against 0.86) and slower (0.7 against 1.15).

This is the passage in note 117 that said "amber while a tool runs on this machine" and it
supersedes it. `--local` and `--local-soft` are out of the stylesheet rather than left sitting
there unused, and the send button, the confirmation's primary button, the focus ring, the
first-run progress bar and the tray's `tool.local` tint all went white with them. The one
thing that did **not** change is the rule that pays for all of this: nothing in the program
is ever teal except something leaving the machine.

## 127. A splash was the one shape this drawing had already rejected

**Asked for:** remove the spreading splash when the orb wakes; animate it some other way.

The wake acknowledgement was a ring expanding out of the orb and fading -- which is a
notification badge, and the only thing in the whole drawing that left the body of the orb.
Every other decision in `orb.js` had gone the other way: no stroke, no edge, nothing with a
hard boundary, because a hard boundary reads as a widget.

It is done from the inside now. For 320 ms every link the lattice could possibly have snaps
in, the nodes swell, the whole thing flares, and it settles back -- the pulse widens the link
threshold rather than drawing anything new, so it uses the channel the mesh already has
instead of borrowing one. Squared falloff, so the leading edge is the whole event: a slow
tail turns an acknowledgement into a second animation competing with whatever state arrives
next, and `wake` arrives while the transcriber is still loading.

## 128. The backstage kept one turn, except when it did not

**Asked for:** a gap between tasks in the backstage, so it is clear which question a run of
lines belongs to.

Two faults behind one complaint.

The panel was cleared per turn -- by `Session.ask`, which is the *typed* path. A spoken turn
never goes through `ask`, so voice quietly accumulated the whole session into one unbroken
list, and `5/5 filing` ran straight into the next `0/5 wake` with nothing between them. That
is the exact shape of the bug this codebase keeps meeting: **a rule enforced at one of its
call sites**, like `mkdir` on `write_file` and the compound-message guard on two fast paths
out of three.

The fix is not to clear it at both. Keeping the last few questions is more useful than keeping
one, now that the panel is always open and 390px wide -- so the trace keeps **three turns**,
trimmed at the `wake` line, which is the sidecar's own marker for where a turn begins. And a
turn boundary is drawn as one: 30px of space and a rule, against 26px between acts and 9px
inside one. Marked in the markup (`act.opens && i > 0`) rather than by counting children,
because note 112 already paid for positional CSS once.

## 129. A config is baked into the binary; the runtime is not

**Reported:** two title bars, one above the other. The lower one -- the one the page draws --
is correct.

`decorations: false` in `tauri.conf.json` is read at *build* time. An executable built before
the title bar moved into the page still has `decorations: true` compiled into it, comes up
with the system caption strip, and then renders a page that draws its own underneath. Nothing
is wrong with either half; they are from two different builds.

So the window asks for it again at startup, in `setup()`. The config stays, because it is
what the window is *created* with and stops the frame appearing for a frame; the call is what
makes any binary, however old its config, end up frameless. `core:window:allow-set-decorations`
goes back into the capability file with it.

The same report mentioned a faint edge beside the orb, and the border is the other half of
this. Windows still draws one around a frameless window, no stylesheet reaches it, and
`titlebar.rs` had been painting it a few shades *lighter* than the background so the window
had an edge against a dark desktop. From inside, that edge is a hairline next to the orb --
which is the thing note 118 removed from the stylesheet and the thing the orb spent a
milestone removing from itself. It is `--bg` exactly now. The window loses its outline against
a dark desktop, which is the trade that was asked for.

## 130. The icon is the orb, not something that resembles it

**Asked for:** the app icon should look like the orb.

It already used the same lattice and the same white, with its own node count and its own link
threshold -- and "its own" is how two drawings of one object drift apart. The large sizes are
the orb exactly now: 34 points, threshold 0.92, which is `listening`, which is the state the
app is in whenever it is waiting for you.

Below 96 pixels it still drops to fourteen points drawn heavier. That is not drift: a web
whose links are thinner than a pixel is a smudge, and an icon set exists precisely so that a
16-pixel version can be a different drawing of the same thing.

## 131. The hairline beside the orb was half a device pixel the clear never reached

**Reported:** a line next to the orb that looks like a border -- "but i think maybe there is a
bug in the component". It was.

Two of them, actually: one pixel wide, about fifty long, one down the right of the orb and one
under it. They survived the border being taken off the panel (note 118) and the window edge
being painted to match the background (note 129), because they were neither.

Measured rather than guessed, by capturing the running window twice a second apart and
diffing: the orb's body changed 7138 pixels between the two frames and the lines changed
**zero**. A still line beside a moving drawing is not being drawn; it is being *left*.

`resize()` sets `canvas.width = Math.round(w * dpr)` and then scales the context by `dpr`.
At a fractional device pixel ratio those two disagree: 190 CSS pixels at 1.25 is 237.5 device
pixels, stored in a 238-wide buffer. `clearRect(0, 0, this.w, this.h)` under the transform
reaches 237.5 -- so the last half-pixel column, and the matching row, is never cleared, and
whatever was drawn there on some early frame stays for the life of the window.

The clear happens in device space now, with the transform reset, so it covers the buffer
rather than the box. A `ResizeObserver` went in beside it: a window resize is not the only
thing that changes this element, and the panel's media query, the setup screen and compact
mode all resize it without the window moving.

**The general form, and it is not about canvases.** Two numbers describing one thing, rounded
by different rules -- `round(w * dpr)` for the buffer and `w * dpr` for the transform -- differ
by less than a pixel, and less than a pixel is exactly the size of a fault nobody looks for.
The evidence that separated it from four plausible CSS causes was that it did not move.

## 132. A running app holds its own executable, so `tauri dev` stops rebuilding the shell

**Reported, twice:** the title bar is still doubled and the icon has not changed.

Both were fixed and neither had reached the machine. `npm run tauri dev` watches two things
and treats them very differently: the page is served by Vite and hot-reloads, while the shell
is a compiled binary that has to be replaced on disk. Windows will not let you replace a
running executable, so the moment the app is up, `cargo` fails with

    error: failed to remove file `...\target\debug\sunday.exe`
    Caused by: Access is denied. (os error 5)

and the session carries on with the *old* shell under the *new* page. That is exactly the
reported symptom: the page draws its own title bar, the stale binary still has
`decorations: true` compiled in from `tauri.conf.json`, and the window has two. The icon is
the same story -- it is a resource embedded in the exe at build time.

So the fix for both was "stop it and start it again", and neither is a code change. What *is*
worth keeping is the diagnosis, because the failure is silent from the outside and looks
exactly like a change that did not work:

- `tauri.conf.json` is read at **build** time. Nothing in it can reach a running window.
- The icon is compiled into the executable. Same.
- A frontend change reaches the window instantly, which is what makes the two halves look
  like one thing until they disagree.

`main.rs` asks for `set_decorations(false)` at startup anyway (note 129), which makes the
config's staleness survivable rather than fatal -- but only from the next build onward.

## 133. Three panels, one ground, two rules, and 3:4:3

**Asked for:** the backstage on the same background as the rest of the app; a line between
each of the three panels, as there used to be; the widths at 3:4:3; and the two controls under
the orb side by side, icons only.

All four are the same correction, which is that the window had been saying *panel* four
different ways at once. The backstage sat on `--surface` while the other two sat on `--bg`, so
it read as a drawer bolted to the side rather than a third of one window. The rules between
the columns had been taken out with the hairline note 118 removed -- and that was an
over-correction: **the hairline note 118 removed ran down the side of the orb**, which is a
thing made of light and the one place an edge is wrong. A rule between two panels is not that;
it is what says where each one begins, and with all three on one ground it is the only thing
that does. (The line still visible after 118 turned out to be neither -- see note 131.)

The widths are fractions now, `minmax(0, 3fr) minmax(0, 4fr) minmax(0, 3fr)` rather than
`300px minmax(0, 1fr) 390px`. Fixed side panels meant the middle column absorbed every pixel
of a wider window, which it has no use for -- the transcript is capped at a 720px reading
measure and was only centring itself in the slack. The `minmax(0, ...)` is not decoration:
a grid track's default floor is its own content, so one long unbroken word in the backstage
would push the ratio out. The media query lost its column override with them and now only
shrinks the orb, which is a fixed square and the first thing to overflow a narrow panel.

The controls are two 46px circles side by side with a microphone and a compact glyph in them.
There was never much information in the words: the line under the orb already says
*listening* or *microphone off*, so **Voice on** underneath it said the same thing twice and
took the wall the orb had been given on purpose. The state is in the glyph -- a slash across
the microphone when it is shut -- and the sentence is in the `title`.

## 134. The orb is a fraction of its panel, and the drawing scales with it

**Asked for:** a bigger orb, now that the panel is wider, sitting nearer the middle of it.

Both are the same correction as note 133 and were left half done there. The columns became
fractions; the orb stayed 190 pixels, hanging off the top of the panel with a column of air
underneath, which made the whole left side read as a toolbar that had run out of tools.

It is `min(300px, 84%)` of its own column now, with `aspect-ratio: 1`, and the side panel
centres its four rows as a group instead of stacking them from the ceiling. 260px at a
1180-wide window, 99px of air above and below the group -- measured, not guessed. The
breakpoint that used to shrink the orb at 1120px is gone: a ratio gives ground on its own,
and width-counting CSS is wrong the first time something else is rendered.

**The part that was not asked for and had to happen anyway.** Every absolute number in the
drawing -- node radius 0.9 to 3, link width 1 -- was tuned at 190 pixels, so the same web was
chunky in the compact window and wispy in a wide one. They are scaled off `unit / 95` now,
bounded to [0.55, 1.9], so 130px and 300px are one object at two sizes rather than two
different drawings. A drawing with one hard-coded size in it and a box that no longer has one
is a thing that only looks wrong at the sizes nobody tested.

**And a real bug, found by the check rather than by looking.** Assigning `canvas.width` wipes
the bitmap *even when the number does not change* -- and a `ResizeObserver` notifies once as
soon as it starts observing. So the observer added in note 131 was throwing away the frame
that had just been drawn. Sixty times a second that is invisible; once, on a canvas that is
being stepped by hand, it is a blank orb, which is how it was found. `resize()` returns early
when nothing has changed.
