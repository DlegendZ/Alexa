# Sunday

A voice assistant that lives on one Windows desktop, runs on a single 6 GB
graphics card, and treats the internet as a door it has to unlock — not a place
it sends your work.

One small local model (`qwen3.5:2b` via Ollama) does the thinking, reads your
files, checks the weather and looks up prices. When it genuinely needs the open
web, it goes through a single guarded door — and what passes through that door
is written by something that has never seen your private data.

Full design: `doc/sunday_architecture.html` (local, not in git).
Deviations found while building: [ARCHITECTURE_NOTES.md](ARCHITECTURE_NOTES.md).

## Setup, once

Ollama must be running with the model pulled:

```bash
ollama pull qwen3.5:2b
```

Install Sunday into the virtualenv. This is what makes `sunday` runnable from
any directory, with no `PYTHONPATH` to remember:

```bash
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Running it

```bash
.venv\Scripts\sunday.exe
```

That is the terminal client. It stays alive for the whole build — it is the
fastest way to test the core without an app in the way. `.venv\Scripts\python.exe
-m sunday.main` does the same thing if you prefer the module form.

The sidecar exposes the same core over a WebSocket:

```bash
.venv\Scripts\sunday-sidecar.exe
```

It binds to `127.0.0.1` on an ephemeral port and writes the port and a
per-launch token to `%LOCALAPPDATA%\Sunday\handshake.json`. Serve `web/debug.html`
over loopback — not `file://`, because a page from an opaque origin cannot open
a WebSocket — and paste both in:

```bash
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
| `src/sunday/server.py` | the sidecar's WebSocket protocol |
| `web/debug.html` | a plain page that drives a whole turn |

## Build progress

| # | Milestone | State |
| --- | --- | --- |
| 1 | Single agent, text only | done |
| 2 | File tools + sandbox | done |
| 3 | Guardrail | done |
| 4 | The airlock + web | done |
| 5 | Memory | done |
| 6 | Streaming | done |
| 7 | Voice in | not started |
| 8 | Voice out + echo | not started |
| 9 | Barge-in | cancellation done, audio side not started |
| 10 | The socket | done |
| 11 | Tauri shell + orb | not started |
| 12 | Windows integration | not started |
| 13 | Calendar + mail | not started |

## Tests

```bash
.venv/Scripts/python.exe -m pytest
```

The privacy guarantees are tested rather than asserted: reading `.env` taints
the turn, a mailed `ghp_` token is redacted, and a turn that reads a private
file and searches the web produces a query with nothing private in it.
