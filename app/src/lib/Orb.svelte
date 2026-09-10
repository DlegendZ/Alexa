<script>
  import { onMount } from 'svelte';
  import { Orb } from './orb.js';

  let {
    state = 'idle',
    mic = 0,
    out = 0,
    fps = { focused: 60, blurred: 10 },
    pulse = 0,
    refuse = 0,
  } = $props();

  let canvas;
  let orb;

  onMount(() => {
    orb = new Orb(canvas, { fpsFocused: fps.focused, fpsBlurred: fps.blurred });
    orb.start();
    return () => orb.destroy();
  });

  /* The canvas is not reactive, so the props are pushed into it. Levels go
   * every frame the socket sends one; the state only when it changes.
   *
   * The frame rates need pushing too, and did not have an effect of their own.
   * `onMount` runs in the child before the parent, and the parent only learns
   * the real numbers when the `settings()` promise resolves -- so the orb was
   * always built from the fallback and `[ui] fps_focused` never reached it.
   * Invisible while the config happens to hold the defaults, which is note
   * 146's failure mode with the lookup succeeding. */
  $effect(() => {
    if (!orb) return;
    orb.fpsFocused = fps.focused;
    orb.fpsBlurred = fps.blurred;
  });
  $effect(() => {
    orb?.setState(state);
  });
  $effect(() => {
    orb?.setLevels({ mic, out });
  });

  /* Two events that are not states, for a caller with no socket to send them
   * as states -- the settings screen. Counters rather than flags, so the same
   * thing twice in a row is still two: `pulse` is the wake flash, from the
   * inside, and `refuse` is the red one that goes back to whatever it
   * interrupted. Declared after the state effect on purpose: when both move in
   * one tick the refusal has to land on top of the new state, not under it. */
  let pulsed = 0;
  let refused = 0;
  $effect(() => {
    const n = pulse;
    if (!orb || n === pulsed) return;
    pulsed = n;
    orb.flashWake();
  });
  $effect(() => {
    const n = refuse;
    if (!orb || n === refused) return;
    refused = n;
    orb.setState('blocked');
  });
</script>

<canvas bind:this={canvas} aria-hidden="true"></canvas>

<style>
  canvas {
    display: block;
    width: 100%;
    height: 100%;
  }
</style>
