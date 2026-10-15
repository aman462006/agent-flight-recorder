import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // Served publicly under /dashboard/ by the Vercel rewrite — the built
  // assets must reference themselves with that prefix or they 404.
  base: '/dashboard/',
  server: { proxy: { '/api': 'http://localhost:8000' } }
});
