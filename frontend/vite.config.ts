/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In development the API is proxied, so the browser sees one origin, the same as behind nginx
const api = process.env.OSTRAKA_API ?? 'http://127.0.0.1:6176';

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': api, '/mcp': api, '/ws': { target: api, ws: true } } },
  test: { environment: 'jsdom', include: ['src/**/*.test.{ts,tsx}'] },
});
