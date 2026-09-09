/* The shell's half of the socket.
 *
 * One connection, one turn at a time, and every message the sidecar can send
 * has a case here. The protocol is the sidecar's; this file is the only place
 * in the window that knows it exists.
 */

const PING_EVERY_MS = 5000;
/** Three missed pings and the sidecar is gone. Not two -- one slow turn on a
 *  busy card can eat a reply -- and not five, which is half a minute of a
 *  window pretending to be connected to a process that has died. */
const MISSES_ALLOWED = 3;

/** Where the connection details come from.
 *
 *  In the shell, Rust has already spawned the sidecar and read
 *  `handshake.json`; the WebView cannot read a file and should not learn how.
 *  In a browser -- which is how this window is developed, because a browser
 *  reloads in a tenth of a second and a Rust build does not -- they come off
 *  the query string, the same way `web/debug.html` takes them.
 */
export async function discover() {
  const tauri = globalThis.__TAURI__;
  if (tauri?.core?.invoke) return tauri.core.invoke('connection');
  const params = new URLSearchParams(location.search);
  const port = params.get('port');
  const token = params.get('token');
  if (!port || !token) return null;
  return { port: Number(port), token };
}

let nextId = 0;

export class Session {
  /** What the orb is showing. `connecting` until the socket says otherwise. */
  state = $state('connecting');
  /** Why the window looks like that, in words, under the orb. */
  status = $state('connecting');
  connected = $state(false);
  model = $state('');
  /** What it calls itself. From `[assistant] name` by way of `ready` -- never
   *  written down in the window, which would go stale the day it is renamed. */
  name = $state('');
  mode = $state('text');
  muted = $state(false);
  busy = $state(false);

  micLevel = $state(0);
  outLevel = $state(0);

  /** What a first run is still short of: Ollama running, the model pulled,
   *  the voice models downloaded. Three separate facts, because they are three
   *  different jobs for whoever is reading. Null until `ready` says. */
  setup = $state(null);
  /** The download in flight, if there is one. */
  fetching = $state(null);
  fetchError = $state('');

  /** The conversation, in order. Notices and confirmations live in it too, so
   *  a redaction appears where it happened rather than in a side panel that
   *  nobody is looking at when it matters. */
  entries = $state([]);
  /** Every trace line of the current turn. Cleared when the next one starts. */
  trace = $state([]);
  /** The splitter's second sink, shown so the sentence boundaries are visible
   *  without a synthesiser attached. */
  sentences = $state([]);
  /** Outstanding confirmations, by id. There is normally at most one. */
  confirms = $state([]);

  #socket = null;
  #reply = null; // the entry tokens are being appended to
  #ping = 0;
  #missed = 0;
  #closing = false;

  async connect() {
    const found = await discover();
    if (!found) {
      this.state = 'error';
      this.status = 'no handshake -- pass ?port= and ?token= or run the shell';
      return;
    }
    this.open(found);
  }

