<script>
  import { onMount } from 'svelte';
  import Orb from './lib/Orb.svelte';
  import Transcript from './lib/Transcript.svelte';
  import Backstage from './lib/Backstage.svelte';
  import Setup from './lib/Setup.svelte';
  import { Session } from './lib/session.svelte.js';
  import { onShellEvent, setCompact, setState, settings } from './lib/shell.js';

  const session = new Session();

  let text = $state('');
  let compact = $state(new URLSearchParams(location.search).has('compact'));
  /* Closed by default. The trace is still emitted -- it is the only thing that
   * can tell "memory was read and had nothing" from "memory could not be
   * opened" -- but a running column of machine narration is not what a person
   * wants beside a conversation, and it was the first thing that made this
   * window feel like a debugger rather than an assistant. */
  let showBackstage = $state(false);
  let fps = $state({ focused: 60, blurred: 10 });
  /* Skipping only ever hides the screen. It cannot hide a `blocked` one,
     because there is nothing behind it to use. */
  let skippedSetup = $state(false);

  onMount(() => {
    session.connect();
    settings().then((s) => {
      if (!s) return;
      fps = { focused: s.fps_focused, blurred: s.fps_blurred };
      if (s.start_minimised) toggleCompact(true);
      /* A hotkey another program already owns is the quietest possible
       * failure: you press the keys and nothing at all happens, with no
       * console in a packaged app to explain it. */
      if (s.hotkey_error) session.notice(s.hotkey_error);
    });

    /* The tray menu and the global hotkey happen where there is no DOM, so
     * they arrive as events rather than clicks. */
    const off = [
      onShellEvent('listen', () => session.listen()),
      onShellEvent('toggle-mute', () => toggleMute()),
      onShellEvent('toggle-mode', () => toggleMode()),
      onShellEvent('shutdown', () => session.shutdown()),
      /* The shell spawned the sidecar, so the shell is what notices it die
       * and what restarts it. The window only learns the connection is now a
       * different connection. */
      onShellEvent('sidecar-restarted', (handshake) => session.open(handshake)),
      onShellEvent('sidecar-lost', (why) => session.lost(why)),
    ];
    return () => off.forEach((p) => p.then?.((f) => f?.()));
  });

  /* The tray is tinted with whatever the orb is showing, which is what keeps
   * "something is leaving this machine" visible when the window is not. */
  $effect(() => {
    setState(session.muted ? 'muted' : session.state);
  });

  /* The window is the only client, so it is the one that has to say goodbye.
   * `shutdown` is what flushes the session summary into Chroma. */
  $effect(() => {
    const bye = () => session.shutdown();
    addEventListener('beforeunload', bye);
    return () => removeEventListener('beforeunload', bye);
  });

  function submit(event) {
    event?.preventDefault();
    /* Typing does not switch modes. Typing *is* the switch, for this turn. */
    if (session.ask(text)) text = '';
  }

  /* Return sends, explicitly rather than by implicit form submission.
   *
   * The markup is a real form with a real submit button, so a browser would do
   * this on its own -- but "on its own" is a behaviour no harness here can
   * drive, and Return is the key a person actually presses. A path that cannot
   * be run is a path nobody checks, and the last time that was true of this
   * repo it was the command in the README.
   */
  function onKey(event) {
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    submit();
  }

  function toggleMute() {
    session.setMuted(!session.muted);
    session.muted = !session.muted;
  }

  function toggleMode() {
    session.setMode(session.mode === 'voice' ? 'text' : 'voice');
  }

  function toggleCompact(value = !compact) {
    compact = value;
    setCompact(value);
  }

  /* What it is doing, in words, under the name. Said only when it is worth
     saying: "idle" is what a window looks like when nothing is happening, so
     it does not need a caption. */
  const SAYING = {
    connecting: 'starting up',
    listening: 'listening',
    transcribing: 'catching that',
    thinking: 'thinking',
    'tool.local': 'working on your machine',
    'tool.external': 'reaching the web',
    blocked: 'refused that',
    speaking: 'speaking',
    muted: 'muted',
  };
  const saying = $derived(
    session.connected ? (SAYING[session.muted ? 'muted' : session.state] ?? '') : session.status,
  );
</script>

