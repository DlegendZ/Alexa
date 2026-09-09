<script>
  import { onMount } from 'svelte';
  import Orb from './lib/Orb.svelte';
  import Transcript from './lib/Transcript.svelte';
  import Backstage from './lib/Backstage.svelte';
  import Setup from './lib/Setup.svelte';
  import { Session } from './lib/session.svelte.js';
  import {
    close,
    minimise,
    onShellEvent,
    quit,
    setCompact,
    setState,
    settings,
    toggleMaximise,
  } from './lib/shell.js';

  const session = new Session();

  let text = $state('');
  let compact = $state(new URLSearchParams(location.search).has('compact'));
  let fps = $state({ focused: 60, blurred: 10 });
  /* Skipping only ever hides the screen. It cannot hide a `blocked` one,
     because there is nothing behind it to use. */
  let skippedSetup = $state(false);

  /* What a person types to leave. There is no Quit button any more: the title
     bar has a close, and this is the other way out. Handled here rather than
     as a tool, because quitting is a thing the window does and not a thing the
     model should be able to decide to do. */
  const LEAVING = /^\s*(exit|quit|bye|goodbye|keluar)\s*[.!]?\s*$/i;

  onMount(() => {
    session.connect();
    settings().then((s) => {
      if (!s) return;
      fps = { focused: s.fps_focused, blurred: s.fps_blurred };
      if (s.start_minimised) toggleCompact(true);
    });

    /* The tray menu happens where there is no DOM, so it arrives as an event
     * rather than a click. */
    const off = [
      onShellEvent('toggle-voice', () => toggleVoice()),
      onShellEvent('shutdown', () => session.shutdown()),
      /* The tray can pull the window out of compact, because compact is
         exactly the state in which the window may be hard to click. */
      onShellEvent('expanded', () => (compact = false)),
      onShellEvent('sidecar-restarted', (handshake) => session.open(handshake)),
      onShellEvent('sidecar-lost', (why) => session.lost(why)),
    ];
    return () => off.forEach((p) => p.then?.((f) => f?.()));
  });

  /* The tray is tinted with whatever the orb is showing, which is what keeps
   * "something is leaving this machine" visible when the window is not. */
  $effect(() => {
    setState(session.state);
  });

  $effect(() => {
    const bye = () => session.shutdown();
    addEventListener('beforeunload', bye);
    return () => removeEventListener('beforeunload', bye);
  });

  function submit(event) {
    event?.preventDefault();
    if (LEAVING.test(text)) {
      text = '';
      sayGoodbye();
      return;
    }
    /* Typing does not switch anything. Typing works whether or not the
     * microphone is open, which is the point of there being one switch. */
    if (session.ask(text)) text = '';
  }

  /* Return sends, explicitly rather than by implicit form submission -- the
   * markup would do it unaided, but "unaided" is a path no harness here can
   * drive, and Return is the key a person actually presses. */
  function onKey(event) {
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    submit();
  }

  function toggleVoice() {
    session.setVoice(!session.voice);
  }

  function toggleCompact(value = !compact) {
    compact = value;
    setCompact(value);
  }

  /* Quit in the right order: the socket is told first, because `shutdown` is
   * what flushes the session summary into Chroma and, as it turns out, what
   * leaves the database readable. The shell then waits and stops waiting. */
  function sayGoodbye() {
    session.shutdown();
    quit();
  }

  const SAYING = {
    connecting: 'starting up',
    listening: 'listening',
    transcribing: 'catching that',
    thinking: 'thinking',
    'tool.local': 'working on your machine',
    'tool.external': 'reaching the web',
    blocked: 'refused that',
    speaking: 'speaking',
    idle: 'microphone off',
  };
  const saying = $derived(
    session.connected ? (SAYING[session.state] ?? '') : session.status,
  );

  /* The newest backstage line, in the place a person is already looking.
   *
   * It is the same text the panel shows, deliberately: a second wording for
   * the same fact is a second thing to keep true. Before the first line
   * arrives there is still something honest to say -- the turn has started
   * and nothing has been looked at yet. */
  const doingNow = $derived(session.trace.at(-1)?.text || 'starting the turn');
</script>

