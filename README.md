# Alexa

A local agent that lives on one Windows desktop, runs on a single 6 GB graphics
card, and treats the internet as a door it has to unlock — not a place it sends
your work.

**Alexa is what it answers to; `sunday` is what it is made of.** The window
title, the executable and the installer say Alexa, because that is the name a
person says out loud, and it comes from `[assistant] name` in the config. The
repository, the Python package, the sidecar process and `%LOCALAPPDATA%\Sunday`
say sunday, deliberately: renaming the data directory would orphan the memory
store without saying so, leaving a fresh empty one beside it that looks like it
is working. So every command below is `sunday`-something, and that is not a
leftover.

## What this actually is

Not a voice assistant with a chat box bolted on. It is the same shape as the
agent runtimes people have started keeping open beside their editor — a model
in a loop that decides which tool to call, calls it, reads what came back and
decides again, up to twelve calls in one turn, with memory read before it starts
and written after it finishes. Voice is one of two ways in. Typing is the other,
and neither is a mode you switch between.

What is different is where it runs. **The thinking is local**: one small model,
`qwen3.5:4b` through Ollama, on your own card. No prompt, no file, no calendar
entry and no piece of mail is sent anywhere. There is exactly one remote call in
the whole program — DeepSeek summarising a page the assistant fetched — and it
sits behind an airlock that composes the outgoing query in a context your
private data was never allowed into. That is the design, not a setting:

> A local model can use your private things and the public web in the same
> breath, because the query that goes outside is composed in a room your private
> things were never allowed into.

