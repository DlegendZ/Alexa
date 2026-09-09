<script>
  let { entries = [], onanswer = () => {} } = $props();

  let box;

  /* Follow the bottom, but only while the reader is already there. Yanking
   * someone back down while they are reading the middle of an answer is the
   * one thing a transcript can do that makes it unreadable. */
  let stuck = $state(true);
  const onScroll = () => {
    stuck = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  };

  $effect(() => {
    entries.length;
    if (stuck && box) box.scrollTop = box.scrollHeight;
  });
</script>

<div class="transcript" bind:this={box} onscroll={onScroll}>
  {#each entries as entry (entry.id)}
    {#if entry.kind === 'you' || entry.kind === 'sunday'}
      <div class="turn">
        <div class="who">{entry.kind}</div>
        <div class="said">{entry.text}</div>
      </div>
    {:else if entry.kind === 'note'}
      <div class="note {entry.tone}">{entry.text}</div>
    {:else if entry.kind === 'confirm'}
      <div class="note stop confirm">
        <div>{entry.text}{entry.answered ? `  → ${entry.answered}` : ''}</div>
        {#if !entry.answered}
          <div class="buttons">
            <!-- Keeping it is the safe half, so it is the button that has
                 focus. Nothing is written or destroyed until somebody says so,
                 and a stray Return should mean no. -->
            <button type="button" onclick={() => onanswer(entry.id, false)}>
              {entry.action === 'delete' ? 'Keep it' : 'Leave it'}
            </button>
            <button type="button" onclick={() => onanswer(entry.id, true)}>
              {entry.action === 'delete' ? 'Delete' : 'Overwrite'}
            </button>
          </div>
        {/if}
      </div>
    {/if}
  {/each}
</div>

<style>
  .transcript {
    display: grid;
    gap: 14px;
    align-content: start;
    overflow-y: auto;
    padding: 4px 2px;
  }
  .turn {
    display: grid;
    gap: 4px;
  }
  .who {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--dim);
  }
  .said {
    white-space: pre-wrap;
  }
  .note {
    font-size: 13px;
    color: var(--dim);
    border-left: 2px solid var(--line);
    padding-left: 10px;
  }
  .note.local {
    border-color: var(--local);
  }
  .note.external {
    border-color: var(--external);
  }
  .note.stop {
    border-color: var(--stop);
    color: #e5a3a1;
  }
  .confirm {
    display: grid;
    gap: 8px;
  }
  .buttons {
    display: flex;
    gap: 8px;
  }
</style>