<main class:compact>
  {#if compact}
    <!-- Just the orb. It is the whole status display, so a window with only
         the orb in it is still a window that tells you everything. -->
    <button
      class="orbonly drag"
      type="button"
      title="double-click for the full window"
      ondblclick={() => toggleCompact(false)}
    >
      <Orb
        state={session.state}
        muted={session.muted}
        mic={session.micLevel}
        out={session.outLevel}
        {fps}
      />
    </button>
  {:else if session.blocked || session.fetching || (session.needsSetup && !skippedSetup)}
    <!-- A first run that has not finished. The window says which of the three
         halves is missing rather than refusing questions silently. -->
    <Setup
      setup={session.setup}
      fetching={session.fetching}
      error={session.fetchError}
      onfetch={() => session.fetchModels()}
      onskip={session.blocked ? null : () => (skippedSetup = true)}
    />
  {:else}
    <div class="body" class:withpanel={showBackstage}>
      <section class="stage">
        <div class="hero">
          <div class="orb">
            <Orb
              state={session.state}
              muted={session.muted}
              mic={session.micLevel}
              out={session.outLevel}
              {fps}
            />
          </div>
          <div class="who">
            <h1>{session.name}</h1>
            <p class="saying" class:showing={Boolean(saying)}>{saying || ' '}</p>
          </div>
          <div class="tools">
            <button type="button" onclick={toggleMode} disabled={!session.connected}>
              {session.mode === 'voice' ? 'Voice on' : 'Voice off'}
            </button>
            <button type="button" onclick={toggleMute} disabled={!session.connected}>
              {session.muted ? 'Unmute' : 'Mute'}
            </button>
            <button type="button" class:on={showBackstage} onclick={() => (showBackstage = !showBackstage)}>
              Backstage
            </button>
            <button type="button" onclick={() => toggleCompact()}>Compact</button>
          </div>
        </div>

        <Transcript entries={session.entries} onanswer={(id, ok) => session.answer(id, ok)} />

        <form onsubmit={submit}>
          <div class="field">
            <input
              bind:value={text}
              onkeydown={onKey}
              placeholder="Say something…"
              autocomplete="off"
              disabled={!session.connected}
            />
            <button
              class="icon"
              type="button"
              title="push to talk"
              aria-label="push to talk"
              onclick={() => session.listen()}
              disabled={!session.connected}
            >
              ◎
            </button>
            {#if session.busy}
              <button
                class="icon stop"
                type="button"
                title="stop"
                aria-label="stop"
                onclick={() => session.cancel()}
              >
                ■
              </button>
            {:else}
              <button
                class="icon send"
                type="submit"
                title="send"
                aria-label="send"
                disabled={!session.connected || !text.trim()}
              >
                ↑
              </button>
            {/if}
          </div>
        </form>
      </section>

      {#if showBackstage}
        <Backstage trace={session.trace} />
      {/if}
    </div>
  {/if}
</main>

<style>
  main {
    height: 100vh;
    display: grid;
  }

  .body {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    overflow: hidden;
  }
  .body.withpanel {
    grid-template-columns: minmax(0, 1fr) 320px;
  }
  .stage {
    display: grid;
    grid-template-rows: auto minmax(0, 1fr) auto;
    overflow: hidden;
  }

  /* The orb, the name and the controls on one line. It was a 190px band with
     a lone orb floating in it, which spent a fifth of the window on something
     that is sixty pixels of actual information. */
  .hero {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 14px 24px 10px;
  }
  .orb {
    width: 64px;
    height: 64px;
    flex: none;
  }
  .who {
    flex: 1;
    min-width: 0;
  }
  h1 {
    font-family: var(--serif);
    font-size: 21px;
    font-weight: 600;
    margin: 0;
    letter-spacing: 0.01em;
  }
  .saying {
    margin: 1px 0 0;
    font-size: 13px;
    color: var(--dim);
    opacity: 0;
    transition: opacity 180ms ease;
  }
  .saying.showing {
    opacity: 1;
  }
  .tools {
    display: flex;
    gap: 2px;
    flex: none;
  }
  .tools button {
    font-size: 13px;
    padding: 6px 11px;
  }
  .tools button.on {
    background: var(--raised);
    color: var(--text);
  }

  form {
    padding: 10px 24px 20px;
  }
  /* One rounded field with its buttons inside it, rather than four separate
     controls in a row. The row read as a form; this reads as somewhere to
     talk. */
  .field {
    max-width: 660px;
    margin: 0 auto;
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--r-lg);
    padding: 5px 6px 5px 8px;
    transition: border-color 140ms ease;
  }
  .field:focus-within {
    border-color: color-mix(in srgb, var(--local) 55%, var(--line));
  }
  .field input {
    flex: 1;
    min-width: 0;
    border: 0;
    background: transparent;
    padding: 9px 8px;
    font-size: 15px;
  }
  .field input:focus-visible {
    outline: none;
  }
  .field input::placeholder {
    color: var(--faint);
  }
  .icon {
    flex: none;
    width: 36px;
    height: 36px;
    padding: 0;
    display: grid;
    place-items: center;
    border-radius: 50%;
    font-size: 15px;
    line-height: 1;
  }
  .icon.send {
    background: var(--local);
    color: #1d1408;
    font-weight: 700;
  }
  .icon.send:hover:not(:disabled) {
    background: #e0855f;
    color: #1d1408;
  }
  .icon.send:disabled {
    background: var(--raised);
    color: var(--faint);
  }
  .icon.stop {
    background: var(--stop-soft);
    color: var(--stop);
    font-size: 11px;
  }

  /* Compact: the orb fills the window and is the drag handle. Double-click
     comes back, so a single click cannot dismiss it by accident. */
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

  @media (max-width: 820px) {
    .body.withpanel {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
