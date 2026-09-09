import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// The shell loads the built files off disk, so every asset path has to be
// relative -- an absolute one resolves against the WebView's root and finds
// nothing.
export default defineConfig({
  base: './',
  plugins: [svelte()],
  clearScreen: false,
  server: { port: 5173, strictPort: true },
  build: { outDir: 'dist', emptyOutDir: true, target: 'esnext' },
});