Reading anything credential-shaped shuts the web door for the rest of the turn.
Writing a file asks first; deleting always asks; the sandbox answers before the
model does. Those are tested rather than asserted — see [Tests](#tests).

**The hands are still small.** Twelve tools today:

| | |
| --- | --- |
| files | `read_file`, `write_file`, `list_dir`, `move_file`, `copy_file`, `delete_file` |
| the world | `get_weather`, `get_asset_price`, `ask_external` (search → fetch → extract → summarise) |
| your own things | `calendar_read`, `mail_search` (read-only, Google, behind OAuth) |
| itself | `list_capabilities` |

## Where it goes next

**The machine is finished; the hands are not.** All thirteen milestones are
built: the agent loop, the sandbox, the guardrail, the airlock and its web
pipeline, both tiers of memory, streaming, the socket, the whole of voice, and a
window of its own. That is the hard, boring half — the part that decides whether
an agent is safe to give a filesystem to — and it is done and tested.

What is left is the interesting half:

- **More hands.** Twelve tools is enough to prove the loop and not enough to be
  useful all day. Anything with a clear refusal and a clear provenance label can
  join: a terminal, a code editor, a browser it drives rather than reads, a
  clipboard, a screenshot, a task list.
- **Sharper.** A 4b model is small enough to fit beside everything else on a 6 GB
  card and small enough to get things wrong in ways a bigger one would not. Some
  of that is prompt, some is fast paths, some is knowing when to think longer.
- **Cheaper.** Every tool is paid for in context on every turn, whether or not it
  is called. Twelve of them cost 1552 tokens off the top. That number decides how
  many hands it can have at once, so it is the number the next stage is about.

Full design: [`doc/sunday_architecture.html`](doc/sunday_architecture.html) —
the specification the code is written against. Open it in a browser; it is one
file with no dependencies.

## From nothing to an app you can double-click

There is no binary on the releases page and there is not going to be one: the
installer this project can currently produce carries the window and not the
Python half, which would give you something that starts and then says it cannot
find its own sidecar. Building it yourself takes about ten minutes, most of
which is downloads, and the result is better than a release anyway — it runs
against your own models and your own folders.

Every command here is **PowerShell**, which is what Windows gives you.

**1. Prerequisites.** [Python 3.12+](https://www.python.org/downloads/),
[Node](https://nodejs.org/) and [Ollama](https://ollama.com/download). Rust as
well, if you want the desktop window rather than the terminal client:

```powershell
winget install Rustlang.Rustup
```

Open a new terminal afterwards, or `cargo` will not be on your path — and that
failure reads as `program not found` rather than as a stale environment.

**2. The repository and its virtualenv.**

```powershell
git clone https://github.com/DlegendZ/Sunday.git; cd Sunday
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev,voice]"
```

The editable install is what makes `sunday` runnable from any directory with no
`PYTHONPATH` to remember, and what writes the console scripts. Leave `,voice`
out if you only want to type at it — PortAudio is the part that fails on a
machine with no sound card.

**3. The model.** Ollama has to be running; it installs itself as a service, so
usually it already is.

```powershell
ollama pull qwen3.5:4b
```

That is 2.5 GB, and it is the whole of the thinking. Nothing else is downloaded
from anywhere at runtime except what you explicitly ask it to look up.

**4. The voice models**, if you want to talk to it. About **1 GB** with the
configured transcriber — measured: Kokoro 337 MB, Parakeet 631 MB, and the wake
word and the VAD 7 MB between them. They are not in the repository:

```powershell
.venv\Scripts\python.exe -m sunday.audio.models
```

**5. Configuration.** Copy the template and set the folders it may open — the
`[files] roots` block is the only thing you *must* change, because the paths in
it are somebody else's:

```powershell
Copy-Item config.example.toml config.toml
```

**6. Check it works** before building anything around it:

```powershell
.venv\Scripts\sunday.exe
```

That is the terminal client, and it exercises the whole core. If it answers, the
window will too.

**7. Build the app.**

```powershell
cd app; npm install; npm run tauri build; cd ..
```

The first build compiles a few hundred Rust crates and takes a while; later ones
take about a minute. It produces
`app\src-tauri\target\release\sunday.exe`, and
[a shortcut to it](#a-shortcut) is the whole of "installed".

Optional extras, none of which the app needs to run: a
[DeepSeek key](#configuration) sharpens what the web pipeline brings back, and
[Google sign-in](#calendar-and-mail-if-you-want-them) adds read-only calendar
and mail.

## Setup, in more detail

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
.venv\Scripts\python.exe -m sunday.tools.google
```

`sunday-google.exe` does the same thing, but console scripts are written at
install time -- a `.venv` created before that entry point existed does not have
one, and neither `sunday-models` nor `sunday-mic`. One reinstall writes all
five, and the module above works without it:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Google will refuse the sign-in until your own address is on the client's test
user list -- *OAuth consent screen → Audience → Test users* in the console. A
personal client stays in **Testing**, because `gmail.readonly` is a restricted
scope and leaving Testing means a verification review. Testing also expires the
refresh token every seven days, so the command above is not a once-only thing;
both tools say so when it happens.

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

The title bar is drawn by the page, so it carries three controls and
nothing else. **Closing quits**, and quits in the right order: the X,
Alt+F4, typing **exit** in the box and **Quit Alexa** in the tray all take
the same route, because the sidecar has to be told before anything is
killed — `shutdown` is what folds the session summary into Chroma, and
Chroma is SQLite. Nothing opens a console window: the sidecar is a console
application and is started with `CREATE_NO_WINDOW`, so the app is one
window and not a window plus a black rectangle.

One switch controls the voice half. On means the microphone is open, the
wake word is listening and replies are spoken; off means the window is a
text box. Typing works either way.

The window can also be run on its own, against a sidecar you started by hand,
which is much faster to iterate on and is how it was built:

```powershell
cd app; npm run dev
```

Then open `http://localhost:5173/?port=PORT&token=TOKEN` with the two values from
`handshake.json`.

### An app you can double-click

```powershell
cd app; npm run tauri build
```

Produces `app\src-tauri\target\release\sunday.exe` — about 3.5 MB,
double-clickable, no terminal and no dev server. Run it from there, or make a
shortcut to it. It spawns the sidecar itself and finds it by walking up to
`.venv`, so **it works anywhere on this machine as long as the repository stays
where it is**. That is the whole of it for personal use; nothing else has to be
installed.

Ollama still has to be running, because the model does. And close the window
before rebuilding: Windows will not replace a running executable, so cargo fails
with `Access is denied` rather than saying which of your changes did not arrive.

### A shortcut

One command makes one on the desktop and one in the Start menu, so Windows
search finds it by name:

```powershell
$exe = "$PWD\app\src-tauri\target\release\sunday.exe"
$s = New-Object -ComObject WScript.Shell
foreach ($p in @("$([Environment]::GetFolderPath('Desktop'))\Alexa.lnk", "$([Environment]::GetFolderPath('Programs'))\Alexa.lnk")) { $l = $s.CreateShortcut($p); $l.TargetPath = $exe; $l.WorkingDirectory = "$PWD"; $l.IconLocation = "$exe,0"; $l.Description = 'Alexa - a local voice assistant'; $l.Save() }
```

Run it from the repository root, because **`WorkingDirectory` is not
decoration.** The exe finds its sidecar by walking up from itself, and the
Python half finds `config.toml` and `.env` from the package's own location —
both are position-independent. The shell is not: it reads the `[ui]` block out
of the current directory, its parent, and then `%LOCALAPPDATA%\Sunday`. Start
it in the folder holding the exe and it finds none of them, comes up on the
defaults, and does not mention it.

Move the repository and both shortcuts break; make them again.

### Giving it to somebody else

The same command also writes an NSIS installer to
`target\release\bundle\nsis\Alexa_0.1.0_x64-setup.exe`, and that installer is
**not distributable yet**. It carries the window and not the Python sidecar,
which is still supposed to ship as a PyInstaller one-folder build beside the
exe. Installed on a machine without this repository, the shell starts and then
reports that it cannot find `sunday-sidecar.exe`. That is broken rather than
unsafe, but it is broken.

If you do package it, three things are worth knowing before a first release,
because none of them are visible from the code:

- **`.env` is the whole risk.** It holds `DEEPSEEK_API_KEY` and the Google
  client secret. It is gitignored, so the repository is safe — but a PyInstaller
  spec takes what it is told to take, and one that sweeps the project root puts
  that key *inside the binary*, where `.gitignore` means nothing and a published
  release is permanent. Exclude it explicitly.
- **`%LOCALAPPDATA%\Sunday` must never be in the build.** It is the memory
  store — every turn ever filed — and `google_token.json`.
- **An unsigned binary is a scary download, not a dangerous one.** SmartScreen
  will warn about it, and antivirus false positives on PyInstaller bundles are
  routine. Code signing is the only fix, and it costs money.

And say plainly in the release what the thing does on the machine that runs it:
it opens a microphone if voice is on, reads the folders named in its config, and
writes files after asking. All of that is the point, and all of it is something
a stranger downloading a binary should be told rather than discover.

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
| `app/src-tauri/` | the Rust shell: window, tray, title bar, and the sidecar's life |

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
| 7 | Voice in | done | Wake word, VAD, Parakeet. `sunday --voice`, or `set_voice` over the socket |
| 8 | Voice out + echo | done | Kokoro speaks a sentence while the model writes the next; all three echo layers |
| 9 | Barge-in | done | Talking over it stops it in about 100 ms — including while it is thinking |
| 10 | The socket | done | The sidecar protocol; `web/debug.html` drives a whole turn |
| 11 | Tauri shell + orb | done | A real window; the orb is the whole status display and both amplitudes it moves to are measured. The shell spawns the sidecar and drives a whole turn |
| 12 | Windows integration | done | Tray tinted with the state, a title bar the page draws itself, single instance, NSIS, autostart, and a first-run screen that says what is missing. The installer itself has not been produced yet |
| 13 | Calendar + mail | done | OAuth through `python -m sunday.tools.google`, read-only, token on the credential list |

## Tests

```powershell
.venv\Scripts\python.exe -m pytest
```

The privacy guarantees are tested rather than asserted: reading `.env` taints
the turn, a mailed `ghp_` token is redacted, and a turn that reads a private
file and searches the web produces a query with nothing private in it.
