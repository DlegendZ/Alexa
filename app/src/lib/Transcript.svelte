<script>
  let { entries = [], onanswer = () => {} } = $props();

  let box;

  /* Follow the bottom, but only while the reader is already there. Yanking
   * someone back down while they are reading the middle of an answer is the
   * one thing a transcript can do that makes it unreadable. */
  let stuck = $state(true);
  const onScroll = () => {
    stuck = box.scrollHeight - box.scrollTop - box.clientHeight < 48;
  };

  $effect(() => {
    entries.length;
    entries.at(-1)?.text?.length;
    if (stuck && box) box.scrollTop = box.scrollHeight;
  });
</script>

<div class="transcript" bind:this={box} onscroll={onScroll}>
  <div class="column">
    {#each entries as entry (entry.id)}
      {#if entry.kind === 'you'}
        <div class="said you">{entry.text}</div>
      {:else if entry.kind === 'sunday'}
        <div class="said them">{entry.text}</div>
      {:else if entry.kind === 'note'}
        <!-- One quiet line, in the place it happened. A redaction shown in a
             side panel is a redaction nobody reads at the moment it matters. -->
        <div class="note {entry.tone}"><span class="dot"></span>{entry.text}</div>
      {:else if entry.kind === 'confirm'}
        <div class="confirm">
          <div class="ask">{entry.text}</div>
          {#if entry.answered}
            <div class="answered">{entry.answered}</div>
          {:else}
            <div class="buttons">
              <!-- Keeping it is the safe half, so it comes first and a stray
                   Return does nothing. Nothing is written or destroyed until
                   somebody says so. -->
              <button type="button" onclick={() => onanswer(entry.id, false)}>
                {entry.action === 'delete' ? 'Keep it' : 'Leave it'}
              </button>
              <button class="primary" type="button" onclick={() => onanswer(entry.id, true)}>
                {entry.action === 'delete' ? 'Delete' : 'Overwrite'}
              </button>
            </div>
          {/if}
        </div>
      {/if}
    {/each}
  </div>
</div>

<style>
  .transcript {
    overflow-y: auto;
    padding: 28px 28px 8px;
  }
  /* A reading column rather than the full width of the window. Long lines are
     the fastest way to make prose unreadable, and this is mostly prose. */
  .column {
    max-width: 720px;
    margin: 0 auto;
    display: grid;
    gap: 26px;
    align-content: start;
  }

  .said {
    white-space: pre-wrap;
    line-height: 1.65;
  }
  /* No "YOU" and "SUNDAY" labels shouting in caps above every line. Who said
     it is carried by where it sits and what it looks like, which is how a
     conversation reads on paper. */
  .you {
    justify-self: end;
    max-width: 80%;
    background: var(--surface);
    padding: 13px 18px;
    border-radius: var(--r-lg) var(--r-lg) 6px var(--r-lg);
    color: var(--text);
  }
  .them {
    font-size: 18px;
    line-height: 1.72;
    color: var(--text);
  }

  .note {
    display: flex;
    align-items: baseline;
    gap: 11px;
    font-size: 15px;
    color: var(--dim);
  }
  .dot {
    flex: none;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--faint);
    transform: translateY(-2px);
  }
  .note.local .dot {
    background: var(--local);
  }
  .note.external .dot {
    background: var(--external);
  }
  .note.stop {
    color: var(--stop);
  }
  .note.stop .dot {
    background: var(--stop);
  }

  .confirm {
    display: grid;
    gap: 12px;
    background: var(--stop-soft);
    border: 1px solid color-mix(in srgb, var(--stop) 35%, transparent);
    border-radius: var(--r-md);
    padding: 16px;
  }
  .ask {
    color: var(--text);
  }
  .answered {
    font-size: 13.5px;
    color: var(--dim);
  }
  .buttons {
    display: flex;
    gap: 8px;
  }
  .buttons button {
    border-color: var(--line);
  }
</style>
