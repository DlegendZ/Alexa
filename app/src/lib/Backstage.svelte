<script>
  /* The backstage channel. Every other message on the socket reports an
   * outcome; these report what is happening, which is the only thing that
   * separates "long-term memory was read and had nothing" from "long-term
   * memory could not be opened". Only the second is a fault, and from outside
   * they produce the identical empty answer. */
  let { trace = [], sentences = [] } = $props();

  let box;
  $effect(() => {
    trace.length;
    if (box) box.scrollTop = box.scrollHeight;
  });
</script>

<aside>
  <h2>Backstage</h2>
  <ul bind:this={box} class="trace">
    {#each trace as line, i (i)}
      <li data-step={line.step}>
        <b>{line.heading || line.step}</b>
        <span>{line.text}</span>
      </li>
    {/each}
    {#if !trace.length}
      <li class="empty"><span>nothing has happened yet</span></li>
    {/if}
  </ul>

  <h2>Sentences to speech</h2>
  <ul class="sentences">
    {#each sentences as sentence, i (i)}
      <li>{sentence}</li>
    {/each}
  </ul>
</aside>

<style>
  aside {
    display: grid;
    grid-template-rows: auto minmax(0, 1fr) auto auto;
    gap: 8px;
    background: var(--panel);
    border-left: 1px solid var(--line);
    padding: 16px;
    overflow: hidden;
  }
  h2 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--dim);
    margin: 0;
    font-weight: 600;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 5px;
    align-content: start;
    font-size: 12px;
  }
  .trace {
    overflow-y: auto;
  }
  .trace li {
    border-left: 2px solid var(--line);
    padding-left: 9px;
    color: var(--dim);
  }
  .trace li b {
    display: block;
    color: #b9bfcc;
    font-weight: 600;
    font-size: 10.5px;
    text-transform: uppercase;
    letter-spacing: 0.07em;
  }
  /* Memory is the half of a turn you cannot otherwise watch happen. */
  .trace li[data-step='memory_read'],
  .trace li[data-step='memory_write'] {
    border-color: var(--memory);
  }
  .trace li[data-step='tools'] {
    border-color: var(--local);
  }
  .trace li[data-step='done'] {
    border-color: #3a3f49;
    color: #b9bfcc;
  }
  .empty {
    opacity: 0.6;
  }
  .sentences {
    max-height: 20vh;
    overflow-y: auto;
    color: var(--dim);
  }
  .sentences li {
    border-left: 2px solid var(--local);
    padding-left: 10px;
  }
</style>
