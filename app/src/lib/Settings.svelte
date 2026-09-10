<script>
  /* The settings screen.
   *
   * It renders a form it does not know. Every field, its label, its help, its
   * type and whether editing it needs a restart come down the socket from
   * `sunday/settings.py`, and this file lays them out. That is deliberate and
   * it is the same rule `tests/test_shell.py` enforces for the protocol: a
   * form written out here against a dataclass written out in Python is two
   * ends that drift, except that these two would drift silently -- a renamed
   * field renders as an empty box, and a new one never appears at all.
   *
   * Credentials are the exception, and they are the exception in one
   * direction only. They come down as "set" and four characters, never as
   * values, so this screen can say which key is in there and can never leak
   * one back out through a devtools panel or a crash report. What goes up is
   * only what the user actually typed into a box.
   *
   * Two buttons. Save writes; Cancel throws away whatever has not been saved
   * and closes. There was a third, Discard, which was Cancel without the
   * closing -- three buttons for two outcomes, read every time. Each opening
   * mounts a new copy of this component (App keys it), so there is no second
   * copy of the edits anywhere to forget to clear: reopening always starts
   * from what the sidecar says is true, even when it happens while the last
   * copy is still fading out.
   *
   * The right-hand column is an orb, and it is the same renderer as the main
   * window's, told the same kind of facts. White and attentive while something
   * is unsaved, a pulse from the inside for each change and for a save that
   * took, red for a save that was refused, teal while the browser is talking
   * to Google. Colour stays spent on the two facts it is spent on everywhere
   * else -- something leaving the machine, and a refusal -- which is why a
   * successful save is a pulse and not a colour.
   */
  import { tick, untrack } from 'svelte';
  import { fly } from 'svelte/transition';
  import { cubicOut } from 'svelte/easing';
  import Orb from './Orb.svelte';
  import { ms } from './motion.js';

  let {
    data,
    saving = false,
    outcome = null,
    forgetting = false,
    forgot = null,
    google = null,
    canRestart = false,
    fps = { focused: 60, blurred: 10 },
    onsave = () => {},
    onforget = () => {},
    ongoogle = () => {},
    onrestart = () => {},
    onclose = () => {},
  } = $props();

  /* Edits, by `section.field`. A field not in here is showing what the sidecar
   * sent, which is the value actually in use. */
  let edits = $state({});
  let creds = $state({});
  let section = $state('assistant');
  let confirming = $state(false);

  /* Whether the next payload is the answer to a save.
   *
   * A fresh payload arrives after a save, but also after emptying the store
   * and after signing in to Google. Clearing the edits on every one of them
   * threw away a half-finished form because somebody pressed an unrelated
   * button on another page -- and the only thing on this screen that is
   * allowed to throw edits away is Cancel. */
  let awaitingSave = false;

  $effect(() => {
    data;
    if (awaitingSave) {
      edits = {};
      creds = {};
      awaitingSave = false;
    }
    confirming = false;
  });

  const sections = $derived(data?.sections ?? []);
  const current = $derived(sections.find((s) => s.key === section) ?? sections[0]);
  const byKey = $derived(new Map(sections.flatMap((s) => s.fields).map((f) => [f.key, f])));
  const pages = $derived([
    ...sections.map((s) => ({ key: s.key, title: s.title })),
    { key: 'keys', title: 'Keys and sign-in' },
    { key: 'store', title: 'Long-term memory' },
  ]);

  function held(field) {
    return field.key in edits ? edits[field.key] : field.value;
  }

  /* What a value means rather than how the box holds it. A number box hands
   * back a string, a list box keeps the blank line the caret is on, and a
   * folder row with no path is not a folder -- none of which is a change. */
  function norm(field, value) {
    switch (field.kind) {
      case 'int':
      case 'float': {
        const n = typeof value === 'string' && value.trim() !== '' ? Number(value) : value;
        return typeof n === 'number' && !Number.isNaN(n) ? n : String(value ?? '');
      }
      case 'lines':
        return (value ?? []).map((s) => String(s).trim()).filter(Boolean);
      case 'roots':
        return (value ?? [])
          .map((r) => ({ label: (r.label ?? '').trim(), path: (r.path ?? '').trim() }))
          .filter((r) => r.path);
      case 'text':
      case 'long':
        return value ?? '';
      default:
        return value;
    }
  }

  /* Deep enough for what these hold, and nothing here is ever big enough for
   * JSON to cost anything. */
  const same = (field, a, b) => JSON.stringify(norm(field, a)) === JSON.stringify(norm(field, b));

  /* What would actually be written. Typing a value and typing it back is not
   * a change, so it does not light Save and it is not counted. */
  const changed = $derived(
    Object.keys(edits).filter((key) => {
      const field = byKey.get(key);
      return !field || !same(field, edits[key], field.value);
    }),
  );
  const credChanged = $derived(
    Object.entries(creds)
      .filter(([key, value]) => value !== '' || data?.credentials?.find((c) => c.key === key)?.set)
      .map(([key]) => key),
  );
  const unsaved = $derived(changed.length + credChanged.length);
  const dirty = $derived(unsaved > 0);

  // -- the orb ---------------------------------------------------------

  /* Counters rather than flags: each new value is one event, so the same
   * thing happening twice in a row is still two pulses. */
  let pulse = $state(0);
  let refusals = $state(0);

  /* React to a prop *changing*, not to it being there. The first run is the
   * value the screen opened with, which is not news. */
  function onChange(read, react) {
    let first = true;
    let last;
    $effect(() => {
      const now = read();
      if (first) {
        first = false;
        last = now;
        return;
      }
      if (now === last) return;
      last = now;
      untrack(() => react(now));
    });
  }

  onChange(
    () => outcome,
    (o) => {
      if (!o) return;
      if (o.ok) pulse += 1;
      else {
        refusals += 1;
        awaitingSave = false;
      }
    },
  );
  onChange(
    () => forgot,
    (f) => {
      if (f) f.ok ? (pulse += 1) : (refusals += 1);
    },
  );
  onChange(
    () => google?.status,
    (s) => {
      if (s === 'done') pulse += 1;
      else if (s === 'error') refusals += 1;
    },
  );

  const orbState = $derived(
    google?.status === 'running'
      ? 'tool.external'
      : saving || forgetting
        ? 'thinking'
        : dirty || confirming
          ? 'listening'
          : 'idle',
  );

  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

  const caption = $derived(
    google?.status === 'running'
      ? 'Talking to Google'
      : saving
        ? 'Saving'
        : forgetting
          ? 'Emptying long-term memory'
          : outcome && !outcome.ok
            ? 'Not saved'
            : dirty
              ? plural(unsaved, 'unsaved change', 'unsaved changes')
              : outcome?.ok
                ? 'Saved'
                : 'Nothing unsaved',
  );
  const detail = $derived(
    google?.status === 'running'
      ? 'In your browser. The one thing on this screen that leaves the machine.'
      : saving || forgetting
        ? ''
        : outcome && !outcome.ok
          ? outcome.error
          : dirty
            ? 'Save keeps them. Cancel throws them away.'
            : outcome?.ok && outcome.restart.length
              ? `${outcome.restart.join(', ')} — waiting for a restart.`
              : '',
  );

  // -- editing ---------------------------------------------------------

  function set(field, value) {
    edits = { ...edits, [field.key]: value };
    pulse += 1;
  }

  function reset(field) {
    set(field, structuredClone($state.snapshot(field.default)));
  }

  function save() {
    const snap = $state.snapshot(edits);
    const values = {};
    for (const key of changed) values[key] = snap[key];
    const typedIn = $state.snapshot(creds);
    const credentials = {};
    for (const key of credChanged) credentials[key] = typedIn[key];
    awaitingSave = true;
    onsave({ values, credentials });
  }

  // -- the roots editor ------------------------------------------------

  function roots(field) {
    return (held(field) ?? []).map((r) => ({ label: r.label ?? '', path: r.path ?? '' }));
  }

  function setRoot(field, index, part, value) {
    set(
      field,
      roots(field).map((r, i) => (i === index ? { ...r, [part]: value } : r)),
    );
  }

  function addRoot(field) {
    set(field, [...roots(field), { label: '', path: '' }]);
  }

  function dropRoot(field, index) {
    set(
      field,
      roots(field).filter((_, i) => i !== index),
    );
  }

  // -- credentials -----------------------------------------------------

  function typed(entry, value) {
    creds = { ...creds, [entry.key]: value };
    pulse += 1;
  }

  function clear(entry) {
    typed(entry, '');
  }

  const mb = (bytes) =>
    bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} kB`;

  const prompt = $derived(data?.prompt ?? null);
  /* The prompt is paid for out of the overhead reservation, along with the
   * bound tool schemas and the framing. Past this it is not an error -- it
   * quietly shrinks the memory slices instead, which is the kind of failure
   * this codebase keeps writing notes about, so it is said out loud. */
  const promptHeavy = $derived(Boolean(prompt) && prompt.tokens > prompt.builtin_tokens * 2.5);

  // -- moving between pages --------------------------------------------

  let navEl = $state(null);
  let paneEl = $state(null);
  /* The highlight is one element that slides, not a background that jumps
   * from button to button. It is placed by measuring the button it sits
   * behind -- never by counting rows -- and it only starts animating once it
   * has been placed, or opening the screen would slide it down from the top. */
  let pill = $state({ top: 0, height: 0 });
  let pillLive = $state(false);

  function place() {
    const button = navEl?.querySelector(`button[data-key="${section}"]`);
    if (!button) return;
    pill = { top: button.offsetTop, height: button.offsetHeight };
    if (!pillLive) requestAnimationFrame(() => (pillLive = true));
  }

  $effect(() => {
    section;
    pages.length;
    if (navEl) tick().then(place);
  });

  function go(key) {
    if (key === section) return;
    section = key;
    confirming = false;
    /* A new page starts at its top. Carrying the scroll over put you half-way
     * down a page you had never seen. */
    if (paneEl) paneEl.scrollTop = 0;
  }
</script>

<section class="settings">
  <header>
    <h1>Settings</h1>
    <div class="actions">
      <button class="primary" type="button" disabled={!dirty || saving} onclick={save}>
        {saving ? 'Saving…' : 'Save'}
      </button>
      <button type="button" class="ghost" onclick={onclose}>Cancel</button>
    </div>
  </header>

  {#if outcome}
    <div class="banner" class:bad={!outcome.ok} in:fly={{ y: -6, duration: ms(200), easing: cubicOut }}>
      {#if !outcome.ok}
        <p>{outcome.error}</p>
      {:else}
        <p>Saved to {data?.paths?.config}.</p>
        {#each outcome.warnings as warning}
          <p class="warn">{warning}</p>
        {/each}
        {#if outcome.restart.length}
          <p class="warn">
            {outcome.restart.join(', ')} — written, and not in use until it restarts.
          </p>
          {#if canRestart}
            <button type="button" class="ghost" onclick={onrestart}>Restart now</button>
          {:else}
            <p class="warn">Close and reopen the app to pick them up.</p>
          {/if}
        {/if}
      {/if}
    </div>
  {/if}

  {#if !data}
    <p class="lead">Reading the settings…</p>
  {:else}
    <div class="split">
      <nav bind:this={navEl}>
        <span
          class="pill"
          class:live={pillLive}
          style:transform={`translateY(${pill.top}px)`}
          style:height={`${pill.height}px`}
          aria-hidden="true"
        ></span>
        {#each pages as p (p.key)}
          <button type="button" data-key={p.key} class:on={p.key === section} onclick={() => go(p.key)}>
            {p.title}
          </button>
        {/each}
      </nav>

      <div class="pane" bind:this={paneEl}>
        {#key section}
          <div class="page" in:fly={{ y: 10, duration: ms(240), easing: cubicOut }}>
            {#if section === 'keys'}
              <!-- Every one of these is optional, and the app is expected to run
                   with all of them empty. What each costs when it is missing is
                   said next to it rather than left to be discovered. -->
              <h2>Keys and sign-in</h2>
              <p class="blurb">
                All of these are optional and none of them ship with the app — they are yours,
                and they are kept in {data.paths.credentials}, which the assistant treats as a
                credential: reading it shuts the web door for that turn.
              </p>

              {#each data.credentials as entry}
                <div class="field">
                  <label for={`cred-${entry.key}`}>{entry.label}</label>
                  <p class="help">
                    {entry.cost}
                    {#if entry.source === 'environment'}
                      It is currently coming from the environment or a .env file; typing here
                      overrides that.
                    {/if}
                  </p>
                  <div class="row">
                    <input
                      id={`cred-${entry.key}`}
                      type={entry.secret ? 'password' : 'text'}
                      autocomplete="off"
                      spellcheck="false"
                      value={entry.key in creds ? creds[entry.key] : ''}
                      placeholder={entry.set ? entry.hint : 'not set'}
                      oninput={(e) => typed(entry, e.currentTarget.value)}
                    />
                    {#if entry.set && entry.secret}
                      <button type="button" class="ghost" onclick={() => clear(entry)}>Clear</button>
                    {/if}
                  </div>
                  {#if entry.key in creds && creds[entry.key] === '' && entry.set}
                    <p class="help">Will be removed when you save.</p>
                  {/if}
                </div>
              {/each}

              <div class="field">
                <label for="google-signin">Google sign-in</label>
                <p class="help">
                  Calendar and mail are read-only, and they need a Google desktop OAuth client
                  above before this will do anything. A personal client stays in Google's testing
                  mode, which expires the sign-in every seven days — this is the button that
                  fixes that too.
                </p>
                <button
                  id="google-signin"
                  type="button"
                  class="ghost"
                  disabled={google?.status === 'running'}
                  onclick={ongoogle}
                >
                  {google?.status === 'running' ? 'Waiting for your browser…' : 'Sign in to Google'}
                </button>
                {#if google?.lines?.length}
                  <div class="log">
                    {#each google.lines as line}<p>{line}</p>{/each}
                  </div>
                {/if}
              </div>
            {:else if section === 'store'}
              <h2>Long-term memory</h2>
              <p class="blurb">
                Every finished turn is written here, on this machine, and nowhere else. It is what
                lets it remember something you said last week.
              </p>
              <div class="field">
                <p class="help">
                  {data.memory.remembered === null
                    ? 'The store could not be opened to count it.'
                    : `${data.memory.remembered} thing(s) remembered`}, {mb(data.memory.bytes)} at
                  {data.memory.path}
                </p>
                {#if forgot}
                  <p class="help">
                    {forgot.ok
                      ? `Deleted ${forgot.removed} thing(s). It starts again from here.`
                      : forgot.error}
                  </p>
                {/if}
                <!-- Two steps, and the second one says the number. There is no
                     undo behind this: the store is the only copy. -->
                {#if !confirming}
                  <button type="button" class="ghost" onclick={() => (confirming = true)}>
                    Delete everything it remembers
                  </button>
                {:else}
                  <div class="row">
                    <button type="button" class="ghost" disabled={forgetting} onclick={onforget}>
                      {forgetting
                        ? 'Deleting…'
                        : `Yes — delete ${data.memory.remembered ?? 'everything'}, permanently`}
                    </button>
                    <button type="button" class="ghost" onclick={() => (confirming = false)}>
                      Keep it
                    </button>
                  </div>
                  <p class="help">
                    This cannot be undone, and it also ends the conversation you are in — otherwise
                    it would be folded back into the store when you close the window.
                  </p>
                {/if}
              </div>
            {:else if current}
              <h2>{current.title}</h2>
              <p class="blurb">{current.blurb}</p>

              {#each current.fields as field (field.key)}
                <div class="field">
                  <div class="head">
                    <label for={field.key}>{field.label}</label>
                    {#if field.restart}<span class="tag">needs a restart</span>{/if}
                    {#if !same(field, held(field), field.default)}
                      <button type="button" class="link" onclick={() => reset(field)}>reset</button>
                    {/if}
                  </div>
                  {#if field.help}<p class="help">{field.help}</p>{/if}

                  {#if field.kind === 'bool'}
                    <label class="check">
                      <input
                        id={field.key}
                        type="checkbox"
                        checked={held(field)}
                        onchange={(e) => set(field, e.currentTarget.checked)}
                      />
                      <span>{held(field) ? 'On' : 'Off'}</span>
                    </label>
                  {:else if field.kind === 'long'}
                    <textarea
                      id={field.key}
                      rows="14"
                      spellcheck="false"
                      placeholder="Empty means the built-in prompt"
                      value={held(field)}
                      oninput={(e) => set(field, e.currentTarget.value)}
                    ></textarea>
                    {#if prompt}
                      <p class="help" class:warn={promptHeavy}>
                        The built-in is {prompt.builtin_tokens} tokens; what is saved now is
                        {prompt.tokens}, out of {prompt.overhead} reserved for the prompt, the tool
                        schemas and the framing together. Longer does not fail — it takes the room
                        from memory instead. Keep it short: this one grew to 673 tokens once and
                        the model started reciting it back at people.
                      </p>
                      <details>
                        <summary>Show the built-in</summary>
                        <pre>{prompt.builtin}</pre>
                      </details>
                    {/if}
                  {:else if field.kind === 'lines'}
                    <textarea
                      id={field.key}
                      rows="8"
                      spellcheck="false"
                      value={(held(field) ?? []).join('\n')}
                      oninput={(e) => set(field, e.currentTarget.value.split('\n'))}
                    ></textarea>
                  {:else if field.kind === 'roots'}
                    <div class="roots">
                      {#each roots(field) as root, i}
                        <div class="row">
                          <input
                            aria-label="name"
                            placeholder="name"
                            class="name"
                            value={root.label}
                            oninput={(e) => setRoot(field, i, 'label', e.currentTarget.value)}
                          />
                          <input
                            aria-label="path"
                            placeholder="C:/Users/you/Documents"
                            value={root.path}
                            oninput={(e) => setRoot(field, i, 'path', e.currentTarget.value)}
                          />
                          <button type="button" class="ghost" onclick={() => dropRoot(field, i)}>
                            Remove
                          </button>
                        </div>
                      {/each}
                      <button type="button" class="ghost" onclick={() => addRoot(field)}>
                        Add a folder
                      </button>
                    </div>
                  {:else}
                    <input
                      id={field.key}
                      type={field.kind === 'text' ? 'text' : 'number'}
                      step={field.kind === 'float' ? 'any' : '1'}
                      spellcheck="false"
                      value={held(field)}
                      oninput={(e) => set(field, e.currentTarget.value)}
                    />
                  {/if}

                  {#if field.measured}
                    <p class="help measured">{field.measured}</p>
                  {/if}
                </div>
              {/each}
            {/if}
          </div>
        {/key}
      </div>

      <!-- The empty third of the screen, given to the one thing in this program
           that shows state without words. It says the same as the caption under
           it, in the language the main window already taught. -->
      <aside class="watch" aria-live="polite">
        <div class="orb">
          <Orb state={orbState} {fps} {pulse} refuse={refusals} />
        </div>
        <p class="caption">{caption}</p>
        <p class="detail">{detail}</p>
      </aside>
    </div>
  {/if}
</section>

<style>
  /* Every grid declares its column. A track's floor is its own content, and
     the longest thing on this screen is a Windows path with no spaces in it. */
  .settings {
    display: grid;
    grid-template-rows: auto auto minmax(0, 1fr);
    grid-template-columns: minmax(0, 1fr);
    min-height: 0;
  }
  header {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    padding: 18px 26px 12px;
  }
  h1 {
    font-size: 25px;
    margin: 0;
    font-weight: 600;
  }
  .actions {
    display: flex;
    gap: 8px;
  }
  .actions button {
    font-size: 16px;
    min-width: 92px;
  }
  /* Three columns: where you are, what you are changing, and what it did.
     Fractions for the last two, like the main window, so a wider window gives
     the room to both rather than to a form already capped at a reading
     measure. */
  .split {
    display: grid;
    grid-template-columns: 220px minmax(0, 1fr) minmax(0, 0.5fr);
    min-height: 0;
  }
  nav {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 8px 12px 24px;
    overflow-y: auto;
    scrollbar-gutter: stable;
    border-right: 1px solid var(--line-soft);
  }
  .pill {
    position: absolute;
    top: 0;
    left: 12px;
    right: 12px;
    border-radius: var(--r-sm);
    background: var(--surface);
    pointer-events: none;
  }
  .pill.live {
    transition:
      transform 260ms cubic-bezier(0.22, 1, 0.36, 1),
      height 260ms cubic-bezier(0.22, 1, 0.36, 1);
  }
  nav button {
    position: relative;
    text-align: left;
    padding: 8px 12px;
    font-size: 16px;
    border-radius: var(--r-sm);
  }
  nav button.on,
  nav button.on:hover:not(:disabled) {
    background: transparent;
    color: var(--text);
  }
  .pane {
    min-height: 0;
    padding: 8px 30px 48px;
    overflow-y: auto;
    /* A scrollbar that appears part-way down reflows everything above it. */
    scrollbar-gutter: stable;
  }
  .page {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    align-content: start;
    gap: 24px;
  }
  h2 {
    font-size: 21px;
    margin: 0;
    font-weight: 600;
  }
  .blurb,
  .lead {
    margin: -14px 0 0;
    color: var(--dim);
    font-size: 15.5px;
    max-width: 62ch;
    overflow-wrap: anywhere;
  }
  .lead {
    margin: 0;
    padding: 8px 26px;
  }
  .field {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 7px;
    max-width: 62ch;
  }
  .head {
    display: flex;
    align-items: baseline;
    gap: 10px;
  }
  label {
    font-size: 16.5px;
    font-weight: 600;
  }
  .help {
    margin: 0;
    color: var(--dim);
    font-size: 15px;
    line-height: 1.55;
    /* A 74-character path has no spaces in it, so it is the minimum width of
       everything containing it unless this says otherwise. */
    overflow-wrap: anywhere;
  }
  .measured {
    border-left: 2px solid var(--line);
    padding-left: 12px;
  }
  .warn {
    color: var(--text);
  }
  .tag {
    font-size: 13px;
    color: var(--faint);
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 1px 9px;
  }
  .link {
    border: 0;
    padding: 0;
    font-size: 14px;
    color: var(--faint);
    text-decoration: underline;
  }
  .link:hover {
    background: transparent;
    color: var(--text);
  }
  input,
  textarea {
    background: var(--surface);
    border-color: var(--line);
    width: 100%;
    min-width: 0;
    font-size: 16px;
    transition: border-color 140ms ease;
  }
  input:focus,
  textarea:focus {
    border-color: color-mix(in srgb, var(--text) 40%, var(--line));
  }
  textarea {
    resize: vertical;
    line-height: 1.55;
  }
  .row {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .row input {
    min-width: 0;
  }
  .row .name {
    flex: 0 0 120px;
  }
  .roots {
    display: grid;
    gap: 8px;
  }
  .check {
    display: flex;
    align-items: center;
    gap: 10px;
    font-weight: 400;
  }
  .check input {
    width: auto;
  }
  .ghost {
    border-color: var(--line);
    white-space: nowrap;
    font-size: 15.5px;
    /* A grid item stretches, and a full-width "Delete everything it
       remembers" reads as a banner rather than as a button you press. */
    justify-self: start;
  }
  .banner {
    margin: 0 26px 8px;
    padding: 12px 16px;
    border: 1px solid var(--line);
    border-radius: var(--r-sm);
    display: grid;
    gap: 6px;
  }
  /* Red is a refusal, and a save that did not happen is one. Nothing else on
     this screen is coloured: deleting the store is destructive but it is not a
     refusal and it is not something leaving the machine, so it is white and it
     asks twice instead. */
  .banner.bad {
    border-color: var(--stop);
  }
  .banner p {
    margin: 0;
    font-size: 15.5px;
    overflow-wrap: anywhere;
  }
  .banner button {
    justify-self: start;
  }
  .log {
    display: grid;
    gap: 4px;
    border-left: 2px solid var(--line);
    padding-left: 12px;
  }
  .log p {
    margin: 0;
    font-size: 14.5px;
    color: var(--dim);
    overflow-wrap: anywhere;
  }
  details {
    font-size: 14.5px;
    color: var(--dim);
  }
  summary {
    cursor: pointer;
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    background: var(--surface);
    border-radius: var(--r-sm);
    padding: 12px;
    font: 13.5px/1.6 var(--mono);
  }

  /* -- the orb column -------------------------------------------------- */
  .watch {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    align-content: center;
    justify-items: center;
    gap: 4px;
    padding: 24px 22px 48px;
    /* A rule between two panels, which is what says where each begins -- not
       an edge on the orb, which must never have one. */
    border-left: 1px solid var(--line-soft);
    text-align: center;
    min-height: 0;
  }
  .watch .orb {
    width: min(240px, 80%);
    aspect-ratio: 1;
  }
  .caption {
    margin: 10px 0 0;
    font-size: 18px;
    font-weight: 600;
  }
  /* Always in the layout, so the caption does not jump when a detail comes
     and goes -- the same rule as the working strip under the transcript. */
  .detail {
    margin: 0;
    min-height: 3.2em;
    max-width: 30ch;
    color: var(--dim);
    font-size: 14.5px;
    line-height: 1.55;
    overflow-wrap: anywhere;
  }
</style>
