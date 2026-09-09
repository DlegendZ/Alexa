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

### Calendar and mail, if you want them

Both are read-only and both need a Google **desktop** OAuth client. Create one
in the Google Cloud console, enable the Calendar and Gmail APIs, and put the two
values in `.env` at the repo root beside `DEEPSEEK_API_KEY`:

```
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
```

Then sign in once. This opens a browser, and the code comes back to a socket on
this machine rather than through your clipboard:

```powershell
.venv\Scripts\sunday-google.exe
```

The refresh token lands in `%LOCALAPPDATA%\Sunday\google_token.json`, which is
on the credential list — if the agent ever reads that file the web door shuts for
the rest of the turn. Until you sign in, both tools refuse and say which command
to run.

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

### The window

The desktop app is a Tauri 2 shell around the same socket. It needs Node and
Rust — `winget install Rustlang.Rustup`, then a new terminal.

```powershell
cd app; npm install
```

```powershell
npm run tauri dev
```

The shell spawns the sidecar itself, reads the handshake, restarts it up to three
times a minute if it dies, and gives up honestly after that. Closing the window
hides it to the tray; **Quit** is what stops the sidecar, because that is what
flushes the session summary to memory. `Ctrl+Alt+Space` focuses the window and
starts listening from anywhere.

The window can also be run on its own, against a sidecar you started by hand,
which is much faster to iterate on and is how it was built:

```powershell
cd app; npm run dev
```

Then open `http://localhost:5173/?port=PORT&token=TOKEN` with the two values from
`handshake.json`.

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
rather than failing. `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` go there
too, if you want the calendar and the mailbox.

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
| `src/sunday/tools/google.py` | calendar and mail, read-only, and the one-time sign-in |
| `src/sunday/stream.py` | the sentence splitter and the two sinks |
| `src/sunday/fastpaths.py` | the three patterns answered in code, before the model |
| `src/sunday/trace.py` | the backstage narration, one line per step |
| `src/sunday/audio/` | wake word, VAD, transcription, synthesis, echo |
| `src/sunday/audio/listener.py` | every decision voice makes, with no hardware in it |
| `src/sunday/audio/check.py` | measures your microphone and room against your own voice |
| `src/sunday/server.py` | the sidecar's WebSocket protocol |
| `web/debug.html` | a plain page that drives a whole turn |
| `app/src/` | the window: the orb, the transcript, the backstage panel |
| `app/src/lib/orb.js` | every state the orb draws, on a 2D canvas, never WebGL |
| `app/src-tauri/` | the Rust shell: window, tray, hotkey, and the sidecar's life |

## Build progress

**All thirteen are running.** The assistant is usable from the terminal, from a
browser page, and from its own window — by typing or out loud. The shell builds,
launches, spawns the sidecar, reads the handshake and drives a whole turn.

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
| 11 | Tauri shell + orb | done | A real window; the orb is the whole status display and both amplitudes it moves to are measured. The shell spawns the sidecar and drives a whole turn |
| 12 | Windows integration | done | Tray tinted with the state, `Ctrl+Alt+Space`, single instance, NSIS, autostart, and a first-run screen that says what is missing. The installer itself has not been produced yet |
| 13 | Calendar + mail | done | OAuth once through `sunday-google`, read-only, token on the credential list |

## Tests

```powershell
.venv\Scripts\python.exe -m pytest
```

The privacy guarantees are tested rather than asserted: reading `.env` taints
the turn, a mailed `ghp_` token is redacted, and a turn that reads a private
file and searches the web produces a query with nothing private in it.
