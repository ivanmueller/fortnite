import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev server: http://localhost:5173. Saving any file under src/ updates Chrome instantly (HMR).
// /api requests are proxied to the Python API on port 8000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    open: true,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
});
