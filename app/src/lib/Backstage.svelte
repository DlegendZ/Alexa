<script>
  /* The backstage channel.
   *
   * Every other message on the socket reports an outcome; these report what is
   * happening, which is the only thing that separates "long-term memory was
   * read and had nothing" from "long-term memory could not be opened". Only
   * the second is a fault, and from outside they produce the identical empty
   * answer.
   *
   * The first version of this panel shouted. Every line had an uppercase
   * heading over it in letter-spaced 10px caps, which meant a turn produced
   * twenty small headings and no shape at all -- the eye had nowhere to rest
   * and the panel read as an error log. The step is a quiet prefix now, the
   * text is the size of text, and only the three steps worth finding at a
   * glance carry a colour.
   */
  let { trace = [] } = $props();

  let box;
  $effect(() => {
    trace.length;
    if (box) box.scrollTop = box.scrollHeight;
  });

  /* "3/5 memory" is the heading the sidecar sends; the number is the useful
     half and the word is repeated on every line of that step. */
  const step = (line) => (line.heading || line.step || '').replace(/^\d+\/\d+\s*/, '');
</script>

<aside>
  <header>
    <span>Backstage</span>
    <span class="count">{trace.length ? `${trace.length} steps` : ''}</span>
  </header>
  <ol bind:this={box}>
    {#each trace as line, i (i)}
      <li data-step={line.step}>
        <span class="what">{step(line)}</span>
        <span class="text">{line.text}</span>
      </li>
    {/each}
    {#if !trace.length}
      <li class="empty"><span class="text">Nothing has happened yet.</span></li>
    {/if}
  </ol>
</aside>

<style>
  aside {
    display: grid;
    grid-template-rows: auto minmax(0, 1fr);
    background: var(--surface);
    border-left: 1px solid var(--line-soft);
    overflow: hidden;
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 16px 18px 10px;
    color: var(--text);
    font-weight: 600;
    font-size: 14px;
  }
  .count {
    color: var(--faint);
    font-weight: 400;
    font-size: 12.5px;
  }
  ol {
    list-style: none;
    margin: 0;
    padding: 0 18px 18px;
    display: grid;
    gap: 14px;
    align-content: start;
    overflow-y: auto;
  }
  li {
    display: grid;
    gap: 2px;
    padding-left: 11px;
    border-left: 2px solid var(--line);
  }
  .what {
    font-size: 12px;
    color: var(--faint);
  }
  .text {
    font-size: 13px;
    line-height: 1.55;
    color: var(--dim);
  }
  /* Memory is the half of a turn you cannot otherwise watch happen; tools are
     what actually touched something; the last line is the post-mortem. Nothing
     else earns a colour. */
  li[data-step='memory_read'],
  li[data-step='memory_write'] {
    border-left-color: var(--memory);
  }
  li[data-step='tools'] {
    border-left-color: var(--local);
  }
  li[data-step='done'] {
    border-left-color: var(--faint);
  }
  li[data-step='done'] .text {
    color: var(--text);
  }
  .empty {
    border-left-color: transparent;
  }
</style>
