<script>
  import { onMount } from 'svelte';
  import { Orb } from './orb.js';

  let { state = 'idle', muted = false, mic = 0, out = 0, fps = { focused: 60, blurred: 10 } } =
    $props();

  let canvas;
  let orb;

  onMount(() => {
    orb = new Orb(canvas, { fpsFocused: fps.focused, fpsBlurred: fps.blurred });
    orb.start();
    return () => orb.destroy();
  });

  /* The canvas is not reactive, so the props are pushed into it. Levels go
   * every frame the socket sends one; the state only when it changes. */
  $effect(() => {
    orb?.setState(state);
  });
  $effect(() => {
    if (orb) orb.muted = muted;
  });
  $effect(() => {
    orb?.setLevels({ mic, out });
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
