<script>
  import { onMount } from 'svelte';
  import Orb from './lib/Orb.svelte';
  import Transcript from './lib/Transcript.svelte';
  import Backstage from './lib/Backstage.svelte';
  import Setup from './lib/Setup.svelte';
  import { Session } from './lib/session.svelte.js';
  import { onShellEvent, quit, setCompact, setState, settings } from './lib/shell.js';

  const session = new Session();

  let text = $state('');
  let compact = $state(new URLSearchParams(location.search).has('compact'));
  let showBackstage = $state(true);
  let fps = $state({ focused: 60, blurred: 10 });
  let hotkey = $state('');
  /* Skipping only ever hides the screen. It cannot hide a `blocked` one,
     because there is nothing behind it to use. */
  let skippedSetup = $state(false);

  onMount(() => {
    session.connect();
    settings().then((s) => {
      if (!s) return;
      fps = { focused: s.fps_focused, blurred: s.fps_blurred };
      hotkey = s.hotkey || '';
      if (s.start_minimised) toggleCompact(true);
      /* A shortcut every candidate for which is already taken is the quietest
       * possible failure: you press keys and nothing at all happens, with no
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
      /* The tray can pull the window out of compact, because compact is
       * exactly the state in which the window may be hard to click. */
      onShellEvent('expanded', () => (compact = false)),
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

  /* Return sends, explicitly rather than by implicit form submission -- the
   * markup would do it unaided, but "unaided" is a path no harness here can
   * drive, and Return is the key a person actually presses. */
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
    muted: 'muted',
    idle: 'ready',
  };
  const saying = $derived(
    session.connected ? (SAYING[session.muted ? 'muted' : session.state] ?? '') : session.status,
  );
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
      <Orb
        state={session.state}
        muted={session.muted}
        mic={session.micLevel}
        out={session.outLevel}
        {fps}
      />
    </button>
  {:else if session.blocked || session.fetching || (session.needsSetup && !skippedSetup)}
    <Setup
      setup={session.setup}
      fetching={session.fetching}
      error={session.fetchError}
      onfetch={() => session.fetchModels()}
      onskip={session.blocked ? null : () => (skippedSetup = true)}
    />
  {:else}
    <div class="body" class:withpanel={showBackstage}>
      <!-- Left: the orb, big, with nothing competing with it. It is the whole
           status display, so it gets a wall rather than a corner. -->
      <aside class="side">
        <div class="orb">
          <Orb
            state={session.state}
            muted={session.muted}
            mic={session.micLevel}
            out={session.outLevel}
            {fps}
          />
        </div>
        <h1>{session.name}</h1>
        <p class="saying">{saying}</p>

        <div class="controls">
          <button type="button" class:on={session.mode === 'voice'} onclick={toggleMode} disabled={!session.connected}>
            <span class="glyph">{session.mode === 'voice' ? '◉' : '○'}</span>
            {session.mode === 'voice' ? 'Voice on' : 'Voice off'}
          </button>
          <button type="button" class:on={session.muted} onclick={toggleMute} disabled={!session.connected}>
            <span class="glyph">{session.muted ? '⊘' : '⏺'}</span>
            {session.muted ? 'Muted' : 'Mic live'}
          </button>
          <button type="button" class:on={showBackstage} onclick={() => (showBackstage = !showBackstage)}>
            <span class="glyph">☰</span> Backstage
          </button>
          <button type="button" onclick={() => toggleCompact(true)}>
            <span class="glyph">⤡</span> Compact
          </button>
        </div>

        <div class="foot">
          {#if hotkey}
            <p class="hint">Push to talk anywhere: <b>{hotkey}</b></p>
          {/if}
          <!-- Distinct from the window's close, which hides to the tray. This
               is the one that stops the sidecar, and it says so. -->
          <button class="quit" type="button" onclick={sayGoodbye}>Quit Alexa</button>
        </div>
      </aside>

      <!-- Middle: the conversation. -->
      <section class="stage">
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
              title={hotkey ? `Push to talk (${hotkey})` : 'Push to talk'}
              aria-label="push to talk"
              onclick={() => session.listen()}
              disabled={!session.connected}
            >
              ◎
            </button>
            {#if session.busy}
              <button class="icon stop" type="button" title="Stop" aria-label="stop" onclick={() => session.cancel()}>
                ■
              </button>
            {:else}
              <button
                class="icon send"
                type="submit"
                title="Send"
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
  main.compact {
    grid-template-rows: 28px minmax(0, 1fr);
  }

  .body {
    display: grid;
    grid-template-columns: 250px minmax(0, 1fr);
    overflow: hidden;
  }
  .body.withpanel {
    grid-template-columns: 250px minmax(0, 1fr) 340px;
  }

  /* -- left: the orb ---------------------------------------------------- */
  .side {
    display: grid;
    grid-template-rows: auto auto auto 1fr auto;
    justify-items: center;
    gap: 4px;
    padding: 28px 20px 20px;
    border-right: 1px solid var(--line-soft);
    text-align: center;
  }
  .orb {
    width: 150px;
    height: 150px;
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
  .foot {
    width: 100%;
    display: grid;
    gap: 10px;
  }
  .hint {
    margin: 0;
    font-size: 13px;
    color: var(--faint);
    line-height: 1.45;
  }
  .hint b {
    color: var(--dim);
    font-weight: 600;
  }
  .quit {
    border: 1px solid var(--line);
    width: 100%;
  }
  .quit:hover {
    border-color: var(--stop);
    color: var(--stop);
    background: var(--stop-soft);
  }

  /* -- middle: the conversation ----------------------------------------- */
  .stage {
    display: grid;
    grid-template-rows: minmax(0, 1fr) auto;
    overflow: hidden;
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
  .field:focus-within {
    border-color: color-mix(in srgb, var(--local) 55%, var(--line));
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
    width: 44px;
    height: 44px;
    padding: 0;
    display: grid;
    place-items: center;
    border-radius: 50%;
    font-size: 19px;
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
    font-size: 13px;
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

  @media (max-width: 1000px) {
    .body.withpanel {
      grid-template-columns: 250px minmax(0, 1fr);
    }
  }
</style>
