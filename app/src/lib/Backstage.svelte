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
   * and the panel read as an error log. The second version went too far the
   * other way: one flat list, evenly spaced, with the step as a quiet prefix
   * on every line. That has no shape either, because a turn is not twenty
   * things, it is five acts of three or four things each.
   *
   * So: one heading per act, a real gap between acts, and no repetition
   * inside one. The heading is the sidecar's own -- "3/5 tools" -- because
   * the number is the useful half and inventing a second wording for it here
   * would be a second thing to keep true.
   */
  let { trace = [], busy = false } = $props();

  let box;
  $effect(() => {
    trace.length;
    if (box) box.scrollTop = box.scrollHeight;
  });

  /* Consecutive lines of the same step are one act. Consecutive, not grouped
   * by name: the agent talks, calls a tool, talks again, and that really is
   * three acts in order rather than two acts shuffled together.
   *
   * And a `wake` line opens a turn, so it opens a block. The panel keeps the
   * last few questions rather than only the one in flight -- which is what
   * makes the break matter, because without it 5/5 filing runs straight into
   * the next 0/5 and there is nothing saying where one question ended. */
  const acts = $derived.by(() => {
    const out = [];
    for (const line of trace) {
      const last = out[out.length - 1];
      if (last && last.step === line.step && line.step !== 'wake') last.lines.push(line);
      else
        out.push({
          step: line.step,
          heading: line.heading || line.step,
          opens: line.step === 'wake',
          lines: [line],
        });
    }
    return out;
  });

  const latest = $derived(trace.at(-1) ?? null);

  /* What each line is about, in colour.
   *
   * Only what is worth finding at a glance carries one, and it is the same
   * scheme the orb uses: a tool running on this machine is white, one
   * reaching off it is teal, a refusal or a no is red. Memory keeps its own
   * blue because it is the half of a turn you cannot otherwise watch happen,
   * which is the reason this panel exists at all.
   */
  function tone(line) {
    const detail = line.detail || {};
    if (detail.ok === false || detail.why || detail.approved === false) return 'stop';
    if (detail.scope === 'external' || detail.query !== undefined) return 'external';
    if (line.step === 'tools') return 'local';
    if (line.step === 'memory_read' || line.step === 'memory_write') return 'memory';
    if (line.step === 'done') return 'done';
    return '';
  }
</script>

<aside>
  <header>
    <span>Backstage</span>
    <span class="count">{trace.length ? `${trace.length} steps` : ''}</span>
  </header>

  <!-- What it is doing right now, said once, at the top, where it does not
       move. Reading the bottom of a list that is still growing is reading a
       moving target; this is the same fact holding still. -->
  {#if busy}
    <div class="now">
      <span class="spinner" aria-hidden="true"></span>
      <div class="said">
        <span class="step">{latest?.heading ?? 'starting'}</span>
        <span class="text">{latest?.text ?? 'the turn has begun; nothing has been looked at yet.'}</span>
      </div>
    </div>
  {/if}

  <div class="scroller" bind:this={box}>
    {#each acts as act, i (i)}
      <section class="act" class:turn={act.opens && i > 0}>
        <h3>{act.heading}</h3>
        <ol>
          {#each act.lines as line, j (j)}
            <li class={tone(line)}>{line.text}</li>
          {/each}
        </ol>
      </section>
    {/each}
    {#if !trace.length && !busy}
      <p class="empty">Nothing has happened yet.</p>
    {/if}
  </div>
</aside>

<style>
  aside {
    display: grid;
    grid-template-rows: auto auto minmax(0, 1fr);
    /* The same ground as the rest of the window. A panel on its own shade
       reads as a drawer bolted to the side; the three columns are one
       surface, and the rule between them is what says where each begins. */
    background: var(--bg);
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
    font-size: 16px;
  }
  .count {
    color: var(--faint);
    font-weight: 400;
    font-size: 13.5px;
  }

  .now {
    display: flex;
    gap: 11px;
    align-items: flex-start;
    margin: 0 14px 12px;
    padding: 11px 13px;
    border-radius: var(--r-md);
    background: var(--raised);
  }
  .said {
    display: grid;
    gap: 3px;
    min-width: 0;
  }
  .step {
    font-size: 12.5px;
    color: var(--faint);
  }
  .now .text {
    overflow-wrap: anywhere;
    font-size: 14px;
    line-height: 1.5;
    color: var(--text);
  }
  /* One mote going round. Same shape as the one over the composer, and for
     the same reason: a bar would promise a proportion nobody can measure. */
  .spinner {
    flex: none;
    margin-top: 3px;
    width: 15px;
    height: 15px;
    border-radius: 50%;
    background: radial-gradient(circle at 50% 12%, var(--text) 0 2.6px, transparent 2.8px);
    animation: orbit 900ms linear infinite;
  }
  @keyframes orbit {
    to {
      transform: rotate(360deg);
    }
  }

  .scroller {
    overflow-y: auto;
    padding: 0 18px 18px;
  }
  /* The gap between two acts is what makes a turn readable as five things
     rather than twenty. Inside an act the lines are close, because they are
     one thing being described. */
  .act + .act {
    margin-top: 26px;
  }
  /* And a turn is not an act. A new question starting where the last one's
     filing ended -- 5/5 straight into 0/5 -- is the one boundary the panel
     was not drawing, and it is the boundary that says which question a line
     belongs to. Marked in the markup rather than by counting children: which
     block is first is a fact the component has, and positional CSS is wrong
     the first time something else is rendered above it. */
  .turn {
    margin-top: 30px;
    padding-top: 26px;
    border-top: 1px solid var(--line);
  }
  h3 {
    margin: 0 0 8px;
    font-size: 12.5px;
    font-weight: 600;
    color: var(--faint);
  }
  ol {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 9px;
  }
  li {
    padding-left: 11px;
    border-left: 2px solid var(--line);
    /* Trace lines carry paths and queries, which do not break on their own. */
    overflow-wrap: anywhere;
    font-size: 14.5px;
    line-height: 1.6;
    color: var(--dim);
  }
  li.memory {
    border-left-color: var(--memory);
  }
  li.local {
    border-left-color: var(--text);
  }
  li.external {
    border-left-color: var(--external);
    color: var(--text);
  }
  li.stop {
    border-left-color: var(--stop);
    color: var(--stop);
  }
  li.done {
    border-left-color: var(--faint);
    color: var(--text);
  }
  .empty {
    margin: 0;
    font-size: 14.5px;
    color: var(--faint);
  }
</style>
