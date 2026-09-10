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

  /* The other half of that rule. Not following you down is right; leaving you
   * to drag a scrollbar back to a reply that is still being written is not.
   * The button only exists while it has somewhere to go. */
  const toBottom = () => {
    box?.scrollTo({ top: box.scrollHeight, behavior: 'smooth' });
    stuck = true;
  };

  /* One scroll per frame, not one per token.
   *
   * Reading `scrollHeight` forces the browser to lay the document out, and a
   * reply arrives a word at a time -- so following the bottom naively is a
   * synchronous reflow per word on a page that is already reflowing. Coalesced
   * to the next frame, which is the soonest it could be seen anyway. */
  let queued = 0;
  const follow = () => {
    if (queued || !box) return;
    queued = requestAnimationFrame(() => {
      queued = 0;
      if (stuck && box) box.scrollTop = box.scrollHeight;
    });
  };

  $effect(() => {
    entries.length;
    entries.at(-1)?.text?.length;
    follow();
    return () => {
      if (queued) cancelAnimationFrame(queued);
      queued = 0;
    };
  });
</script>

<div class="pane">
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

  {#if !stuck}
    <button class="jump" type="button" title="Back to the newest message" onclick={toBottom}>
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 5v13M12 18.5l-6.2-6.2M12 18.5l6.2-6.2" />
      </svg>
      <span>Newest</span>
    </button>
  {/if}
</div>

<style>
  .pane {
    position: relative;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
  }
  .transcript {
    overflow-y: auto;
    /* Reserve the scrollbar's channel whether or not there is a scrollbar.
       Without it the first reply long enough to scroll takes 10px out of the
       column *while it is being written*, so every line already on screen
       reflows mid-sentence. Measured: 512 → 502 and back, several times in one
       turn. */
    scrollbar-gutter: stable;
    padding: 28px 28px 8px;
  }
  /* A reading column rather than the full width of the window. Long lines are
     the fastest way to make prose unreadable, and this is mostly prose. */
  .column {
    max-width: 720px;
    margin: 0 auto;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 26px;
    align-content: start;
  }

  /* Floats over the transcript rather than sitting under it, because the row
     it would otherwise take is a row the conversation is using. */
  .jump {
    position: absolute;
    left: 50%;
    bottom: 14px;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 7px 15px 7px 12px;
    font-size: 14px;
    border-radius: 999px;
    border: 1px solid var(--line);
    background: var(--raised);
    color: var(--text);
    box-shadow: 0 6px 20px rgb(0 0 0 / 0.35);
  }
  .jump:hover {
    background: var(--line);
  }
  .jump svg {
    width: 17px;
    height: 17px;
    fill: none;
    stroke: currentColor;
    stroke-width: 2.2;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  .said {
    white-space: pre-wrap;
    /* A path or a URL is one word as far as line breaking is concerned, and
       `pre-wrap` will not break it. Measured: a 74-character path made the
       bubble 726px wide inside a 472px column, which is how the conversation
       ended up reaching into the backstage. Anything is allowed to break now,
       because a column that holds is worth more than a filename that does. */
    overflow-wrap: anywhere;
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
    min-width: 0;
    overflow-wrap: anywhere;
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
  /* On this machine is white; only what leaves it gets a colour. */
  .note.local .dot {
    background: var(--text);
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
