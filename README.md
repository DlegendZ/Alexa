# Sunday

A voice assistant that lives on one Windows desktop, runs on a single 6 GB
graphics card, and treats the internet as a door it has to unlock — not a place
it sends your work.

One small local model (`qwen3.5:4b` via Ollama) does the thinking, reads your
files, checks the weather and looks up prices. When it genuinely needs the open
web, it goes through a single guarded door — and what passes through that door
is written by something that has never seen your private data.

Full design: `doc/sunday_architecture.html` (local, not in git).
Deviations found while building: [ARCHITECTURE_NOTES.md](ARCHITECTURE_NOTES.md).

## Setup, once

Ollama must be running with the model pulled:

```powershell
ollama pull qwen3.5:4b
```

Install Sunday into the virtualenv. This is what makes `sunday` runnable from
any directory, with no `PYTHONPATH` to remember:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

### Voice, once more

Speaking to it needs a few extra packages and about 1 GB of models, which are
not in the repo:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[voice]"
```

```powershell
.venv\Scripts\python.exe -m sunday.audio.models
```

Then measure your own microphone, because every threshold in `[audio]` and
`[wake]` is a guess until somebody does. It records eight seconds while you
talk and prints what each piece made of it:

```powershell
.venv\Scripts\python.exe -m sunday.audio.check
```

## Running it

```powershell
.venv\Scripts\sunday.exe
```

That is the terminal client. It stays alive for the whole build — it is the
fastest way to test the core without an app in the way. `.venv\Scripts\python.exe
-m sunday.main` does the same thing if you prefer the module form.

Add `--voice` to open the microphone as well. Say **"alexa"**, then ask. It
answers out loud, and for thirty seconds afterwards you can keep talking without
the wake word at all. Talking over it stops it — while it is speaking, and while
it is still thinking.

The phrase is its own name: `[assistant] name` and `[wake] model` are meant to
agree, and `alexa` is one of the three openWakeWord ships pretrained.

```powershell
.venv\Scripts\sunday.exe --voice
```

The sidecar exposes the same core over a WebSocket:

```powershell
.venv\Scripts\sunday-sidecar.exe
```

It binds to `127.0.0.1` on an ephemeral port and writes the port and a
per-launch token to `%LOCALAPPDATA%\Sunday\handshake.json`. Serve `web/debug.html`
over loopback — not `file://`, because a page from an opaque origin cannot open
a WebSocket — and paste both in:

```powershell
.venv\Scripts\python.exe -m http.server 8777 --bind 127.0.0.1 --directory web
```

### A note on shells

These commands are written for **PowerShell**, which is what Windows gives you
by default. Do not prefix them with `PYTHONPATH=src` — that is bash syntax, and
PowerShell reads it as a command name and fails with
`The term 'PYTHONPATH=src' is not recognized`. Since the editable install above,
nothing needs it. In Git Bash the same commands work with forward slashes:
`.venv/Scripts/sunday.exe`.

## Configuration

Copy `config.example.toml` to `config.toml` at the repo root (development) or to
`%LOCALAPPDATA%\Sunday\config.toml` (installed). The repo-root copy wins. Set
`SUNDAY_HOME` to move the data directory.

`.env` at the repo root holds `DEEPSEEK_API_KEY`, used only by the airlock's
summariser. Without it, the web pipeline degrades to returning raw snippets
rather than failing.

## Layout

| Path | What |
| --- | --- |
| `src/sunday/graph.py` | `memory_read → agent → compose_reply → memory_write` |
| `src/sunday/runtime.py` | the tool loop, the caps, the door checks, one turn |
| `src/sunday/state.py` | `SundayState` and the provenance labels |
| `src/sunday/guardrail.py` | source taint by path, shape match by pattern |
| `src/sunday/airlock.py` | composes the outgoing query in a fresh context |
| `src/sunday/web.py` | search → decide → fetch → extract → summarise |
| `src/sunday/memory/` | RAM session store, Chroma store, window budget |
| `src/sunday/tools/` | weather, asset prices, files, `ask_external` |
| `src/sunday/stream.py` | the sentence splitter and the two sinks |
| `src/sunday/fastpaths.py` | the three patterns answered in code, before the model |
| `src/sunday/trace.py` | the backstage narration, one line per step |
| `src/sunday/audio/` | wake word, VAD, transcription, synthesis, echo |
| `src/sunday/audio/listener.py` | every decision voice makes, with no hardware in it |
| `src/sunday/audio/check.py` | measures your microphone and room against your own voice |
| `src/sunday/server.py` | the sidecar's WebSocket protocol |
| `web/debug.html` | a plain page that drives a whole turn |

## Build progress

**Milestones 1 to 10 of 13 are running.** Everything below the line is the
desktop app; the assistant itself is complete and usable from the terminal or
over the socket, by typing or out loud.

| # | Milestone | State | What it means |
| --- | --- | --- | --- |
| 1 | Single agent, text only | done | One agent answers in the terminal with weather and prices |
| 2 | File tools + sandbox | done | Reads and writes inside the roots, refuses everything else |
| 3 | Guardrail | done | Reading `.env` taints the turn; a `ghp_` token is redacted |
| 4 | The airlock + web | done | A private file and a web search in one turn, with nothing private in the query |
| 5 | Memory | done | Chroma write-through, distance cutoff, slice budgets, idle summary |
| 6 | Streaming | done | Tokens as generated; the splitter emits whole sentences |
| 7 | Voice in | done | Wake word, VAD, Parakeet. `sunday --voice`, or `listen` over the socket |
| 8 | Voice out + echo | done | Kokoro speaks a sentence while the model writes the next; all three echo layers |
| 9 | Barge-in | done | Talking over it stops it in about 100 ms — including while it is thinking |
| 10 | The socket | done | The sidecar protocol; `web/debug.html` drives a whole turn |
| 11 | Tauri shell + orb | not started | A real window, with the orb wired to real events |
| 12 | Windows integration | not started | Tray, hotkey, single instance, installer |
| 13 | Calendar + mail | not started | OAuth once, read-only, token on the credential list |

## Tests

```powershell
.venv\Scripts\python.exe -m pytest
```

The privacy guarantees are tested rather than asserted: reading `.env` taints
the turn, a mailed `ghp_` token is redacted, and a turn that reads a private
file and searches the web produces a query with nothing private in it.
