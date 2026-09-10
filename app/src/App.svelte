<script>
  import { onMount } from 'svelte';
  import { fade, fly } from 'svelte/transition';
  import { cubicOut } from 'svelte/easing';
  import Orb from './lib/Orb.svelte';
  import Transcript from './lib/Transcript.svelte';
  import Backstage from './lib/Backstage.svelte';
  import Setup from './lib/Setup.svelte';
  import Settings from './lib/Settings.svelte';
  import { Session } from './lib/session.svelte.js';
  import { ms } from './lib/motion.js';
  import {
    applyLaunchSettings,
    inShell,
    minimise,
    onShellEvent,
    quit,
    restartSidecar,
    setCompact,
    setState,
    settings,
    startDragging,
    toggleMaximise,
  } from './lib/shell.js';

  const session = new Session();

  let text = $state('');
  let compact = $state(new URLSearchParams(location.search).has('compact'));
  let fps = $state({ focused: 60, blurred: 10 });
  /* Skipping only ever hides the screen. It cannot hide a `blocked` one,
     because there is nothing behind it to use. */
  let skippedSetup = $state(false);

  /* The settings screen sits over everything, including the first-run screen.
     That is on purpose and it is the case that matters most: a stranger's
     first launch is exactly when the folders are wrong, the microphone is the
     wrong one and no credential has been entered, and a settings screen you
     can only reach once the app already works is a settings screen you cannot
     reach when you need it. */
  let showSettings = $state(false);
  /* Which opening this is. The screen is keyed on it, so every opening mounts
     a new one -- and it has to be keyed rather than merely unmounted, because
     closing plays an outro, and reopening before the outro finishes makes
     Svelte *resume* the instance on its way out rather than build another.
     Measured: Cancel, then the gear, and the edit Cancel had just thrown away
     was still in the box and still counted as unsaved. */
  let opened = $state(0);

  function openSettings() {
    opened += 1;
    showSettings = true;
    session.loadSettings();
  }

  /* `[ui]` as the shell read it. At launch, and again after every save: the
     frame rates used to be read here once, so a saved change did nothing
     until the window was reloaded, and nothing on the screen said so. */
  function useUi(s) {
    if (!s) return;
    fps = { focused: s.fps_focused, blurred: s.fps_blurred };
  }

  /* A save that took is the shell's cue as well. It owns the Run key and the
     frame rates, and neither is the sidecar's to apply -- which is why they
     are not on the restart list: the restart that list offers is the
     sidecar's. */
  $effect(() => {
    if (session.saveOutcome?.ok) applyLaunchSettings().then(useUi);
  });

  /* In that order, and the order is the point. `shutdown` over the socket is
     what folds the session summary into Chroma, and Chroma is SQLite -- the
     shell waits for the process to go before it resorts to killing it. Asking
     Rust to restart without telling the sidecar first is a force-kill with
     extra steps. Same sequence as quitting, for the same reason. */
  function restartNow() {
    session.shutdown();
    restartSidecar();
  }

  /* What a person types to leave. There is no Quit button any more: the title
     bar has a close, and this is the other way out. Handled here rather than
     as a tool, because quitting is a thing the window does and not a thing the
     model should be able to decide to do. */
  const LEAVING = /^\s*(exit|quit|bye|goodbye|keluar)\s*[.!]?\s*$/i;

  onMount(() => {
    session.connect();
    settings().then((s) => {
      useUi(s);
      /* `start_minimised` means hidden, and the Rust half already hides the
       * window (`main.rs`). Calling `toggleCompact` here as well made it mean
       * *and compact* -- always on top at 200x240 -- which nothing documents
       * and which the tray's Show cannot undo, because only `expand` leaves
       * compact. One setting, one meaning, one place. */
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
   * drive, and Return is the key a person actually presses. Shift+Return falls
   * through to the textarea's own behaviour, which is a new line. */
  function onKey(event) {
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    submit();
  }

  /* The composer is a textarea that grows, not an input that scrolls.
   *
   * A single-line input answers a long question by hiding the beginning of it,
   * which is the one thing a box you are still writing in must not do: you
   * cannot check what you asked without arrowing back through it. It wraps
   * now, up to about seven lines, and scrolls only past that -- and the height
   * is set from `scrollHeight`, so it is measured rather than counted.
   *
   * `height = 'auto'` first, every time. Without it `scrollHeight` is measured
   * against the height the box already has, so the box can only ever grow --
   * delete a paragraph and it keeps the room.
   */
  const COMPOSER_MAX = 168;
  let composer = $state(null);

  function resize() {
    if (!composer) return;
    /* Empty is `rows="1"`'s business, not a measurement's. Measuring it is
     * also wrong at the worst moment: on the first paint the flex row has not
     * been laid out, the box is momentarily zero wide, and the placeholder
     * wraps to a paragraph -- so the composer came up 168px tall, at the cap,
     * with nothing in it. Nothing to measure means nothing to set. */
    composer.style.height = '';
    if (!text) return;
    composer.style.height = 'auto';
    composer.style.height = `${Math.min(composer.scrollHeight, COMPOSER_MAX)}px`;
  }

  /* Driven by the text rather than by the keystroke, so it is also right after
   * a send clears the box, which is not a key event at all. */
  $effect(() => {
    text;
    resize();
  });

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
  /* `error` and `wake` are deliberately not in SAYING -- one has a sentence of
   * its own on `status`, the other is a flash rather than a state. Falling back
   * to `status` covers both: it left the line blank at the one moment the orb
   * goes red and the reason is sitting in `status` unread. */
  const saying = $derived(
    session.connected ? (SAYING[session.state] ?? session.status) : session.status,
  );

  /* The newest backstage line, in the place a person is already looking.
   *
   * It is the same text the panel shows, deliberately: a second wording for
   * the same fact is a second thing to keep true. Before the first line
   * arrives there is still something honest to say -- the turn has started
   * and nothing has been looked at yet. */
  const doingNow = $derived(session.trace.at(-1)?.text || 'starting the turn');

  /* Compact runs at the focused rate whether or not it is focused.
   *
   * `fps_blurred` exists so a window nobody is looking at stops asking for
   * frames next to a model that wants the whole machine. Compact is the exact
   * case that reasoning does not cover: it is a small always-on-top circle you
   * put in a corner *to watch while you work in something else*, so it is
   * never focused and was therefore always running at ten frames a second,
   * which is visibly not smooth. It is the one window that is looked at more
   * when it is blurred than when it is not. */
  const compactFps = $derived({ focused: fps.focused, blurred: fps.focused });

  /* One mousedown, three meanings, and none of them can be a drag region.
   *
   * Note 114: `-webkit-app-region: drag` swallows mouse events before the page
   * sees them, which is what made the click that left compact impossible to
   * fire. Asking the shell to start the drag on mousedown leaves every event
   * where the page can still read it -- so a double click can mean something,
   * and the whole window can still be picked up and moved. */
  function onCompactPress(event) {
    if (event.button !== 0) return;
    if (event.detail === 2) {
      toggleCompact(false);
      return;
    }
    startDragging();
  }
</script>

<!-- The composer's height is measured, so it has to be measured again when
     the width it wraps against changes. -->
<svelte:window onresize={resize} />

<main class:compact>
  {#if compact}
    <!-- Just the orb, filling the window. Every pixel of it drags, a double
         click expands, and the corner carries an explicit button as well --
         because this is the state in which being unable to get out strands the
         whole app: a small circle, always on top, with no menu and no title
         bar. The tray is still the third way, and the only one that survives
         the window itself being unclickable. -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="compactwindow" onmousedown={onCompactPress} ondblclick={() => toggleCompact(false)}>
      <Orb state={session.state} mic={session.micLevel} out={session.outLevel} fps={compactFps} />
      <button
        class="expand"
        type="button"
        title="Back to the full window"
        aria-label="back to the full window"
        onmousedown={(event) => event.stopPropagation()}
        onclick={() => toggleCompact(false)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M14 4h6v6M20 4l-7.5 7.5M10 20H4v-6M4 20l7.5-7.5" />
        </svg>
      </button>
    </div>
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
        <button
          type="button"
          class:on={showSettings}
          title="Settings"
          aria-label="settings"
          onclick={() => (showSettings ? (showSettings = false) : openSettings())}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="3.2" />
            <path
              d="M12 3.6v2.2M12 18.2v2.2M20.4 12h-2.2M5.8 12H3.6M17.9 6.1l-1.6 1.6M7.7 16.3l-1.6 1.6M17.9 17.9l-1.6-1.6M7.7 7.7L6.1 6.1"
            />
          </svg>
        </button>
        <button type="button" title="Minimise" aria-label="minimise" onclick={() => minimise()}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14" /></svg>
        </button>
        <button type="button" title="Maximise" aria-label="maximise" onclick={() => toggleMaximise()}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5.5" y="5.5" width="13" height="13" rx="1.5" /></svg>
        </button>
        <!-- Closes, and closing quits: the same thing typing `exit` does,
             in the same order, because `shutdown` is what folds the session
             summary into Chroma before anything is killed. -->
        <button class="shut" type="button" title="Close Alexa" aria-label="close" onclick={sayGoodbye}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      </div>
    </div>

    <!-- Every screen below sits in the same grid cell (`.layer`), so the one
         leaving and the one arriving overlap for the length of the crossfade
         instead of stacking into a third row -- which would be a jump, at the
         exact moment the eye is on it. -->
    {#if showSettings}
      <div
        class="layer wrap stack"
        in:fly={{ y: 14, duration: ms(280), delay: ms(60), easing: cubicOut }}
        out:fade={{ duration: ms(140) }}
      >
      <!-- A confirmation is the one question that cannot wait for the screen
           to close. The settings screen covers the transcript, where the card
           normally lives; a turn already running -- a spoken one, usually --
           can still ask to overwrite or delete, and a card nobody can see
           resolves to "no" after two minutes. So while any is outstanding it
           is shown here as well, above everything, with the same two answers. -->
      {#if session.confirms.length}
        <div class="asks">
          {#each session.confirms as ask (ask.id)}
            <div class="ask">
              <span>{ask.text}</span>
              <div class="answers">
                <button type="button" onclick={() => session.answer(ask.id, false)}>
                  {ask.action === 'delete' ? 'Keep it' : 'Leave it'}
                </button>
                <button class="primary" type="button" onclick={() => session.answer(ask.id, true)}>
                  {ask.action === 'delete' ? 'Delete' : 'Overwrite'}
                </button>
              </div>
            </div>
          {/each}
        </div>
      {/if}
      <div class="fill wrap">
      {#key opened}
      <Settings
        data={session.settings}
        saving={session.savingSettings}
        outcome={session.saveOutcome}
        forgetting={session.forgetting}
        forgot={session.forgot}
        google={session.google}
        canRestart={inShell()}
        onsave={(payload) => session.saveSettings(payload)}
        onforget={() => session.forgetAll()}
        ongoogle={() => session.googleAuth()}
        onrestart={restartNow}
        onclose={() => (showSettings = false)}
        {fps}
      />
      {/key}
      </div>
      </div>
    {:else if session.blocked || session.fetching || (session.needsSetup && !skippedSetup)}
      <div class="layer wrap" in:fade={{ duration: ms(220), delay: ms(60) }} out:fade={{ duration: ms(140) }}>
      <Setup
        setup={session.setup}
        fetching={session.fetching}
        error={session.fetchError}
        onfetch={() => session.fetchModels()}
        onskip={session.blocked ? null : () => (skippedSetup = true)}
      />
      </div>
    {:else}
      <div class="body layer" in:fade={{ duration: ms(220), delay: ms(60) }} out:fade={{ duration: ms(140) }}>
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

          <!-- Two controls, side by side, and no words on them. The line
               under the orb already says what is happening; a stack of
               labelled buttons under that says it twice and takes the wall
               the orb was given. What each one does is in its title. -->
          <div class="controls">
            <!-- One switch. On means the microphone is open, the wake word is
                 listening and replies are spoken; off means this is a text
                 box. They used to be two toggles and a hotkey, and no
                 combination of them was useful. -->
            <button
              type="button"
              class:on={session.voice}
              title={session.voice ? 'Voice on — microphone open, replies spoken' : 'Voice off — typing only'}
              aria-label={session.voice ? 'turn voice off' : 'turn voice on'}
              onclick={toggleVoice}
              disabled={!session.connected}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 3.5a2.8 2.8 0 0 1 2.8 2.8v5.4a2.8 2.8 0 0 1-5.6 0V6.3A2.8 2.8 0 0 1 12 3.5z" />
                <path d="M5.8 11.2a6.2 6.2 0 0 0 12.4 0M12 17.6V21" />
                {#if !session.voice}
                  <path d="M4.4 4.4l15.2 15.2" />
                {/if}
              </svg>
            </button>
            <button
              type="button"
              title="Compact — just the orb, on top of everything"
              aria-label="compact mode"
              onclick={() => toggleCompact(true)}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <rect x="3.4" y="4.6" width="17.2" height="14.8" rx="2.6" />
                <rect x="11.6" y="11.6" width="6.6" height="5.4" rx="1.6" fill="currentColor" stroke="none" />
              </svg>
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
          <div class="working" class:on={session.busy} aria-hidden={!session.busy}>
            <span class="spinner" aria-hidden="true"></span>
            <span class="what">{doingNow}</span>
          </div>

          <form onsubmit={submit}>
            <div class="field">
              <textarea
                bind:this={composer}
                bind:value={text}
                onkeydown={onKey}
                rows="1"
                placeholder="Say something…"
                autocomplete="off"
                disabled={!session.connected}
              ></textarea>
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
  /* Every grid declares its column, not only the three that were measured
     into note 142: a track's floor is its own content, so an undeclared one is
     a column waiting for the first long word. */
  main {
    height: 100vh;
    display: grid;
    grid-template-rows: 38px minmax(0, 1fr);
    grid-template-columns: minmax(0, 1fr);
  }
  main.compact {
    grid-template-rows: minmax(0, 1fr);
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
  /* The one title-bar control that is a toggle rather than an action, so it
     is the one that has an on state. */
  .windowbuttons button.on {
    color: var(--text);
    background: var(--surface);
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

  .layer {
    grid-row: 2;
    grid-column: 1;
    min-height: 0;
  }
  .wrap {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr);
  }
  /* The settings layer has a row for outstanding confirmations above the
     screen. The screen is pinned to the second row, so with nothing to ask
     the first is empty and takes no height. */
  .stack {
    grid-template-rows: auto minmax(0, 1fr);
  }
  .fill {
    grid-row: 2;
    min-height: 0;
  }
  .asks {
    grid-row: 1;
    display: grid;
    gap: 8px;
    padding: 12px 26px 0;
  }
  /* The transcript's card, laid out as a strip: the same ground, the same
     edge and the same two answers, safe one first. */
  .ask {
    display: flex;
    align-items: center;
    gap: 14px;
    background: var(--stop-soft);
    border: 1px solid color-mix(in srgb, var(--stop) 35%, transparent);
    border-radius: var(--r-md);
    padding: 12px 14px 12px 18px;
  }
  .ask span {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .answers {
    display: flex;
    gap: 8px;
    flex: none;
  }
  .answers button {
    border-color: var(--line);
  }

  .body {
    display: grid;
    /* 3:4:3, in fractions rather than pixels. The orb needs room to move and
       the backstage is a column of prose; the transcript is the one column
       that reads perfectly well narrower, because it is already capped at a
       720px reading measure and was only ever centring itself in the slack.
       `minmax(0, …)` on all three because a grid track's default floor is its
       content, and one long unbroken word in the backstage would otherwise
       push the ratio out. */
    grid-template-columns: minmax(0, 3fr) minmax(0, 4fr) minmax(0, 3fr);
    overflow: hidden;
  }

  /* -- left: the orb ---------------------------------------------------- */
  .side {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    /* Centred as a group rather than stacked from the top. The panel is a
       third of the window and the orb is the only thing in it that matters;
       hanging it off the ceiling with a column of air underneath made the
       whole side read as a toolbar that had run out of tools. */
    grid-template-rows: repeat(4, auto);
    align-content: center;
    justify-items: center;
    gap: 4px;
    padding: 24px 22px;
    /* A rule between the columns, which is not the hairline note 118 removed:
       that one ran down the side of the orb itself and was, in the end, half a
       device pixel the canvas never cleared. This one divides three panels,
       which is what a divider is for. */
    border-right: 1px solid var(--line-soft);
    text-align: center;
  }
  /* Sized to the panel, not to a number. The columns are fractions now, so a
     wider window gives the orb the room rather than giving it to a transcript
     that is capped at a reading measure anyway. The cap stops it becoming a
     wall on a very wide screen. */
  .orb {
    width: min(300px, 84%);
    aspect-ratio: 1;
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
    margin-top: 28px;
    display: flex;
    justify-content: center;
    gap: 10px;
  }
  .controls button {
    width: 46px;
    height: 46px;
    padding: 0;
    display: grid;
    place-items: center;
    border-radius: 50%;
    border: 1px solid var(--line);
  }
  .controls svg {
    width: 22px;
    height: 22px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.9;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .controls button.on {
    background: var(--raised);
    border-color: transparent;
    color: var(--text);
  }

  /* -- middle: the conversation ----------------------------------------- */
  .stage {
    display: grid;
    grid-template-rows: minmax(0, 1fr) auto auto;
    /* The column has to be declared, and this is the whole of the streaming
       overlap. A grid's implicit column is `auto`, whose *minimum* is the
       widest item's min-content -- and the working strip below is
       `white-space: nowrap`, so a long trace line made the stage's own track
       wider than the panel it sits in. Measured while a reply streamed: the
       transcript's client width walked 502 → 512 → 543 → 577 inside a 512px
       column, and the strip itself hung 65px past the divider. Same rule as
       note 133 in the outer grid, one level down. */
    grid-template-columns: minmax(0, 1fr);
    overflow: hidden;
  }
  /* Always in the layout, visible only while a turn is running.
     Rendering it with `{#if}` moved the transcript by 29 pixels at the start
     of every turn and again at the end -- a jump, twice, exactly when the
     reader is watching the text. A row that is always there costs a strip of
     air above the composer and buys a page that does not move. */
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
    opacity: 0;
    pointer-events: none;
    transition: opacity 140ms ease;
  }
  .working.on {
    opacity: 1;
  }
  /* `min-width: 0` because a flex item's floor is its own content, so without
     it a long trace line cannot shrink and the ellipsis never fires. */
  .working .what {
    min-width: 0;
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
    /* The buttons stay on the last line as the box grows, where the caret is,
       rather than floating in the middle of a paragraph. */
    align-items: flex-end;
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
  .field textarea {
    flex: 1;
    min-width: 0;
    border: 0;
    background: transparent;
    padding: 11px 8px;
    font-size: 16.5px;
    line-height: 1.45;
    /* No corner grip: the height is the text's to decide, not the mouse's. */
    resize: none;
    /* Below the cap `scrollHeight` equals the height, so no bar appears and
       nothing reflows; past it this is what makes the box scroll instead of
       growing off the top of the window. */
    overflow-y: auto;
    max-height: 168px;
    /* A path pasted into it has no spaces, and the box is narrower than the
       transcript. Same rule as note 135, on the way in. */
    overflow-wrap: anywhere;
  }
  .field textarea:focus-visible {
    outline: none;
  }
  .field textarea::placeholder {
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
  .compactwindow {
    position: relative;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    min-height: 0;
    background: var(--bg);
    cursor: grab;
  }
  .compactwindow:active {
    cursor: grabbing;
  }
  /* Big enough to hit without looking, small enough not to be the subject.
     It sits over the orb's corner, where the web is thinnest. */
  .expand {
    position: absolute;
    top: 6px;
    right: 6px;
    width: 30px;
    height: 30px;
    padding: 0;
    display: grid;
    place-items: center;
    border-radius: 50%;
    color: var(--faint);
    cursor: pointer;
    opacity: 0.55;
    transition: opacity 120ms ease, background 120ms ease, color 120ms ease;
  }
  .expand:hover {
    opacity: 1;
    background: var(--raised);
    color: var(--text);
  }
  .expand svg {
    width: 17px;
    height: 17px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  /* No breakpoint. The columns are fractions and the orb is a fraction of its
     column, so both give ground on their own -- which is the whole reason the
     one that was here could go. Positional and width-counting CSS is wrong the
     first time something else is rendered; a ratio is not. */
</style>
