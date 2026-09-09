<script>
  import { onMount } from 'svelte';
  import Orb from './lib/Orb.svelte';
  import Transcript from './lib/Transcript.svelte';
  import Backstage from './lib/Backstage.svelte';
  import Setup from './lib/Setup.svelte';
  import { Session } from './lib/session.svelte.js';
  import {
    inShell,
    minimise,
    onShellEvent,
    quit,
    setCompact,
    setState,
    settings,
  } from './lib/shell.js';

  const session = new Session();

  let text = $state('');
  let compact = $state(new URLSearchParams(location.search).has('compact'));
  let showBackstage = $state(true);
  let fps = $state({ focused: 60, blurred: 10 });
  /* Skipping only ever hides the screen. It cannot hide a `blocked` one,
     because there is nothing behind it to use. */
  let skippedSetup = $state(false);

  onMount(() => {
    session.connect();
    settings().then((s) => {
      if (!s) return;
      fps = { focused: s.fps_focused, blurred: s.fps_blurred };
      showBackstage = s.trace;
      if (s.start_minimised) toggleCompact(true);
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
   * `shutdown` is what flushes the session summary into Chroma; a window that
   * closes without it loses the last conversation. */
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

  /* Quit in the right order: the socket is told first, because `shutdown` is
   * what flushes the session summary into Chroma. The shell then waits for the
   * process to go and stops waiting after five seconds. */
  function sayGoodbye() {
    session.shutdown();
    quit();
  }

  const stateLabel = $derived(session.connected ? session.state : session.status);
</script>

<main class:compact>
  <header class="drag">
    <div class="title">
      <strong>Sunday</strong>
      <span class="status" title={session.status}>{stateLabel}</span>
    </div>
    <div class="controls nodrag">
      <button type="button" onclick={toggleMode} disabled={!session.connected}>
        {session.mode === 'voice' ? 'Voice on' : 'Voice off'}
      </button>
      <button type="button" onclick={toggleMute} disabled={!session.connected}>
        {session.muted ? 'Unmute' : 'Mute'}
      </button>
      <button type="button" onclick={() => (showBackstage = !showBackstage)}>
        {showBackstage ? 'Hide backstage' : 'Backstage'}
      </button>
      <button type="button" onclick={() => toggleCompact()}>Compact</button>
      {#if inShell()}
        <button class="always" type="button" onclick={minimise}>&minus;</button>
        <button class="always" type="button" onclick={sayGoodbye}>&times;</button>
      {/if}
    </div>
  </header>

  {#if session.blocked || session.fetching || (session.needsSetup && !skippedSetup)}
    <!-- A first run that has not finished. The window says which of the three
         halves is missing rather than refusing questions silently -- a socket
         that accepts a question it cannot answer is worse than one that says
         what is short. -->
    <Setup
      setup={session.setup}
      fetching={session.fetching}
      error={session.fetchError}
      onfetch={() => session.fetchModels()}
      onskip={session.blocked ? null : () => (skippedSetup = true)}
    />
  {:else if compact}
    <!-- Just the orb. It is the whole status display, so a window with only
         the orb in it is still a window that tells you everything. -->
    <button
      class="orbonly nodrag"
      type="button"
      title="back to the full window"
      onclick={() => toggleCompact(false)}
    >
      <Orb
        state={session.state}
        muted={session.muted}
        mic={session.micLevel}
        out={session.outLevel}
        {fps}
      />
    </button>
  {:else}
    <div class="body" class:withpanel={showBackstage}>
      <section class="stage">
        <div class="orb">
          <Orb
            state={session.state}
            muted={session.muted}
            mic={session.micLevel}
            out={session.outLevel}
            {fps}
          />
        </div>

        <Transcript entries={session.entries} onanswer={(id, ok) => session.answer(id, ok)} />

        <form onsubmit={submit}>
          <input
            bind:value={text}
            onkeydown={onKey}
            placeholder="Ask Sunday something…"
            autocomplete="off"
            disabled={!session.connected}
          />
          <button type="submit" disabled={!session.connected || !text.trim()}>Send</button>
          <button
            type="button"
            title="push to talk"
            onclick={() => session.listen()}
            disabled={!session.connected}
          >
            Mic
          </button>
          <button type="button" onclick={() => session.cancel()} disabled={!session.busy}>
            Stop
          </button>
        </form>
      </section>

      {#if showBackstage}
        <Backstage trace={session.trace} sentences={session.sentences} />
      {/if}
    </div>
  {/if}
</main>

<style>
  main {
    height: 100vh;
    display: grid;
    grid-template-rows: auto minmax(0, 1fr);
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 8px 10px 8px 14px;
    border-bottom: 1px solid var(--line);
    background: var(--panel);
  }
  .title {
    display: flex;
    align-items: baseline;
    gap: 10px;
    min-width: 0;
  }
  .status {
    color: var(--dim);
    font-size: 12px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .controls {
    display: flex;
    gap: 6px;
  }
  .controls button {
    padding: 5px 10px;
    font-size: 12px;
  }

  .body {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    overflow: hidden;
  }
  .body.withpanel {
    grid-template-columns: minmax(0, 1fr) 300px;
  }
  .stage {
    display: grid;
    grid-template-rows: 190px minmax(0, 1fr) auto;
    gap: 12px;
    padding: 12px 16px 16px;
    overflow: hidden;
  }
  .orb {
    min-height: 0;
  }
  form {
    display: flex;
    gap: 8px;
  }
  form input {
    flex: 1;
    min-width: 0;
  }

  /* Compact: the orb fills the window and clicking it comes back. */
  main.compact {
    grid-template-rows: auto minmax(0, 1fr);
    background: transparent;
  }
  main.compact header {
    background: transparent;
    border-bottom: 0;
    padding: 4px 6px;
  }
  /* Compact is just the orb, so the controls go -- except the window's own,
     which are marked rather than counted. Written as "all but the last two"
     it depended on how many buttons happened to exist, and in a browser,
     where the shell's two are not rendered, it hid the wrong two. */
  main.compact .controls button:not(.always) {
    display: none;
  }
  .orbonly {
    border: 0;
    background: none;
    padding: 0;
    width: 100%;
    height: 100%;
  }

  @media (max-width: 760px) {
    .body.withpanel {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