<main class:compact>
  {#if compact}
    <!-- Just the orb. Two ways out, because this is the state in which being
         unable to get out strands the whole app: the strip at the top is the
         drag handle and carries an explicit button, and the orb itself is
         clickable. The orb is deliberately NOT a drag region -- making it one
         is what swallowed every click on it. -->
    <div class="compactbar drag">
      <button class="nodrag chip" type="button" title="back to the full window" onclick={() => toggleCompact(false)}>
        Expand
      </button>
    </div>
    <button
      class="orbonly nodrag"
      type="button"
      title="back to the full window"
      onclick={() => toggleCompact(false)}
    >
      <Orb state={session.state} mic={session.micLevel} out={session.outLevel} {fps} />
    </button>
  {:else}
    <!-- The title bar, drawn here rather than by Windows.
         Windows will not let you keep its caption buttons and drop the icon
         and the title, and it draws all three at a size chosen for a file
         manager. So the frame is off and this is the whole bar: nothing on
         the left but somewhere to drag, and three controls on the right at a
         size you can hit. -->
    <div class="titlebar drag">
      <div class="grip"></div>
      <div class="windowbuttons nodrag">
        <button type="button" title="Minimise" aria-label="minimise" onclick={() => minimise()}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14" /></svg>
        </button>
        <button type="button" title="Maximise" aria-label="maximise" onclick={() => toggleMaximise()}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5.5" y="5.5" width="13" height="13" rx="1.5" /></svg>
        </button>
        <!-- Hides to the tray, the way the native close did. Quitting is
             `exit` in the box, or the tray menu. -->
        <button class="shut" type="button" title="Close to tray" aria-label="close" onclick={() => close()}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      </div>
    </div>

    {#if session.blocked || session.fetching || (session.needsSetup && !skippedSetup)}
      <Setup
        setup={session.setup}
        fetching={session.fetching}
        error={session.fetchError}
        onfetch={() => session.fetchModels()}
        onskip={session.blocked ? null : () => (skippedSetup = true)}
      />
    {:else}
      <div class="body">
        <!-- Left: the orb, big, with nothing competing with it. It is the whole
             status display, so it gets a wall rather than a corner, and no rule
             down the side of it -- a hairline beside a thing made of light is
             the one edge the orb spent a milestone getting rid of. -->
        <aside class="side">
          <div class="orb">
            <Orb state={session.state} mic={session.micLevel} out={session.outLevel} {fps} />
          </div>
          <h1>{session.name}</h1>
          <p class="saying">{saying}</p>

          <div class="controls">
            <!-- One switch. On means the microphone is open, the wake word is
                 listening and replies are spoken; off means this is a text
                 box. They used to be two toggles and a hotkey, and no
                 combination of them was useful. -->
            <button
              type="button"
              class:on={session.voice}
              onclick={toggleVoice}
              disabled={!session.connected}
            >
              <span class="glyph">{session.voice ? '◉' : '○'}</span>
              {session.voice ? 'Voice on' : 'Voice off'}
            </button>
            <button type="button" onclick={() => toggleCompact(true)}>
              <span class="glyph">⤡</span> Compact
            </button>
          </div>
        </aside>

        <!-- Middle: the conversation. -->
        <section class="stage">
          <Transcript entries={session.entries} onanswer={(id, ok) => session.answer(id, ok)} />

          <!-- What it is doing, while it is doing it. The backstage panel says
               the same thing at length; this is the one line of it that
               belongs where the person is already looking. Waiting with no
               idea what is being waited on is the thing this removes. -->
          {#if session.busy}
            <div class="working">
              <span class="spinner" aria-hidden="true"></span>
              <span class="what">{doingNow}</span>
            </div>
          {/if}

          <form onsubmit={submit}>
            <div class="field">
              <input
                bind:value={text}
                onkeydown={onKey}
                placeholder="Say something…"
                autocomplete="off"
                disabled={!session.connected}
              />
              {#if session.busy}
                <button class="icon stop" type="button" title="Stop" aria-label="stop" onclick={() => session.cancel()}>
                  <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5.5" y="5.5" width="13" height="13" rx="2.5" /></svg>
                </button>
              {:else}
                <button
                  class="icon send"
                  type="submit"
                  title="Send"
                  aria-label="send"
                  disabled={!session.connected || !text.trim()}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M12 19V6M12 5.5l-6.2 6.2M12 5.5l6.2 6.2" />
                  </svg>
                </button>
              {/if}
            </div>
          </form>
        </section>

        <!-- Right: the backstage, always. It was behind a toggle, and a panel
             that answers "is it stuck or is it reading" is no use to anybody
             who has to decide to open it first. -->
        <Backstage trace={session.trace} busy={session.busy} />
      </div>
    {/if}
  {/if}
</main>

<style>
  main {
    height: 100vh;
    display: grid;
    grid-template-rows: 38px minmax(0, 1fr);
  }
  main.compact {
    grid-template-rows: 28px minmax(0, 1fr);
  }

  /* -- the title bar ----------------------------------------------------- */
  .titlebar {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: stretch;
    background: var(--bg);
  }
  .grip {
    min-width: 0;
  }
  .windowbuttons {
    display: flex;
  }
  .windowbuttons button {
    width: 48px;
    border: 0;
    border-radius: 0;
    padding: 0;
    display: grid;
    place-items: center;
    color: var(--dim);
  }
  .windowbuttons svg {
    width: 19px;
    height: 19px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.9;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .windowbuttons button:hover:not(:disabled) {
    background: var(--raised);
    color: var(--text);
  }
  .windowbuttons .shut:hover:not(:disabled) {
    background: var(--stop);
    color: #1d1408;
  }

  .body {
    display: grid;
    /* Wider than they were, both of them. The orb needs room to move and the
       backstage is a column of prose; the transcript is the one column that
       reads perfectly well narrower, because it is already capped at a
       reading measure and was only ever centring itself in the slack. */
    grid-template-columns: 300px minmax(0, 1fr) 390px;
    overflow: hidden;
  }

  /* -- left: the orb ---------------------------------------------------- */
  .side {
    display: grid;
    grid-template-rows: auto auto auto 1fr;
    justify-items: center;
    gap: 4px;
    padding: 22px 22px 20px;
    text-align: center;
  }
  .orb {
    width: 190px;
    height: 190px;
  }
  h1 {
    font-size: 27px;
    font-weight: 600;
    margin: 10px 0 0;
    letter-spacing: 0.01em;
  }
  .saying {
    margin: 0;
    font-size: 14.5px;
    color: var(--dim);
  }
  .controls {
    align-self: start;
    margin-top: 24px;
    display: grid;
    gap: 6px;
    width: 100%;
  }
  .controls button {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    text-align: left;
    padding: 11px 14px;
  }
  .controls button.on {
    background: var(--raised);
    color: var(--text);
  }
  .glyph {
    font-size: 17px;
    line-height: 1;
    width: 20px;
    text-align: center;
    flex: none;
  }

  /* -- middle: the conversation ----------------------------------------- */
  .stage {
    display: grid;
    grid-template-rows: minmax(0, 1fr) auto auto;
    overflow: hidden;
  }
  .working {
    max-width: 720px;
    width: 100%;
    margin: 0 auto;
    padding: 6px 30px 0;
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 14px;
    color: var(--dim);
  }
  .working .what {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* One mote going round, in the same white the orb is at rest. A bar would
     be a progress bar, and there is no progress to report. */
  .spinner {
    flex: none;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: radial-gradient(circle at 50% 12%, var(--text) 0 2.4px, transparent 2.6px);
    animation: orbit 900ms linear infinite;
  }
  @keyframes orbit {
    to {
      transform: rotate(360deg);
    }
  }
  form {
    padding: 12px 28px 26px;
  }
  .field {
    max-width: 720px;
    margin: 0 auto;
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--r-lg);
    padding: 6px 8px 6px 10px;
    transition: border-color 140ms ease;
  }
  /* The focus edge is the light the box comes up in, and it is white for the
     same reason the orb is: colour in this window means a tool is running. */
  .field:focus-within {
    border-color: color-mix(in srgb, var(--text) 45%, var(--line));
  }
  .field input {
    flex: 1;
    min-width: 0;
    border: 0;
    background: transparent;
    padding: 11px 8px;
    font-size: 16.5px;
  }
  .field input:focus-visible {
    outline: none;
  }
  .field input::placeholder {
    color: var(--faint);
  }
  .icon {
    flex: none;
    width: 46px;
    height: 46px;
    padding: 0;
    display: grid;
    place-items: center;
    border-radius: 50%;
  }
  /* Big enough to hit and heavy enough to read. The arrow used to be a text
     glyph at whatever weight the serif drew it, which at 19px was a hairline
     in a 44px circle. */
  .icon svg {
    width: 24px;
    height: 24px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2.4;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .icon.send {
    background: var(--text);
    color: var(--bg);
  }
  .icon.send:hover:not(:disabled) {
    background: #ffffff;
    color: var(--bg);
  }
  .icon.send:disabled {
    background: var(--raised);
    color: var(--faint);
  }
  .icon.stop {
    background: var(--stop-soft);
    color: var(--stop);
  }
  /* The square is the whole button, so it is drawn at a size you can see:
     a 10px glyph in a 46px circle reads as a dot. */
  .icon.stop svg {
    width: 26px;
    height: 26px;
    fill: currentColor;
    stroke: none;
  }

  /* -- compact ---------------------------------------------------------- */
  .compactbar {
    display: flex;
    justify-content: center;
    align-items: center;
    background: var(--bg);
  }
  .chip {
    font-size: 12.5px;
    padding: 3px 12px;
    border-radius: 999px;
    border: 1px solid var(--line);
  }
  .orbonly {
    border: 0;
    background: none;
    padding: 0;
    width: 100%;
    height: 100%;
    border-radius: 0;
  }
  .orbonly:hover {
    background: none;
  }

  /* The backstage is not behind a toggle any more, so it cannot be dropped
     at a narrow width either -- it would leave a three-column grid with
     something in a fourth row. Both side panels give ground instead. */
  @media (max-width: 1120px) {
    .body {
      grid-template-columns: 240px minmax(0, 1fr) 310px;
    }
    .orb {
      width: 150px;
      height: 150px;
    }
  }
</style>
