import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  base: './',
  plugins: [svelte()],
  build: {
    cssCodeSplit: true,
    sourcemap: false,
    target: 'es2022'
  },
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node'
  }
});
