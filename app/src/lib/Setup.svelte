<script>
  /* First run.
   *
   * Three things can be missing and they are three different jobs, so they are
   * listed apart rather than as "setup incomplete". Ollama is the one this app
   * cannot fix for you; the other two are a few gigabytes and a button.
   */
  let { setup, fetching, error = '', onfetch = () => {}, onskip = null } = $props();

  const mb = (bytes) => `${(bytes / 1e6).toFixed(0)} MB`;

  const percent = $derived(
    fetching && fetching.total ? Math.min(100, (fetching.done / fetching.total) * 100) : 0,
  );
</script>

<section class="setup">
  <h1>Nearly there</h1>
  <p class="lead">
    A first launch has a few gigabytes to fetch. It happens once, and nothing here leaves
    your machine except the downloads themselves.
  </p>

  <ul>
    <li class:done={setup.ollama}>
      <b>Ollama</b>
      {#if setup.ollama}
        running
      {:else}
        <!-- The one thing the app genuinely cannot do for you: it is a
             separate program, and installing it is not this window's business. -->
        not running. Start it, then press Retry — this app cannot install it for you.
      {/if}
    </li>
    <li class:done={!setup.model}>
      <b>The language model</b>
      {setup.model ? `${setup.model} has not been pulled yet` : 'pulled'}
    </li>
    <li class:done={!setup.models.length}>
      <b>Voice</b>
      {#if setup.models.length}
        {setup.models.length} file(s) still to download — wake word, voice activity,
        the transcriber and the speaker. Without them, typing still works.
      {:else}
        ready
      {/if}
    </li>
  </ul>

  {#if fetching}
    <div class="progress">
      <div class="bar"><div class="fill" style="width:{percent}%"></div></div>
      <div class="what">
        {fetching.what || 'starting'}
        {#if fetching.total}
          — {mb(fetching.done)} of {mb(fetching.total)}
        {:else if fetching.status}
          — {fetching.status}
        {/if}
      </div>
    </div>
  {:else}
    <div class="buttons">
      <button class="primary" type="button" onclick={onfetch}>
        {setup.ollama ? 'Download what is missing' : 'Retry'}
      </button>
      {#if onskip}
        <!-- Only offered when there is something behind this screen to use.
             Voice is the skippable half; the model is not. -->
        <button class="ghost" type="button" onclick={onskip}>Not now — let me type</button>
      {/if}
    </div>
  {/if}

  {#if error}
    <p class="error">{error}</p>
  {/if}
</section>

<style>
  .setup {
    max-width: 520px;
    margin: 0 auto;
    align-self: center;
    display: grid;
    gap: 16px;
    padding: 32px 24px;
  }
  h1 {
    font-family: var(--serif);
    font-size: 26px;
    margin: 0;
    font-weight: 600;
  }
  .lead {
    margin: 0;
    color: var(--dim);
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 10px;
  }
  /* Outstanding is quiet, done is white. Neither is teal and neither is red:
     teal is something leaving this machine and red is a refusal, and a setup
     step that has not run yet is neither. Note 126 converted the progress bar
     in this file and missed these two rules, which is the same shape as
     `mkdir` surviving on `write_file` after the other two lost it. */
  li {
    border-left: 2px solid var(--line);
    padding-left: 14px;
    color: var(--dim);
    font-size: 14px;
    line-height: 1.55;
  }
  li.done {
    border-color: var(--text);
  }
  li b {
    display: block;
    color: var(--text);
    font-weight: 600;
  }
  .buttons {
    display: flex;
    gap: 8px;
  }
  .buttons .ghost {
    border-color: var(--line);
  }
  .progress {
    display: grid;
    gap: 8px;
  }
  .bar {
    height: 6px;
    border-radius: 3px;
    background: var(--raised);
    overflow: hidden;
  }
  .fill {
    height: 100%;
    background: var(--text);
    transition: width 120ms linear;
  }
  .what {
    color: var(--dim);
    font-size: 13px;
  }
  .error {
    color: var(--stop);
    margin: 0;
  }
</style>