  open({ port, token }) {
    /* A restart hands over a new port and token. Dropping the old socket
     * first means its `onclose` cannot arrive afterwards and paint an error
     * over a connection that is working. */
    if (this.#socket) {
      this.#closing = true;
      this.#stopPinging();
      this.#socket.close();
    }
    this.#closing = false;
    this.state = 'connecting';
    this.status = 'connecting';
    const socket = new WebSocket(`ws://127.0.0.1:${port}`);
    this.#socket = socket;
    socket.onopen = () => socket.send(JSON.stringify({ type: 'hello', token }));
    socket.onmessage = (event) => this.#handle(JSON.parse(event.data));
    socket.onerror = () => {
      this.state = 'error';
      this.status = 'the socket would not open';
    };
    socket.onclose = () => {
      this.#stopPinging();
      this.connected = false;
      this.busy = false;
      if (this.#closing) return;
      this.state = 'error';
      this.status = 'the sidecar closed the connection';
    };
  }

  close() {
    this.#closing = true;
    this.#stopPinging();
    this.#socket?.close();
    this.#socket = null;
  }

  /** The shell has given up restarting it. Three crashes in a minute is not a
   *  thing another restart fixes, and a window that goes on saying `idle` over
   *  a process that is gone is the failure this whole channel exists to
   *  prevent. */
  lost(why) {
    this.#closing = true;
    this.#stopPinging();
    this.connected = false;
    this.busy = false;
    this.state = 'error';
    this.status = String(why);
    this.#note(String(why), 'stop');
  }

  send(message) {
    if (this.#socket?.readyState !== WebSocket.OPEN) return false;
    this.#socket.send(JSON.stringify(message));
    return true;
  }

  // -- what the window does -------------------------------------------

  ask(text) {
    const said = text.trim();
    if (!said || !this.connected) return false;
    /* Cleared per turn rather than accumulated: the backstage panel answers
     * "what is happening now", and a thousand lines of history answers a
     * different question badly. */
    this.trace = [];
    this.sentences = [];
    this.busy = true;
    return this.send({ type: 'text_input', text: said });
  }

  listen() {
    this.send({ type: 'listen' });
  }

  cancel() {
    this.send({ type: 'cancel' });
  }

  setMode(mode) {
    this.send({ type: 'set_mode', mode });
  }

  setMuted(muted) {
    this.send({ type: 'set_mute', muted });
  }

  shutdown() {
    this.#closing = true;
    this.send({ type: 'shutdown' });
  }

  /** Download whatever is missing. The sidecar does the work, because the
   *  URLs, the sizes and the rule about which transcriber is wanted all live
   *  there already -- a second downloader would be a second thing to keep in
   *  step with the config. */
  fetchModels() {
    this.fetchError = '';
    this.fetching = { what: '', status: 'starting', done: 0, total: 0 };
    this.send({ type: 'fetch_models' });
  }

  /** True while the app cannot answer a question yet. The voice models are
   *  deliberately not on this list: without them the ear refuses and says so,
   *  and a question typed into the box still works. */
  get blocked() {
    return Boolean(this.setup) && (!this.setup.ollama || Boolean(this.setup.model));
  }

  /** True while anything at all is outstanding.
   *
   *  Wider than `blocked` on purpose. A machine with Ollama already running
   *  and the model already pulled is still missing a quarter of a gigabyte of
   *  voice on its first launch, and a first-run screen that only appears when
   *  the app is broken would never show that -- the wake word would simply not
   *  work, which is the failure voice spent a milestone learning to say out
   *  loud instead. It can be skipped, because typing does not need any of it.
   */
  get needsSetup() {
    return this.blocked || Boolean(this.setup?.models?.length);
  }

  /** Something the shell wants said in the transcript. The window has one
   *  place where things that went sideways are reported, and it is the same
   *  place a redaction or a blocked lookup appears. */
  notice(text) {
    this.#note(String(text));
  }

  answer(id, approved) {
    this.send({ type: 'confirm_response', id, approved });
    this.confirms = this.confirms.filter((c) => c.id !== id);
    const entry = this.entries.find((e) => e.kind === 'confirm' && e.id === id);
    if (entry) entry.answered = approved ? 'went ahead' : 'left alone';
  }

  // -- the health ping -------------------------------------------------

  #startPinging() {
    this.#stopPinging();
    this.#missed = 0;
    this.#ping = setInterval(() => {
      if (this.#missed >= MISSES_ALLOWED) {
        /* Three missed pings is a dead sidecar, and a window that goes on
         * showing `idle` over one is worse than a window that says so. The
         * shell restarts it; here we only stop lying about it. */
        this.state = 'error';
        this.status = 'the sidecar stopped answering';
        this.connected = false;
        this.#stopPinging();
        this.#socket?.close();
        return;
      }
      this.#missed += 1;
      this.send({ type: 'ping' });
    }, PING_EVERY_MS);
  }

  #stopPinging() {
    if (this.#ping) clearInterval(this.#ping);
    this.#ping = 0;
  }

  // -- what arrives ----------------------------------------------------

  #push(entry) {
    this.entries = [...this.entries, { id: (nextId += 1), ...entry }];
    return this.entries[this.entries.length - 1];
  }

  #note(text, tone = '') {
    this.#push({ kind: 'note', text, tone });
  }

  #handle(message) {
    switch (message.type) {
      case 'ready':
        this.connected = true;
        this.model = message.model;
        this.name = message.name || '';
        this.mode = message.mode;
        this.muted = message.muted;
        this.setup = message.setup ?? null;
        this.state = message.muted ? 'muted' : 'idle';
        this.status = message.model;
        this.#startPinging();
        break;

      case 'fetch':
        if (message.status === 'error') {
          this.fetching = null;
          this.fetchError = message.text || 'the download stopped';
        } else if (message.status === 'done') {
          this.fetching = null;
        } else {
          this.fetching = {
            what: message.what || '',
            status: message.status || '',
            done: message.done || 0,
            total: message.total || 0,
          };
        }
        break;

      case 'pong':
        this.#missed = 0;
        break;

      case 'state':
        this.state = message.value;
        if (message.value !== 'listening') this.micLevel = 0;
        if (message.value !== 'speaking') this.outLevel = 0;
        break;

      case 'level':
        this.micLevel = message.rms ?? 0;
        this.outLevel = message.out ?? 0;
        break;

      case 'mode':
        this.mode = message.value;
        break;

      case 'partial':
        if (message.final) {
          this.#push({ kind: 'you', text: message.text });
          this.#reply = null;
          this.busy = true;
        }
        break;

      case 'token':
        if (!this.#reply) this.#reply = this.#push({ kind: 'sunday', text: '' });
        this.#reply.text += message.text;
        break;

      case 'sentence':
        this.sentences = [...this.sentences, message.text];
        break;

      case 'tool':
        /* The orb's colour is the privacy affordance, so it is driven from
         * the scope and not from a state message the sidecar does not send. */
        this.state = message.scope === 'external' ? 'tool.external' : 'tool.local';
        this.#note(
          message.scope === 'external'
            ? `${message.name} — this leaves your machine`
            : `${message.name} — on your machine`,
          message.scope === 'external' ? 'external' : 'local',
        );
        break;

      case 'query':
        /* The exact text that crossed the airlock. Shown because the whole
         * design rests on it being nothing private, and a claim you cannot
         * check is a claim. */
        this.#note(`query that left: ${message.text}`, 'external');
        break;

      case 'blocked':
        this.state = 'blocked';
        this.#note(`${message.name} refused`, 'stop');
        break;

      case 'confirm': {
        const entry = this.#push({
          kind: 'confirm',
          id: message.id,
          text: message.text,
          path: message.path,
          action: message.action,
          answered: '',
        });
        this.confirms = [...this.confirms, entry];
        break;
      }

      case 'trace':
        this.trace = [...this.trace, message];
        break;

      case 'notice':
        this.#note(message.text);
        break;

      case 'error':
        this.state = 'error';
        this.status = message.text;
        this.#note(message.text, 'stop');
        break;

      case 'done':
        this.#reply = null;
        this.busy = false;
        if (!message.committed) this.#note('turn discarded, nothing remembered', 'stop');
        break;
    }
  }
}
